# %%
import re
import json
from pathlib import Path
from datetime import datetime
import pandas as pd

CANONICAL_COLUMNS = [
    "first_name", "middle_name", "last_name",
    "sex", "birth_date", "document", "pnr", "ticket_number",
    "seat", "meal", "baggage",
    "departure_date", "departure_time", "arrival_date", "arrival_time",
    "flight_number", "airline_code", "flight_suffix", "codeshare", "status",
    "from_code", "from_city", "from_country",
    "to_code", "to_city", "to_country",
    "fare_class", "fare_basis", "agent", "operated_by",
    "aircraft", "travel_time",
    "ff_program", "ff_status", "ff_number",
    "validity_start", "validity_end", "days_of_week",
    "uid",
]

USER_COLUMNS = [
    "nickname", "sex", "first_name", "last_name",
    "ff_program", "ff_status", "ff_number",
]

PROGRAM_MAP = {
    "SU": "Aeroflot Bonus",
    "FB": "Flying Blue",
    "KE": "Korean Air SKYPASS",
    "DT": "Delta SkyMiles",
    "DL": "Delta SkyMiles",
    "AF": "Flying Blue",
    "AZ": "Alitalia MilleMiglia",
    "OK": "Czech Airlines OK Plus",
}

base = Path("../data/converted")
out_dir = Path("../data/unified")
out_dir.mkdir(exist_ok=True)

def make_output(inp, out: Path, fn, col = CANONICAL_COLUMNS):
    out.parent.mkdir(exist_ok=True)
    flights = pd.DataFrame(fn(inp), columns=col)
    flights = flights.drop_duplicates()
    flights.to_csv(out, index=False)


def clean(x):
    if x is None:
        return ""
    s = str(x).strip()
    if s.upper() in {"N/A", "NA", "NAN", "NULL"}:
        return ""
    return s


def g(row, key):
    return clean(row.get(key, ""))


def norm_date(value, fmt=None):
    s = clean(value)
    if not s:
        return ""
    if fmt:
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            return s
    for f in ("%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%d.%m.%Y"):
        try:
            return datetime.strptime(s, f).date().isoformat()
        except ValueError:
            pass
    return s


def parse_bool(value):
    s = clean(value).lower()
    if s in {"true", "1", "yes", "y"}:
        return "true"
    if s in {"false", "0", "no", "n"}:
        return "false"
    return clean(value)


def parse_class_fare(value):
    s = clean(value)
    if not s:
        return "", ""
    parts = s.split()
    if len(parts) >= 2:
        return parts[0], parts[1]
    return s[:1], s


def parse_ff(value):
    s = clean(value)
    if not s:
        return "", ""
    m = re.search(r"FF#\s*([A-Z]{2})\s*(\d+)", s)
    if not m:
        m = re.match(r"^([A-Z]{2})\s*(\d+)$", s)
    if not m:
        return "", ""
    code, number = m.group(1), m.group(2)
    return PROGRAM_MAP.get(code, code), number


def split_last_first_middle(value):
    """Return first, middle, last. Assumes LAST FIRST MIDDLE."""
    parts = clean(value).split()
    if len(parts) >= 3:
        return parts[1], " ".join(parts[2:]), parts[0]
    if len(parts) == 2:
        return parts[1], "", parts[0]
    return "", "", ""


def split_boarding_pass_name(value):
    """Boarding-pass names are ambiguous for 2 tokens; split only 3+ tokens."""
    parts = clean(value).split()
    return parts[1], " ".join(parts[2:]), parts[0]

# %%

# 1) boarding-data.csv
def convert_boarding_data(path):
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    out = []
    for i, r in df.iterrows():
        row = {
            "first_name": g(r, "PassengerFirstName"),
            "middle_name": g(r, "PassengerSecondName"),
            "last_name": g(r, "PassengerLastName"),
            "sex": {"Male": "M", "Female": "F"}.get(g(r, "PassengerSex"), g(r, "PassengerSex")),
            "birth_date": norm_date(g(r, "PassengerBirthDate"), "%Y-%m-%d"),
            "document": g(r, "PassengerDocument"),
            "pnr": "" if g(r, "BookingCode").lower() == "not presented" else g(r, "BookingCode"),
            "ticket_number": "" if g(r, "TicketNumber").lower() == "not presented" else g(r, "TicketNumber"),
            "baggage": g(r, "Baggage"),
            "arrival_date": norm_date(g(r, "FlightDate")),
            "arrival_time": g(r, "FlightTime"),
            "flight_number": g(r, "FlightNumber"),
            "codeshare": g(r, "CodeShare"),
            "to_city": g(r, "Destination"),
        }
        row["passenger_name_raw"] = " ".join(
            x for x in [row["last_name"], row["first_name"], row["middle_name"]] if x
        )
        out.append(row)
    return out

