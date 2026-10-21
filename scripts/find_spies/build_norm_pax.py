import re
import glob
import os
import pandas as pd

def norm_doc(s):
    if pd.isna(s): return None
    s = re.sub(r"\s+", "", str(s).strip().upper())
    return s or None

def norm_ticket_strict(s):
    if pd.isna(s): return None
    s = re.sub(r"\s+", "", str(s).strip().upper())
    return s if re.fullmatch(r"\d{13,16}", s) else None

def norm_name(s):
    if pd.isna(s): return None
    s = str(s).strip().upper()
    s = re.sub(r"['\-\.,]", " ", s)
    return re.sub(r"\s+", " ", s).strip() or None

def script_of(name):
    if not name: return None
    if re.search(r"[А-ЯЁ]", name): return "cyr"
    if re.search(r"[A-Z]", name): return "lat"
    return None

def norm_flight(s):
    if pd.isna(s): return None
    return re.sub(r"(NO|\*)$", "", str(s).strip().upper())

def load_sirena():
    s = pd.read_csv("converted/sirena-export.csv", dtype=str)
    return pd.DataFrame({
        "name":   s["PaxName"].map(norm_name),
        "doc":    s["TravelDoc"].map(norm_doc),
        "ticket": s["e-Ticket"].map(norm_ticket_strict),
        "ff_raw": s["PaxAdditionalInfo"].str.extract(r"FF#\s*([A-Z0-9]+)\s*(\d+)")
                    .agg(lambda r: f"{r[0]}{r[1]}" if pd.notna(r[0]) else None, axis=1),
        "birthdate": pd.to_datetime(s["PaxBirthDate"], errors="coerce").dt.strftime("%Y-%m-%d"),
        "flight": s["FlightCode"].map(norm_flight),
        "date":   pd.to_datetime(s["DepartDate"], errors="coerce").dt.strftime("%Y-%m-%d"),
        "source": "sirena",
        "dep_time": s["DepartTime"],
        "arr_time":  s["ArrivalTime"],
        "from_code": s["ShFrom"].str.strip().str.upper(),
        "to_code":   s["Dest"].str.strip().str.upper(),
    })

def load_boarding_data():
    b = pd.read_csv("converted/boarding-data.csv", dtype=str)
    full = (b["PassengerLastName"].fillna("") + " " +
            b["PassengerFirstName"].fillna("") + " " +
            b["PassengerSecondName"].fillna(""))
    return pd.DataFrame({
        "name":   full.map(norm_name),
        "doc":    b["PassengerDocument"].map(norm_doc),
        "ticket": b["TicketNumber"].map(norm_ticket_strict),
        "pnr":    b["BookingCode"].str.strip().str.upper()
                    .where(lambda x: x != "NOT PRESENTED"),
        "birthdate": pd.to_datetime(b["PassengerBirthDate"], errors="coerce",
                                    dayfirst=False).dt.strftime("%Y-%m-%d"),
        "flight": b["FlightNumber"].map(norm_flight),
        "date":   pd.to_datetime(b["FlightDate"], errors="coerce").dt.strftime("%Y-%m-%d"),
        "source": "boarding_data",
        "dep_time": b["FlightTime"],
        "to_city":   b["Destination"].str.strip().str.upper(),
    })

def load_boarding_pass():
    frames = [pd.read_csv(f, dtype=str) for f in glob.glob("converted/boarding-pass/*.csv")]
    bp = pd.concat(frames, ignore_index=True)
    return pd.DataFrame({
        "name":   bp["PASSENGER_NAME"].map(norm_name),
        "ticket": bp["E_TICKET"].map(norm_ticket_strict),
        "pnr":    bp["PNR"].str.strip().str.upper(),
        "flight": bp["FLIGHT_NUMBER"].map(norm_flight),
        "date":   pd.to_datetime(bp["DEPARTURE_DATE"], errors="coerce").dt.strftime("%Y-%m-%d"),
        "source": "boarding_pass",
        "dep_time": bp["DEPARTURE_TIME"],
        "from_city": bp["FROM"].str.strip().str.upper(),
        "to_city":   bp["TO"].str.strip().str.upper(),
    })

def main():
    cols = ["name", "doc", "ticket", "pnr", "ff_raw", "birthdate",
        "flight", "date", "dep_time", "arr_time",
        "from_code", "to_code", "from_city", "to_city", "source"]
    parts = [load_sirena(), load_boarding_data(), load_boarding_pass()]
    pax = pd.concat([d.reindex(columns=cols) for d in parts], ignore_index=True)

    pax["script"] = pax["name"].map(script_of)

    # fix 1: junk-документы (серия 0000 / не 10–12 цифр)
    pax["doc"] = pax["doc"].where(
        pax["doc"].str.fullmatch(r"\d{10,12}") & ~pax["doc"].str.startswith("0000", na=False)
    )

    os.makedirs("norm", exist_ok=True)
    pax.to_csv("norm/pax_all.csv", index=False)
    print(f"pax rows: {len(pax)}")
    print(pax.groupby("source").size())
    print("docs non-null:", pax["doc"].notna().sum())
    print("tickets non-null:", pax["ticket"].notna().sum())

if __name__ == "__main__":
    main()
