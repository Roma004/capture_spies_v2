"""Load the reference data/unified/ tree as 30 deterministic batches."""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Dict, List, Tuple

from .ingest import DuplicateBatch, Manifest, csv_rows, deterministic_batch_id
from .pipeline import _iter_checksum, finalize_lake, ingest_batch
from .config import FORUM_USER_COLUMNS, V1_COLUMNS

SOURCE_PRIORITY = {
    "boarding-data": 0,
    "sirena-export": 1,
    "boarding-pass": 2,
    "pointz": 3,
    "skyteam-exchange": 4,
    "flyer-forum-users": 5,
    "flyer-forum-flights": 6,
    "skyteam-timetable": 7,
}


def discover(unified_dir: Path) -> List[Tuple[str, Path]]:
    out: List[Tuple[str, Path]] = []
    for p in sorted(unified_dir.iterdir()):
        if p.is_file() and p.suffix == ".csv":
            out.append((p.stem, p))
        elif p.is_dir():
            for f in sorted(p.glob("*.csv")):
                out.append((p.name, f))
    out.sort(key=lambda sp: (SOURCE_PRIORITY.get(sp[0], 99), str(sp[1])))
    return out


def _effective_date(path: Path) -> str:
    name = path.stem
    if len(name) >= 7 and name[:4].isdigit() and name[4] == "-" and name[5:7].isdigit():
        return f"{name[:7]}-01"
    return "2017-01-01"


def make_manifest(source: str, path: Path) -> Dict:
    schema_version = "v1-forum-users" if source == "flyer-forum-users" else "v1"
    columns = FORUM_USER_COLUMNS if schema_version == "v1-forum-users" else V1_COLUMNS
    checksum = _iter_checksum(csv_rows(path, source, schema_version), columns)
    bid = deterministic_batch_id(source, path.name, checksum)
    return {
        "batch_id": bid,
        "source_name": source,
        "schema_version": schema_version,
        "batch_kind": "delta",
        "effective_date": _effective_date(path),
        "content_checksum": checksum,
        "trust_identity": None,
        "trust_fact": None,
        "is_schedule": None,
        "force": False,
    }


def load_unified(con, unified_dir: Path, run_key: str = "default") -> List[dict]:
    results = []
    files = discover(unified_dir)
    for source, path in files:
        manifest = make_manifest(source, path)
        # remove explicit Nones so defaults apply
        manifest = {k: v for k, v in manifest.items() if v is not None}
        schema_version = manifest["schema_version"]
        rows_factory = (lambda p=path, s=source, sv=schema_version:
                        csv_rows(p, s, sv))
        try:
            res = ingest_batch(con, manifest, rows_factory, run_key=run_key,
                               finalize_after=False, verify_checksum=False)
        except DuplicateBatch:
            # Resumable load: this file's rows are already committed.
            continue
        results.append(res)
    fin = finalize_lake(con, run_key=run_key)
    results.append({"finalize": fin})
    return results
