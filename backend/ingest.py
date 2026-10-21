"""Batch ingestion pipeline (SPEC sections 3, 6, 9)."""
from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Iterator, List, Optional, Tuple

import pyarrow as pa

from . import normalize as N
from .config import (
    DEFAULT_TRUST, FACT_SOURCES, FORUM_USER_COLUMNS, ROOT, V1_COLUMNS,
    backend_mode,
)

log = logging.getLogger(__name__)

CHUNK = 50_000


class BatchRejected(Exception):
    """Raised when a batch fails validation (HTTP 422 semantics)."""

    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.message = message
        self.status = status


class DuplicateBatch(Exception):
    """Raised for idempotent no-op (HTTP 409 semantics)."""

    def __init__(self, batch_id: Optional[str] = None):
        super().__init__("duplicate batch")
        self.batch_id = batch_id


# ---------------------------------------------------------------------------
# manifest
# ---------------------------------------------------------------------------

@dataclass
class Manifest:
    batch_id: str
    source_name: str
    schema_version: str
    batch_kind: str
    effective_date: Optional[date]
    trust_identity: str
    trust_fact: str
    is_schedule: bool
    content_checksum: str
    supersedes: List[str] = field(default_factory=list)
    retracts: List[str] = field(default_factory=list)
    force: bool = False

    @property
    def columns(self) -> List[str]:
        return FORUM_USER_COLUMNS if self.schema_version == "v1-forum-users" else V1_COLUMNS

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Manifest":
        source = (d.get("source_name") or "").strip()
        if source not in DEFAULT_TRUST:
            raise BatchRejected(f"unknown source_name: {source!r}")
        schema_version = d.get("schema_version") or "v1"
        if schema_version not in ("v1", "v1-forum-users"):
            raise BatchRejected(f"unknown schema_version: {schema_version!r}")
        batch_kind = d.get("batch_kind") or "delta"
        if batch_kind not in ("snapshot", "delta"):
            raise BatchRejected(f"unknown batch_kind: {batch_kind!r}")
        eff = d.get("effective_date")
        if eff is None:
            raise BatchRejected("effective_date is required")
        eff_date = N.parse_date(eff)
        if eff_date is None:
            raise BatchRejected(f"invalid effective_date: {eff!r}")
        checksum = d.get("content_checksum")
        if not checksum:
            raise BatchRejected("content_checksum is required")
        if not str(checksum).startswith("sha256:"):
            raise BatchRejected("content_checksum must start with 'sha256:'")
        di, df, ds = DEFAULT_TRUST[source]
        trust_identity = (d.get("trust_identity") or di or "C").upper()
        trust_fact = (d.get("trust_fact") or df or "C").upper()
        if trust_identity not in ("A", "B", "C"):
            raise BatchRejected("trust_identity must be A, B or C")
        if trust_fact not in ("A", "B", "C"):
            raise BatchRejected("trust_fact must be A, B or C")
        is_schedule = d.get("is_schedule")
        if is_schedule is None:
            is_schedule = ds
        bid = d.get("batch_id") or str(uuid.uuid4())
        try:
            bid = str(uuid.UUID(str(bid)))
        except ValueError:
            raise BatchRejected(f"invalid batch_id: {bid!r}")
        return cls(
            batch_id=bid,
            source_name=source,
            schema_version=schema_version,
            batch_kind=batch_kind,
            effective_date=eff_date,
            trust_identity=trust_identity,
            trust_fact=trust_fact,
            is_schedule=bool(is_schedule),
            content_checksum=str(checksum),
            supersedes=list(d.get("supersedes") or []),
            retracts=list(d.get("retracts") or []),
            force=bool(d.get("force", False)),
        )


def deterministic_batch_id(source_name: str, logical_name: str, checksum: str) -> str:
    h = hashlib.sha256(f"{source_name}|{logical_name}|{checksum}".encode()).digest()
    return str(uuid.UUID(bytes=h[:16], version=4))


# ---------------------------------------------------------------------------
# record normalization
# ---------------------------------------------------------------------------

def build_checksum(rows: Iterable[Dict[str, Any]], columns: List[str]) -> str:
    return N.compute_checksum(rows, columns)


