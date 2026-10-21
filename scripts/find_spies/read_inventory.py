import pandas as pd, json, collections

inv = pd.read_csv("inventory.csv")

# 1) сколько файлов и где
inv["dir"] = inv["path"].str.split("/").str[-2]
print(inv.groupby("dir").size())

# 2) какие колонки встречаются чаще всего
counter = collections.Counter()
for cols in inv["columns"].fillna(""):
    for c in cols.split("|"):
        counter[c.strip().lower()] += 1
for name, n in counter.most_common(30):
    print(n, name)

# 3) в каких файлах есть паспорт / билет / FF
for key in ["passport", "doc", "паспорт", "ticket", "билет", "ff", "frequent"]:
    mask = inv["columns"].fillna("").str.lower().str.contains(key)
    print(f"--- {key}: {mask.sum()} файлов")
    if mask.sum() < 30:
        print(inv.loc[mask, "path"].tolist())
    else:
        print(inv.loc[mask, "path"].head(10).tolist(), "...")
