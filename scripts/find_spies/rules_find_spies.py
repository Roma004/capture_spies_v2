import re
import os
import pandas as pd

def to_min(t):
    if pd.isna(t): return None
    try:
        h, m = str(t).split(":")[:2]
        return int(h) * 60 + int(m)
    except: return None

def build_intervals(s):
    """s — DataFrame с date, dep_time, arr_time. Возвращает dep_abs/arr_abs."""
    s = s.copy()
    s["dep_m"] = s["dep_time"].map(to_min)
    s["arr_m"] = s["arr_time"].map(to_min)
    # overnight: прилёт после полуночи
    s.loc[s["arr_m"] < s["dep_m"], "arr_m"] += 24 * 60
    base = pd.to_datetime(s["date"])
    s["dep_abs"] = base + pd.to_timedelta(s["dep_m"], unit="m")
    s["arr_abs"] = base + pd.to_timedelta(s["arr_m"], unit="m")
    return s

def r_overlap(pax):
    """R_overlap: один паспорт, два рейса с пересекающимися интервалами (sirena)."""
    s = pax[(pax["source"] == "sirena")
            & pax["doc"].notna()
            & pax["date"].notna()
            & pax["dep_time"].notna()
            & pax["arr_time"].notna()].copy()
    s = build_intervals(s)

    # дедуп кодышей: одинаковые (date, dep_time, arr_time) = один физ. рейс
    s = s.sort_values("flight").drop_duplicates(
        subset=["doc", "date", "dep_time", "arr_time"], keep="first"
    )

    l = s[["doc", "name", "flight", "date", "from_code", "to_code",
           "dep_abs", "arr_abs"]].copy()
    r = l.copy()
    m = l.merge(r, on="doc", suffixes=("_a", "_b"))
    m = m[m["flight_a"] < m["flight_b"]]
    overlap = m[(m["dep_abs_a"] < m["arr_abs_b"]) &
                (m["dep_abs_b"] < m["arr_abs_a"])].copy()
    # отсечь пограничные кодыши с теми же интервалами
    overlap = overlap[
        ~((overlap["dep_abs_a"] == overlap["dep_abs_b"]) &
          (overlap["arr_abs_a"] == overlap["arr_abs_b"]))
    ]
    overlap["duration_min"] = (
        (overlap[["arr_abs_a", "arr_abs_b"]].min(axis=1)
         - overlap[["dep_abs_a", "dep_abs_b"]].max(axis=1))
        .dt.total_seconds() / 60
    ).round(0)
    return overlap

def r_same_day(pax):
    """R_same_day: 4+ разных рейса у одного паспорта в один день."""
    g = (pax.dropna(subset=["doc", "date"])
            .groupby(["doc", "date"])
            .agg(n_flights=("flight", "nunique"),
                 flights=("flight", lambda s: ",".join(sorted(set(s.dropna())))),
                 tos=("to_code", lambda s: ",".join(sorted(set(s.dropna())))),
                 sources=("source", lambda s: ",".join(sorted(set(s)))))
            .reset_index())
    return g[g["n_flights"] >= 4]

def r_tight_transit(pax):
    """R_tight_transit: прилёт и следующий вылет в один и тот же день с интервалом < 20 мин (sirena)."""
    s = pax[(pax["source"] == "sirena") & pax["doc"].notna()
            & pax["date"].notna()
            & pax["dep_time"].notna() & pax["arr_time"].notna()].copy()
    s = build_intervals(s)
    s = s.sort_values("flight").drop_duplicates(
        subset=["doc", "date", "dep_time", "arr_time"], keep="first")

    l = s[["doc", "name", "flight", "date", "from_code", "to_code",
           "dep_abs", "arr_abs"]].copy()
    r = l.copy()
    m = l.merge(r, on="doc", suffixes=("_a", "_b"))
    # B летит позже, чем A прилетела, но интервал < 20 мин
    gap = (m["dep_abs_b"] - m["arr_abs_a"]).dt.total_seconds() / 60
    m = m[(gap >= 0) & (gap < 20)]
    m["gap_min"] = gap.round(0)
    return m

def norm_name_loose(s):
    """Убирает одиночные инициалы (I, И., V и т.п.)."""
    if pd.isna(s): return None
    s = re.sub(r"(?<=\s)[А-ЯЁA-Z]\.?(?=\s|$)", "", str(s).upper())
    return re.sub(r"\s+", " ", s).strip() or None

def name_key(s):
    """Первые два токена: ФАМИЛИЯ ИМЯ. Отчества и хвосты отбрасываем."""
    if pd.isna(s): return None
    parts = str(s).split()
    if len(parts) >= 2: return " ".join(parts[:2])
    return parts[0] if parts else None

