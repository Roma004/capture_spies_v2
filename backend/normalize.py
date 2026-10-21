"""Normalization rules (SPEC section 6) implemented as pure functions."""
from __future__ import annotations

import csv
import hashlib
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from .config import V1_COLUMNS

# ---------------------------------------------------------------------------
# reference maps
# ---------------------------------------------------------------------------

TRANSLIT_TABLE_CACHE: Dict[int, Dict[int, str]] = {}


DEFAULT_TRANSLIT = {
    "А": "A", "Б": "B", "В": "V", "Г": "G", "Д": "D", "Е": "E", "Ё": "E",
    "Ж": "ZH", "З": "Z", "И": "I", "Й": "I", "К": "K", "Л": "L", "М": "M",
    "Н": "N", "О": "O", "П": "P", "Р": "R", "С": "S", "Т": "T", "У": "U",
    "Ф": "F", "Х": "KH", "Ц": "TS", "Ч": "CH", "Ш": "SH", "Щ": "SHCH",
    "Ъ": "", "Ы": "Y", "Ь": "", "Э": "E", "Ю": "IU", "Я": "IA",
}

DEFAULT_PROGRAM_MAP = {
    "SU": "Aeroflot Bonus", "FB": "Flying Blue", "AF": "Flying Blue",
    "KL": "Flying Blue", "KE": "Korean Air SKYPASS", "DL": "Delta SkyMiles",
    "DT": "Delta SkyMiles", "AZ": "Alitalia MilleMiglia",
    "OK": "Czech Airlines OK Plus", "MU": "China Eastern",
    "CZ": "China Southern", "AM": "Aeromexico",
}

DEFAULT_AIRPORT_MAP = {"REA": "ICN", "NUX": "NOJ"}


@dataclass
class ReferenceMaps:
    translit: Dict[str, str] = field(default_factory=lambda: dict(DEFAULT_TRANSLIT))
    programs: Dict[str, str] = field(default_factory=lambda: dict(DEFAULT_PROGRAM_MAP))
    airports: Dict[str, str] = field(default_factory=lambda: dict(DEFAULT_AIRPORT_MAP))
    null_policy: Dict[tuple, tuple] = field(default_factory=dict)

    @classmethod
    def load(cls, ref_dir: Path) -> "ReferenceMaps":
        m = cls()
        if not ref_dir.exists():
            return m
        t = ref_dir / "translit_map.csv"
        if t.exists():
            m.translit = {}
            with t.open(newline="", encoding="utf-8") as fh:
                for r in csv.DictReader(fh):
                    if r.get("src_char"):
                        m.translit[r["src_char"].strip().upper()] = (r.get("dst") or "").strip()
        # The seed directory in the provided hand-off ships ``programm_map.csv``
        # (double m); accept either spelling without modifying reference data.
        p = ref_dir / "program_map.csv"
        if not p.exists():
            p = ref_dir / "programm_map.csv"
        if p.exists():
            m.programs = {}
            with p.open(newline="", encoding="utf-8") as fh:
                for r in csv.DictReader(fh):
                    if r.get("prefix"):
                        m.programs[r["prefix"].strip().upper()] = r["program_name"].strip()
        a = ref_dir / "airport_map.csv"
        if a.exists():
            m.airports = {}
            with a.open(newline="", encoding="utf-8") as fh:
                for r in csv.DictReader(fh):
                    if r.get("raw_code"):
                        m.airports[r["raw_code"].strip().upper()] = r["canonical_code"].strip()
        n = ref_dir / "null_policy.csv"
        if n.exists():
            m.null_policy = {}
            with n.open(newline="", encoding="utf-8") as fh:
                for r in csv.DictReader(fh):
                    m.null_policy[(r["source_name"].strip(), r["column_name"].strip())] = (
                        r["policy"].strip(), r["semantic"].strip())
        return m


# ---------------------------------------------------------------------------
# text normalization
# ---------------------------------------------------------------------------

def _translit_table(translit: Optional[Dict[str, str]]) -> Dict[int, str]:
    table = translit or DEFAULT_TRANSLIT
    key = id(table)
    cached = TRANSLIT_TABLE_CACHE.get(key)
    if cached is None or getattr(cached, "_src", None) is not table:
        cached = {ord(k.upper()): v for k, v in table.items()}
        try:
            cached._src = table  # type: ignore[attr-defined]
        except AttributeError:
            pass
        TRANSLIT_TABLE_CACHE[key] = cached
    return cached