def _flag(anoms: List[dict], atype: str, severity: str, dedup: str,
          details: Optional[dict] = None, record_ids=None, person_ids=None):
    anoms.append({
        "type": atype,
        "severity": severity,
        "dedup_key": dedup,
        "details": details or {},
        "record_ids": record_ids or [],
        "person_ids": person_ids or [],
    })


def normalize_record(*, raw: Dict[str, Any], manifest: Manifest, refs,
                     batch_id: str, source_row: int, record_id: str,
                     anomaly_sink) -> Tuple[dict, Optional[str], list]:
    """Normalize a single submitted row.

    Returns (silver_fields, record_key, per_record_anomalies).
    """
    source = manifest.source_name
    translit, programs, airports, policies = refs
    anoms: List[dict] = []

    # One pass of empty-string -> NULL coercion; the rest of the function can
    # then use raw values directly.
    raw = {k: (None if isinstance(v, str) and not v.strip() else v)
           for k, v in raw.items()}

    def g(col):
        return raw.get(col)

    last_raw = g("last_name")
    first_raw = g("first_name")
    middle_raw = g("middle_name")
    last_lat = N.normalize_name(last_raw, translit)
    first_lat = N.normalize_name(first_raw, translit)
    middle_lat = N.normalize_name(middle_raw, translit)

    birth = N.parse_date(g("birth_date"))
    sex_raw = g("sex")
    sex_norm, sex_suspect = N.normalize_sex(sex_raw)
    if sex_suspect:
        _flag(anoms, "SUSPECT_GENDER", "soft", record_id)
    if source == "flyer-forum-users" and isinstance(sex_raw, str) \
            and sex_raw.strip() in ("Male", "Female"):
        _flag(anoms, "SEX_FORMAT", "info", source)

    document_raw = g("document")
    document_type, document_norm = N.document_type_and_norm(document_raw)

    ff_program_norm = N.normalize_ff_program(g("ff_program"), programs)
    ff_number_raw = g("ff_number")
    ff_number_norm = N.normalize_ff_number(ff_number_raw)
    ff_key = None
    if ff_program_norm and ff_number_norm:
        ff_key = f"{ff_program_norm}|{ff_number_norm}"

    if (ff_program_norm == "Delta SkyMiles" and ff_number_raw
            and ff_number_raw.upper().startswith("DT ")):
        _flag(anoms, "DELTA_DT_PREFIX", "info", record_id,
              {"ff_number": ff_number_raw})

    pnr = g("pnr")
    ticket = g("ticket_number")
    name_dob = N.name_dob_key(last_lat, first_lat, birth)
    pnr_last = N.make_pnr_last_key(pnr, last_lat)

    flight_number_norm, flight_operator, invalid_flight = N.parse_flight_number(
        g("flight_number"))
    if invalid_flight:
        _flag(anoms, "INVALID_FLIGHT_NUMBER", "soft", record_id,
              {"raw": g("flight_number")})

    from_raw = g("from_code")
    to_raw = g("to_code")
    from_code, from_unknown = N.normalize_airport(from_raw, airports)
    to_code, to_unknown = N.normalize_airport(to_raw, airports)
    if from_unknown:
        _flag(anoms, "UNKNOWN_AIRPORT_CODE", "info",
              f"{record_id}|from|{from_raw}",
              {"raw": from_raw, "record_id": record_id})
    if to_unknown:
        _flag(anoms, "UNKNOWN_AIRPORT_CODE", "info",
              f"{record_id}|to|{to_raw}",
              {"raw": to_raw, "record_id": record_id})

    dep_date = N.parse_date(g("departure_date"))
    arr_date = N.parse_date(g("arrival_date"))
    dep_time = N.parse_time(g("departure_time"))
    arr_time = N.parse_time(g("arrival_time"))

    operated_raw = g("operated_by")
    operated_by_norm = N.normalize_operated_by(operated_raw)

    codeshare_val, codeshare_unmapped, codeshare_regression = N.normalize_codeshare(
        g("codeshare"))
    if codeshare_regression or codeshare_unmapped:
        _flag(anoms, "CODESHARE_STRING_FORM", "info", record_id,
              {"raw": g("codeshare")})
    if codeshare_val is True and operated_by_norm is None:
        _flag(anoms, "CODESHARE_NO_OPERATOR", "soft", record_id)

    if source in FACT_SOURCES and dep_date is None and arr_date is None \
            and g("flight_number"):
        _flag(anoms, "MISSING_DEPARTURE_DATE", "soft", record_id)

    if (arr_date is not None and dep_date is not None and arr_date < dep_date):
        _flag(anoms, "DATES_INCONSISTENT", "soft", record_id,
              {"departure_date": dep_date.isoformat(),
               "arrival_date": arr_date.isoformat()})

    # null-policy checks
    for col in manifest.columns:
        key = (source, col)
        if key not in policies:
            continue
        policy, semantic = policies[key]
        if policy == "unknown":
            continue
        if policy == "required_for_trust_A" and manifest.trust_identity != "A":
            continue
        if policy not in ("required", "required_for_trust_A"):
            continue
        if g(col) is None:
            severity = "hard" if semantic == "unknown_is_error" else "soft"
            _flag(anoms, "MISSING_REQUIRED_FIELD", severity,
                  f"{record_id}|{col}", {"column": col})

    if manifest.source_name == "sirena-export":
        suffix = g("flight_suffix")
        if suffix in ("NO", "YES"):
            _flag(anoms, "FLIGHT_SUFFIX_NO_YES", "info", source)

    departure_date_missing_reason = None
    if source == "boarding-data" and dep_date is None:
        departure_date_missing_reason = "source_lacks_field"
    if dep_date is None and arr_date is None and g("flight_number"):
        departure_date_missing_reason = "converter_bug"

    effective_dep = dep_date
    if effective_dep is None and arr_date is not None:
        # Needed so that every gold.flights row carries a departure_date
        # (SPEC 11.3 test_no_fact_without_departure_date).
        effective_dep = arr_date

    record_key = None
    no_operator = False
    if not manifest.is_schedule:
        record_key, no_operator = N.make_record_key(
            source_name=source,
            flight_number_norm=flight_number_norm,
            departure_date=effective_dep,
            arrival_date=arr_date,
            from_code_norm=from_code,
            to_code_norm=to_code,
            to_city=g("to_city"),
            codeshare=codeshare_val,
            operated_by_norm=operated_by_norm,
            departure_time=dep_time,
        )
        if no_operator:
            _flag(anoms, "CODESHARE_NO_OPERATOR", "soft", record_id)
        # A codeshare with an operator uses the operator form; the
        # placeholder/no-operator case already fell back above.

    silver = {
        "record_id": record_id,
        "batch_id": batch_id,
        "source_name": source,
        "source_row": source_row,
        "document_norm": document_norm,
        "document_type": document_type,
        "ff_program_norm": ff_program_norm,
        "ff_number_norm": ff_number_norm,
        "ff_key": ff_key,
        "last_name_lat": last_lat,
        "first_name_lat": first_lat,
        "middle_name_lat": middle_lat,
        "birth_date": birth,
        "name_dob_key": name_dob,
        "pnr_last_key": pnr_last,
        "flight_number_norm": flight_number_norm,
        "flight_number_operator": flight_operator,
        "from_code_norm": from_code,
        "to_code_norm": to_code,
        "departure_date": effective_dep,
        "departure_time": dep_time,
        "arrival_date": arr_date,
        "arrival_time": arr_time,
        "operated_by_norm": operated_by_norm,
        "codeshare": codeshare_val,
        "sex_norm": sex_norm,
        "meal": g("meal"),
        "baggage": g("baggage"),
        "ff_status": g("ff_status"),
        "status": g("status"),
        "fare_class": g("fare_class"),
        "fare_basis": g("fare_basis"),
        "agent": g("agent"),
        "aircraft": g("aircraft"),
        "travel_time": g("travel_time"),
        "from_city": g("from_city"),
        "to_city": g("to_city"),
        "uid": g("uid"),
        "person_id": None,
        "name_order_hint": None,
        "departure_date_missing_reason": departure_date_missing_reason,
        "payload": None,  # filled by the caller
        # schedule-only values carried through for gold.flight_schedule
        "_validity_start_md": g("validity_start"),
        "_validity_end_md": g("validity_end"),
        "_days_of_week_mask": g("days_of_week"),
    }
    return silver, record_key, anoms


