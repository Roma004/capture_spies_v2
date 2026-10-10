# %%
import yaml
import io
import pandas as pd
from datetime import date, timedelta

filename = "../../data/Airlines/SkyTeam-Exchange.yaml"
res_dir = "../../data/unified/skyteam-exchange/"

cur_date = date(2017, 6, 1)
day = timedelta(days=1)

def data_conv(d: dict) -> dict:
    res = []
    for dt, data in d.items():
        line = {}
        line["date"] = dt
        for name, ndata in data.items():
            line["id1"] = name
            line["FROM"] = ndata["FROM"]
            line["TO"] = ndata["TO"]
            line["STATUS"] = ndata["STATUS"]
            for k, v in ndata["FF"].items():
                l1 = line.copy()
                l1["FF"] = k
                l1["FF_CLASS"] = v["CLASS"]
                l1["FF_FARE"] = v["FARE"]
                res.append(l1)
    return res

with open(filename, "r", encoding="utf-8") as f:
    # будем предполагать, что в 16 мегабайт входит минимум один ключ
    read_data = ""
    dfs = []
    while (chunk := f.read(2**24)):
        read_data += chunk
        while True:
            cur_date_str = f"'{str(cur_date)}'"
            next_date_str = f"'{str(cur_date + day)}'"
            if (start_pos := read_data.find(cur_date_str)) == -1:
                read_data = ""
                print("skip")
                break
            if (end_pos := read_data.find(next_date_str)) == -1:
                read_data = read_data[start_pos:]
                break
            # игнорируем перенос строки обязательно предшествующий
            # новому ключу с датой
            stream_data = read_data[start_pos:end_pos-1]
            read_data = read_data[end_pos:]
            print(start_pos, end_pos, stream_data[:len(cur_date_str)])
            with io.StringIO(stream_data) as stream:
                data = yaml.safe_load(stream)
            dfs.append(pd.DataFrame(data_conv(data)))
            last_date = cur_date
            cur_date += day
            if last_date.month != cur_date.month:
                df = pd.concat(dfs, ignore_index=True)
                df.to_csv(f"{res_dir}{last_date.year}-{last_date.month:02d}.csv", index=False)
                print(f"processed {last_date.year}-{last_date.month:02d}.csv")
                del dfs
                del df
                dfs = []