# tests/fixtures

Small, self-contained batches for backend unit tests and localized
integration tests. All fixtures are v1 schema (39 columns) unless noted.

## Identity / anomaly fixtures

### rea_mapping.csv
Rows where from_code or to_code == rea/REA (case-insensitive).
Test: normalization maps rea/REA to ICN via ref.airport_map.

### ticket_shared.csv
Each ticket_number appears on >=2 records with different (last, first, birth).
Test: TICKET_SHARED_BY_PERSONS (hard) emitted; persons are NOT merged.

### ff_multi_person.csv
Each (ff_program, ff_number) appears on >=2 records with different name
triplets.
Test: FF_MULTI_PERSON (hard) emitted; merge blocked.

### person_multi_document.csv
Each name triplet appears with both internal (4-6) and foreign (2-7)
document formats.
Test: PERSON_MULTI_DOCUMENT (soft) emitted; merge allowed; document_type
populated.

### codeshare_placeholder.csv
boarding-data rows with codeshare=true and operated_by null.
Test: CODESHARE_NO_OPERATOR (soft) emitted; fallback record_key used.

### boarding_pass_pnr_attach_bp.csv + _sirena.csv
Load order: sirena first (creates persons from pnr/ticket/document), then
boarding-pass (attaches via matching pnr, regardless of name).
Test: after both loads, boarding-pass records have person_id matching the
corresponding sirena person; no AMBIGUOUS_NAME_ORDER_V2 for attached rows.

### ambiguous_name_order.csv
Boarding-pass rows with short/initial-like tokens (<=2 chars) where name
heuristic cannot decide order.
Test: if pnr-attach fails, AMBIGUOUS_NAME_ORDER_V2 (soft) emitted per record.

### boarding_data_arrival_only.csv
boarding-data rows: only arrival_date/time/to_city populated; departure_*
and from_* are null.
Test: record_key = flight_number + arrival_date + to_city; no
MISSING_REQUIRED_FIELD for the expected-null fields.

### forum_pending_promote_users.csv + _sirena.csv
Load order: forum-users first (C trust; all FF keys go to pending_keys;
zero persons created). Then sirena (A trust; same FFs match sirena rows,
persons created, pending keys promoted).
Test: count(persons)==0 after step 1; count(persons)>0 and
count(pending_keys)==0 after step 2; person_events contains
pending_promote.

## Reference / regression fixtures

### translit_pairs.csv
document, cyrillic_last, latin_last, cyrillic_first, latin_first.
1000 pairs from boarding-data (latin) + sirena (cyrillic) sharing the
same document.
Test: transliterate(cyrillic_*) == latin_* for all rows.

### delta_dt_prefix.csv
Sampled from RAW data/converted/pointz.csv — has DT-prefixed ff_number
for Delta SkyMiles. Tests DELTA_DT_PREFIX anomaly detector against
converter regression (current unified output is already fixed).

### missing_departure_date.csv
Synthetic rows with departure_date=null, arrival_date=null, flight_number
present. Tests MISSING_DEPARTURE_DATE (soft) and that no flight_fact is
created.

## expected_counts.json
Baseline counts computed from current data/unified/. Integration tests
read values here, do not hardcode. See the file's notes for exact
expectations and tolerances.

## Skip policy
Unit tests whose fixture file is missing must pytest.skip(reason=...).
Integration tests against data/unified/ must FAIL (not skip) if the
directory is missing.
