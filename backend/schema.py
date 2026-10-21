"""DDL for the layered datalake (SPEC section 5)."""
from __future__ import annotations

DDL = """
CREATE SCHEMA IF NOT EXISTS raw;
CREATE SCHEMA IF NOT EXISTS bronze;
CREATE SCHEMA IF NOT EXISTS silver;
CREATE SCHEMA IF NOT EXISTS identity;
CREATE SCHEMA IF NOT EXISTS ref;
CREATE SCHEMA IF NOT EXISTS gold;
CREATE SCHEMA IF NOT EXISTS views;
CREATE SCHEMA IF NOT EXISTS audit;

CREATE TABLE IF NOT EXISTS raw.manifests (
    batch_id UUID PRIMARY KEY,
    source_name TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    batch_kind TEXT NOT NULL,
    effective_date DATE,
    trust_identity TEXT NOT NULL,
    trust_fact TEXT NOT NULL,
    is_schedule BOOLEAN NOT NULL,
    content_checksum TEXT NOT NULL,
    supersedes JSON,
    retracts JSON,
    parquet_path TEXT NOT NULL,
    loaded_at TIMESTAMP NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_manifests_src_checksum
    ON raw.manifests(source_name, content_checksum);

CREATE TABLE IF NOT EXISTS bronze.batches (
    batch_id UUID PRIMARY KEY,
    source_name TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    batch_kind TEXT NOT NULL,
    effective_date DATE,
    trust_identity TEXT NOT NULL,
    trust_fact TEXT NOT NULL,
    is_schedule BOOLEAN NOT NULL,
    content_checksum TEXT NOT NULL,
    loaded_at TIMESTAMP NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_batches_src_checksum
    ON bronze.batches(source_name, content_checksum);

CREATE TABLE IF NOT EXISTS bronze.records (
    record_id TEXT PRIMARY KEY,
    batch_id UUID NOT NULL,
    source_row INTEGER NOT NULL,
    payload JSON NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    loaded_at TIMESTAMP NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS silver.records_norm (
    record_id TEXT PRIMARY KEY,
    batch_id UUID NOT NULL,
    source_name TEXT NOT NULL,
    source_row INTEGER NOT NULL,
    document_norm TEXT,
    document_type TEXT,
    ff_program_norm TEXT,
    ff_number_norm TEXT,
    ff_key TEXT,
    last_name_lat TEXT,
    first_name_lat TEXT,
    middle_name_lat TEXT,
    birth_date DATE,
    name_dob_key TEXT,
    pnr_last_key TEXT,
    flight_number_norm TEXT,
    flight_number_operator TEXT,
    from_code_norm TEXT,
    to_code_norm TEXT,
    departure_date DATE,
    departure_time TEXT,
    arrival_date DATE,
    arrival_time TEXT,
    operated_by_norm TEXT,
    codeshare BOOLEAN,
    sex_norm TEXT,
    meal TEXT,
    baggage TEXT,
    ff_status TEXT,
    status TEXT,
    fare_class TEXT,
    fare_basis TEXT,
    agent TEXT,
    aircraft TEXT,
    travel_time TEXT,
    from_city TEXT,
    to_city TEXT,
    uid TEXT,
    person_id TEXT,
    name_order_hint TEXT,
    departure_date_missing_reason TEXT,
    payload JSON
);

CREATE TABLE IF NOT EXISTS silver.record_aux (
    record_id TEXT PRIMARY KEY,
    record_key TEXT,
    ticket_number TEXT,
    pnr TEXT
);

CREATE TABLE IF NOT EXISTS identity.persons (
    person_id TEXT PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'active',
    merged_into TEXT,
    primary_source TEXT,
    confidence REAL,
    created_in_batch UUID,
    created_at TIMESTAMP NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS identity.person_keys (
    person_id TEXT NOT NULL,
    key_type TEXT NOT NULL,
    key_value TEXT NOT NULL,
    trust TEXT NOT NULL,
    source_record_id TEXT NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    PRIMARY KEY (person_id, key_type, key_value)
);
CREATE INDEX IF NOT EXISTS ix_person_keys_active
    ON identity.person_keys(key_type, key_value);

CREATE TABLE IF NOT EXISTS identity.person_events (
    event_id BIGINT PRIMARY KEY,
    event_type TEXT NOT NULL,
    person_ids JSON,
    trigger_record_id TEXT,
    batch_id UUID,
    details JSON,
    created_at TIMESTAMP NOT NULL DEFAULT now()
);
CREATE SEQUENCE IF NOT EXISTS identity.person_events_seq START 1;

CREATE TABLE IF NOT EXISTS identity.person_conflicts (
    conflict_id TEXT PRIMARY KEY,
    person_ids JSON,
    conflict_type TEXT,
    severity TEXT,
    key_type TEXT,
    key_value TEXT,
    record_ids JSON,
    status TEXT NOT NULL DEFAULT 'open',
    opened_in_batch UUID,
    opened_at TIMESTAMP NOT NULL DEFAULT now(),
    resolved_in_batch UUID,
    resolved_at TIMESTAMP,
    resolution_note TEXT
);

CREATE TABLE IF NOT EXISTS identity.pending_keys (
    key_type TEXT NOT NULL,
    key_value TEXT NOT NULL,
    source_record_id TEXT NOT NULL,
    batch_id UUID NOT NULL,
    PRIMARY KEY (key_type, key_value, source_record_id)
);

CREATE TABLE IF NOT EXISTS ref.translit_map (
    src_char TEXT PRIMARY KEY,
    dst TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ref.program_map (
    prefix TEXT PRIMARY KEY,
    program_name TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ref.airport_map (
    raw_code TEXT PRIMARY KEY,
    canonical_code TEXT NOT NULL,
    city TEXT,
    country TEXT,
    notes TEXT
);
CREATE TABLE IF NOT EXISTS ref.null_policy (
    source_name TEXT NOT NULL,
    column_name TEXT NOT NULL,
    policy TEXT NOT NULL,
    semantic TEXT NOT NULL,
    PRIMARY KEY (source_name, column_name)
);

CREATE TABLE IF NOT EXISTS gold.flights (
    flight_id TEXT PRIMARY KEY,
    person_id TEXT,
    record_id TEXT NOT NULL UNIQUE,
    record_key TEXT,
    flight_number TEXT,
    operated_by TEXT,
    departure_date DATE,
    departure_time TEXT,
    from_code TEXT,
    to_code TEXT,
    source_name TEXT,
    batch_id UUID,
    dedup_group_id TEXT,
    is_active BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS gold.flight_schedule (
    schedule_id TEXT PRIMARY KEY,
    flight_number TEXT,
    from_code TEXT,
    to_code TEXT,
    departure_time TEXT,
    arrival_time TEXT,
    validity_start_md TEXT,
    validity_end_md TEXT,
    days_of_week_mask TEXT,
    aircraft TEXT,
    travel_time TEXT,
    source_name TEXT,
    batch_id UUID
);

CREATE TABLE IF NOT EXISTS gold.anomalies (
    anomaly_id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    severity TEXT NOT NULL,
    person_ids JSON,
    record_ids JSON,
    details JSON,
    status TEXT NOT NULL DEFAULT 'open',
    opened_in_batch UUID,
    opened_at TIMESTAMP NOT NULL DEFAULT now(),
    resolved_in_batch UUID,
    resolved_at TIMESTAMP,
    resolution_note TEXT
);
CREATE INDEX IF NOT EXISTS ix_anomalies_type_dedup
    ON gold.anomalies(type, status);

CREATE TABLE IF NOT EXISTS audit.batch_runs (
    batch_id UUID,
    source_name TEXT,
    status TEXT,
    error TEXT,
    recorded_at TIMESTAMP NOT NULL DEFAULT now()
);
"""

