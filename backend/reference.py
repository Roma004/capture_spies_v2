"""Load data/reference/*.csv into ref.* tables (SPEC 12)."""
from __future__ import annotations

import csv
import logging
from pathlib import Path

log = logging.getLogger(__name__)


def _read(path: Path):
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def load_reference(con, ref_dir: Path | str, fail_on_malformed: bool = True) -> None:
    ref_dir = Path(ref_dir)
    con.execute("CREATE SCHEMA IF NOT EXISTS ref")
    con.execute("CREATE TABLE IF NOT EXISTS ref.translit_map(src_char TEXT PRIMARY KEY, dst TEXT NOT NULL)")
    con.execute("CREATE TABLE IF NOT EXISTS ref.program_map(prefix TEXT PRIMARY KEY, program_name TEXT NOT NULL)")
    con.execute("CREATE TABLE IF NOT EXISTS ref.airport_map(raw_code TEXT PRIMARY KEY, canonical_code TEXT NOT NULL, city TEXT, country TEXT, notes TEXT)")
    con.execute("CREATE TABLE IF NOT EXISTS ref.null_policy(source_name TEXT NOT NULL, column_name TEXT NOT NULL, policy TEXT NOT NULL, semantic TEXT NOT NULL, PRIMARY KEY(source_name, column_name))")

    con.execute("DELETE FROM ref.translit_map")
    con.execute("DELETE FROM ref.program_map")
    con.execute("DELETE FROM ref.airport_map")
    con.execute("DELETE FROM ref.null_policy")

    if not ref_dir.exists():
        log.warning("reference directory %s is missing; using defaults", ref_dir)
        return

    t = ref_dir / "translit_map.csv"
    if t.exists():
        rows = _read(t)
        for r in rows:
            if not r.get("src_char"):
                if fail_on_malformed:
                    raise ValueError(f"malformed row in {t}: {r}")
                continue
            con.execute("INSERT OR REPLACE INTO ref.translit_map VALUES (?,?)",
                        [r["src_char"].strip().upper(), (r.get("dst") or "").strip()])
    else:
        log.warning("missing %s; using defaults", t)

    p = ref_dir / "program_map.csv"
    if not p.exists():
        p = ref_dir / "programm_map.csv"
    if p.exists():
        for r in _read(p):
            if not r.get("prefix") or not r.get("program_name"):
                if fail_on_malformed:
                    raise ValueError(f"malformed row in {p}: {r}")
                continue
            con.execute("INSERT OR REPLACE INTO ref.program_map VALUES (?,?)",
                        [r["prefix"].strip().upper(), r["program_name"].strip()])
    else:
        log.warning("missing program map under %s; using defaults", ref_dir)

    a = ref_dir / "airport_map.csv"
    if a.exists():
        for r in _read(a):
            if not r.get("raw_code") or not r.get("canonical_code"):
                if fail_on_malformed:
                    raise ValueError(f"malformed row in {a}: {r}")
                continue
            con.execute("INSERT OR REPLACE INTO ref.airport_map VALUES (?,?,?,?,?)",
                        [r["raw_code"].strip().upper(), r["canonical_code"].strip(),
                         (r.get("city") or "").strip(), (r.get("country") or "").strip(),
                         (r.get("notes") or "").strip()])
    else:
        log.warning("missing %s; using defaults", a)

    n = ref_dir / "null_policy.csv"
    if n.exists():
        for r in _read(n):
            if not r.get("source_name") or not r.get("column_name") or not r.get("policy"):
                if fail_on_malformed:
                    raise ValueError(f"malformed row in {n}: {r}")
                continue
            con.execute("INSERT OR REPLACE INTO ref.null_policy VALUES (?,?,?,?)",
                        [r["source_name"].strip(), r["column_name"].strip(),
                         r["policy"].strip(), r["semantic"].strip()])
    else:
        log.warning("missing %s", n)


def reference_maps(con) -> tuple[dict, dict, dict, dict]:
    """Return python-side copies of the ref tables."""
    translit = dict(con.execute("SELECT src_char, dst FROM ref.translit_map").fetchall())
    programs = dict(con.execute("SELECT prefix, program_name FROM ref.program_map").fetchall())
    airports = dict(con.execute("SELECT upper(raw_code), canonical_code FROM ref.airport_map").fetchall())
    policies = {(s, c): (p, sem) for s, c, p, sem in
                con.execute("SELECT source_name, column_name, policy, semantic FROM ref.null_policy").fetchall()}
    return translit, programs, airports, policies
