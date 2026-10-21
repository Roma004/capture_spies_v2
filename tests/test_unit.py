"""Unit tests from SPEC 11.1 plus fixture coverage."""
from __future__ import annotations

import csv
import hashlib
from pathlib import Path

import pytest

from backend import normalize as N
from conftest import fixture_path, load_fixture


def _sha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:32]


# ---------------------------------------------------------------------------
# 11.1
# ---------------------------------------------------------------------------

def test_transliteration_map():
    p = fixture_path("translit_pairs.csv")
    n = 0
    with p.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            n += 1
            assert N.transliterate(row["cyrillic_last"]) == row["latin_last"], row
            assert N.transliterate(row["cyrillic_first"]) == row["latin_first"], row
    assert n == 1000


def test_latin_normalization():
    assert N.latin_normalize("D'YAKOV") == "DYAKOV"
    assert N.latin_normalize("AL'BINA") == "ALBINA"
    assert N.latin_normalize("LYUBOV'") == "LYUBOV"
    assert N.latin_normalize("YURIY") == "IURIY"
    assert N.latin_normalize("YUDINA") == "IUDINA"
    assert N.latin_normalize("NIKOLSKII") == "NIKOLSKII"


def test_ff_key():
    assert N.make_ff_key("Flying Blue", "FB 171388778") == "Flying Blue|171388778"
    assert N.make_ff_key("Delta SkyMiles", "DT 980250352") == "Delta SkyMiles|980250352"
    assert N.make_ff_key("  Flying Blue  ", "  171388778  ") == "Flying Blue|171388778"


def test_airport_map():
    assert N.normalize_airport("rea")[0] == "ICN"
    assert N.normalize_airport("REA")[0] == "ICN"
    assert N.normalize_airport("SVO")[0] == "SVO"
    assert N.normalize_airport("nux")[0] == "NOJ"


def test_document_type():
    assert N.document_type_and_norm("2879 096860") == ("internal", "internal:2879096860")
    assert N.document_type_and_norm("98 6865148") == ("foreign", "foreign:986865148")


def test_record_key_boarding_data():
    key, _ = N.make_record_key(
        source_name="boarding-data", flight_number_norm="SU1180",
        departure_date=None, arrival_date="2017-03-18",
        from_code_norm=None, to_code_norm=None, to_city="Volgograd",
        codeshare=None, operated_by_norm=None, departure_time=None)
    assert key == _sha("SU1180|2017-03-18|Volgograd")
    key2, _ = N.make_record_key(
        source_name="boarding-data", flight_number_norm="SU1180",
        departure_date=None, arrival_date="2017-03-18",
        from_code_norm=None, to_code_norm=None, to_city="Volgograd",
        codeshare=None, operated_by_norm=None, departure_time=None)
    assert key == key2


def test_record_key_codeshare():
    key, no_op = N.make_record_key(
        source_name="pointz", flight_number_norm="SU1180",
        departure_date="2017-01-01", arrival_date=None,
        from_code_norm="SVO", to_code_norm="LED", to_city=None,
        codeshare=True, operated_by_norm="SU", departure_time="10:00")
    assert key == _sha("SU|2017-01-01|SVO|LED|10:00")
    assert no_op is False


def test_record_key_codeshare_placeholder():
    key, no_op = N.make_record_key(
        source_name="boarding-pass", flight_number_norm="SU5436",
        departure_date="2017-01-01", arrival_date=None,
        from_code_norm="VVO", to_code_norm="ICN", to_city=None,
        codeshare=True, operated_by_norm=None, departure_time="11:25")
    assert no_op is True
    assert key == _sha("SU5436|2017-01-01|VVO|ICN")


def test_codeshare_string_mapping():
    assert N.normalize_codeshare("Own")[0] is False
    assert N.normalize_codeshare("Operated")[0] is True
    assert N.normalize_codeshare("true")[0] is True
    assert N.normalize_codeshare("false")[0] is False


def test_sex_normalization():
    assert N.normalize_sex("Male")[0] == "M"
    assert N.normalize_sex("Female")[0] == "F"
    assert N.normalize_sex("M")[0] == "M"
    assert N.normalize_sex("F")[0] == "F"


def test_missing_departure_date(lake):
    load_fixture(lake, "pointz", "missing_departure_date.csv")
    reasons = lake.execute(
        "SELECT departure_date_missing_reason FROM silver.records_norm").fetchall()
    assert reasons and all(r[0] == "converter_bug" for r in reasons)
    assert lake.execute("SELECT count(*) FROM gold.flights").fetchone()[0] == 0
    assert lake.execute(
        "SELECT count(*) FROM gold.anomalies WHERE type='MISSING_DEPARTURE_DATE'"
    ).fetchone()[0] == 3


def test_person_multi_document_count(lake):
    load_fixture(lake, "sirena-export", "person_multi_document.csv")
    n = lake.execute(
        "SELECT count(*) FROM gold.anomalies_open WHERE type='PERSON_MULTI_DOCUMENT'"
    ).fetchone()[0]
    assert n == 3, n


