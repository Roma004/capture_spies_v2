"""Anomaly sink with deterministic ids, occurrence counting and resolution.

Anomaly ids are deterministic (SPEC section 8):
    'a_' + sha256(type || '|' || dedup_key)[:32]

Because of that, existing anomalies never need to be loaded into Python.
Each batch keeps only its *pending* anomalies in memory and flushes them with
a single batched ``INSERT ... ON CONFLICT DO UPDATE``.  Occurrence counts are
incremented in SQL.
"""
from __future__ import annotations

import hashlib
import json
from typing import Dict, Iterable, Optional, Set, Tuple

import pyarrow as pa


def anomaly_id(atype: str, dedup_key: str) -> str:
    return "a_" + hashlib.sha256(f"{atype}|{dedup_key}".encode("utf-8")).hexdigest()[:32]


class AnomalyStore:
    """In-memory view of the *pending* anomalies for one pipeline run."""

    def __init__(self, con=None):
        self.pending: Dict[Tuple[str, str], dict] = {}
        self.con = con

    # ------------------------------------------------------------------
    def emit(self, atype: str, severity: str, dedup_key: str,
             details: Optional[dict] = None,
             record_ids: Optional[Iterable[str]] = None,
             person_ids: Optional[Iterable[str]] = None) -> None:
        key = (atype, dedup_key)
        slot = self.pending.get(key)
        if slot is None:
            slot = {
                "type": atype,
                "severity": severity,
                "dedup_key": dedup_key,
                "details": {},
                "count": 0,
                "record_ids": set(),
                "person_ids": set(),
            }
            self.pending[key] = slot
        slot["count"] += 1
        if details:
            slot["details"].update(details)
        if record_ids:
            slot["record_ids"].update(x for x in record_ids if x)
        if person_ids:
            slot["person_ids"].update(x for x in person_ids if x)

    # ------------------------------------------------------------------
    def flush(self, con, batch_id: Optional[str]) -> Tuple[int, int]:
        """Persist pending anomalies in one batched statement.

        Returns ``(opened, updated)``.  Existing rows keep their status and
        have ``details.occurrences`` incremented; ``person_ids`` /
        ``record_ids`` are unioned.
        """
        if not self.pending:
            return 0, 0

        rows = []
        for (atype, dedup), slot in self.pending.items():
            aid = anomaly_id(atype, dedup)
            details = dict(slot["details"])
            details["dedup_key"] = dedup
            details["occurrences"] = slot["count"]
            rows.append((
                aid,
                atype,
                slot["severity"],
                json.dumps(sorted(slot["person_ids"]), default=str),
                json.dumps(sorted(slot["record_ids"]), default=str),
                json.dumps(details, default=str),
                "open",
                str(batch_id) if batch_id is not None else None,
            ))

        table = pa.table({
            "anomaly_id": pa.array([r[0] for r in rows], type=pa.string()),
            "type": pa.array([r[1] for r in rows], type=pa.string()),
            "severity": pa.array([r[2] for r in rows], type=pa.string()),
            "person_ids": pa.array([r[3] for r in rows], type=pa.string()),
            "record_ids": pa.array([r[4] for r in rows], type=pa.string()),
            "details": pa.array([r[5] for r in rows], type=pa.string()),
            "status": pa.array([r[6] for r in rows], type=pa.string()),
            "opened_in_batch": pa.array([r[7] for r in rows], type=pa.string()),
        })
        con.register("_anom_new", table)
        updated = con.execute(
            "SELECT count(*) FROM _anom_new n "
            "JOIN gold.anomalies a USING(anomaly_id)").fetchone()[0]
        opened = len(rows) - int(updated)
        con.execute("""
            INSERT INTO gold.anomalies
                (anomaly_id, type, severity, person_ids, record_ids,
                 details, status, opened_in_batch)
            SELECT anomaly_id, type, severity, person_ids, record_ids,
                   details, status, opened_in_batch
            FROM _anom_new
            ON CONFLICT (anomaly_id) DO UPDATE SET
                details = json_merge_patch(
                    EXCLUDED.details,
                    json_object(
                        'occurrences',
                        coalesce(
                            CAST(json_extract_string(
                                gold.anomalies.details, '$.occurrences')
                                 AS BIGINT), 1)
                        + CAST(json_extract_string(
                                EXCLUDED.details, '$.occurrences') AS BIGINT))),
                person_ids = to_json(list_distinct(list_concat(
                    CAST(gold.anomalies.person_ids AS VARCHAR[]),
                    CAST(EXCLUDED.person_ids AS VARCHAR[])))),
                record_ids = to_json(list_distinct(list_concat(
                    CAST(gold.anomalies.record_ids AS VARCHAR[]),
                    CAST(EXCLUDED.record_ids AS VARCHAR[]))))
        """)
        con.unregister("_anom_new")
        self.pending.clear()
        return opened, int(updated)

    # ------------------------------------------------------------------
    def resolve_not_detected(self, con, atype: str, detected: Set[str],
                             batch_id: Optional[str]) -> int:
        """Resolve open anomalies of ``atype`` whose dedup key is gone.

        The set of open anomalies is read from SQL, not held in Python.
        """
        rows = con.execute(
            "SELECT anomaly_id, "
            "coalesce(json_extract_string(details, '$.dedup_key'), anomaly_id) "
            "FROM gold.anomalies WHERE type = ? AND status = 'open'",
            [atype]).fetchall()
        ids = [aid for aid, dedup in rows if dedup not in detected]
        if ids:
            t = pa.table({"anomaly_id": pa.array(ids, type=pa.string())})
            con.register("_anom_res", t)
            con.execute(
                "UPDATE gold.anomalies SET status='resolved', "
                "resolved_in_batch=?, resolved_at=now() FROM _anom_res u "
                "WHERE gold.anomalies.anomaly_id=u.anomaly_id",
                [batch_id])
            con.unregister("_anom_res")
        return len(ids)

    def mark_ack(self, atype: str, dedup_key: str) -> None:
        self.con.execute(
            "UPDATE gold.anomalies SET status='ack' WHERE anomaly_id=?",
            [anomaly_id(atype, dedup_key)])
