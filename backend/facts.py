"""Gold layer rebuild and cross-record anomaly detection (SPEC 8.1, 9.10-9.12)."""
from __future__ import annotations

import json
from typing import Dict, List, Set, Tuple

from .anomalies import AnomalyStore

FACT_SOURCE_SQL = "('boarding-data','boarding-pass','sirena-export','pointz','skyteam-exchange','flyer-forum-flights')"

REBUILD_FLIGHTS_SQL = f"""
DELETE FROM gold.flights;

WITH corroborated AS (
    SELECT DISTINCT xk.record_key, x.person_id
    FROM silver.records_norm x
    JOIN bronze.records xb ON xb.record_id = x.record_id
    JOIN bronze.batches xbt ON xbt.batch_id = x.batch_id
    JOIN silver.record_aux xk ON xk.record_id = x.record_id
    WHERE xb.is_active
      AND NOT xbt.is_schedule
      AND xbt.trust_fact IN ('A','B')
      AND xk.record_key IS NOT NULL
),
filtered AS (
    SELECT
        'f_' || substr(s.record_id, 3) AS flight_id,
        s.person_id,
        s.record_id,
        CASE
            WHEN s.source_name = 'boarding-data' THEN
                sha256(
                    coalesce(s.flight_number_norm, '') || '|' ||
                    coalesce(cast(s.arrival_date AS VARCHAR), '') || '|' ||
                    coalesce(s.to_city, '')
                )
            ELSE k.record_key
        END AS record_key,
        s.flight_number_norm,
        s.operated_by_norm,
        s.departure_date,
        s.departure_time,
        s.from_code_norm,
        s.to_code_norm,
        s.source_name,
        s.batch_id,
        bt.trust_fact
    FROM silver.records_norm s
    JOIN bronze.records b ON b.record_id = s.record_id
    JOIN bronze.batches bt ON bt.batch_id = s.batch_id
    LEFT JOIN silver.record_aux k ON k.record_id = s.record_id
    WHERE b.is_active
      AND NOT bt.is_schedule
      AND s.source_name IN {FACT_SOURCE_SQL}
      AND (
          s.departure_date IS NOT NULL
          OR (s.source_name = 'boarding-data' AND s.arrival_date IS NOT NULL)
      )
)
INSERT INTO gold.flights
SELECT
    f.flight_id, f.person_id, f.record_id, f.record_key,
    f.flight_number_norm, f.operated_by_norm,
    f.departure_date, f.departure_time,
    f.from_code_norm, f.to_code_norm,
    f.source_name, f.batch_id, NULL, TRUE
FROM filtered f
WHERE f.trust_fact IN ('A','B')
   OR (
       f.trust_fact = 'C'
       AND f.record_key IS NOT NULL
       AND EXISTS (
           SELECT 1 FROM corroborated c
           WHERE c.record_key = f.record_key
             AND c.person_id IS NOT DISTINCT FROM f.person_id
       )
   );

UPDATE gold.flights SET dedup_group_id = g.min_id
FROM (
    SELECT record_key, min(record_id) AS min_id
    FROM gold.flights
    WHERE record_key IS NOT NULL
    GROUP BY record_key
) g
WHERE gold.flights.record_key = g.record_key;
"""


def _ensure_indexes(con) -> None:
    """One-time indexes for join keys. Idempotent."""
    for stmt in (
        "CREATE INDEX IF NOT EXISTS ix_rn_record_id ON silver.records_norm(record_id)",
        "CREATE INDEX IF NOT EXISTS ix_rn_person_id ON silver.records_norm(person_id)",
        "CREATE INDEX IF NOT EXISTS ix_ra_record_id ON silver.record_aux(record_id)",
        "CREATE INDEX IF NOT EXISTS ix_ra_record_key ON silver.record_aux(record_key)",
        "CREATE INDEX IF NOT EXISTS ix_gf_record_key ON gold.flights(record_key)",
    ):
        con.execute(stmt)


def rebuild_flights(con) -> None:
    _ensure_indexes(con)
    con.execute(REBUILD_FLIGHTS_SQL)


# ---------------------------------------------------------------------------
# cross-record anomaly detection
# ---------------------------------------------------------------------------

