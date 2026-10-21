"""Batch processing orchestration (SPEC section 9)."""
from __future__ import annotations

import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Iterator, List, Optional, Tuple

import pyarrow as pa
import pyarrow.parquet as pq

from . import normalize as N
from . import schema
from .anomalies import AnomalyStore
from .config import (
    FACT_SOURCES, FORUM_USER_COLUMNS, RAW_DIR, V1_COLUMNS, backend_mode,
    ensure_dirs,
)
from .facts import finalize as facts_finalize
from .ingest import (
    CHUNK, BatchRejected, DuplicateBatch, Manifest, SILVER_COLUMNS,
    insert_aux, insert_bronze, insert_schedule, insert_silver,
    normalize_record,
)
from .reference import load_reference, reference_maps
from .resolver import get_resolver, reset_cache

log = logging.getLogger(__name__)

try:  # optional fast JSON
    import orjson as _orjson

    def _json_dumps(obj) -> str:
        return _orjson.dumps(obj, default=str).decode("utf-8")
except Exception:  # pragma: no cover
    def _json_dumps(obj) -> str:
        return json.dumps(obj, default=str)

_RUN_KEY = "default"
_LAKE_KEYS: Dict[int, str] = {}


# ---------------------------------------------------------------------------
# lake bootstrap
# ---------------------------------------------------------------------------

def connect(path: str | Path, load_refs: bool = True, threads: int = 2):
    import duckdb

    existed = Path(path).exists()
    con = duckdb.connect(str(path))
    # Bound DuckDB's memory use so the Python/pyarrow side of the pipeline
    # still fits inside a small (~2 GB) machine.  Without this DuckDB claims
    # 80% of system RAM and the process is OOM-killed during a full load.
    con.execute("SET memory_limit='2600MB'")
    con.execute("SET threads=%d" % threads)
    con.execute("SET preserve_insertion_order=false")
    con.execute("SET checkpoint_threshold='512MB'")
    lake_key = str(Path(path).resolve())
    _LAKE_KEYS[id(con)] = lake_key
    if not existed:
        reset_cache(lake_key)
    con.execute(schema.DDL)
    con.execute(schema.VIEW_DDL)
    if load_refs:
        from .config import REFERENCE_DIR
        load_reference(con, REFERENCE_DIR)
    return con


def reset_lake(path: str | Path) -> None:
    p = Path(path)
    if p.exists():
        p.unlink()
    reset_cache()


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _detect_regressions(manifest: Manifest, rows_iter: Iterable[dict]) -> Tuple[list, dict]:
    """Scan rows once for converter-regression signatures and row stats."""
    bugs = []
    seen = {"codeshare_string": False, "sex_string": False,
            "delta_dt": False, "n": 0, "dep_nonnull": 0}
    for row in rows_iter:
        seen["n"] += 1
        cs = row.get("codeshare")
        if isinstance(cs, str) and cs.strip() in ("Own", "Operated",
                                                   "own", "operated",
                                                   "OWN", "OPERATED"):
            seen["codeshare_string"] = True
        sex = row.get("sex")
        if manifest.source_name == "flyer-forum-users" and isinstance(sex, str) \
                and sex.strip() in ("Male", "Female"):
            seen["sex_string"] = True
        if N.parse_date(row.get("departure_date")) is not None:
            seen["dep_nonnull"] += 1
        prog = (row.get("ff_program") or "")
        num = (row.get("ff_number") or "")
        if isinstance(prog, str) and isinstance(num, str):
            if prog.strip() == "Delta SkyMiles" and num.strip().upper().startswith("DT "):
                seen["delta_dt"] = True
    n = seen["n"]
    if seen["codeshare_string"]:
        bugs.append(("BUG-01", "codeshare string-form 'Own'/'Operated'"))
    if seen["sex_string"]:
        bugs.append(("BUG-02", "flyer-forum-users sex 'Male'/'Female'"))
    if manifest.source_name == "pointz" and n > 0 and seen["dep_nonnull"] == 0:
        bugs.append(("BUG-04", "pointz departure_date null on all rows"))
    if manifest.source_name == "flyer-forum-flights" and n > 0 and seen["dep_nonnull"] == 0:
        bugs.append(("BUG-05", "flyer-forum-flights departure_date null on all rows"))
    if seen["delta_dt"]:
        bugs.append(("BUG-06", "Delta SkyMiles ff_number with 'DT ' prefix"))
    return bugs, seen


