"""Shared pytest helpers."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.ingest import csv_rows  # noqa: E402
from backend.loader import load_unified, make_manifest  # noqa: E402
from backend.pipeline import connect  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures"
UNIFIED = ROOT / "data" / "unified"


def fixture_path(name: str) -> Path:
    p = FIXTURES / name
    if not p.exists():
        pytest.skip(f"fixture missing: {name}")
    return p


def load_fixture(con, source: str, name: str, schema: str = "v1"):
    p = fixture_path(name)
    manifest = make_manifest(source, p)
    return ingest_batch_fixture(con, manifest, source, p, schema)


def ingest_batch_fixture(con, manifest, source, path, schema):
    from backend.pipeline import ingest_batch

    return ingest_batch(con, manifest,
                        lambda: csv_rows(path, source, schema),
                        verify_checksum=False)


@pytest.fixture
def lake(tmp_path):
    con = connect(tmp_path / "lake.duckdb")
    yield con
    con.close()


@pytest.fixture(scope="session")
def full_lake():
    db = Path(os.environ.get("LAKE_DB", str(ROOT / "data" / "lake.duckdb")))
    con = connect(db)
    expected = 4558063
    actual = con.execute("SELECT count(*) FROM silver.records_norm").fetchone()[0]
    if actual < expected:
        load_unified(con, UNIFIED, run_key=str(db.resolve()))
    yield con
    con.close()
