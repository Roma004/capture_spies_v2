import pandas as pd

files = {
    "sirena":    "converted/sirena-export.csv",
    "boarding_d":"converted/boarding-data.csv",
    "bp_12":     "converted/boarding-pass/2017-12.csv",
    "exch_12":   "converted/skyteam-exchange/2017-12.csv",
}
# плюс то, что лежит в converted/, но не названо в выводе
import os
print("converted/:", os.listdir("converted"))

for name, path in files.items():
    print("="*70)
    print(name, path)
    try:
        df = pd.read_csv(path, nrows=5)
        print("shape preview:", df.shape)
        print("columns:", list(df.columns))
        print(df.head())
    except Exception as e:
        print("ERROR:", e)
