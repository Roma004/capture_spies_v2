"""Integration tests against data/unified/ (SPEC 11.2)."""
from __future__ import annotations

import csv
import json

import pytest

from conftest import ROOT, UNIFIED, fixture_path, load_fixture


def _count(con, sql, params=None):
    return con.execute(sql, params or []).fetchone()[0]


def test_pipeline_runs(full_lake):
    con = full_lake
    flights = _count(con, "SELECT count(*) FROM gold.flights")
    schedule = _count(con, "SELECT count(*) FROM gold.flight_schedule")
    persons = _count(con, "SELECT count(*) FROM identity.persons")
    anomalies = _count(con, "SELECT count(*) FROM gold.anomalies")
    assert 3_500_000 <= flights <= 4_500_000, flights
    assert schedule == 116_580, schedule
    assert 50_000 <= persons <= 150_000, persons
    assert anomalies >= 50_000, anomalies


def test_converter_regression(full_lake):
    con = full_lake
    for atype in ("MISSING_DEPARTURE_DATE", "CODESHARE_STRING_FORM",
                  "SEX_FORMAT", "DELTA_DT_PREFIX"):
        n = _count(con, "SELECT count(*) FROM gold.anomalies WHERE type=?", [atype])
        assert n == 0, (atype, n)
    n = _count(con, "SELECT count(*) FROM gold.anomalies "
                    "WHERE type='AMBIGUOUS_NAME_ORDER_V2'")
    assert n <= 100_000, n


def test_boarding_data_pnr_not_bug(full_lake):
    # boarding-data pnr/ticket_number are intentionally absent from
    # null_policy.csv, so a 50% null rate must not raise MISSING_REQUIRED_FIELD.
    n = _count(full_lake, "SELECT count(*) FROM gold.anomalies "
                          "WHERE type='MISSING_REQUIRED_FIELD' "
                          "AND json_extract_string(details,'$.column') IN "
                          "('pnr','ticket_number')")
    assert n == 0, n


def test_ticket_shared_detected(full_lake):
    n = _count(full_lake, "SELECT count(*) FROM gold.anomalies "
                          "WHERE type='TICKET_SHARED_BY_PERSONS'")
    assert abs(n - 1847) <= 0.10 * 1847, n


def test_ff_multi_person_detected(full_lake):
    n = _count(full_lake, "SELECT count(*) FROM gold.anomalies "
                          "WHERE type='FF_MULTI_PERSON'")
    assert abs(n - 1086) <= 0.05 * 1086, n
    pmd = _count(full_lake, "SELECT count(*) FROM gold.anomalies "
                            "WHERE type='PERSON_MULTI_DOCUMENT'")
    expected = json.loads((ROOT / "tests" / "fixtures" /
                           "expected_counts.json").read_text())["person_multi_document"]
    assert abs(pmd - expected) <= 0.05 * expected, (pmd, expected)


def test_forum_does_not_create_person(tmp_path):
    from backend.ingest import csv_rows
    from backend.pipeline import connect, ingest_batch
    from backend.loader import make_manifest

    con = connect(tmp_path / "forum.duckdb")
    users = UNIFIED / "flyer-forum-users.csv"
    flights = UNIFIED / "flyer-forum-flights.csv"
    m = make_manifest("flyer-forum-users", users)
    ingest_batch(con, m, lambda: _head(csv_rows(users, "flyer-forum-users",
                                               "v1-forum-users"), 10_000),
                verify_checksum=False)
    # TODO: change the path
    m = make_manifest("flyer-forum-flights", flights)
    ingest_batch(con, m, lambda: _head(csv_rows(flights, "flyer-forum-flights",
                                               "v1"), 5_000),
                verify_checksum=False)
    assert _count(con, "SELECT count(*) FROM identity.persons") == 0
    assert _count(con, "SELECT count(*) FROM identity.pending_keys") > 0


def _head(it, n):
    for i, x in enumerate(it):
        if i >= n:
            return
        yield x


def test_forum_promote_on_corroboration(lake):
    load_fixture(lake, "flyer-forum-users", "forum_pending_promote_users.csv",
                 schema="v1-forum-users")
    load_fixture(lake, "sirena-export", "forum_pending_promote_sirena.csv")
    assert _count(lake, "SELECT count(*) FROM identity.person_events "
                        "WHERE event_type='pending_promote'") > 0


