#!/usr/bin/env python3
"""
combine_flights.py — memory-friendly combiner for flight exports.

Outputs:
    people.csv                 one row per unique person (person_id PK)
    flights.csv  or            all flight rows + person_id FK
    flights-YYYY-MM.csv        (if --format monthly)
    flights-summary.csv        (if --summary)

Example:
    python combine_flights.py --base data/unified --out data/combined \
        --format monthly --summary --chunksize 200000
"""
import argparse
import glob
import hashlib
import os
import re
import sys
import unicodedata
from collections import Counter, defaultdict

import pandas as pd


# ---------------------------------------------------------------------------
# Schemas & normalisation (identical semantics to the non-streaming version)
# ---------------------------------------------------------------------------
STD_COLS = [
    'first_name', 'middle_name', 'last_name', 'sex', 'birth_date', 'document',
    'pnr', 'ticket_number', 'seat', 'meal', 'baggage', 'departure_date',
    'departure_time', 'arrival_date', 'arrival_time', 'flight_number',
    'airline_code', 'flight_suffix', 'codeshare', 'status', 'from_code',
    'from_city', 'from_country', 'to_code', 'to_city', 'to_country',
    'fare_class', 'fare_basis', 'agent', 'operated_by', 'aircraft',
    'travel_time', 'ff_program', 'ff_status', 'ff_number', 'validity_start',
    'validity_end', 'days_of_week', 'uid',
]

IDENTITY_COLS = [
    'uid', 'document', 'ticket_number', 'first_name', 'middle_name',
    'last_name', 'sex', 'birth_date', 'ff_program', 'ff_number',
]

CYR2LAT = {
    'А': 'A', 'Б': 'B', 'В': 'V', 'Г': 'G', 'Д': 'D', 'Е': 'E', 'Ё': 'E',
    'Ж': 'ZH', 'З': 'Z', 'И': 'I', 'Й': 'I', 'К': 'K', 'Л': 'L', 'М': 'M',
    'Н': 'N', 'О': 'O', 'П': 'P', 'Р': 'R', 'С': 'S', 'Т': 'T', 'У': 'U',
    'Ф': 'F', 'Х': 'KH', 'Ц': 'TS', 'Ч': 'CH', 'Ш': 'SH', 'Щ': 'SHCH',
    'Ъ': '', 'Ы': 'Y', 'Ь': '', 'Э': 'E', 'Ю': 'IU', 'Я': 'IA',
}


def norm_name(s) -> str:
    if not s:
        return ''
    s = str(s).strip().upper()
    if not s:
        return ''
    s = ''.join(CYR2LAT.get(ch, ch) for ch in s)
    s = unicodedata.normalize('NFKD', s)
    return ''.join(ch for ch in s if ('A' <= ch <= 'Z') or ('0' <= ch <= '9'))


def norm_digits(s) -> str:
    return re.sub(r'\D', '', str(s)) if s else ''


def norm_sex(s) -> str:
    s = (s or '').strip().upper()
    if s in ('M', 'MALE'):
        return 'M'
    if s in ('F', 'FEMALE'):
        return 'F'
    return ''


def parse_date(s) -> str:
    if not s:
        return ''
    s = str(s).strip()
    if not s:
        return ''
    for fmt in ('%Y-%m-%d', '%m/%d/%Y', '%d.%m.%Y', '%m/%d/%y'):
        try:
            return pd.to_datetime(s, format=fmt).strftime('%Y-%m-%d')
        except (ValueError, TypeError):
            continue
    return ''


def token_id(s: str) -> int:
    """Deterministic 64-bit id. Collision probability is negligible."""
    return int.from_bytes(hashlib.md5(s.encode('utf-8')).digest()[:8], 'little')


# ---------------------------------------------------------------------------
# Compact union-find over integer token ids
# ---------------------------------------------------------------------------
class UnionFind:
    __slots__ = ('parent',)
    def __init__(self):
        self.parent = {}

    def add(self, x):
        if x not in self.parent:
            self.parent[x] = x

    def find(self, x):
        p = self.parent
        if x not in p:
            p[x] = x
            return x
        root = x
        while p[root] != root:
            root = p[root]
        while p[x] != root:
            p[x], x = root, p[x]
        return root

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


