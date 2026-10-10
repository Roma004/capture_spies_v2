# %%
filename = "../../data/Airlines/Sirena-export-fixed.tab"
res_filename = "../../data/unified/sirena-export.csv"

# %%

# по заголовку сформировать массив ширин столбцов в интерактивном режиме

with open(filename, "r", encoding="utf-8") as f:
    l = f.readline()

columns = []

while l:
    print("|----|----"*10)
    print(l[:100])
    print()

    while True:
        try:
            c = int(input("Enter column length: "))
        except Exception:
            continue

        if input(f"Column name is {l[:c].strip()}. Accept? (y/n): ") in ('y', 'Y'):
            l = l[c:]
            columns.append(c)
            break

# %%

# патч для columns, можно не выполнять, я поправил

with open(filename, "r", encoding="utf-8") as f:
    l = f.readline()

c1 = [l.index(c) for c in columns]
c2 = [c1[0]]

for c in c1[1:]:
    c2.append(c - sum(c2))

c2[-1] = 1000
c2

columns = c2

# %%

columns = [60, 12, 12, 12, 12, 12, 10, 8, 6, 6, 18, 12, 6, 6, 12, 12, 60, 100]

def parse_line(line, col_lengths) -> list:
    l = 0
    res = []
    for cl in col_lengths:
        res.append(line[l:l+cl].strip())
        if ',' in res[-1]:
            res[-1] = f'"{res[-1]}"'
        l += cl
    return res

# %%

# преобразование из tab формата в csv

with open(filename, "r", encoding="utf-8") as f, open(res_filename, "w", encoding="utf-8") as res_f:
    for l in f.readlines():
        res_f.write(','.join(parse_line(l[:-1], columns)) + '\n')