def _iter_checksum(rows_iter: Iterable[dict], columns: List[str]):
    h = hashlib.sha256()
    first = True
    for row in rows_iter:
        if not first:
            h.update(b"\x1f")
        first = False
        h.update(N.row_checksum_payload(row, columns).encode("utf-8"))
    return "sha256:" + h.hexdigest()


def _write_parquet(path: Path, rows_iter: Iterable[dict], columns: List[str]):
    import pyarrow as pa
    import pyarrow.parquet as pq

    path.parent.mkdir(parents=True, exist_ok=True)
    writer = None
    buf: List[dict] = []
    try:
        for row in rows_iter:
            buf.append({c: (None if row.get(c) is None else str(row.get(c)))
                        for c in columns})
            if len(buf) >= CHUNK:
                tbl = pa.Table.from_pylist(buf)
                if writer is None:
                    writer = pq.ParquetWriter(str(path), tbl.schema)
                writer.write_table(tbl)
                buf = []
        if writer is None:
            tbl = pa.Table.from_pylist(buf, schema=pa.schema(
                [(c, pa.string()) for c in columns]))
            writer = pq.ParquetWriter(str(path), tbl.schema)
            if buf:
                writer.write_table(tbl)
        elif buf:
            writer.write_table(pa.Table.from_pylist(buf))
    finally:
        if writer is not None:
            writer.close()
    if not path.exists():
        pq.write_table(pa.Table.from_pylist([], schema=pa.schema(
            [(c, pa.string()) for c in columns])), str(path))


# ---------------------------------------------------------------------------
# core ingest
# ---------------------------------------------------------------------------

def _scan_regression_row(manifest: Manifest, row: dict, seen: dict) -> None:
    seen["n"] += 1
    cs = row.get("codeshare")
    if isinstance(cs, str) and cs.strip() in ("Own", "Operated",
                                              "own", "operated",
                                              "OWN", "OPERATED"):
        seen["codeshare_string"] = True
    sex = row.get("sex")
    if manifest.source_name == "flyer-forum-users" and isinstance(sex, str) \
            and sex.strip() in ("Male", "Female"):
        seen["sex_string"] = True
    if N.parse_date(row.get("departure_date")) is not None:
        seen["dep_nonnull"] += 1
    prog = row.get("ff_program") or ""
    num = row.get("ff_number") or ""
    if isinstance(prog, str) and isinstance(num, str):
        if prog.strip() == "Delta SkyMiles" and num.strip().upper().startswith("DT "):
            seen["delta_dt"] = True


def _regression_bugs(manifest: Manifest, seen: dict) -> list:
    bugs = []
    if seen["codeshare_string"]:
        bugs.append(("BUG-01", "codeshare string-form 'Own'/'Operated'"))
    if seen["sex_string"]:
        bugs.append(("BUG-02", "flyer-forum-users sex 'Male'/'Female'"))
    if manifest.source_name == "pointz" and seen["n"] > 0 and seen["dep_nonnull"] == 0:
        bugs.append(("BUG-04", "pointz departure_date null on all rows"))
    if manifest.source_name == "flyer-forum-flights" and seen["n"] > 0 \
            and seen["dep_nonnull"] == 0:
        bugs.append(("BUG-05", "flyer-forum-flights departure_date null on all rows"))
    if seen["delta_dt"]:
        bugs.append(("BUG-06", "Delta SkyMiles ff_number with 'DT ' prefix"))
    return bugs