def _active(con, sql: str, params=None):
    return con.execute(sql, params or []).fetchall()


def detect_cross_record(con, anoms: AnomalyStore) -> Set[str]:
    """Detect SPEC 8.1 anomalies over all active silver records.

    Returns the set of (type|dedup) keys emitted.
    """
    emitted: Set[str] = set()

    con.execute("""
        CREATE OR REPLACE TEMP VIEW _name_triplets AS
        SELECT s.record_id, s.person_id, s.document_norm,
               s.last_name_lat, s.first_name_lat, s.birth_date,
               sha256(
                   coalesce(s.last_name_lat,'') || '|' ||
                   coalesce(s.first_name_lat,'') || '|' ||
                   coalesce(cast(s.birth_date AS VARCHAR),'')
               ) AS triplet
        FROM silver.records_norm s
        JOIN bronze.records b ON b.record_id = s.record_id
        WHERE b.is_active AND s.last_name_lat IS NOT NULL
          AND s.first_name_lat IS NOT NULL
    """)

    def emit(atype, severity, dedup, details, rids=None, pids=None):
        anoms.emit(atype, severity, dedup, details, record_ids=rids,
                   person_ids=pids)
        emitted.add(f"{atype}|{dedup}")

    # DOCUMENT_MULTI_PERSON
    rows = _active(con, f"""
        SELECT s.document_norm,
               count(DISTINCT s.person_id) AS np,
               count(DISTINCT s.name_dob_key) AS nn,
               list(DISTINCT s.person_id) AS pids
        FROM silver.records_norm s
        JOIN bronze.records b USING(record_id)
        WHERE b.is_active AND s.document_norm IS NOT NULL
          AND s.person_id IS NOT NULL AND s.name_dob_key IS NOT NULL
        GROUP BY s.document_norm
        HAVING count(DISTINCT s.person_id) > 1
           AND count(DISTINCT s.name_dob_key) > 1
    """)
    for doc, np_, nn, pids in rows:
        emit("DOCUMENT_MULTI_PERSON", "hard", doc,
             {"document_norm": doc, "persons": np_, "name_dobs": nn},
             pids=pids)

    # DOCUMENT_MULTI_SEX
    rows = _active(con, f"""
        SELECT s.document_norm, count(DISTINCT s.person_id) AS np,
               count(DISTINCT s.sex_norm) AS nsx, list(DISTINCT s.person_id) AS pids
        FROM silver.records_norm s
        JOIN bronze.records b USING(record_id)
        WHERE b.is_active AND s.document_norm IS NOT NULL
          AND s.person_id IS NOT NULL AND s.sex_norm IN ('M','F')
        GROUP BY s.document_norm
        HAVING count(DISTINCT s.person_id) > 1 AND count(DISTINCT s.sex_norm) > 1
    """)
    for doc, np_, nsx, pids in rows:
        emit("DOCUMENT_MULTI_SEX", "hard", doc,
             {"document_norm": doc, "persons": np_, "sexes": nsx},
             pids=pids)

    # TICKET_SHARED_BY_PERSONS
    rows = _active(con, """
        SELECT a.ticket_number,
               count(DISTINCT s.document_norm) AS ndocs,
               count(DISTINCT t.triplet) AS nnames,
               list(DISTINCT s.person_id) AS pids
        FROM silver.records_norm s
        JOIN silver.record_aux a ON a.record_id = s.record_id
        JOIN bronze.records b ON b.record_id = s.record_id
        JOIN _name_triplets t ON t.record_id = s.record_id
        WHERE b.is_active AND a.ticket_number IS NOT NULL
          AND s.document_norm IS NOT NULL
        GROUP BY a.ticket_number
        HAVING count(DISTINCT s.document_norm) > 1
           AND count(DISTINCT t.triplet) > 1
    """)
    for ticket, ndocs, nnames, pids in rows:
        emit("TICKET_SHARED_BY_PERSONS", "hard", ticket,
             {"ticket_number": ticket, "documents": ndocs, "name_triplets": nnames},
             pids=pids)

    # PERSON_MULTI_DOCUMENT (one anomaly per distinct name triplet).
    # The triplet tolerates a missing birth date: two documents carrying the
    # same (last, first) and no birth date are still the same person chain.
    rows = _active(con, """
        SELECT triplet, count(DISTINCT document_norm) AS nd,
               list(DISTINCT person_id) AS pids,
               list(DISTINCT record_id)[1:100] AS rids
        FROM _name_triplets
        GROUP BY triplet
        HAVING count(DISTINCT document_norm) > 1
    """)
    for nd, ndocs, pids, rids in rows:
        emit("PERSON_MULTI_DOCUMENT", "soft", nd,
             {"name_triplet_key": nd, "documents": ndocs},
             rids=rids, pids=pids)

    # FF_MULTI_PERSON
    rows = _active(con, """
        SELECT s.ff_key,
               count(DISTINCT s.document_norm) AS ndocs,
               count(DISTINCT t.triplet) AS nnames,
               list(DISTINCT s.person_id) AS pids
        FROM silver.records_norm s
        JOIN bronze.records b ON b.record_id = s.record_id
        JOIN _name_triplets t ON t.record_id = s.record_id
        WHERE b.is_active AND s.ff_key IS NOT NULL
          AND s.document_norm IS NOT NULL
        GROUP BY s.ff_key
        HAVING count(DISTINCT s.document_norm) > 1
           AND count(DISTINCT t.triplet) > 1
    """)
    for ff, ndocs, nnames, pids in rows:
        emit("FF_MULTI_PERSON", "hard", ff,
             {"ff_key": ff, "documents": ndocs, "name_triplets": nnames},
             pids=pids)

    # SAME_FLIGHT_NUMBER_DIFFERENT_ROUTE
    rows = _active(con, """
        SELECT s.flight_number_norm, s.departure_date,
               count(DISTINCT coalesce(s.from_code_norm,'') || '>' ||
                                 coalesce(s.to_code_norm,'')) AS nr
        FROM silver.records_norm s
        JOIN bronze.records b USING(record_id)
        WHERE b.is_active AND s.flight_number_norm IS NOT NULL
          AND s.departure_date IS NOT NULL
        GROUP BY s.flight_number_norm, s.departure_date
        HAVING count(DISTINCT coalesce(s.from_code_norm,'') || '>' ||
                                   coalesce(s.to_code_norm,'')) > 1
    """)
    for fn, dd, nr in rows:
        dedup = f"{fn}|{dd}"
        emit("SAME_FLIGHT_NUMBER_DIFFERENT_ROUTE", "info", dedup,
             {"flight_number": fn, "departure_date": str(dd), "routes": nr})

    # SAME_FLIGHT_NUMBER_DIFFERENT_OPERATOR
    rows = _active(con, """
        SELECT s.flight_number_norm, s.departure_date,
               count(DISTINCT s.operated_by_norm) AS nop
        FROM silver.records_norm s
        JOIN bronze.records b USING(record_id)
        WHERE b.is_active AND s.flight_number_norm IS NOT NULL
          AND s.departure_date IS NOT NULL AND s.operated_by_norm IS NOT NULL
        GROUP BY s.flight_number_norm, s.departure_date
        HAVING count(DISTINCT s.operated_by_norm) > 1
    """)
    for fn, dd, nop in rows:
        dedup = f"{fn}|{dd}"
        emit("SAME_FLIGHT_NUMBER_DIFFERENT_OPERATOR", "soft", dedup,
             {"flight_number": fn, "departure_date": str(dd), "operators": nop})

    # ORPHAN_FORUM_FLIGHT
    rows = _active(con, """
        SELECT s.record_id, s.uid
        FROM silver.records_norm s
        JOIN bronze.records b USING(record_id)
        WHERE b.is_active AND s.source_name = 'flyer-forum-flights'
          AND (s.uid IS NULL OR s.uid NOT IN (
                SELECT json_extract_string(ub.payload, '$.nickname')
                FROM bronze.records ub
                JOIN bronze.batches ubt ON ubt.batch_id = ub.batch_id
                WHERE ubt.source_name = 'flyer-forum-users' AND ub.is_active
                  AND json_extract_string(ub.payload, '$.nickname') IS NOT NULL
          ))
    """)
    for rid, uid in rows:
        emit("ORPHAN_FORUM_FLIGHT", "soft", rid, {"uid": uid}, rids=[rid])

    # FORUM_CONFLICT
    rows = _active(con, """
        SELECT s.person_id, s.record_id
        FROM silver.records_norm s
        JOIN bronze.records b USING(record_id)
        WHERE b.is_active AND s.source_name LIKE 'flyer-forum%'
          AND s.person_id IS NOT NULL
          AND EXISTS (
              SELECT 1 FROM silver.records_norm a
              JOIN bronze.records ab ON ab.record_id = a.record_id
              WHERE ab.is_active AND a.person_id = s.person_id
                AND a.source_name NOT LIKE 'flyer-forum%'
                AND (
                     (a.sex_norm IS NOT NULL AND s.sex_norm IS NOT NULL
                      AND a.sex_norm <> s.sex_norm)
                  OR (a.first_name_lat IS NOT NULL AND s.first_name_lat IS NOT NULL
                      AND a.first_name_lat <> s.first_name_lat)
                )
          )
    """)
    for pid, rid in rows:
        emit("FORUM_CONFLICT", "soft", f"{pid}|{rid}", {}, rids=[rid], pids=[pid])

    # ORPHAN_C_FLIGHT: fact-bearing C row without corroboration.
    # Build the corroborated (record_key, person_id) set once and anti-join
    # against it; a correlated EXISTS here is O(N^2) on the full lake.
    rows = _active(con, f"""
        WITH corroborated AS MATERIALIZED (
            SELECT DISTINCT xk.record_key, x.person_id
            FROM silver.records_norm x
            JOIN bronze.records xb ON xb.record_id = x.record_id
            JOIN bronze.batches xbt ON xbt.batch_id = x.batch_id
            JOIN silver.record_aux xk ON xk.record_id = x.record_id
            WHERE xb.is_active AND NOT xbt.is_schedule
              AND xbt.trust_fact IN ('A','B')
              AND xk.record_key IS NOT NULL
        )
        SELECT s.record_id, s.person_id, k.record_key
        FROM silver.records_norm s
        JOIN bronze.records b ON b.record_id = s.record_id
        JOIN bronze.batches bt ON bt.batch_id = s.batch_id
        JOIN silver.record_aux k ON k.record_id = s.record_id
        WHERE b.is_active AND NOT bt.is_schedule
          AND bt.trust_fact = 'C'
          AND s.source_name IN {FACT_SOURCE_SQL}
          AND NOT EXISTS (
              SELECT 1 FROM corroborated c
              WHERE c.record_key = k.record_key
                AND c.person_id IS NOT DISTINCT FROM s.person_id
          )
    """)
    for rid, pid, rkey in rows:
        emit("ORPHAN_C_FLIGHT", "soft", rid, {"record_key": rkey},
             rids=[rid], pids=[pid] if pid else [])

    return emitted


GLOBAL_TYPES = [
    "DOCUMENT_MULTI_PERSON", "DOCUMENT_MULTI_SEX", "TICKET_SHARED_BY_PERSONS",
    "PERSON_MULTI_DOCUMENT", "FF_MULTI_PERSON",
    "SAME_FLIGHT_NUMBER_DIFFERENT_ROUTE",
    "SAME_FLIGHT_NUMBER_DIFFERENT_OPERATOR",
    "ORPHAN_FORUM_FLIGHT", "FORUM_CONFLICT", "ORPHAN_C_FLIGHT",
]


def finalize(con, anoms: AnomalyStore, batch_id: str) -> dict:
    """Rebuild gold facts and run cross-record detection."""
    rebuild_flights(con)
    detected_pairs = detect_cross_record(con, anoms)
    detected_by_type: Dict[str, Set[str]] = {}
    for key in detected_pairs:
        t, d = key.split("|", 1)
        detected_by_type.setdefault(t, set()).add(d)
    resolved = 0
    for t in GLOBAL_TYPES:
        resolved += anoms.resolve_not_detected(con, t, detected_by_type.get(t, set()),
                                               batch_id)
    return {"resolved": resolved}