# ---------------------------------------------------------------------------
# Row → tokens (works on namedtuples produced by itertuples)
# ---------------------------------------------------------------------------
def extract_tokens(nt) -> list:
    toks = []
    uid = getattr(nt, 'uid', '') or ''
    if uid:
        toks.append(token_id('nick:' + uid))

    doc = norm_digits(getattr(nt, 'document', '') or '')
    if doc:
        toks.append(token_id('doc:' + doc))

    ticket = norm_digits(getattr(nt, 'ticket_number', '') or '')
    if ticket:
        toks.append(token_id('ticket:' + ticket))

    ln = norm_name(getattr(nt, 'last_name', '') or '')
    fn = norm_name(getattr(nt, 'first_name', '') or '')
    if ln or fn:
        bd = parse_date(getattr(nt, 'birth_date', '') or '')
        key = 'namebd:' if bd else 'name:'
        toks.append(token_id(f'{key}{ln}|{fn}|{bd}'))

    return toks


def update_agg(agg_entry, nt):
    first = norm_name(getattr(nt, 'first_name', '') or '')
    middle = norm_name(getattr(nt, 'middle_name', '') or '')
    last = norm_name(getattr(nt, 'last_name', '') or '')
    if first:
        agg_entry['first'][first] += 1
    if middle:
        agg_entry['middle'][middle] += 1
    if last:
        agg_entry['last'][last] += 1
    sx = norm_sex(getattr(nt, 'sex', '') or '')
    if sx:
        agg_entry['sex'][sx] += 1
    bd = parse_date(getattr(nt, 'birth_date', '') or '')
    if bd:
        agg_entry['birth'][bd] += 1
    doc = norm_digits(getattr(nt, 'document', '') or '')
    if doc:
        agg_entry['docs'].add(doc)
    tk = norm_digits(getattr(nt, 'ticket_number', '') or '')
    if tk:
        agg_entry['tickets'].add(tk)
    fp = getattr(nt, 'ff_program', '') or ''
    fn = getattr(nt, 'ff_number', '') or ''
    if fp or fn:
        agg_entry['ff'].add(f'{fp}:{fn}' if fp else fn)


# ---------------------------------------------------------------------------
# Chunked file iteration
# ---------------------------------------------------------------------------
def discover_files(base: str) -> list:
    pats = [os.path.join(base, '*.csv'), os.path.join(base, '*', '*.csv')]
    files = sorted({f for p in pats for f in glob.glob(p)})
    return [f for f in files if os.path.basename(f) != 'flyer-forum-users.csv']


def file_identity_cols(f: str) -> list:
    header = pd.read_csv(f, nrows=0, encoding='utf-8-sig').columns.tolist()
    return [c for c in IDENTITY_COLS if c in header]


def iter_identity_chunks(files, chunksize):
    for f in files:
        cols = file_identity_cols(f)
        if not cols:
            continue
        for chunk in pd.read_csv(
            f, usecols=cols, dtype=str, keep_default_na=False,
            chunksize=chunksize, encoding='utf-8-sig',
        ):
            yield f, chunk


def iter_full_chunks(files, chunksize):
    for f in files:
        for chunk in pd.read_csv(
            f, dtype=str, keep_default_na=False,
            chunksize=chunksize, encoding='utf-8-sig',
        ):
            for c in STD_COLS:
                if c not in chunk.columns:
                    chunk[c] = ''
            yield f, chunk[STD_COLS]


# ---------------------------------------------------------------------------
# Forum users (different schema)
# ---------------------------------------------------------------------------
def forum_tokens(fu: pd.DataFrame, uf: UnionFind) -> None:
    for nick, group in fu.groupby('nickname'):
        nick_tok = token_id('nick:' + nick)
        uf.add(nick_tok)
        first = next((r for r in group['first_name'] if r.strip()), '')
        last = next((r for r in group['last_name'] if r.strip()), '')
        ln, fn = norm_name(last), norm_name(first)
        if ln or fn:
            name_tok = token_id(f'name:{ln}|{fn}')
            uf.add(name_tok)
            uf.union(nick_tok, name_tok)


def forum_aggregate(fu: pd.DataFrame, uf: UnionFind, agg: dict) -> None:
    for nick, group in fu.groupby('nickname'):
        root = uf.find(token_id('nick:' + nick))
        a = agg[root]
        first = next((r for r in group['first_name'] if r.strip()), '')
        last = next((r for r in group['last_name'] if r.strip()), '')
        sex = next((r for r in group['sex'] if r.strip()), '')
        if first:
            a['first'][norm_name(first)] += 1
        if last:
            a['last'][norm_name(last)] += 1
        if sex:
            a['sex'][norm_sex(sex)] += 1
        a['nicks'].add(nick)
        for _, r in group.iterrows():
            fp, fn = r['ff_program'].strip(), r['ff_number'].strip()
            if fp or fn:
                a['ff'].add(f'{fp}:{fn}' if fp else fn)