def main():
    pax = pd.read_csv("norm/pax_all.csv", dtype=str)
    pax["name_loose"] = pax["name"].map(norm_name_loose)
    pax["name_key"]   = pax["name_loose"].map(name_key)
    os.makedirs("norm", exist_ok=True)

    # ---------- R1: один документ — разные имена (внутри одного скрипта) ----------
    r1 = (pax.dropna(subset=["doc", "name_key", "birthdate", "script"])
             .groupby(["doc", "birthdate", "script"])["name_key"].nunique()
             .reset_index(name="n_names"))
    r1 = r1[r1["n_names"] > 1]
    print(f"R1 real passport conflicts (per script): {len(r1)}")
    r1.to_csv("norm/r1_passport_conflicts.csv", index=False)
    if len(r1):
        print(r1.head(20).to_string(index=False))

    # ---------- R2: один паспорт — разные ДР (САМОЕ СИЛЬНОЕ) ----------
    r2 = (pax.dropna(subset=["doc", "birthdate"])
             .groupby("doc")["birthdate"].nunique())
    r2 = r2[r2 > 1].reset_index(name="n_bd")
    print(f"\nR2 passports with multiple birthdates: {len(r2)}")
    r2.to_csv("norm/r2_multi_birthdate.csv", index=False)
    for doc in r2["doc"]:
        print(f"\n  === suspect doc {doc} ===")
        print(pax[pax["doc"] == doc]
              .sort_values(["date", "source"])
              [["name", "birthdate", "flight", "date", "source"]]
              .to_string(index=False))

    # ---------- R2b: cross-script + разная ДР ----------
    cross = (pax.dropna(subset=["doc", "birthdate"])
                .groupby("doc")
                .agg(n_scripts=("script", "nunique"),
                     n_bd=("birthdate", "nunique")))
    cross = cross.query("n_scripts > 1 and n_bd > 1")
    print(f"\nR2b cross-script + diff birthdate: {len(cross)}")
    cross.to_csv("norm/r2b_translit_mismatch.csv")

# ---------- R_docs: один человек — несколько паспортов ----------
    per_person = (pax.dropna(subset=["doc", "birthdate", "name_key", "script"])
                     .groupby(["name_key", "birthdate", "script"])["doc"]
                     .nunique().reset_index(name="n_docs"))
    per_person = per_person[per_person["n_docs"] > 1]
    print(f"\nR_docs: persons with >1 passports: {len(per_person)}")
    if len(per_person):
        print(per_person.head(20).to_string(index=False))
    # ---------- R3: один билет — разные имена (внутри одного скрипта) ----------
    r3 = (pax.dropna(subset=["ticket", "name_key", "birthdate", "script"])
             .groupby(["ticket", "birthdate", "script"])["name_key"].nunique()
             .reset_index(name="n_names"))
    r3 = r3[r3["n_names"] > 1]
    print(f"\nR3 real ticket conflicts (per script): {len(r3)}")
    r3.to_csv("norm/r3_ticket_conflicts.csv", index=False)

    # sample: что реально в R3
    if len(r3):
        print("\n--- R3 sample (5 cases) ---")
        for _, row in r3.head(5).iterrows():
            print(f"\nTicket: {row['ticket']} | bd: {row['birthdate']} | script: {row['script']}")
            print(pax[pax["ticket"] == row["ticket"]]
                  [["name", "birthdate", "flight", "date", "source"]]
                  .to_string(index=False))

    # ---------- Регрессия: NIKOLSKII должен быть 1 личностью ----------
    print("\n=== NIKOLSKII regression (должен быть 1 человек) ===")
    print(pax[pax["doc"] == "4396926588"]
          [["name", "name_key", "birthdate", "flight", "date", "source"]]
          .head(15).to_string(index=False))

    # ---------- Приоритетный кейс: Burov / Glebov ----------
    print("\n=== PRIORITY SUSPECT: doc 8248013778 (Burov/Glebov) ===")
    sus = pax[pax["doc"] == "8248013778"].sort_values(["date", "source"])
    print(sus[["name", "birthdate", "flight", "date", "source"]].to_string(index=False))
    print("\nИмён в разрезе скриптов:")
    print(sus.groupby(["script", "birthdate"])["name_key"].nunique())
    print("\n=== R_overlap (пересекающиеся рейсы) ===")
    ovl = r_overlap(pax)
    print(f"случаев: {len(ovl)}, уникальных паспортов: {ovl['doc'].nunique()}")
    ovl.to_csv("norm/r_overlap.csv", index=False)
    if len(ovl):
        for doc in ovl["doc"].unique()[:10]:
            print(f"\n--- doc {doc} ---")
            sub = ovl[ovl["doc"] == doc]
            cols_show = ["name_a", "flight_a", "date_a", "from_code_a", "to_code_a",
                         "dep_abs_a", "arr_abs_a",
                         "flight_b", "date_b", "from_code_b", "to_code_b",
                         "dep_abs_b", "arr_abs_b", "duration_min"]
            print(sub[cols_show].to_string(index=False))

    print("\n=== R_same_day (4+ рейса в день) ===")
    sd = r_same_day(pax)
    print(f"случаев: {len(sd)}")
    sd.to_csv("norm/r_same_day.csv", index=False)
    if len(sd):
        print(sd.head(20).to_string(index=False))

    print("\n=== R_tight_transit (<20 мин между прилётом и вылетом) ===")
    tt = r_tight_transit(pax)
    print(f"случаев: {len(tt)}")
    tt.to_csv("norm/r_tight_transit.csv", index=False)
    if len(tt):
        print(tt[["name_a", "flight_a", "to_code_a", "arr_abs_a",
                  "flight_b", "from_code_b", "dep_abs_b", "gap_min"]].head(20).to_string(index=False))

if __name__ == "__main__":
    main()
