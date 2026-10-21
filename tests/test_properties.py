"""Property tests (SPEC 11.3)."""
from __future__ import annotations

import json

from conftest import ROOT

EXPECTED = json.loads((ROOT / "tests" / "fixtures" /
                       "expected_counts.json").read_text())

RAW_COUNTS = {
    "boarding-data": 155147,
    "sirena-export": 155757,
    "pointz": 436090,
    "boarding-pass": 614158,
    "skyteam-exchange": 1682517,
    "flyer-forum-flights": 1308987,
    "skyteam-timetable": 116580,
}


def _count(con, sql):
    return con.execute(sql).fetchone()[0]


def test_no_flight_without_fact_trust(full_lake):
    bad = _count(full_lake, """
        SELECT count(*) FROM gold.flights f
        JOIN bronze.batches bt ON bt.batch_id = f.batch_id
        WHERE bt.trust_fact = 'C'
          AND NOT EXISTS (
              SELECT 1 FROM gold.flights a
              JOIN bronze.batches abt ON abt.batch_id = a.batch_id
              WHERE abt.trust_fact IN ('A','B')
                AND a.record_key = f.record_key
                AND a.person_id IS NOT DISTINCT FROM f.person_id
          )
    """)
    assert bad == 0, bad


def test_no_person_from_C_only(full_lake):
    bad = _count(full_lake, """
        SELECT count(*) FROM identity.persons p
        WHERE NOT EXISTS (
            SELECT 1 FROM silver.records_norm s
            JOIN bronze.records b ON b.record_id=s.record_id
            JOIN bronze.batches bt ON bt.batch_id=s.batch_id
            WHERE s.person_id = p.person_id
              AND b.is_active
              AND bt.trust_identity IN ('A','B')
              AND s.source_name NOT LIKE 'flyer-forum%'
        )
    """)
    assert bad == 0, bad


def test_no_fact_without_departure_date(full_lake):
    n = _count(full_lake, "SELECT count(*) FROM gold.flights WHERE departure_date IS NULL")
    assert n == 0


def test_row_parity_per_source(full_lake):
    rows = dict(full_lake.execute(
        "SELECT source_name, count(*) FROM silver.records_norm GROUP BY 1").fetchall())
    for source, expected in RAW_COUNTS.items():
        assert rows.get(source) == expected, (source, rows.get(source), expected)