inp = base / "boarding-data.csv"
make_output(inp, out_dir / inp.name, convert_boarding_data)


# %%
# 2) sirena-export.csv
def convert_sirena(path):
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    out = []
    for i, r in df.iterrows():
        first, middle, last = split_last_first_middle(g(r, "PaxName"))
        ff_prog, ff_num = parse_ff(g(r, "PaxAdditionalInfo"))
        fare_class, fare_basis = parse_class_fare(g(r, "TrvClsFare"))
        fc = g(r, "FlightCode")
        m = re.match(r"^([A-Z0-9]{2}\d+)([A-Z]*)$", fc)
        flight_no = m.group(1) if m else fc
        suffix = m.group(2) if m else ""
        row = {
            "first_name": first,
            "middle_name": middle,
            "last_name": last,
            "birth_date": norm_date(g(r, "PaxBirthDate")),
            "departure_date": norm_date(g(r, "DepartDate")),
            "departure_time": g(r, "DepartTime"),
            "arrival_date": norm_date(g(r, "ArrivalDate")),
            "arrival_time": g(r, "ArrivalTime"),
            "flight_number": flight_no,
            "flight_suffix": suffix,
            "from_code": g(r, "ShFrom"),
            "to_code": g(r, "Dest"),
            "pnr": g(r, "Code"),
            "ticket_number": g(r, "e-Ticket"),
            "document": g(r, "TravelDoc"),
            "seat": g(r, "Seat"),
            "meal": g(r, "Meal"),
            "fare_class": fare_class,
            "fare_basis": fare_basis,
            "baggage": g(r, "Baggage"),
            "ff_program": ff_prog,
            "ff_number": ff_num,
            "agent": g(r, "AgentInfo"),
        }
        out.append(row)
    return out

inp = base / "sirena-export.csv"
make_output(inp, out_dir / inp.name, convert_sirena)

# %%

# 3) pointz.csv
def convert_pointz(path):
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    out = []
    for i, r in df.iterrows():
        fare = g(r, "fare")
        row = {
            "uid": g(r, "uid"),
            "first_name": g(r, "first_name"),
            "last_name": g(r, "last_name"),
            "ff_number": g(r, "card_number"),
            "ff_program": g(r, "bonus_program"),
            "flight_number": g(r, "flight_code"),
            "departure_date": norm_date(g(r, "date")),
            "from_code": g(r, "departure"),
            "to_code": g(r, "arrival"),
            "fare_class": fare[:1] if fare else "",
            "fare_basis": fare,
        }
        out.append(row)
    return out

inp = base / "pointz.csv"
make_output(inp, out_dir / inp.name, convert_pointz)

# %%

# 4) flyer-forum-flights.csv
def convert_flyer_forum_flights(path):
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    out = []
    for i, r in df.iterrows():
        row = {
            "uid": g(r, "NickName"),
            "departure_date": norm_date(g(r, "Date")),
            "codeshare": parse_bool(g(r, "Codeshare")),
            "flight_number": g(r, "Flight"),
            "to_city": g(r, "Arrival City"),
            "to_code": g(r, "Arrival Airport"),
            "to_country": g(r, "Arrival Country"),
            "from_city": g(r, "Departure City"),
            "from_code": g(r, "Departure Airport"),
            "from_country": g(r, "Departure Country"),
        }
        out.append(row)
    return out

inp = base / "flyer-forum-flights.csv"
make_output(inp, out_dir / inp.name, convert_flyer_forum_flights)


# %%