# ---------------------------------------------------------------------------
# SQL helpers
# ---------------------------------------------------------------------------

SILVER_COLUMNS = [
    "record_id", "batch_id", "source_name", "source_row", "document_norm",
    "document_type", "ff_program_norm", "ff_number_norm", "ff_key",
    "last_name_lat", "first_name_lat", "middle_name_lat", "birth_date",
    "name_dob_key", "pnr_last_key", "flight_number_norm",
    "flight_number_operator", "from_code_norm", "to_code_norm",
    "departure_date", "departure_time", "arrival_date", "arrival_time",
    "operated_by_norm", "codeshare", "sex_norm", "meal", "baggage",
    "ff_status", "status", "fare_class", "fare_basis", "agent", "aircraft",
    "travel_time", "from_city", "to_city", "uid", "person_id",
    "name_order_hint", "departure_date_missing_reason", "payload",
]

SILVER_TYPES = {
    "birth_date": pa.date32(),
    "departure_date": pa.date32(),
    "arrival_date": pa.date32(),
    "codeshare": pa.bool_(),
    "source_row": pa.int64(),
}


def _pa_table(rows: List[dict], columns: List[str]) -> pa.Table:
    data = {}
    for c in columns:
        vals = [r.get(c) for r in rows]
        data[c] = pa.array(vals, type=SILVER_TYPES.get(c))
    return pa.Table.from_pydict(data)


