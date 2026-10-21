#!/usr/bin/env python3
"""
inventory.py — инвентаризация датасета из CSV-файлов.

Использование:
    python inventory.py ./data
    python inventory.py ./data -o inventory

Выход:
    inventory.md   — человекочитаемый отчёт
    inventory.csv  — машиночитаемая сводка
"""

import argparse
import csv
import json
from pathlib import Path

import pandas as pd

ENCODINGS = ["utf-8-sig", "utf-8", "cp1251", "latin-1"]
DELIMS = ",;\t|"
PREVIEW_N = 5
DTYPE_SAMPLE_N = 1000


def detect_encoding(path: Path, n_bytes: int = 100_000) -> str:
    raw = path.open("rb").read(n_bytes)
    for enc in ENCODINGS:
        try:
            raw.decode(enc)
            return enc
        except UnicodeDecodeError:
            continue
    return "latin-1"


def detect_delimiter(path: Path, encoding: str) -> str:
    try:
        with path.open("r", encoding=encoding, newline="") as f:
            sample = f.read(50_000)
        if not sample.strip():
            return ","
        dialect = csv.Sniffer().sniff(sample, delimiters=DELIMS)
        return dialect.delimiter
    except Exception:
        return ","


def count_rows(path: Path) -> int:
    """Быстрый подсчёт строк по байтам. Может завышать число, если в полях есть \\n."""
    n = 0
    with path.open("rb") as f:
        for _ in f:
            n += 1
    return max(n - 1, 0)  # минус заголовок


def preview_rows(path: Path, encoding: str, delimiter: str, n: int = PREVIEW_N):
    rows = []
    try:
        with path.open("r", encoding=encoding, newline="") as f:
            reader = csv.reader(f, delimiter=delimiter)
            for i, row in enumerate(reader):
                if i > n:  # заголовок + n строк
                    break
                rows.append(row)
    except Exception as e:
        return [], f"preview error: {e}"
    return rows, ""


def infer_schema(path: Path, encoding: str, delimiter: str):
    try:
        df = pd.read_csv(
            path,
            encoding=encoding,
            sep=delimiter,
            nrows=DTYPE_SAMPLE_N,
            engine="python",
            on_bad_lines="skip",
        )
        dtypes = {c: str(t) for c, t in df.dtypes.items()}
        return list(df.columns), dtypes, ""
    except Exception as e:
        return [], {"_error": str(e)}, f"schema error: {e}"


def guess_notes(columns, preview):
    notes = []
    low = [str(c).lower() for c in columns]
    if any("status" in c for c in low):
        notes.append("has status")
    if any(x in c for c in low for x in ("passport", "doc", "паспорт", "документ")):
        notes.append("has passport/doc column")
    if any("ticket" in c or "билет" in c for c in low):
        notes.append("has ticket column")
    if any(c in ("ff", "ff_number", "frequent_flyer") for c in low):
        notes.append("has FF column")
    if any(str(c) != str(c).strip() for c in columns):
        notes.append("HEADERS HAVE WHITESPACE")
    # грубая проверка на пустые колонки
    if preview and len(preview) > 1:
        width = len(preview[0])
        for j in range(width):
            if all(j < len(r) and str(r[j]).strip() == "" for r in preview[1:]):
                notes.append(f"empty col '{preview[0][j] if j < len(preview[0]) else j}'")
    return "; ".join(notes)


def inventory_file(path: Path) -> dict:
    info = {
        "path": str(path),
        "size_bytes": path.stat().st_size,
        "encoding": "",
        "delimiter": "",
        "n_rows": None,
        "n_cols": None,
        "columns": [],
        "dtypes": {},
        "preview": [],
        "notes": "",
    }
    try:
        info["encoding"] = detect_encoding(path)
        info["delimiter"] = detect_delimiter(path, info["encoding"])
        info["n_rows"] = count_rows(path)
        cols, dtypes, serr = infer_schema(path, info["encoding"], info["delimiter"])
        info["columns"] = cols
        info["dtypes"] = dtypes
        info["n_cols"] = len(cols)
        preview, perr = preview_rows(path, info["encoding"], info["delimiter"])
        info["preview"] = preview
        extra = "; ".join(x for x in (guess_notes(cols, preview), serr, perr) if x)
        info["notes"] = extra
    except Exception as e:
        info["notes"] = f"ERROR: {e}"
    return info


def escape_cell(x) -> str:
    return str(x).replace("|", "\\|").replace("\n", " ")


def write_markdown(items, out_path: Path):
    lines = ["# Data inventory", "", f"Files: {len(items)}", ""]
    lines.append("## Summary")
    lines.append("")
    lines.append("| # | path | enc | delim | rows | cols | notes |")
    lines.append("|---|------|-----|-------|------|------|-------|")
    for i, it in enumerate(items, 1):
        delim = {"\t": "\\t", "\n": "\\n"}.get(it["delimiter"], it["delimiter"])
        lines.append(
            f"| {i} | `{escape_cell(it['path'])}` | {it['encoding']} | "
            f"`{escape_cell(delim)}` | {it['n_rows']} | {it['n_cols']} | "
            f"{escape_cell(it['notes'])} |"
        )
    lines.append("")
    lines.append("## Details")
    for it in items:
        lines.append("")
        lines.append(f"### `{it['path']}`")
        lines.append("")
        lines.append(f"- encoding: `{it['encoding']}`")
        lines.append(f"- delimiter: `{escape_cell(it['delimiter'])}`")
        lines.append(f"- rows (excl. header): {it['n_rows']}")
        lines.append(f"- columns ({it['n_cols']}): `{it['columns']}`")
        lines.append(f"- dtypes (sample {DTYPE_SAMPLE_N}):")
        for c, t in it["dtypes"].items():
            lines.append(f"  - `{c}`: {t}")
        if it["notes"]:
            lines.append(f"- notes: {it['notes']}")
        lines.append("")
        lines.append("Preview (first 5 rows incl. header):")
        lines.append("")
        lines.append("```")
        for row in it["preview"]:
            lines.append(",".join(escape_cell(c) for c in row))
        lines.append("```")
    out_path.write_text("\n".join(lines), encoding="utf-8")


def write_csv(items, out_path: Path):
    rows = []
    for it in items:
        rows.append({
            "path": it["path"],
            "size_bytes": it["size_bytes"],
            "encoding": it["encoding"],
            "delimiter": it["delimiter"],
            "n_rows": it["n_rows"],
            "n_cols": it["n_cols"],
            "columns": "|".join(map(str, it["columns"])),
            "dtypes_json": json.dumps(it["dtypes"], ensure_ascii=False),
            "notes": it["notes"],
        })
    pd.DataFrame(rows).to_csv(out_path, index=False, encoding="utf-8-sig")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root", type=Path, help="корень датасета")
    ap.add_argument("-o", "--out", type=Path, default=Path("inventory"),
                    help="префикс выходных файлов")
    args = ap.parse_args()

    files = sorted(p for p in args.root.rglob("*.csv") if p.is_file())
    if not files:
        print(f"Не найдено *.csv в {args.root}", flush=True)
        return

    items = []
    for i, f in enumerate(files, 1):
        print(f"[{i}/{len(files)}] {f}", flush=True)
        items.append(inventory_file(f))

    write_markdown(items, args.out.with_suffix(".md"))
    write_csv(items, args.out.with_suffix(".csv"))
    print(f"готово: {args.out.with_suffix('.md')}, {args.out.with_suffix('.csv')}")


if __name__ == "__main__":
    main()
