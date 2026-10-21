"""Identity resolution (SPEC section 7).

The resolver keeps an in-memory key index for the whole lake.  A fresh
instance can be rebuilt from the identity/silver tables, which is what a
new process does; a long-running process reuses the cached instance.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Set, Tuple

import pyarrow as pa

from .anomalies import AnomalyStore


def sha32(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]


@dataclass
class Person:
    person_id: str
    status: str = "active"
    merged_into: Optional[str] = None
    primary_source: Optional[str] = None
    confidence: float = 0.5
    created_in_batch: Optional[str] = None
    strong: int = 0
    total: int = 0


_SILVER_UPDATE_COLUMNS = ["record_id", "person_id", "name_order_hint"]


class IdentityResolver:
    def __init__(self):
        self.persons: Dict[str, Person] = {}
        self.key_index: Dict[Tuple[str, str], Set[str]] = {}
        self.keys_by_person: Dict[str, Set[Tuple[str, str]]] = {}
        self.key_meta: Dict[Tuple[str, str, str], Tuple[str, str]] = {}
        self.name_dobs: Dict[str, Set[str]] = {}
        self.docs: Dict[str, Set[str]] = {}
        self.ffs: Dict[str, Set[str]] = {}
        self.tickets: Dict[str, Set[str]] = {}
        self.pnrs: Dict[str, Set[str]] = {}
        self.names: Dict[str, Set[Tuple[str, str]]] = {}
        self.sexes: Dict[str, Set[str]] = {}
        self.first_names: Dict[str, Set[str]] = {}
        self.ab_sexes: Dict[str, Set[str]] = {}
        self.ab_first_names: Dict[str, Set[str]] = {}
        self.ticket_record: Dict[Tuple[str, str], str] = {}
        self.pnr_index: Dict[str, Set[str]] = {}
        self.name_pair_index: Dict[Tuple[str, str], Set[str]] = {}
        self.pending: Set[Tuple[str, str, str]] = set()

        self._new_keys: List[tuple] = []
        self._deactivated_keys: List[tuple] = []
        self._new_events: List[dict] = []
        self._pending_inserts: List[tuple] = []
        self._pending_deletes: List[tuple] = []
        self._silver_updates: Dict[str, Tuple[Optional[str], Optional[str]]] = {}
        self._conflicts: List[dict] = []
        self._loaded = False

    # ------------------------------------------------------------------
    # canonical helpers
    # ------------------------------------------------------------------
    def canonical(self, pid: str) -> str:
        seen = 0
        while pid in self.persons:
            p = self.persons[pid]
            if p.status == "merged" and p.merged_into:
                pid = p.merged_into
                seen += 1
                if seen > 50:
                    break
            else:
                break
        return pid

    # ------------------------------------------------------------------
    # load from database
    # ------------------------------------------------------------------
    def load(self, con) -> None:
        self.persons.clear()
        for (pid, status, merged_into, primary, confidence,
             created_in_batch) in con.execute(
                "SELECT person_id, status, merged_into, primary_source, "
                "confidence, created_in_batch FROM identity.persons").fetchall():
            self.persons[pid] = Person(pid, status or "active", merged_into,
                                       primary, float(confidence or 0.0),
                                       str(created_in_batch) if created_in_batch else None)

        for pid, kt, kv, trust, rid in con.execute(
                "SELECT person_id, key_type, key_value, trust, source_record_id "
                "FROM identity.person_keys WHERE is_active").fetchall():
            cpid = self.canonical(pid)
            if cpid != pid:
                pid = cpid
            self._index_key(pid, kt, kv, trust, rid)

        for kt, kv, rid in con.execute(
                "SELECT key_type, key_value, source_record_id FROM identity.pending_keys").fetchall():
            self.pending.add((kt, kv, rid))

        # record-derived attributes
        rows = con.execute(
            "SELECT s.person_id, s.last_name_lat, s.first_name_lat, s.sex_norm, "
            "s.name_dob_key, a.ticket_number, a.pnr, s.source_name, s.record_id "
            "FROM silver.records_norm s LEFT JOIN silver.record_aux a USING(record_id) "
            "WHERE s.person_id IS NOT NULL").fetchall()
        for pid, last, first, sex, nd, ticket, pnr, source, rid in rows:
            pid = self.canonical(pid)
            is_forum = (source or "").startswith("flyer-forum")
            if ticket:
                self.ticket_record.setdefault((pid, ticket), rid)
            if not is_forum:
                if sex in ("M", "F"):
                    self.ab_sexes.setdefault(pid, set()).add(sex)
                if first:
                    self.ab_first_names.setdefault(pid, set()).add(first)
            if last and first:
                self.names.setdefault(pid, set()).add((last, first))
                self.name_pair_index.setdefault((last, first), set()).add(pid)
            if first:
                self.first_names.setdefault(pid, set()).add(first)
            if sex in ("M", "F"):
                self.sexes.setdefault(pid, set()).add(sex)
            if nd:
                self.name_dobs.setdefault(pid, set()).add(nd)
            if ticket:
                self.tickets.setdefault(pid, set()).add(ticket)
            if pnr:
                self.pnrs.setdefault(pid, set()).add(pnr)
                self.pnr_index.setdefault(pnr, set()).add(pid)

        for pid, total, strong in con.execute(
                "SELECT person_id, count(*), count(*) FILTER (WHERE "
                "name_dob_key IS NOT NULL OR document_norm IS NOT NULL) "
                "FROM silver.records_norm WHERE person_id IS NOT NULL GROUP BY 1").fetchall():
            pid = self.canonical(pid)
            p = self.persons.get(pid)
            if p:
                p.total = int(total)
                p.strong = int(strong)
                p.confidence = min(1.0, p.strong / p.total) if p.total else p.confidence
        self._loaded = True

    # ------------------------------------------------------------------
    def _index_key(self, pid, kt, kv, trust=None, rid=None):
        self.key_index.setdefault((kt, kv), set()).add(pid)
        self.keys_by_person.setdefault(pid, set()).add((kt, kv))
        if trust is not None:
            self.key_meta[(pid, kt, kv)] = (trust, rid)

    def _ensure_loaded(self, con):
        if not self._loaded:
            self.load(con)

    # ------------------------------------------------------------------
    # conflict detection
    # ------------------------------------------------------------------
    def _hard_conflict(self, rec: dict, pid: str) -> Optional[Tuple[str, str]]:
        """Return (rule, existing_record_id) when a hard conflict applies."""
        nd = rec.get("name_dob_key")
        if not nd:
            return None
        pnds = self.name_dobs.get(pid, set())
        if not pnds or all(x == nd for x in pnds):
            return None
        # same document?
        doc = rec.get("document_norm")
        if doc and doc in self.docs.get(pid, set()):
            rid = self._record_with_key(pid, "document", doc)
            return ("R1", rid)
        ff = rec.get("ff_key")
        if ff and ff in self.ffs.get(pid, set()):
            rid = self._record_with_key(pid, "ff", ff)
            return ("R2", rid)
        ticket = rec.get("ticket_number")
        if ticket and ticket in self.tickets.get(pid, set()):
            # find a record of this person carrying the ticket
            rid = self._ticket_record(pid, ticket)
            return ("R3", rid)
        return None

    def _record_with_key(self, pid: str, kt: str, kv: str) -> Optional[str]:
        meta = self.key_meta.get((pid, kt, kv))
        return meta[1] if meta else None

    def _ticket_record(self, pid: str, ticket: str) -> Optional[str]:
        return self.ticket_record.get((pid, ticket))

    # ------------------------------------------------------------------
    # main entry point
    # ------------------------------------------------------------------
    def process_batch(self, con, manifest, batch_id: str, anoms: AnomalyStore) -> None:
        self._ensure_loaded(con)
        cursor = con.execute(
            "SELECT s.record_id, s.source_name, s.document_norm, s.name_dob_key, "
            "s.ff_key, s.pnr_last_key, s.last_name_lat, s.first_name_lat, "
            "s.sex_norm, s.person_id, a.ticket_number, a.pnr "
            "FROM silver.records_norm s "
            "LEFT JOIN silver.record_aux a USING(record_id) "
            "JOIN bronze.records b USING(record_id) "
            "WHERE s.batch_id = ? AND b.is_active "
            "ORDER BY s.record_id",
            [batch_id],
        )
        trust_identity = manifest.trust_identity
        while True:
            rows = cursor.fetchmany(20000)
            if not rows:
                break
            for (rid, source, doc, nd, ff, pnr_last, last, first, sex,
                 person_id, ticket, pnr) in rows:
                rec = {
                    "record_id": rid,
                    "source_name": source,
                    "document_norm": doc,
                    "name_dob_key": nd,
                    "ff_key": ff,
                    "pnr_last_key": pnr_last,
                    "last_name_lat": last,
                    "first_name_lat": first,
                    "sex_norm": sex,
                    "ticket_number": ticket,
                    "pnr": pnr,
                }
                self._process_record(con, manifest, batch_id, rec, anoms,
                                     trust_identity)

        self._flush(con, batch_id)

    # ------------------------------------------------------------------
    def _process_record(self, con, manifest, batch_id, rec, anoms, trust):
        rid = rec["record_id"]
        source = rec["source_name"]
        is_bp = source == "boarding-pass"
        is_forum = source.startswith("flyer-forum")
        doc = rec["document_norm"]
        nd = rec["name_dob_key"]
        ff = rec["ff_key"]
        pnr_last = rec["pnr_last_key"]
        pnr = rec["pnr"]
        ticket = rec["ticket_number"]
        last = rec["last_name_lat"]
        first = rec["first_name_lat"]

        # ---- candidate collection -------------------------------------
        cands: Dict[str, int] = {}  # pid -> best priority (1 strongest)
        if not is_bp:
            if doc:
                for pid in self.key_index.get(("document", doc), ()):
                    cands[pid] = min(cands.get(pid, 9), 1)
            if nd:
                for pid in self.key_index.get(("name_dob", nd), ()):
                    cands[pid] = min(cands.get(pid, 9), 2)
        pnr_cands: Set[str] = set()
        if is_bp:
            if pnr:
                pnr_cands.update(self.pnr_index.get(pnr, ()))
            if pnr_last:
                pnr_cands.update(self.key_index.get(("pnr_name", pnr_last), ()))
            if not pnr_cands and pnr and first:
                alt = sha32(f"{pnr}|{first}")
                pnr_cands.update(self.key_index.get(("pnr_name", alt), ()))
            for pid in pnr_cands:
                cands[pid] = min(cands.get(pid, 9), 4)

        ff_cands: Set[str] = set()
        if ff:
            ff_cands.update(self.key_index.get(("ff", ff), ()))

        # ---- boarding pass: pnr attach or name-order only -------------
        if is_bp:
            self._process_boarding_pass(rec, cands, anoms)
            return

        # ---- normal identity resolution -------------------------------
        if not cands:
            if ff_cands:
                # ff is confirm-only: attach to an existing person if any
                pid = sorted({self.canonical(x) for x in ff_cands})[0]
                conflict = self._hard_conflict(rec, pid)
                if conflict:
                    rule, existing_rid = conflict
                    p = self.persons.get(pid)
                    if p and p.status == "active":
                        p.status = "suspect"
                    anoms.emit("SPLIT_CANDIDATE", "hard", pid,
                               {"rules": [rule],
                                "record_ids": [existing_rid, rid]},
                               record_ids=[existing_rid, rid], person_ids=[pid])
                    if (doc or nd) and trust in ("A", "B"):
                        self._create_person(rec, manifest, batch_id, anoms)
                    else:
                        self._store_pending(rec, batch_id)
                    return
                anoms.emit("FF_UNVERIFIED", "soft", f"{ff}|{rid}")
                self._maybe_forum_conflict(pid, rec, anoms)
                self._attach(pid, rec, trust, rid, batch_id, strong=False)
                return
            if (doc or nd) and trust in ("A", "B"):
                self._create_person(rec, manifest, batch_id, anoms)
            else:
                self._store_pending(rec, batch_id)
            return

        # conflicts
        conflicts = {}
        for pid in cands:
            c = self._hard_conflict(rec, pid)
            if c:
                conflicts[pid] = c

        if conflicts:
            # mark suspect, emit split candidate, attach only via document
            for pid, (rule, existing_rid) in conflicts.items():
                p = self.persons.get(pid)
                if p and p.status == "active":
                    p.status = "suspect"
                anoms.emit("SPLIT_CANDIDATE", "hard", pid,
                           {"rules": [rule],
                            "record_ids": [existing_rid, rid]},
                           record_ids=[existing_rid, rid], person_ids=[pid])
                self._conflicts.append({
                    "conflict_id": "c_" + sha32(f"{rule}|{pid}|{rid}"),
                    "person_ids": [pid],
                    "conflict_type": rule,
                    "severity": "hard",
                    "key_type": {"R1": "document", "R2": "ff",
                                 "R3": "ticket"}[rule],
                    "key_value": (doc if rule == "R1" else ff if rule == "R2"
                                  else ticket),
                    "record_ids": [existing_rid, rid],
                })
            attachable = [pid for pid in cands
                          if self.canonical(pid) in self.docs and doc
                          and doc in self.docs.get(self.canonical(pid), set())]
            if attachable:
                pid = self.canonical(sorted(attachable)[0])
                self._maybe_forum_conflict(pid, rec, anoms)
                self._attach(pid, rec, trust, rid, batch_id,
                             strong=bool(doc or nd))
            else:
                if (doc or nd) and trust in ("A", "B"):
                    self._create_person(rec, manifest, batch_id, anoms)
                else:
                    self._store_pending(rec, batch_id)
            return

        if len(cands) > 1:
            pid = self._merge(set(cands), rid, batch_id, anoms)
        else:
            pid = self.canonical(next(iter(cands)))

        self._maybe_forum_conflict(pid, rec, anoms)
        self._attach(pid, rec, trust, rid, batch_id, strong=bool(doc or nd))

    # ------------------------------------------------------------------
    def _process_boarding_pass(self, rec, cands, anoms):
        rid = rec["record_id"]
        pid = None
        if cands:
            pid = self.canonical(sorted(cands)[0])
        first = rec["first_name_lat"]
        last = rec["last_name_lat"]
        if pid:
            names = self.names.get(pid, set())
            hint = "unknown"
            if (first, last) in names:
                hint = "given_first"
            elif (last, first) in names:
                hint = "surname_first"
            self._attach(pid, rec, "B", rid, None, strong=False,
                         record_keys=False)
            self._silver_updates[rid] = (pid, hint)
            return
        # pnr attach failed -> name-order lookup only
        hint = "unknown"
        if first and last:
            if self.name_pair_index.get((first, last)):
                hint = "given_first"
            elif self.name_pair_index.get((last, first)):
                hint = "surname_first"
        if hint == "unknown":
            anoms.emit("AMBIGUOUS_NAME_ORDER_V2", "soft", rid,
                       {"pnr": rec.get("pnr"), "ticket_number": rec.get("ticket_number"),
                        "order_A": [first, last], "order_B": [last, first]})
        self._silver_updates[rid] = (None, hint)

    # ------------------------------------------------------------------
    def _attach(self, pid, rec, trust, rid, batch_id, strong,
                record_keys: bool = True):
        pid = self.canonical(pid)
        p = self.persons.get(pid)
        if p is None:
            return
        nd = rec.get("name_dob_key")
        doc = rec.get("document_norm")
        ff = rec.get("ff_key")
        pnr_last = rec.get("pnr_last_key")
        ticket = rec.get("ticket_number")
        pnr = rec.get("pnr")
        last = rec.get("last_name_lat")
        first = rec.get("first_name_lat")
        sex = rec.get("sex_norm")

        key_list = []
        if record_keys:
            if doc:
                key_list.append(("document", doc))
            if nd:
                key_list.append(("name_dob", nd))
            if ff:
                key_list.append(("ff", ff))
            if pnr_last:
                key_list.append(("pnr_name", pnr_last))
        else:
            if pnr_last:
                key_list.append(("pnr_name", pnr_last))
        for kt, kv in key_list:
            self._add_key(pid, kt, kv, trust, rid)

        if nd:
            self.name_dobs.setdefault(pid, set()).add(nd)
        if doc:
            self.docs.setdefault(pid, set()).add(doc)
        if ff:
            self.ffs.setdefault(pid, set()).add(ff)
        if ticket:
            self.tickets.setdefault(pid, set()).add(ticket)
            self.ticket_record.setdefault((pid, ticket), rid)
        if pnr:
            self.pnrs.setdefault(pid, set()).add(pnr)
            self.pnr_index.setdefault(pnr, set()).add(pid)
        if last and first:
            self.names.setdefault(pid, set()).add((last, first))
            self.name_pair_index.setdefault((last, first), set()).add(pid)
        if first:
            self.first_names.setdefault(pid, set()).add(first)
        if sex in ("M", "F"):
            self.sexes.setdefault(pid, set()).add(sex)
        if not rec["source_name"].startswith("flyer-forum"):
            if sex in ("M", "F"):
                self.ab_sexes.setdefault(pid, set()).add(sex)
            if first:
                self.ab_first_names.setdefault(pid, set()).add(first)

        p.total += 1
        if strong:
            p.strong += 1
        p.confidence = min(1.0, p.strong / p.total) if p.total else p.confidence
        self._silver_updates[rid] = (pid, None)

    def _add_key(self, pid, kt, kv, trust, rid):
        cur = self.key_index.get((kt, kv), set())
        if pid in cur:
            # refresh metadata if absent
            self.key_meta.setdefault((pid, kt, kv), (trust, rid))
            return
        self._index_key(pid, kt, kv, trust, rid)
        self._new_keys.append((pid, kt, kv, trust, rid, True))

    # ------------------------------------------------------------------
    def _create_person(self, rec, manifest, batch_id, anoms):
        rid = rec["record_id"]
        pid = "p_" + rid[2:]
        p = Person(person_id=pid, status="active", primary_source=manifest.source_name,
                   confidence=1.0 if (rec.get("document_norm") or rec.get("name_dob_key")) else 0.5,
                   created_in_batch=batch_id, strong=1 if (rec.get("document_norm") or rec.get("name_dob_key")) else 0,
                   total=1)
        self.persons[pid] = p
        # keys
        key_list = []
        if rec.get("document_norm"):
            key_list.append(("document", rec["document_norm"]))
        if rec.get("name_dob_key"):
            key_list.append(("name_dob", rec["name_dob_key"]))
        if rec.get("ff_key"):
            key_list.append(("ff", rec["ff_key"]))
        if rec.get("pnr_last_key"):
            key_list.append(("pnr_name", rec["pnr_last_key"]))
        for kt, kv in key_list:
            self._add_key(pid, kt, kv, manifest.trust_identity, rid)
            # promote pending keys
            self._promote_pending(kt, kv, pid, rid, batch_id)
        nd = rec.get("name_dob_key")
        doc = rec.get("document_norm")
        ff = rec.get("ff_key")
        ticket = rec.get("ticket_number")
        pnr = rec.get("pnr")
        last = rec.get("last_name_lat")
        first = rec.get("first_name_lat")
        sex = rec.get("sex_norm")
        if nd:
            self.name_dobs.setdefault(pid, set()).add(nd)
        if doc:
            self.docs.setdefault(pid, set()).add(doc)
        if ff:
            self.ffs.setdefault(pid, set()).add(ff)
        if ticket:
            self.tickets.setdefault(pid, set()).add(ticket)
            self.ticket_record[(pid, ticket)] = rid
        if pnr:
            self.pnrs.setdefault(pid, set()).add(pnr)
            self.pnr_index.setdefault(pnr, set()).add(pid)
        if last and first:
            self.names.setdefault(pid, set()).add((last, first))
            self.name_pair_index.setdefault((last, first), set()).add(pid)
        if first:
            self.first_names.setdefault(pid, set()).add(first)
        if sex in ("M", "F"):
            self.sexes.setdefault(pid, set()).add(sex)
        if not rec["source_name"].startswith("flyer-forum"):
            if sex in ("M", "F"):
                self.ab_sexes.setdefault(pid, set()).add(sex)
            if first:
                self.ab_first_names.setdefault(pid, set()).add(first)
        self._silver_updates[rid] = (pid, None)

    # ------------------------------------------------------------------
    def _promote_pending(self, kt, kv, pid, rid, batch_id):
        matches = [p for p in list(self.pending) if p[0] == kt and p[1] == kv]
        for (pkt, pkv, prid) in matches:
            self.pending.discard((pkt, pkv, prid))
            self._pending_deletes.append((pkt, pkv, prid))
        if matches:
            self._new_keys.append((pid, kt, kv, "A", rid, True))
            self.key_meta[(pid, kt, kv)] = ("A", rid)
            self._new_events.append({
                "event_type": "pending_promote",
                "person_ids": [pid],
                "trigger_record_id": rid,
                "batch_id": batch_id,
                "details": {"key_type": kt, "key_value": kv,
                            "promoted_records": [m[2] for m in matches]},
            })

    def _store_pending(self, rec, batch_id):
        rid = rec["record_id"]
        keys = []
        if rec.get("document_norm"):
            keys.append(("document", rec["document_norm"]))
        if rec.get("name_dob_key"):
            keys.append(("name_dob", rec["name_dob_key"]))
        if rec.get("ff_key"):
            keys.append(("ff", rec["ff_key"]))
        if rec.get("pnr_last_key"):
            keys.append(("pnr_name", rec["pnr_last_key"]))
        for kt, kv in keys:
            self.pending.add((kt, kv, rid))
            self._pending_inserts.append((kt, kv, rid, batch_id))
        self._silver_updates[rid] = (None, None)

    # ------------------------------------------------------------------
    def _merge(self, pids: Set[str], trigger_rid: str, batch_id, anoms) -> str:
        pids = {self.canonical(p) for p in pids}
        if len(pids) <= 1:
            return next(iter(pids))
        def sort_key(pid):
            p = self.persons[pid]
            return (-(p.confidence or 0.0), pid)
        ordered = sorted(pids, key=sort_key)
        canonical_pid = ordered[0]
        losers = ordered[1:]
        cp = self.persons[canonical_pid]
        for loser in losers:
            lp = self.persons[loser]
            lp.status = "merged"
            lp.merged_into = canonical_pid
            # move keys
            for (kt, kv) in list(self.keys_by_person.get(loser, set())):
                idx = self.key_index.get((kt, kv))
                if idx:
                    idx.discard(loser)
                    idx.add(canonical_pid)
                self.keys_by_person.setdefault(canonical_pid, set()).add((kt, kv))
                self._deactivated_keys.append((loser, kt, kv))
                trust, rid = self.key_meta.get((loser, kt, kv), ("C", None))
                if (canonical_pid, kt, kv) not in self.key_meta:
                    self._new_keys.append((canonical_pid, kt, kv, trust, rid, True))
                    self.key_meta[(canonical_pid, kt, kv)] = (trust, rid)
            self.keys_by_person.pop(loser, None)
            for attr in ("name_dobs", "docs", "ffs", "tickets", "pnrs",
                         "names", "sexes", "first_names", "ab_sexes",
                         "ab_first_names"):
                src = getattr(self, attr)
                src.setdefault(canonical_pid, set()).update(src.pop(loser, set()))
            for pnr in self.pnrs.get(loser, set()):
                pidset = self.pnr_index.get(pnr)
                if pidset:
                    pidset.discard(loser)
                    pidset.add(canonical_pid)
            for pair in self.names.get(loser, set()):
                pidset = self.name_pair_index.get(pair)
                if pidset:
                    pidset.discard(loser)
                    pidset.add(canonical_pid)
            cp.strong += lp.strong
            cp.total += lp.total
            self._new_events.append({
                "event_type": "merge",
                "person_ids": [loser],
                "trigger_record_id": trigger_rid,
                "batch_id": batch_id,
                "details": {"canonical": canonical_pid},
            })
        cp.confidence = min(1.0, cp.strong / cp.total) if cp.total else cp.confidence
        return canonical_pid

    # ------------------------------------------------------------------
    def _maybe_forum_conflict(self, pid, rec, anoms):
        if not rec["source_name"].startswith("flyer-forum"):
            return
        # authoritative A/B records of this person
        psex = self.ab_sexes.get(pid, set())
        pfirst = self.ab_first_names.get(pid, set())
        rec_sex = rec.get("sex_norm")
        rec_first = rec.get("first_name_lat")
        if (rec_sex and psex and all(s != rec_sex for s in psex)) or \
                (rec_first and pfirst and all(f != rec_first for f in pfirst)):
            anoms.emit("FORUM_CONFLICT", "soft",
                       f"{pid}|{rec['record_id']}",
                       {"person_id": pid, "record_id": rec["record_id"]},
                       person_ids=[pid], record_ids=[rec["record_id"]])

    # ------------------------------------------------------------------
    def flush_state(self, con, batch_id):
        """Kept for API compatibility; state is flushed in _flush."""
        self._flush(con, batch_id)

    def _flush(self, con, batch_id):
        # persons
        if self.persons:
            t = pa.table({
                "person_id": [p.person_id for p in self.persons.values()],
                "status": [p.status for p in self.persons.values()],
                "merged_into": [p.merged_into for p in self.persons.values()],
                "primary_source": [p.primary_source for p in self.persons.values()],
                "confidence": [p.confidence for p in self.persons.values()],
                "created_in_batch": [p.created_in_batch for p in self.persons.values()],
            })
            con.register("_p_state", t)
            con.execute(
                "UPDATE identity.persons SET status=t.status, merged_into=t.merged_into, "
                "confidence=t.confidence FROM _p_state t "
                "WHERE identity.persons.person_id=t.person_id")
            con.execute(
                "INSERT INTO identity.persons "
                "(person_id,status,merged_into,primary_source,confidence,created_in_batch) "
                "SELECT person_id,status,merged_into,primary_source,confidence,created_in_batch "
                "FROM _p_state t WHERE NOT EXISTS "
                "(SELECT 1 FROM identity.persons p WHERE p.person_id=t.person_id)")
            con.unregister("_p_state")

        if self._new_keys:
            # dedupe
            uniq = {(k[0], k[1], k[2]): k for k in self._new_keys}
            ks = list(uniq.values())
            t = pa.table({
                "person_id": [k[0] for k in ks],
                "key_type": [k[1] for k in ks],
                "key_value": [k[2] for k in ks],
                "trust": [k[3] for k in ks],
                "source_record_id": [k[4] for k in ks],
            })
            con.register("_k_new", t)
            con.execute(
                "INSERT INTO identity.person_keys "
                "(person_id,key_type,key_value,trust,source_record_id,is_active) "
                "SELECT person_id,key_type,key_value,trust,source_record_id,TRUE FROM _k_new k "
                "WHERE NOT EXISTS (SELECT 1 FROM identity.person_keys p "
                "WHERE p.person_id=k.person_id AND p.key_type=k.key_type "
                "AND p.key_value=k.key_value)")
            con.unregister("_k_new")

        if self._deactivated_keys:
            ks = list(set(self._deactivated_keys))
            t = pa.table({
                "person_id": [k[0] for k in ks],
                "key_type": [k[1] for k in ks],
                "key_value": [k[2] for k in ks],
            })
            con.register("_k_del", t)
            con.execute(
                "UPDATE identity.person_keys SET is_active=FALSE FROM _k_del d "
                "WHERE identity.person_keys.person_id=d.person_id "
                "AND identity.person_keys.key_type=d.key_type "
                "AND identity.person_keys.key_value=d.key_value")
            con.unregister("_k_del")

        if self._pending_inserts:
            ks = list({(k[0], k[1], k[2]): k for k in self._pending_inserts}.values())
            t = pa.table({
                "key_type": [k[0] for k in ks],
                "key_value": [k[1] for k in ks],
                "source_record_id": [k[2] for k in ks],
                "batch_id": [k[3] for k in ks],
            })
            con.register("_pend", t)
            con.execute(
                "INSERT INTO identity.pending_keys "
                "SELECT key_type,key_value,source_record_id,batch_id FROM _pend p "
                "WHERE NOT EXISTS (SELECT 1 FROM identity.pending_keys x "
                "WHERE x.key_type=p.key_type AND x.key_value=p.key_value "
                "AND x.source_record_id=p.source_record_id)")
            con.unregister("_pend")

        if self._pending_deletes:
            ks = list(set(self._pending_deletes))
            t = pa.table({
                "key_type": [k[0] for k in ks],
                "key_value": [k[1] for k in ks],
                "source_record_id": [k[2] for k in ks],
            })
            con.register("_pend_del", t)
            con.execute(
                "DELETE FROM identity.pending_keys USING _pend_del d "
                "WHERE identity.pending_keys.key_type=d.key_type "
                "AND identity.pending_keys.key_value=d.key_value "
                "AND identity.pending_keys.source_record_id=d.source_record_id")
            con.unregister("_pend_del")

        if self._silver_updates:
            items = list(self._silver_updates.items())
            t = pa.table({
                "record_id": [k for k, _ in items],
                "person_id": [v[0] for _, v in items],
                "name_order_hint": [v[1] for _, v in items],
            })
            con.register("_sil_upd", t)
            con.execute(
                "UPDATE silver.records_norm SET person_id=u.person_id, "
                "name_order_hint=COALESCE(u.name_order_hint, silver.records_norm.name_order_hint) "
                "FROM _sil_upd u WHERE silver.records_norm.record_id=u.record_id")
            con.unregister("_sil_upd")

        if self._new_events:
            start = con.execute(
                "SELECT coalesce(max(event_id),0) FROM identity.person_events").fetchone()[0]
            rows = []
            for i, ev in enumerate(self._new_events, start=1):
                rows.append((start + i, ev["event_type"],
                             json.dumps(ev["person_ids"]),
                             ev["trigger_record_id"], ev["batch_id"],
                             json.dumps(ev["details"], default=str)))
            con.executemany(
                "INSERT INTO identity.person_events "
                "(event_id,event_type,person_ids,trigger_record_id,batch_id,details) "
                "VALUES (?,?,?,?,?,?)", rows)

        if self._conflicts:
            rows = []
            for c in self._conflicts:
                rows.append((c["conflict_id"], json.dumps(c["person_ids"]),
                             c["conflict_type"], c["severity"], c["key_type"],
                             c["key_value"], json.dumps(c["record_ids"]),
                             "open", batch_id))
            con.executemany(
                "INSERT OR REPLACE INTO identity.person_conflicts "
                "(conflict_id,person_ids,conflict_type,severity,key_type,key_value,"
                "record_ids,status,opened_in_batch) VALUES (?,?,?,?,?,?,?,?,?)", rows)

        self._new_keys.clear()
        self._deactivated_keys.clear()
        self._new_events.clear()
        self._pending_inserts.clear()
        self._pending_deletes.clear()
        self._silver_updates.clear()
        self._conflicts.clear()


# ---------------------------------------------------------------------------
# process-wide cache
# ---------------------------------------------------------------------------
_CACHE: Dict[str, IdentityResolver] = {}


def get_resolver(con, key: str) -> IdentityResolver:
    r = _CACHE.get(key)
    if r is None:
        r = IdentityResolver()
        r.load(con)
        _CACHE[key] = r
    return r


def reset_cache(key: str | None = None) -> None:
    if key is None:
        _CACHE.clear()
    else:
        _CACHE.pop(key, None)
