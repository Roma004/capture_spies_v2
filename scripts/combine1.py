# %%
# Imports, schemas, transliteration table
# ---------------------------------------------------------------------------
# Load every library and constant used anywhere below.
#
# STD_COLS      : the 39-column schema shared by every export except
#                 flyer-forum-users.csv. Used when writing the final flights.
# IDENTITY_COLS : the narrow subset we re-read during the identity passes.
#                 Reading 10 columns instead of 39 keeps each chunk small.
# CYR2LAT       : GOST-like Cyrillic → Latin map. Chosen so that a Russian
#                 passport name (НИКОЛЬСКИЙ) and the same passenger exported
#                 in Latin (NIKOLSKII) collapse to the same key.
# ---------------------------------------------------------------------------
import glob
import hashlib
import os
import re
import unicodedata
from collections import Counter, defaultdict

import pandas as pd

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

BASE          = '../data/unified'
OUT           = '../data/combined'
CHUNKSIZE     = 200_000
FLIGHT_FORMAT = 'single'        # or 'monthly'
WRITE_SUMMARY = True
SKIP_ALIASES  = False

os.makedirs(OUT, exist_ok=True)

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
    for fmt in ('%Y-%m-%d', '%m/%d/%Y', '%d.%m.%Y', '%m/%d/%y'):
        try:
            return pd.to_datetime(s, format=fmt).strftime('%Y-%m-%d')
        except (ValueError, TypeError):
            continue
    return ''

def token_id(s: str) -> int:
    return int.from_bytes(hashlib.md5(s.encode('utf-8')).digest()[:8], 'little')

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

# Per-row token extraction and aggregation
# ---------------------------------------------------------------------------
# extract_tokens(nt) : from one row (namedtuple), produce the identity keys
#     it participates in. Order matters: the first token becomes the
#     "primary" one other tokens are unioned to. We prefer the most
#     reliable key first (nickname, then document, then ticket, then name).
#
# update_agg(entry, nt) : fold a row's raw name/sex/birth/doc/ticket/FF
#     values into a per-component aggregation entry. Using Counters means
#     the most-frequent spelling wins when variants disagree.
#
# _empty_agg_entry() : factory for the per-component buckets.
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
    for col, key in (('first_name', 'first'), ('middle_name', 'middle'),
                     ('last_name', 'last')):
        v = norm_name(getattr(nt, col, '') or '')
        if v:
            agg_entry[key][v] += 1
    sx = norm_sex(getattr(nt, 'sex', '') or '')
    if sx:
        agg_entry['sex'][sx] += 1
    bd = parse_date(getattr(nt, 'birth_date', '') or '')
    if bd:
        agg_entry['birth'][bd] += 1
    d = norm_digits(getattr(nt, 'document', '') or '')
    if d:
        agg_entry['docs'].add(d)
    t = norm_digits(getattr(nt, 'ticket_number', '') or '')
    if t:
        agg_entry['tickets'].add(t)
    fp = getattr(nt, 'ff_program', '') or ''
    fn = getattr(nt, 'ff_number', '') or ''
    if fp or fn:
        agg_entry['ff'].add(f'{fp}:{fn}' if fp else fn)

def _empty_agg_entry():
    return {
        'first': Counter(), 'middle': Counter(), 'last': Counter(),
        'sex': Counter(), 'birth': Counter(),
        'docs': set(), 'tickets': set(), 'ff': set(), 'nicks': set(),
    }

# %%
# File discovery + chunk iterators
# ---------------------------------------------------------------------------
# Why two iterators?
#
#   iter_identity_chunks : reads only the 10 IDENTITY_COLS. This is what
#       passes 1 and 2 use — cheap enough to run twice if it saves RAM.
#   iter_full_chunks     : reads all STD_COLS. Only used by pass 3 (writing
#       flights) and the optional aliases pass.
#
# discover_files skips flyer-forum-users.csv because it has a different
# schema and is handled separately.
# ---------------------------------------------------------------------------
def discover_files(base):
    pats = [os.path.join(base, '*.csv'), os.path.join(base, '*', '*.csv')]
    files = sorted({f for p in pats for f in glob.glob(p)})
    return [f for f in files if os.path.basename(f) != 'flyer-forum-users.csv']

def file_identity_cols(f):
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

FILES = discover_files(BASE)
print(f'found {len(FILES)} flight files (+ forum users separately)')

# %%
# Load flyer-forum users
# ---------------------------------------------------------------------------
# flyer-forum-users.csv is the ONLY file with a non-standard schema. It
# carries a `nickname` that flyer-forum-flights.csv references via `uid`.
#
# We load it fully into RAM — it's small (tens of rows in the sample,
# maybe thousands in production) — and we extract two things from it:
#
#   1. the nickname → (first, last, sex) mapping used as a fallback when
#      a forum user has no name in any flight export;
#   2. the loyalty accounts attached to each nickname.
# ---------------------------------------------------------------------------
FU_PATH = os.path.join(BASE, 'flyer-forum-users.csv')
if os.path.exists(FU_PATH):
    forum_users = pd.read_csv(FU_PATH, dtype=str, keep_default_na=False,
                              encoding='utf-8-sig')
else:
    forum_users = pd.DataFrame(columns=[
        'nickname', 'sex', 'first_name', 'last_name',
        'ff_program', 'ff_status', 'ff_number',
    ])

NICK_INFO = {}                       # nickname -> {first, last, sex}
NICK_FF   = defaultdict(set)         # nickname -> {program:number, ...}

for nick, g in forum_users.groupby('nickname'):
    NICK_INFO[nick] = {
        'first': next((x for x in g['first_name'] if x.strip()), ''),
        'last':  next((x for x in g['last_name']  if x.strip()), ''),
        'sex':   next((x for x in g['sex']        if x.strip()), ''),
    }
    for _, r in g.iterrows():
        fp, fn_ = r['ff_program'].strip(), r['ff_number'].strip()
        if fp or fn_:
            NICK_FF[nick].add(f'{fp}:{fn_}' if fp else fn_)

print(f'{len(NICK_INFO)} forum nicknames loaded')
NICK_INFO

# %%
# ---------------------------------------------------------------------------
# forum_tokens(uf)        : called BEFORE pass 1. Registers nickname tokens
#                           and unions each nickname to its name token (if
#                           the forum user has a name). This lets a
#                           flyer-forum-flights row join a standard export
#                           row by name, not only by nickname.
#
# forum_aggregate(agg,uf) : called BEFORE pass 2's file scan. Folds the
#                           forum profile's names / sex / FF into the
#                           aggregation dict so that nickname-only people
#                           still get canonical fields.
# ---------------------------------------------------------------------------
def forum_tokens(uf):
    for nick in NICK_INFO:
        nick_tok = token_id('nick:' + nick)
        uf.add(nick_tok)
        info = NICK_INFO[nick]
        ln, fn = norm_name(info['last']), norm_name(info['first'])
        if ln or fn:
            name_tok = token_id(f'name:{ln}|{fn}')
            uf.add(name_tok)
            uf.union(nick_tok, name_tok)

def forum_aggregate(agg, uf):
    for nick in NICK_INFO:
        root = uf.find(token_id('nick:' + nick))
        a = agg[root]
        info = NICK_INFO[nick]
        if info['first']:
            a['first'][norm_name(info['first'])] += 1
        if info['last']:
            a['last'][norm_name(info['last'])] += 1
        if info['sex']:
            a['sex'][norm_sex(info['sex'])] += 1
        a['nicks'].add(nick)
        a['ff'] |= NICK_FF.get(nick, set())
# %%