def insert_silver(con, rows: List[dict]) -> None:
    if not rows:
        return
    tbl = _pa_table(rows, SILVER_COLUMNS)
    con.register("_silver_chunk", tbl)
    collist = ", ".join(SILVER_COLUMNS)
    con.execute(f"INSERT INTO silver.records_norm ({collist}) SELECT {collist} FROM _silver_chunk")
    con.unregister("_silver_chunk")


def insert_aux(con, rows: List[tuple]) -> None:
    if not rows:
        return
    tbl = pa.table({
        "record_id": [r[0] for r in rows],
        "record_key": [r[1] for r in rows],
        "ticket_number": [r[2] for r in rows],
        "pnr": [r[3] for r in rows],
    })
    con.register("_aux_chunk", tbl)
    con.execute("INSERT INTO silver.record_aux SELECT record_id, record_key, ticket_number, pnr FROM _aux_chunk")
    con.unregister("_aux_chunk")


def insert_bronze(con, rows: List[tuple]) -> None:
    if not rows:
        return
    tbl = pa.table({
        "record_id": [r[0] for r in rows],
        "batch_id": [r[1] for r in rows],
        "source_row": pa.array([r[2] for r in rows], type=pa.int64()),
        "payload": [r[3] for r in rows],
    })
    con.register("_bronze_chunk", tbl)
    con.execute("INSERT INTO bronze.records SELECT record_id, batch_id, source_row, payload, TRUE, now() FROM _bronze_chunk")
    con.unregister("_bronze_chunk")


def insert_schedule(con, rows: List[tuple]) -> None:
    if not rows:
        return
    t = pa.table({
        "schedule_id": [r[0] for r in rows],
        "flight_number": [r[1] for r in rows],
        "from_code": [r[2] for r in rows],
        "to_code": [r[3] for r in rows],
        "departure_time": [r[4] for r in rows],
        "arrival_time": [r[5] for r in rows],
        "validity_start_md": [r[6] for r in rows],
        "validity_end_md": [r[7] for r in rows],
        "days_of_week_mask": [r[8] for r in rows],
        "aircraft": [r[9] for r in rows],
        "travel_time": [r[10] for r in rows],
        "source_name": [r[11] for r in rows],
        "batch_id": [r[12] for r in rows],
    })
    con.register("_sched_chunk", t)
    con.execute("INSERT INTO gold.flight_schedule SELECT * FROM _sched_chunk")
    con.unregister("_sched_chunk")


# ---------------------------------------------------------------------------
# file-backed batch source
# ---------------------------------------------------------------------------

def csv_rows(path: Path, source_name: str, schema_version: str) -> Iterator[dict]:
    import csv

    from .config import SOURCES  # noqa: F401

    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for idx, row in enumerate(reader, start=1):
            clean = {k: (v if v not in (None, "") else None) for k, v in row.items()}
            if schema_version == "v1-forum-users":
                clean = {c: clean.get(c) for c in FORUM_USER_COLUMNS}
                clean["source_file"] = path.name
                clean["source_row"] = str(idx)
            else:
                clean = {c: clean.get(c) for c in V1_COLUMNS}
            yield clean


def normalize_text_rows(rows: Iterable[dict], columns: List[str]) -> Iterator[dict]:
    for r in rows:
        yield {c: r.get(c) for c in columns}
