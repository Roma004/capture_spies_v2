"""Configuration and constants for the flight-data consolidation service."""
from __future__ import annotations

import os
from pathlib import Path

# Repository root (the directory containing SPEC.txt)
ROOT = Path(__file__).resolve().parent.parent

DATA_DIR = ROOT / "data"
UNIFIED_DIR = DATA_DIR / "unified"
REFERENCE_DIR = DATA_DIR / "reference"
LAKE_PATH = DATA_DIR / "lake.duckdb"
RAW_DIR = DATA_DIR / "raw"
REPORTS_DIR = ROOT / "reports"

# canonical 39-column v1 row order
V1_COLUMNS = [
    "first_name", "middle_name", "last_name", "sex", "birth_date",
    "document", "pnr", "ticket_number", "seat", "meal", "baggage",
    "departure_date", "departure_time", "arrival_date", "arrival_time",
    "flight_number", "airline_code", "flight_suffix", "codeshare", "status",
    "from_code", "from_city", "from_country", "to_code", "to_city",
    "to_country", "fare_class", "fare_basis", "agent", "operated_by",
    "aircraft", "travel_time", "ff_program", "ff_status", "ff_number",
    "validity_start", "validity_end", "days_of_week", "uid",
]

FORUM_USER_COLUMNS = [
    "nickname", "sex", "first_name", "last_name", "ff_program",
    "ff_status", "ff_number", "source_file", "source_row",
]

SOURCES = [
    "boarding-data", "boarding-pass", "sirena-export", "pointz",
    "skyteam-exchange", "skyteam-timetable", "flyer-forum-users",
    "flyer-forum-flights",
]

FACT_SOURCES = {
    "boarding-data", "boarding-pass", "sirena-export", "pointz",
    "skyteam-exchange", "flyer-forum-flights",
}

DEFAULT_TRUST = {
    "boarding-data":       ("A", "A", False),
    "boarding-pass":       ("B", "A", False),
    "sirena-export":       ("A", "A", False),
    "pointz":              ("B", "A", False),
    "skyteam-exchange":    ("B", "A", False),
    "skyteam-timetable":   ("C", "C", True),
    "flyer-forum-users":   ("C", "C", False),
    "flyer-forum-flights": ("C", "C", False),
}


def backend_mode() -> str:
    return os.environ.get("BACKEND_MODE", "tolerant").strip().lower()


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