def transliterate(value: Optional[str], translit: Optional[Dict[str, str]] = None) -> Optional[str]:
    """Cyrillic -> Latin per ICAO Doc 9303.  Latin characters pass through."""
    if value is None:
        return None
    s = str(value)
    if s.isascii():
        return s
    return s.upper().translate(_translit_table(translit))


_APOSTROPHES = {"'", "\u2019", "`", "\u2018"}
_WS_RE = re.compile(r"\s+")


_STRIP_TABLE = {ord(c): None for c in "\u2019\u2018`-'"}
_WORD_START_Y = re.compile(r"(?<![A-Z0-9])YI?")


def latin_normalize(value: Optional[str]) -> Optional[str]:
    """Uppercase + trim/collapse + apostrophe/hyphen removal + Y-at-word-start."""
    if value is None:
        return None
    s = str(value).upper()
    if "'" in s or "\u2019" in s or "\u2018" in s or "`" in s or "-" in s:
        s = s.translate(_STRIP_TABLE)
    if "  " in s:
        s = " ".join(s.split())
    s = s.strip()
    if not s:
        return s
    if "Y" in s:
        s = _WORD_START_Y.sub("I", s)
    return s


def normalize_name(value: Optional[str], translit: Optional[Dict[str, str]] = None) -> Optional[str]:
    if value is None:
        return None
    return latin_normalize(transliterate(value, translit))


# ---------------------------------------------------------------------------
# ff / airport / document / misc
# ---------------------------------------------------------------------------

def normalize_ff_number(raw: Optional[str]) -> Optional[str]:
    if raw is None:
        return None
    digits = re.sub(r"\D", "", str(raw))
    return digits or None


def normalize_ff_program(raw: Optional[str], programs: Optional[Dict[str, str]] = None) -> Optional[str]:
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    table = programs or DEFAULT_PROGRAM_MAP
    prefix = s[:2].upper()
    if prefix in table:
        return table[prefix]
    return s


def make_ff_key(program: Optional[str], number: Optional[str],
                programs: Optional[Dict[str, str]] = None) -> Optional[str]:
    p = normalize_ff_program(program, programs)
    n = normalize_ff_number(number)
    if not p or not n:
        return None
    return f"{p}|{n}"


AIRPORT_RE = re.compile(r"^[A-Z]{3}$")


def normalize_airport(code: Optional[str], airports: Optional[Dict[str, str]] = None):
    """Return (normalized_or_None, unknown_flag).

    unknown_flag is True when a non-empty raw value could not be normalized.
    """
    if code is None:
        return None, False
    raw = str(code).strip()
    if not raw:
        return None, False
    up = raw.upper()
    table = airports if airports is not None else DEFAULT_AIRPORT_MAP
    hit = table.get(up)
    if hit is not None:
        return hit, False
    if len(up) == 3 and up.isalpha() and up.isascii():
        return up, False
    return None, True


INTERNAL_DOC_RE = re.compile(r"^\d{4} \d{6}$")
FOREIGN_DOC_RE = re.compile(r"^\d{2} \d{7}$")