# 5) flyer-forum-users.csv -> long FF-user format
def convert_flyer_forum_users(path):
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    out = []
    programs = [
        ("KE", "Korean Air SKYPASS"),
        ("DT", "Delta SkyMiles"),
        ("SU", "Aeroflot Bonus"),
        ("FB", "Flying Blue"),
    ]
    for i, r in df.iterrows():
        for code, prog_name in programs:
            status = g(r, f"Programm {code} Status")
            number = g(r, f"Programm {code} Number")
            if status or number:
                out.append({
                    "nickname": g(r, "NickName"),
                    "sex": g(r, "Sex"),
                    "first_name": g(r, "First Name"),
                    "last_name": g(r, "Last Name"),
                    "ff_program": prog_name,
                    "ff_status": status,
                    "ff_number": number,
                })
    return out

inp = base / "flyer-forum-users.csv"
make_output(inp, out_dir / inp.name, convert_flyer_forum_users, USER_COLUMNS)


# %%
# 6) boarding-pass/YYYY-MM.csv
def convert_boarding_pass(path):
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    out = []
    for i, r in df.iterrows():
        first, middle, last = split_boarding_pass_name(g(r, "PASSENGER_NAME"))
        row = {
            "passenger_name_raw": g(r, "PASSENGER_NAME"),
            "first_name": first,
            "middle_name": middle,
            "last_name": last,
            "flight_number": g(r, "FLIGHT_NUMBER"),
            "from_city": g(r, "FROM"),
            "from_code": g(r, "DEPARTURE_AIRPORT"),
            "to_city": g(r, "TO"),
            "to_code": g(r, "ARRIVAL_AIRPORT"),
            "gate": g(r, "GATE"),
            "departure_date": norm_date(g(r, "DEPARTURE_DATE")),
            "departure_time": g(r, "DEPARTURE_TIME"),
            "operated_by": g(r, "OPERATED_BY"),
            "seat": g(r, "SEAT"),
            "pnr": g(r, "PNR"),
            "ticket_number": g(r, "E_TICKET"),
        }
        out.append(row)
    return out

for inp in sorted((base / "boarding-pass").glob("*.csv")):
    out = out_dir / "boarding-pass" / inp.name
    make_output(inp, out, convert_boarding_pass)
    print(inp.name)

# %%

# 7) skyteam-exchange/YYYY-MM.csv
def convert_skyteam_exchange(path):
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    out = []
    for i, r in df.iterrows():
        ff_prog, ff_num = parse_ff(g(r, "FF"))
        row = {
            "departure_date": norm_date(g(r, "date")),
            "flight_number": g(r, "id1"),
            "from_code": g(r, "FROM"),
            "to_code": g(r, "TO"),
            "status": g(r, "STATUS"),
            "ff_program": ff_prog,
            "ff_number": ff_num,
            "fare_class": g(r, "FF_CLASS"),
            "fare_basis": g(r, "FF_FARE"),
        }
        out.append(row)
    return out

for inp in sorted((base / "skyteam-exchange").glob("*.csv")):
    out = out_dir / "skyteam-exchange" / inp.name
    make_output(inp, out, convert_skyteam_exchange)
    print(inp.name)

# %%

# 8) skyteam-timetable/<AIRPORT>.csv
def convert_skyteam_timetable(path):
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    out = []
    for i, r in df.iterrows():
        row = {
            "from_code": g(r, "from_code"),
            "from_city": g(r, "from_city"),
            "from_country": g(r, "from_country"),
            "to_code": g(r, "to_code"),
            "to_city": g(r, "to_city"),
            "to_country": g(r, "to_country"),
            "validity_start": g(r, "validity_start"),
            "validity_end": g(r, "validity_end"),
            "days_of_week": g(r, "days"),
            "departure_time": g(r, "dep_time"),
            "arrival_time": g(r, "arr_time"),
            "flight_number": g(r, "flight"),
            "aircraft": g(r, "aircraft"),
            "travel_time": g(r, "travel_time"),
        }
        out.append(row)
    return out

out = out_dir / "skyteam-timetable.csv"
dfs = []
for inp in sorted((base / "skyteam-timetable").glob("*.csv")):
    dfs.append(pd.DataFrame(convert_skyteam_timetable(inp), columns=CANONICAL_COLUMNS).drop_duplicates())
pd.concat(dfs, axis=0).to_csv(out, index=False)