def test_merge_does_not_lose_flights(lake):
    load_fixture(lake, "sirena-export", "person_multi_document.csv")
    before = _count(lake, "SELECT count(*) FROM gold.flights")
    # finalize is idempotent; re-running identity rebuild must not drop facts
    from backend.pipeline import finalize_lake
    finalize_lake(lake, run_key="unit")
    after = _count(lake, "SELECT count(*) FROM gold.flights")
    assert before == after == 9


def test_deterministic_order_independent(tmp_path):
    from backend.ingest import csv_rows
    from backend.pipeline import connect, ingest_batch
    from backend.loader import make_manifest

    a = fixture_path("person_multi_document.csv")
    b = fixture_path("ff_multi_person.csv")

    def build(order, name):
        con = connect(tmp_path / name)
        for p in order:
            m = make_manifest("sirena-export", p)
            ingest_batch(con, m, lambda p=p: csv_rows(p, "sirena-export", "v1"),
                         verify_checksum=False)
        rows = con.execute(
            "SELECT person_id, status, merged_into, primary_source, confidence "
            "FROM identity.persons ORDER BY person_id").fetchall()
        con.close()
        return rows

    one = build([a, b], "one.duckdb")
    two = build([b, a], "two.duckdb")
    assert one == two


def test_idempotent_reupload(lake):
    from backend.ingest import csv_rows
    from backend.pipeline import ingest_batch
    from backend.loader import make_manifest

    p = fixture_path("person_multi_document.csv")
    m = make_manifest("sirena-export", p)
    ingest_batch(lake, m, lambda: csv_rows(p, "sirena-export", "v1"),
                 verify_checksum=False)
    n1 = _count(lake, "SELECT count(*) FROM bronze.records")
    m2 = dict(m, force=True)
    ingest_batch(lake, m2, lambda: csv_rows(p, "sirena-export", "v1"),
                 verify_checksum=False)
    n2 = _count(lake, "SELECT count(*) FROM bronze.records")
    assert n1 == n2


def test_rea_mapped(full_lake):
    bad = _count(full_lake, """
        SELECT count(*) FROM bronze.records b
        JOIN bronze.batches bt ON bt.batch_id=b.batch_id
        JOIN silver.records_norm s USING(record_id)
        WHERE bt.source_name='boarding-pass'
          AND b.is_active
          AND (lower(json_extract_string(b.payload,'$.from_code'))='rea'
               OR lower(json_extract_string(b.payload,'$.to_code'))='rea')
          AND (coalesce(s.from_code_norm,'') <> 'ICN'
               AND coalesce(s.to_code_norm,'') <> 'ICN')
    """)
    assert bad == 0, bad


def test_transliteration_end_to_end(full_lake):
    rows = full_lake.execute("""
        SELECT DISTINCT s.source_name, c.canonical_person_id
        FROM silver.records_norm s
        JOIN bronze.records b ON b.record_id=s.record_id
        JOIN bronze.batches bt ON bt.batch_id=b.batch_id
        LEFT JOIN identity.person_canonical c ON c.original_person_id=s.person_id
        WHERE s.document_norm='internal:4396926588'
          AND bt.source_name IN ('boarding-data','sirena-export')
          AND b.is_active
    """).fetchall()
    sources = {r[0] for r in rows}
    persons = {r[1] for r in rows}
    assert {"boarding-data", "sirena-export"} <= sources
    assert len(persons) == 1 and None not in persons, rows


def test_boarding_pass_name_order_fixed(full_lake):
    con = full_lake
    known = con.execute("""
        SELECT last_name_lat, first_name_lat FROM silver.records_norm
        WHERE source_name IN ('boarding-data','pointz')
          AND last_name_lat IS NOT NULL AND first_name_lat IS NOT NULL
        GROUP BY 1,2
    """).fetchall()
    pairs = {(l, f) for l, f in known}
    firsts = {f for _, f in pairs}
    lasts = {l for l, _ in pairs}
    candidates = con.execute("""
        SELECT first_name_lat, last_name_lat FROM silver.records_norm
        WHERE source_name='boarding-pass'
          AND first_name_lat IS NOT NULL AND last_name_lat IS NOT NULL
          AND first_name_lat IN (SELECT DISTINCT first_name_lat
                                 FROM silver.records_norm
                                 WHERE source_name IN ('boarding-data','pointz'))
          AND last_name_lat IN (SELECT DISTINCT last_name_lat
                                FROM silver.records_norm
                                WHERE source_name IN ('boarding-data','pointz'))
    """).fetchall()
    total = len(candidates)
    reversed_ = sum(1 for f, l in candidates
                    if (f, l) not in pairs and (l, f) in pairs)
    assert total > 0
    assert reversed_ / total <= 0.01, (reversed_, total)