def document_type_and_norm(document: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """Return (document_type, document_norm) per 6.12."""
    if document is None:
        return None, None
    raw = str(document).strip()
    if not raw:
        return None, None
    dtype = None
    if INTERNAL_DOC_RE.match(raw):
        dtype = "internal"
    elif FOREIGN_DOC_RE.match(raw):
        dtype = "foreign"
    compact = raw.replace(" ", "")
    if dtype is None:
        return None, "unknown:" + compact
    return dtype, f"{dtype}:{compact}"


def normalize_sex(raw: Any) -> tuple[Optional[str], bool]:
    """Return (sex_norm, suspect).  Empty/None -> (None, False)."""
    if raw is None:
        return None, False
    s = str(raw).strip()
    if s == "":
        return None, False
    if s in ("M", "MALE", "male", "Male", "m"):
        return "M", False
    if s in ("F", "FEMALE", "female", "Female", "f"):
        return "F", False
    return None, True


_CODESHARE_FALSE = {"Own", "own", "OWN", "false", "False", "FALSE", "0", "no", "No", "N"}
_CODESHARE_TRUE = {"Operated", "operated", "OPERATED", "true", "True", "TRUE", "1", "yes", "Yes", "Y"}


def normalize_codeshare(raw: Any) -> tuple[Optional[bool], bool, bool]:
    """Return (value, unmapped_string, regression_string_form).

    ``unmapped_string`` is True for strings outside the mapping table.
    ``regression_string_form`` is True for the BUG-01 'Own'/'Operated' forms
    which are mapped but also reported as CODESHARE_STRING_FORM in tolerant mode.
    """
    if raw is None:
        return None, False, False
    if isinstance(raw, bool):
        return raw, False, False
    s = str(raw).strip()
    if s == "":
        return None, False, False
    if s.lower() in ("true", "false"):
        return s.lower() == "true", False, False
    if s in _CODESHARE_FALSE:
        return False, False, s in ("Own", "own", "OWN")
    if s in _CODESHARE_TRUE:
        return True, False, s in ("Operated", "operated", "OPERATED")
    return None, True, False


FLIGHT_NO_RE = re.compile(r"^[A-Z0-9]{2}[0-9]+[A-Z]*$")


def parse_flight_number(raw: Optional[str]) -> tuple[Optional[str], Optional[str], bool]:
    """Return (flight_number_norm, operator, invalid)."""
    if raw is None:
        return None, None, False
    s = str(raw).strip().upper()
    if not s:
        return None, None, False
    if not FLIGHT_NO_RE.match(s):
        return None, None, True
    return s, s[:2], False


def normalize_operated_by(raw: Optional[str]) -> Optional[str]:
    if raw is None:
        return None
    s = str(raw).strip().upper()
    if not s:
        return None
    if s == "OPERATED BY SOME OTHER AIRLINE":
        return None
    return s


# ---------------------------------------------------------------------------
# keys
# ---------------------------------------------------------------------------

def _sha32(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]


def name_dob_key(last: Optional[str], first: Optional[str], birth: Any) -> Optional[str]:
    if not last or not first or birth is None:
        return None
    if len(last) == 1 or len(first) == 1:
        return None
    if isinstance(birth, (date,)):
        b = birth.isoformat()
    else:
        b = str(birth)
    if not b:
        return None
    return _sha32(f"{last}|{first}|{b}")


def make_pnr_last_key(pnr: Optional[str], last: Optional[str]) -> Optional[str]:
    if not pnr or not last:
        return None
    return _sha32(f"{str(pnr).strip()}|{last}")


def _s(value) -> str:
    """Canonical string form used inside key material (NULL -> empty)."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def make_record_key(*, source_name: str, flight_number_norm, departure_date,
                    arrival_date, from_code_norm, to_code_norm, to_city,
                    codeshare, operated_by_norm, departure_time):
    """record_key per 6.5.  Returns (record_key, codeshare_no_operator)."""
    if source_name == "boarding-data":
        if not flight_number_norm or not arrival_date or not to_city:
            return None, False
        return _sha32(f"{flight_number_norm}|{arrival_date}|{to_city}"), False
    if not flight_number_norm or not departure_date:
        return None, False
    no_operator = False
    if codeshare is True:
        if operated_by_norm:
            return _sha32(
                f"{operated_by_norm}|{departure_date}|{_s(from_code_norm)}|"
                f"{_s(to_code_norm)}|{_s(departure_time)}"
            ), False
        no_operator = True
    return _sha32(
        f"{flight_number_norm}|{departure_date}|{_s(from_code_norm)}|{_s(to_code_norm)}"
    ), no_operator


# ---------------------------------------------------------------------------
# checksum helpers
# ---------------------------------------------------------------------------

def canonical_field(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def row_checksum_payload(row: dict, columns: Iterable[str]) -> str:
    return "|".join(canonical_field(row.get(c)) for c in columns)


def compute_checksum(rows: Iterable[dict], columns: Iterable[str]) -> str:
    cols = list(columns)
    h = hashlib.sha256()
    first = True
    for row in rows:
        if not first:
            h.update(b"\x1f")
        first = False
        h.update(row_checksum_payload(row, cols).encode("utf-8"))
    return "sha256:" + h.hexdigest()


# ---------------------------------------------------------------------------
# input coercion
# ---------------------------------------------------------------------------

def clean_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, str):
        return value if value.strip() != "" else None
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def parse_date(value: Any) -> Optional[date]:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    s = str(value).strip()
    if not s:
        return None
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        return None


def parse_time(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, str):
        s = value
        if len(s) == 5 and s[2] == ":" and s[:2].isdigit() and s[3:].isdigit():
            return s
        s = s.strip()
        if not s:
            return None
        if len(s) == 5 and s[2] == ":" and s[:2].isdigit() and s[3:].isdigit():
            return s
        m = re.match(r"^(\d{1,2}):(\d{2})", s)
        if m:
            return f"{int(m.group(1)):02d}:{m.group(2)}"
        return s
    if hasattr(value, "strftime"):
        return value.strftime("%H:%M")
    return parse_time(str(value))


def parse_bool(value: Any) -> Optional[bool]:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    s = str(value).strip().lower()
    if s in ("true", "1", "yes", "y"):
        return True
    if s in ("false", "0", "no", "n"):
        return False
    return None