VIEW_DDL = """
CREATE OR REPLACE VIEW identity.person_canonical AS
WITH RECURSIVE resolve(original_person_id, current_person_id, depth) AS (
    SELECT person_id, person_id, 0 FROM identity.persons
    UNION ALL
    SELECT r.original_person_id,
           p.merged_into,
           r.depth + 1
    FROM resolve r
    JOIN identity.persons p ON p.person_id = r.current_person_id
    WHERE p.status = 'merged' AND p.merged_into IS NOT NULL AND r.depth < 50
)
SELECT original_person_id AS original_person_id,
       current_person_id  AS canonical_person_id
FROM resolve r
WHERE NOT EXISTS (
    SELECT 1 FROM identity.persons p
    WHERE p.person_id = r.current_person_id
      AND p.status = 'merged' AND p.merged_into IS NOT NULL
);

CREATE OR REPLACE VIEW gold.anomalies_open AS
SELECT * FROM gold.anomalies WHERE status = 'open';

CREATE OR REPLACE VIEW gold.flight_dedup AS
SELECT
    f.record_key,
    min(f.dedup_group_id) AS dedup_group_id,
    min(f.flight_number) AS flight_number,
    min(f.operated_by) AS operated_by,
    min(f.departure_date) AS departure_date,
    min(f.from_code) AS from_code,
    min(f.to_code) AS to_code,
    min(c.canonical_person_id) AS person_id,
    array_agg(f.record_id ORDER BY f.record_id) AS source_record_ids,
    array_agg(DISTINCT f.source_name ORDER BY f.source_name) AS source_names
FROM gold.flights f
JOIN silver.records_norm s ON s.record_id = f.record_id
LEFT JOIN identity.person_canonical c ON c.original_person_id = f.person_id
WHERE f.record_key IS NOT NULL AND f.is_active AND s.record_id IS NOT NULL
GROUP BY f.record_key;

CREATE OR REPLACE VIEW gold.flights_enriched AS
SELECT f.*,
       sch.planned_aircraft,
       sch.planned_travel_time,
       sch.planned_arrival_time
FROM gold.flights f
LEFT JOIN LATERAL (
    SELECT s.aircraft  AS planned_aircraft,
           s.travel_time AS planned_travel_time,
           s.arrival_time AS planned_arrival_time
    FROM gold.flight_schedule s
    WHERE s.flight_number = f.flight_number
      AND s.from_code = f.from_code
      AND s.to_code = f.to_code
    ORDER BY s.validity_start_md ASC NULLS LAST, s.schedule_id ASC
    LIMIT 1
) sch ON TRUE;
"""
