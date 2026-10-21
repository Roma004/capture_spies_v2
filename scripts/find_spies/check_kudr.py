import pandas as pd

pax = pd.read_csv("norm/pax_all.csv", dtype=str)

name_l = pax["name"].fillna("").str.upper()
sur_mask   = name_l.str.contains(r"КУДРЯВЦЕВА|KUDRIAVTSEVA", regex=True)
first_mask = name_l.str.contains(r"\bАМАЛИЯ\b|\bAMALIIA\b|\bAMALIA\b", regex=True)
mask = sur_mask & first_mask

sus = pax[mask].sort_values(["birthdate", "date", "dep_time", "source"])
print(f"=== Строк по Амалии Кудрявцевой: {len(sus)} ===")
print(sus[["name", "doc", "ticket", "pnr", "birthdate",
           "flight", "date", "dep_time", "source"]].to_string(index=False))

# --- паспорта ---
print("\n=== Уникальные паспорта ===")
print(sus.dropna(subset=["doc"]).groupby("doc").agg(
    n_rows=("name", "size"),
    names=("name", lambda s: sorted(set(s))),
    bd=("birthdate", lambda s: sorted(set(s.dropna()))),
    flights=("flight", lambda s: sorted(set(s.dropna()))),
    dates_min=("date", lambda s: s.dropna().min()),
    dates_max=("date", lambda s: s.dropna().max()),
    sources=("source", lambda s: sorted(set(s))),
).to_string())

# --- по каждому паспорту: полный трек ---
print("\n=== Разбивка по паспортам ===")
for doc, grp in sus.dropna(subset=["doc"]).groupby("doc"):
    print(f"\n--- doc {doc} ---")
    print(grp[["name", "birthdate", "flight", "date", "dep_time", "source"]]
          .to_string(index=False))

# --- пересечение периодов использования паспортов ---
docs = sus.dropna(subset=["doc"])["doc"].unique()
if len(docs) >= 2:
    print("\n=== Проверка на ротацию документа ===")
    periods = (sus.dropna(subset=["doc", "date"])
                  .groupby("doc")["date"].agg(["min", "max"])
                  .sort_values("min"))
    print(periods.to_string())
    a, b = periods.index[0], periods.index[1]
    overlap = periods.loc[a, "max"] >= periods.loc[b, "min"]
    print(f"периоды {'ПЕРЕСЕКАЮТСЯ' if overlap else 'НЕ пересекаются'} "
          f"→ {'подозрительно' if overlap else 'похоже на ротацию документа'}")

# --- есть ли общий ticket/pnr между паспортами ---
print("\n=== Общие ticket/pnr между паспортами ===")
for col in ("ticket", "pnr"):
    tmp = sus.dropna(subset=[col, "doc"])[["doc", col]].drop_duplicates()
    shared = tmp.groupby(col)["doc"].nunique()
    shared = shared[shared > 1]
    print(f"{col}: {len(shared)} значений с двумя паспортами")
    if len(shared):
        print(shared.head(10).to_string())

# --- санити-чек ---
print("\n=== Санити-чек: всего Кудрявцевых в данных ===")
all_kudr = pax[sur_mask]
nk = all_kudr["name"].str.split().str[:2].str.join(" ")
print(f"строк: {len(all_kudr)}, уникальных (ФИ, ДР): "
      f"{all_kudr.assign(nk=nk).groupby(['nk','birthdate']).size().shape[0]}")