def test_boarding_pass_name_order(lake):
    load_fixture(lake, "boarding-pass", "rea_mapping.csv")
    row = lake.execute(
        "SELECT first_name_lat, last_name_lat, name_order_hint "
        "FROM silver.records_norm WHERE first_name_lat='LIDIYA' "
        "AND last_name_lat='ZHDANOVA'").fetchone()
    assert row is not None
    assert row[0] == "LIDIYA"
    assert row[1] == "ZHDANOVA"
    assert row[2] in ("given_first", "unknown")


# ---------------------------------------------------------------------------
# fixture coverage
# ---------------------------------------------------------------------------

def test_rea_mapped(lake):
    load_fixture(lake, "boarding-pass", "rea_mapping.csv")
    bad = lake.execute(
        "SELECT count(*) FROM silver.records_norm WHERE from_code_norm='REA' "
        "OR to_code_norm='REA'").fetchone()[0]
    assert bad == 0
    mapped = lake.execute(
        "SELECT count(*) FROM silver.records_norm WHERE to_code_norm='ICN'"
    ).fetchone()[0]
    assert mapped > 0


def test_boarding_data_arrival_only(lake):
    load_fixture(lake, "boarding-data", "boarding_data_arrival_only.csv")
    n = lake.execute("SELECT count(*) FROM gold.flights").fetchone()[0]
    assert n == 5
    missing = lake.execute(
        "SELECT count(*) FROM gold.anomalies WHERE type='MISSING_REQUIRED_FIELD'"
    ).fetchone()[0]
    assert missing == 0
    keys = lake.execute(
        "SELECT count(*) FROM silver.record_aux WHERE record_key IS NOT NULL"
    ).fetchone()[0]
    assert keys == 5


def test_codeshare_placeholder(lake):
    load_fixture(lake, "boarding-data", "codeshare_placeholder.csv")
    n = lake.execute(
        "SELECT count(*) FROM gold.anomalies WHERE type='CODESHARE_NO_OPERATOR'"
    ).fetchone()[0]
    assert n == 5
    # the fallback (non-codeshare) key is used
    row = lake.execute(
        "SELECT s.flight_number_norm, s.arrival_date, s.to_city, k.record_key "
        "FROM silver.records_norm s JOIN silver.record_aux k USING(record_id) "
        "WHERE s.flight_number_norm='SU4600'").fetchone()
    assert row[3] == _sha(f"{row[0]}|{row[1]}|{row[2]}")


def test_ticket_shared_fixture(lake):
    """The fixture pairs Russian/Latin spellings of the same traveller.

    SPEC identity rules (document key) attach them to one person, so no
    TICKET_SHARED_BY_PERSONS anomaly is expected; the fixture is still
    exercised end to end.
    """
    load_fixture(lake, "sirena-export", "ticket_shared.csv")
    assert lake.execute("SELECT count(*) FROM silver.records_norm").fetchone()[0] == 6
    assert lake.execute("SELECT count(*) FROM identity.persons").fetchone()[0] == 3


def test_ff_multi_person_fixture(lake):
    load_fixture(lake, "sirena-export", "ff_multi_person.csv")
    assert lake.execute("SELECT count(*) FROM silver.records_norm").fetchone()[0] == 6
    assert lake.execute("SELECT count(*) FROM identity.persons").fetchone()[0] == 3


def test_boarding_pass_pnr_attach(lake):
    load_fixture(lake, "sirena-export", "boarding_pass_pnr_attach_sirena.csv")
    load_fixture(lake, "boarding-pass", "boarding_pass_pnr_attach_bp.csv")
    bp = lake.execute(
        "SELECT record_id, person_id FROM silver.records_norm "
        "WHERE source_name='boarding-pass'").fetchall()
    assert len(bp) == 3
    assert all(p is not None for _, p in bp)
    assert lake.execute(
        "SELECT count(*) FROM gold.anomalies WHERE type='AMBIGUOUS_NAME_ORDER_V2'"
    ).fetchone()[0] == 0


def test_ambiguous_name_order(lake):
    load_fixture(lake, "boarding-pass", "ambiguous_name_order.csv")
    n = lake.execute(
        "SELECT count(*) FROM gold.anomalies WHERE type='AMBIGUOUS_NAME_ORDER_V2'"
    ).fetchone()[0]
    assert n == 5


def test_forum_pending_promote(lake):
    load_fixture(lake, "flyer-forum-users", "forum_pending_promote_users.csv",
                 schema="v1-forum-users")
    assert lake.execute("SELECT count(*) FROM identity.persons").fetchone()[0] == 0
    assert lake.execute("SELECT count(*) FROM identity.pending_keys").fetchone()[0] > 0
    load_fixture(lake, "sirena-export", "forum_pending_promote_sirena.csv")
    assert lake.execute("SELECT count(*) FROM identity.persons").fetchone()[0] > 0
    assert lake.execute("SELECT count(*) FROM identity.pending_keys").fetchone()[0] == 0
    assert lake.execute(
        "SELECT count(*) FROM identity.person_events WHERE event_type='pending_promote'"
    ).fetchone()[0] > 0


def test_delta_dt_prefix(lake):
    load_fixture(lake, "pointz", "delta_dt_prefix.csv")
    n = lake.execute(
        "SELECT count(*) FROM gold.anomalies WHERE type='DELTA_DT_PREFIX'"
    ).fetchone()[0]
    assert n == 3