def _empty_agg_entry():
    return {
        'first': Counter(), 'middle': Counter(), 'last': Counter(),
        'sex': Counter(), 'birth': Counter(),
        'docs': set(), 'tickets': set(), 'ff': set(), 'nicks': set(),
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--base', default='data/unified')
    ap.add_argument('--out', default='data/combined')
    ap.add_argument('--chunksize', type=int, default=200_000)
    ap.add_argument('--format', choices=['single', 'monthly'],
                    default='single',
                    help='single = one flights.csv; monthly = flights-YYYY-MM.csv')
    ap.add_argument('--summary', action='store_true',
                    help='write flights-summary.csv with per-month counts')
    ap.add_argument('--skip-aliases', action='store_true',
                    help='do not write person_aliases.csv')
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    files = discover_files(args.base)
    print(f'found {len(files)} flight files (+ forum users)', file=sys.stderr)

    fu_path = os.path.join(args.base, 'flyer-forum-users.csv')
    fu = pd.read_csv(fu_path, dtype=str, keep_default_na=False,
                     encoding='utf-8-sig') if os.path.exists(fu_path) \
         else pd.DataFrame(columns=['nickname', 'sex', 'first_name', 'last_name',
                                    'ff_program', 'ff_status', 'ff_number'])

    # -------- Pass 1: union-find -------------------------------------------
    uf = UnionFind()
    forum_tokens(fu, uf)
    n_rows = 0
    for f, chunk in iter_identity_chunks(files, args.chunksize):
        for nt in chunk.itertuples(index=False):
            toks = extract_tokens(nt)
            if not toks:
                continue
            base = toks[0]
            uf.add(base)
            for t in toks[1:]:
                uf.union(base, t)
            n_rows += 1
    print(f'pass 1: {n_rows:,} identity rows, '
          f'{len(uf.parent):,} unique tokens', file=sys.stderr)

    # -------- Assign person_id per component --------------------------------
    all_roots = sorted(set(uf.find(t) for t in uf.parent))
    root_to_pid = {r: i + 1 for i, r in enumerate(all_roots)}
    print(f'assigned {len(all_roots):,} person_ids', file=sys.stderr)

    # -------- Pass 2: aggregate per component ------------------------------
    agg = defaultdict(_empty_agg_entry)
    forum_aggregate(fu, uf, agg)
    for f, chunk in iter_identity_chunks(files, args.chunksize):
        for nt in chunk.itertuples(index=False):
            toks = extract_tokens(nt)
            if not toks:
                continue
            root = uf.find(toks[0])
            update_agg(agg[root], nt)

    # -------- Write people --------------------------------------------------
    people_rows = []
    for root in all_roots:
        a = agg[root]
        first = a['first'].most_common(1)[0][0] if a['first'] else ''
        middle = a['middle'].most_common(1)[0][0] if a['middle'] else ''
        last = a['last'].most_common(1)[0][0] if a['last'] else ''
        sex = a['sex'].most_common(1)[0][0] if a['sex'] else ''
        birth = a['birth'].most_common(1)[0][0] if a['birth'] else ''
        nicks = sorted(a['nicks'])
        if (not first and not last) and nicks:
            info = fu[fu['nickname'] == nicks[0]]
            if not info.empty:
                r0 = info.iloc[0]
                first = first or norm_name(r0['first_name'])
                last = last or norm_name(r0['last_name'])
                sex = sex or norm_sex(r0['sex'])
        people_rows.append({
            'person_id': root_to_pid[root],
            'first_name': first,
            'middle_name': middle,
            'last_name': last,
            'sex': sex,
            'birth_date': birth,
            'nickname': ';'.join(nicks),
            'n_flights': 0,                      # filled during pass 3
            'documents': ';'.join(sorted(a['docs'])),
            'tickets': ';'.join(sorted(a['tickets'])),
            'ff_accounts': ';'.join(sorted(a['ff'])),
        })

    people = pd.DataFrame(people_rows)
    people_path = os.path.join(args.out, 'people.csv')
    people.to_csv(people_path, index=False)
    print(f'wrote {people_path} ({len(people):,} people)', file=sys.stderr)

    # -------- Pass 3: write flights in chunks ------------------------------
    flight_counts = Counter()                        # person_id -> n
    monthly_stats = Counter()                        # 'YYYY-MM' -> [flights, people]
    monthly_people = defaultdict(set)
    monthly_airlines = defaultdict(set)

    if args.format == 'monthly':
        handles = {}
        headers_done = set()

        def get_handle(month):
            path = os.path.join(args.out, f'flights-{month}.csv')
            if path not in handles:
                handles[path] = open(path, 'w', encoding='utf-8', newline='')
            return path, handles[path]

        try:
            for _f, chunk in iter_full_chunks(files, args.chunksize):
                pids = []
                for nt in chunk.itertuples(index=False):
                    toks = extract_tokens(nt)
                    if toks:
                        pid = root_to_pid.get(uf.find(toks[0]))
                        pids.append(pid)
                        if pid:
                            flight_counts[pid] += 1
                    else:
                        pids.append(None)

                chunk = chunk.copy()
                chunk.insert(0, 'person_id',
                             pd.array(pids, dtype='Int64'))

                months = chunk['departure_date'].str.slice(0, 7)
                months = months.where(months.str.len() > 0, 'unknown')
                for month, sub in chunk.groupby(months, sort=False):
                    path, h = get_handle(month)
                    sub.to_csv(h, index=False,
                               header=(path not in headers_done))
                    headers_done.add(path)
                    if args.summary:
                        monthly_stats[month] += len(sub)
                        monthly_people[month].update(
                            int(x) for x in sub['person_id'].dropna().unique())
                        monthly_airlines[month].update(
                            sub.loc[sub['airline_code'] != '', 'airline_code'])
        finally:
            for h in handles.values():
                h.close()
    else:
        out_path = os.path.join(args.out, 'flights.csv')
        first = True
        with open(out_path, 'w', encoding='utf-8', newline='') as h:
            for _f, chunk in iter_full_chunks(files, args.chunksize):
                pids = []
                for nt in chunk.itertuples(index=False):
                    toks = extract_tokens(nt)
                    if toks:
                        pid = root_to_pid.get(uf.find(toks[0]))
                        pids.append(pid)
                        if pid:
                            flight_counts[pid] += 1
                    else:
                        pids.append(None)

                chunk = chunk.copy()
                chunk.insert(0, 'person_id',
                             pd.array(pids, dtype='Int64'))
                chunk.to_csv(h, index=False, header=first)
                first = False
        print(f'wrote {out_path}', file=sys.stderr)

    # -------- Back-fill n_flights on people --------------------------------
    people['n_flights'] = people['person_id'].map(flight_counts).fillna(0).astype(int)
    people.to_csv(people_path, index=False)
    print(f'updated n_flights on {people_path}', file=sys.stderr)

    # -------- Optional summary ---------------------------------------------
    if args.summary:
        rows = []
        for month in sorted(monthly_stats, key=lambda m: (m == 'unknown', m)):
            rows.append({
                'month': month,
                'flights': monthly_stats[month],
                'passengers': len(monthly_people[month]),
                'airlines': len(monthly_airlines[month]),
            })
        summary_path = os.path.join(args.out, 'flights-summary.csv')
        pd.DataFrame(rows).to_csv(summary_path, index=False)
        print(f'wrote {summary_path}', file=sys.stderr)

    # -------- Optional aliases (small, streamed) ---------------------------
    if not args.skip_aliases:
        alias_rows = []
        seen = set()
        for _f, chunk in iter_full_chunks(files, args.chunksize):
            for nt in chunk.itertuples(index=False):
                toks = extract_tokens(nt)
                if not toks:
                    continue
                pid = root_to_pid.get(uf.find(toks[0]))
                key = (
                    pid,
                    (getattr(nt, 'last_name', '') or '').strip(),
                    (getattr(nt, 'first_name', '') or '').strip(),
                    (getattr(nt, 'birth_date', '') or '').strip(),
                    (getattr(nt, 'document', '') or '').strip(),
                    (getattr(nt, 'ticket_number', '') or '').strip(),
                )
                if key in seen:
                    continue
                seen.add(key)
                alias_rows.append({
                    'person_id': pid,
                    'last_name': key[1], 'first_name': key[2],
                    'birth_date': key[3], 'document': key[4],
                    'ticket_number': key[5],
                })
        if alias_rows:
            pd.DataFrame(alias_rows).to_csv(
                os.path.join(args.out, 'person_aliases.csv'), index=False)

    print('done', file=sys.stderr)


if __name__ == '__main__':
    main()