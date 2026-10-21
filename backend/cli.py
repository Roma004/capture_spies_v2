"""Command line entry points."""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

from .config import LAKE_PATH, REPORTS_DIR, UNIFIED_DIR
from .loader import load_unified
from .pipeline import connect, finalize_lake, reset_lake


def cmd_init(args) -> int:
    if args.recreate:
        reset_lake(args.db)
    con = connect(args.db)
    print(f"initialized {args.db}")
    return 0


def cmd_load(args) -> int:
    con = connect(args.db)
    data_dir = args.data_dir_pos or args.data_dir
    results = load_unified(con, Path(data_dir), run_key=str(Path(args.db).resolve()))
    for r in results:
        print(json.dumps(r, default=str))
    return 0


def cmd_finalize(args) -> int:
    con = connect(args.db)
    print(json.dumps(finalize_lake(con, run_key=str(Path(args.db).resolve())),
                     default=str))
    return 0


def cmd_query(args) -> int:
    con = connect(args.db)
    cur = con.execute(args.sql)
    cols = [d[0] for d in cur.description]
    rows = cur.fetchall()
    import csv as _csv

    w = _csv.writer(sys.stdout)
    w.writerow(cols)
    for row in rows:
        w.writerow(row)
    return 0


def cmd_report(args) -> int:
    con = connect(args.db)
    lines = ["# Consistency audit", "",
             "Post-fix audit of `data/unified/`. The only expected findings",
             "are listed below; anything else is a regression.", ""]
    lines.append("| Finding | Count | Detail |")
    lines.append("| --- | --- | --- |")

    def scalar(sql, params=None):
        row = con.execute(sql, params or []).fetchone()
        return row[0] if row else None

    bd_n = scalar("SELECT count(*) FROM silver.records_norm WHERE source_name='boarding-data'")
    bd_pnr = scalar("SELECT count(*) FROM silver.records_norm WHERE source_name='boarding-data' "
                    "AND record_id IN (SELECT record_id FROM silver.record_aux WHERE pnr IS NULL)")
    bd_ticket = scalar("SELECT count(*) FROM silver.records_norm WHERE source_name='boarding-data' "
                       "AND record_id IN (SELECT record_id FROM silver.record_aux WHERE ticket_number IS NULL)")
    sr_n = scalar("SELECT count(*) FROM silver.records_norm WHERE source_name='sirena-export'")
    sr_ff = scalar("SELECT count(*) FROM silver.records_norm WHERE source_name='sirena-export' "
                   "AND ff_number_norm IS NULL")
    sr_suffix = scalar("SELECT count(*) FROM bronze.records b JOIN bronze.batches bt "
                       "ON bt.batch_id=b.batch_id WHERE bt.source_name='sirena-export' "
                       "AND json_extract_string(b.payload,'$.flight_suffix') IN ('NO','YES')")
    bp_n = scalar("SELECT count(*) FROM silver.records_norm WHERE source_name='boarding-pass'")
    bp_ph = scalar("SELECT count(*) FROM bronze.records b JOIN bronze.batches bt "
                   "ON bt.batch_id=b.batch_id WHERE bt.source_name='boarding-pass' "
                   "AND upper(trim(json_extract_string(b.payload,'$.operated_by')))="
                   "'OPERATED BY SOME OTHER AIRLINE'")

    def pct(x, n):
        return f"{100.0*x/n:.1f}%" if n else "n/a"

    lines.append(f"| boarding-data pnr null | {bd_pnr} | {pct(bd_pnr, bd_n)} of {bd_n} |")
    lines.append(f"| boarding-data ticket_number null | {bd_ticket} | {pct(bd_ticket, bd_n)} of {bd_n} |")
    lines.append(f"| sirena-export ff_number null | {sr_ff} | {pct(sr_ff, sr_n)} of {sr_n} |")
    lines.append(f"| sirena-export flight_suffix NO/YES | {sr_suffix} | informational only, not a suffix |")
    lines.append(f"| boarding-pass operated_by placeholder | {bp_ph} | {pct(bp_ph, bp_n)} of {bp_n} |")
    lines.append("")
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {out}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="backend")
    ap.add_argument("--db", default=str(LAKE_PATH))
    sub = ap.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init")
    p.add_argument("--recreate", action="store_true")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("load-unified")
    p.add_argument("data_dir_pos", nargs="?", default=None,
                   help="unified data directory (alternative to --data-dir)")
    p.add_argument("--data-dir", default=str(UNIFIED_DIR))
    p.set_defaults(func=cmd_load)

    p = sub.add_parser("finalize")
    p.set_defaults(func=cmd_finalize)

    p = sub.add_parser("query")
    p.add_argument("sql")
    p.set_defaults(func=cmd_query)

    p = sub.add_parser("report")
    p.add_argument("--out", default=str(REPORTS_DIR / "consistency.md"))
    p.set_defaults(func=cmd_report)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
