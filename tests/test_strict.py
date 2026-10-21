"""Strict/tolerant converter-regression handling (SPEC 4.4, 13)."""
from __future__ import annotations

import uuid

import pytest

from backend import normalize as N
from backend.config import FORUM_USER_COLUMNS, V1_COLUMNS
from backend.ingest import BatchRejected
from backend.pipeline import ingest_batch

BASE_ROW = {
    "first_name": "IVAN", "middle_name": None, "last_name": "PETROV",
    "sex": "M", "birth_date": "1980-01-01", "document": "1234 567890",
    "pnr": "ABC123", "ticket_number": "5555555555555555",
    "seat": None, "meal": None, "baggage": None,
    "departure_date": "2017-01-01", "departure_time": "10:00",
    "arrival_date": "2017-01-01", "arrival_time": "12:00",
    "flight_number": "SU1234", "airline_code": None, "flight_suffix": None,
    "codeshare": "false", "status": None, "from_code": "SVO",
    "from_city": None, "from_country": None, "to_code": "LED",
    "to_city": None, "to_country": None, "fare_class": None,
    "fare_basis": None, "agent": None, "operated_by": None,
    "aircraft": None, "travel_time": None, "ff_program": None,
    "ff_status": None, "ff_number": None, "validity_start": None,
    "validity_end": None, "days_of_week": None, "uid": None,
}


def manifest_for(source, rows, schema="v1"):
    columns = FORUM_USER_COLUMNS if schema == "v1-forum-users" else V1_COLUMNS
    filled = [{c: r.get(c) for c in columns} for r in rows]
    return {
        "batch_id": str(uuid.uuid4()),
        "source_name": source,
        "schema_version": schema,
        "batch_kind": "delta",
        "effective_date": "2017-01-01",
        "content_checksum": N.compute_checksum(filled, columns),
    }


def run(con, source, rows, schema="v1"):
    m = manifest_for(source, rows, schema)
    return ingest_batch(con, m, lambda: iter(rows))


def test_strict_rejects_bug01(lake, monkeypatch):
    monkeypatch.setenv("BACKEND_MODE", "strict")
    row = dict(BASE_ROW, codeshare="Own")
    with pytest.raises(BatchRejected):
        run(lake, "boarding-data", [row])


def test_strict_rejects_bug02(lake, monkeypatch):
    monkeypatch.setenv("BACKEND_MODE", "strict")
    row = {"nickname": "FF1", "sex": "Male", "first_name": "I",
           "last_name": "P", "ff_program": "Flying Blue", "ff_status": None,
           "ff_number": "123", "source_file": "x", "source_row": "1"}
    with pytest.raises(BatchRejected):
        run(lake, "flyer-forum-users", [row], schema="v1-forum-users")


def test_strict_rejects_bug04(lake, monkeypatch):
    monkeypatch.setenv("BACKEND_MODE", "strict")
    row = dict(BASE_ROW, departure_date=None, arrival_date=None,
               flight_number="SU1234")
    with pytest.raises(BatchRejected):
        run(lake, "pointz", [row])


def test_strict_rejects_bug05(lake, monkeypatch):
    monkeypatch.setenv("BACKEND_MODE", "strict")
    row = dict(BASE_ROW, departure_date=None, arrival_date=None,
               flight_number="SU1234")
    with pytest.raises(BatchRejected):
        run(lake, "flyer-forum-flights", [row])


def test_strict_rejects_bug06(lake, monkeypatch):
    monkeypatch.setenv("BACKEND_MODE", "strict")
    row = dict(BASE_ROW, ff_program="Delta SkyMiles", ff_number="DT 12345")
    with pytest.raises(BatchRejected):
        run(lake, "pointz", [row])


def test_tolerant_records_regressions(lake, monkeypatch):
    monkeypatch.setenv("BACKEND_MODE", "tolerant")
    row = dict(BASE_ROW, codeshare="Operated")
    run(lake, "boarding-data", [row])
    types = {r[0] for r in lake.execute(
        "SELECT DISTINCT type FROM gold.anomalies").fetchall()}
    assert "CODESHARE_STRING_FORM" in types