def ingest_batch(con, manifest_dict: Dict[str, Any],
                 rows_factory: Callable[[], Iterable[dict]],
                 run_key: str = _RUN_KEY,
                 finalize_after: bool = True,
                 verify_checksum: bool = True) -> Dict[str, Any]:
    """Ingest one batch.

    ``rows_factory`` must return a fresh iterator every call.
    """
    run_key = _LAKE_KEYS.get(id(con), run_key)
    manifest = Manifest.from_dict(manifest_dict)
    columns = manifest.columns
    mode = backend_mode()
    started = time.time()

    # checksum verification (one streaming pass)
    if verify_checksum:
        computed = _iter_checksum(rows_factory(), columns)
        if computed != manifest.content_checksum:
            raise BatchRejected(
                f"content_checksum mismatch: manifest={manifest.content_checksum} "
                f"computed={computed}")

    # idempotency
    existing = con.execute(
        "SELECT batch_id FROM bronze.batches WHERE source_name=? AND content_checksum=?",
        [manifest.source_name, manifest.content_checksum]).fetchone()
    if existing and not manifest.force:
        raise DuplicateBatch(str(existing[0]))
    if con.execute("SELECT 1 FROM bronze.batches WHERE batch_id=?",
                   [manifest.batch_id]).fetchone():
        return {
            "batch_id": manifest.batch_id, "source_name": manifest.source_name,
            "status": "noop", "inserted": 0, "updated": 0, "merged": 0,
            "anomalies_opened": 0, "anomalies_resolved": 0,
            "duration_ms": int((time.time() - started) * 1000), "errors": 0,
        }

    ensure_dirs()
    parquet_path = Path(RAW_DIR) / f"{manifest.batch_id}.parquet"

    anoms = AnomalyStore()

    counts = {"inserted": 0, "updated": 0, "merged": 0}
    seen = {"codeshare_string": False, "sex_string": False,
            "delta_dt": False, "n": 0, "dep_nonnull": 0}
    con.execute("BEGIN TRANSACTION")
    try:
        # raw archive is written first; if strict mode rejects below we
        # remove it again.
        _write_parquet(parquet_path, rows_factory(), columns)

        refs = reference_maps(con)

        buf_bronze, buf_silver, buf_aux, buf_sched = [], [], [], []
        source_row = 0
        for row in rows_factory():
            source_row += 1
            _scan_regression_row(manifest, row, seen)
            rid = "r_" + hashlib.sha256(
                f"{manifest.batch_id}|{source_row}".encode()).hexdigest()[:32]
            payload = _json_dumps({c: row.get(c) for c in columns})
            silver, record_key, row_anoms = normalize_record(
                raw=row, manifest=manifest, refs=refs,
                batch_id=manifest.batch_id, source_row=source_row,
                record_id=rid, anomaly_sink=anoms)
            silver["payload"] = payload
            for a in row_anoms:
                anoms.emit(a["type"], a["severity"], a["dedup_key"],
                           a["details"], record_ids=[rid])
            buf_bronze.append((rid, manifest.batch_id, source_row, payload))
            buf_silver.append(silver)
            buf_aux.append((rid, record_key,
                            N.clean_text(row.get("ticket_number")),
                            N.clean_text(row.get("pnr"))))
            if manifest.is_schedule:
                buf_sched.append((
                    "s_" + rid[2:],
                    silver["flight_number_norm"], silver["from_code_norm"],
                    silver["to_code_norm"], silver["departure_time"],
                    silver["arrival_time"], silver["_validity_start_md"],
                    silver["_validity_end_md"], silver["_days_of_week_mask"],
                    silver["aircraft"], silver["travel_time"],
                    manifest.source_name, manifest.batch_id))
            if len(buf_bronze) >= CHUNK:
                insert_bronze(con, buf_bronze)
                insert_silver(con, buf_silver)
                insert_aux(con, buf_aux)
                counts["inserted"] += len(buf_bronze)
                buf_bronze, buf_silver, buf_aux = [], [], []
        if buf_bronze:
            insert_bronze(con, buf_bronze)
            insert_silver(con, buf_silver)
            insert_aux(con, buf_aux)
            counts["inserted"] += len(buf_bronze)
        if buf_sched:
            insert_schedule(con, buf_sched)

        bugs = _regression_bugs(manifest, seen)
        if bugs and mode == "strict":
            raise BatchRejected(
                "converter regression: " + ", ".join(b[0] for b in bugs))

        con.execute(
            "INSERT INTO raw.manifests (batch_id, source_name, schema_version, "
            "batch_kind, effective_date, trust_identity, trust_fact, is_schedule, "
            "content_checksum, supersedes, retracts, parquet_path) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            [manifest.batch_id, manifest.source_name, manifest.schema_version,
             manifest.batch_kind, manifest.effective_date, manifest.trust_identity,
             manifest.trust_fact, manifest.is_schedule,
             manifest.content_checksum, json.dumps(manifest.supersedes),
             json.dumps(manifest.retracts), str(parquet_path)])
        con.execute(
            "INSERT INTO bronze.batches (batch_id, source_name, schema_version, "
            "batch_kind, effective_date, trust_identity, trust_fact, is_schedule, "
            "content_checksum) VALUES (?,?,?,?,?,?,?,?,?)",
            [manifest.batch_id, manifest.source_name, manifest.schema_version,
             manifest.batch_kind, manifest.effective_date, manifest.trust_identity,
             manifest.trust_fact, manifest.is_schedule,
             manifest.content_checksum])

        # supersedes / retracts
        if manifest.supersedes or manifest.retracts:
            reset_cache(run_key)
        if manifest.supersedes:
            for old in manifest.supersedes:
                con.execute("UPDATE bronze.records SET is_active=FALSE WHERE batch_id=?",
                            [old])
                con.execute(
                    "UPDATE identity.person_keys SET is_active=FALSE WHERE source_record_id IN "
                    "(SELECT record_id FROM bronze.records WHERE batch_id=?)", [old])
        if manifest.retracts:
            for rid in manifest.retracts:
                con.execute("UPDATE bronze.records SET is_active=FALSE WHERE record_id=?",
                            [rid])
                con.execute(
                    "UPDATE identity.person_keys SET is_active=FALSE WHERE source_record_id=?",
                    [rid])

        for bug, desc in bugs:
            anoms.emit("CONVERTER_REGRESSION", "hard",
                       f"{bug}|{manifest.batch_id}", {"signature": desc, "bug": bug})

        # identity resolution
        if not manifest.is_schedule:
            resolver = get_resolver(con, run_key)
            resolver.process_batch(con, manifest, manifest.batch_id, anoms)

        # gold facts + cross-record anomalies
        if finalize_after:
            facts_finalize(con, anoms, manifest.batch_id)

        opened, updated_anoms = anoms.flush(con, manifest.batch_id)

        # snapshot handling
        if manifest.batch_kind == "snapshot":
            con.execute(
                "UPDATE identity.person_keys SET is_active=FALSE WHERE source_record_id IN "
                "(SELECT record_id FROM bronze.records WHERE source_name=? AND batch_id<>? "
                "AND is_active)", [manifest.source_name, manifest.batch_id])
            con.execute(
                "UPDATE bronze.records SET is_active=FALSE WHERE source_name=? "
                "AND batch_id<>? AND is_active", [manifest.source_name, manifest.batch_id])
            reset_cache(run_key)

        con.execute("COMMIT")
    except Exception as exc:  # noqa: BLE001
        con.execute("ROLLBACK")
        try:
            parquet_path.unlink(missing_ok=True)
        except Exception:
            pass
        try:
            con.execute(
                "INSERT INTO audit.batch_runs (batch_id, source_name, status, error) "
                "VALUES (?,?,?,?)",
                [manifest.batch_id, manifest.source_name, "failed", str(exc)[:2000]])
        except Exception:
            pass
        raise

    duration_ms = int((time.time() - started) * 1000)
    result = {
        "batch_id": manifest.batch_id,
        "source_name": manifest.source_name,
        "status": "completed",
        "inserted": counts["inserted"],
        "updated": 0,
        "merged": 0,
        "anomalies_opened": opened,
        "anomalies_resolved": 0,
        "duration_ms": duration_ms,
        "errors": 0,
    }
    log.info(json.dumps(result))
    return result

def finalize_lake(con, run_key: str = _RUN_KEY) -> Dict[str, Any]:
    """Run global cross-record detection and anomaly reconciliation."""
    anoms = AnomalyStore()
    result = facts_finalize(con, anoms, None)
    opened, updated = anoms.flush(con, None)
    return {"anomalies_opened": opened, "anomalies_updated": updated, **result}
