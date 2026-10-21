# %%
# Для начала я экспортнул исходный файл через okular в txt.
import csv
import re
from collections import defaultdict

input_path = "../../data/tmp/skyteam-timetable.txt"
schedule_path = "../../data/unified/skyteam-timetable.csv"

MONTHS = {
    'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'may': 5, 'jun': 6,
    'jul': 7, 'aug': 8, 'sep': 9, 'oct': 10, 'nov': 11, 'dec': 12,
}


# Validity: "01 Nov - 31 Jan"
VALIDITY_RE = re.compile(
    r'(\d{2})\s+([A-Za-z]{3})\s*-\s*(\d{2})\s+([A-Za-z]{3})'
)

# Одна строка расписания (одна колонка).
ROW_RE = re.compile(
    r'(\d{2}\s+[A-Za-z]{3}\s*-\s*\d{2}\s+[A-Za-z]{3})\s+'  # 1 validity
    r'([\s1-7]+?)\s+'                                       # 2 days
    r'(\d{2}:\d{2})\s+'                                     # 3 dep
    r'(\d{2}:\d{2}(?:\+\d+)?)\s+'                           # 4 arr
    r'([A-Z0-9]+\*?)\s+'                                    # 5 flight
    r'([A-Z0-9]{2,3})\s+'                                   # 6 aircraft
    r'(\d+H\d+M)'                                           # 7 travel time
)

COUNTRY_CODE_RE_S = r'\s+([A-Za-z ,]+)\s+([A-Z]{3})\s+'
FROM_RE = re.compile('FROM:' + COUNTRY_CODE_RE_S)
TO_RE = re.compile('TO:' + COUNTRY_CODE_RE_S)


# %%

inp = """
Example input data:
FROM:      Aalborg, Denmark                                   AAL      FROM:      Amsterdam , Netherlands                            AMS
TO:        Amsterdam , Netherlands                            AMS      TO:        Aalborg, Denmark                                   AAL
Validity          Days    Dep     Arr     Flight   Aircraft   Travel   Validity          Days    Dep     Arr     Flight   Aircraft   Travel
                          Time    Time                         Time                              Time    Time                         Time
01 Nov - 31 Jan   1234567 06:00   07:25   KL1328   73W        1H25M    01 Nov - 31 Jan   1234567 10:05   11:30   KL1333   73W        1H25M
01 Nov - 31 Jan   1234567 12:10   13:35   KL1334   73W        1H25M    01 Nov - 23 Dec 1234567 16:25     17:45   KL1335   EQV        1H20M
01 Nov - 23 Dec 1234567 18:15     19:35   KL1336   EQV        1H20M    26 Dec - 06 Jan   1 34567 16:25   17:45   KL1335   EQV        1H20M
26 Dec - 06 Jan   1 34567 18:15   19:35   KL1336   EQV        1H20M    07 Jan - 31 Jan   1234567 16:25   17:45   KL1335   EQV        1H20M
07 Jan - 31 Jan   1234567 18:15   19:35   KL1336   EQV        1H20M    01 Nov - 31 Jan   1234567 21:00   22:20   KL1337   73W        1H20M
01 Nov - 31 Jan   1234567 06:00   07:25   KL1328   73W        1H25M    01 Nov - 31 Jan   1234567 10:05   11:30   KL1333   73W        1H25M
01 Nov - 31 Jan   1234567 12:10   13:35   KL1334   73W        1H25M    01 Nov - 23 Dec 1234567 16:25     17:45   KL1335   EQV        1H20M
01 Nov - 23 Dec 1234567 18:15     19:35   KL1336   EQV        1H20M    26 Dec - 06 Jan   1 34567 16:25   17:45   KL1335   EQV        1H20M
26 Dec - 06 Jan   1 34567 18:15   19:35   KL1336   EQV        1H20M    07 Jan - 31 Jan   1234567 16:25   17:45   KL1335   EQV        1H20M
07 Jan - 31 Jan   1234567 18:15   19:35   KL1336   EQV        1H20M    01 Nov - 31 Jan   1234567 21:00   22:20   KL1337   73W        1H20M
01 Nov - 31 Jan   1234567 06:00   07:25   KL1328   73W        1H25M    01 Nov - 31 Jan   1234567 10:05   11:30   KL1333   73W        1H25M
01 Nov - 31 Jan   1234567 12:10   13:35   KL1334   73W        1H25M    01 Nov - 23 Dec 1234567 16:25     17:45   KL1335   EQV        1H20M
01 Nov - 23 Dec 1234567 18:15     19:35   KL1336   EQV        1H20M    26 Dec - 06 Jan   1 34567 16:25   17:45   KL1335   EQV        1H20M
26 Dec - 06 Jan   1 34567 18:15   19:35   KL1336   EQV        1H20M    07 Jan - 31 Jan   1234567 16:25   17:45   KL1335   EQV        1H20M
07 Jan - 31 Jan   1234567 18:15   19:35   KL1336   EQV        1H20M    01 Nov - 31 Jan   1234567 21:00   22:20   KL1337   73W        1H20M
01 Nov - 31 Jan   1234567 06:00   07:25   KL1328   73W        1H25M    01 Nov - 31 Jan   1234567 10:05   11:30   KL1333   73W        1H25M
01 Nov - 31 Jan   1234567 12:10   13:35   KL1334   73W        1H25M    01 Nov - 23 Dec 1234567 16:25     17:45   KL1335   EQV        1H20M
01 Nov - 23 Dec 1234567 18:15     19:35   KL1336   EQV        1H20M    26 Dec - 06 Jan   1 34567 16:25   17:45   KL1335   EQV        1H20M
26 Dec - 06 Jan   1 34567 18:15   19:35   KL1336   EQV        1H20M    07 Jan - 31 Jan   1234567 16:25   17:45   KL1335   EQV        1H20M
07 Jan - 31 Jan   1234567 18:15   19:35   KL1336   EQV        1H20M    01 Nov - 31 Jan   1234567 21:00   22:20   KL1337   73W        1H20M
01 Nov - 31 Jan   1234567 06:00   07:25   KL1328   73W        1H25M    01 Nov - 31 Jan   1234567 10:05   11:30   KL1333   73W        1H25M
01 Nov - 31 Jan   1234567 12:10   13:35   KL1334   73W        1H25M    01 Nov - 23 Dec 1234567 16:25     17:45   KL1335   EQV        1H20M
01 Nov - 23 Dec 1234567 18:15     19:35   KL1336   EQV        1H20M    26 Dec - 06 Jan   1 34567 16:25   17:45   KL1335   EQV        1H20M
26 Dec - 06 Jan   1 34567 18:15   19:35   KL1336   EQV        1H20M    07 Jan - 31 Jan   1234567 16:25   17:45   KL1335   EQV        1H20M
07 Jan - 31 Jan   1234567 18:15   19:35   KL1336   EQV        1H20M    01 Nov - 31 Jan   1234567 21:00   22:20   KL1337   73W        1H20M
01 Nov - 31 Jan   1234567 06:00   07:25   KL1328   73W        1H25M    01 Nov - 31 Jan   1234567 10:05   11:30   KL1333   73W        1H25M
01 Nov - 31 Jan   1234567 12:10   13:35   KL1334   73W        1H25M    01 Nov - 23 Dec 1234567 16:25     17:45   KL1335   EQV        1H20M
01 Nov - 23 Dec 1234567 18:15     19:35   KL1336   EQV        1H20M    26 Dec - 06 Jan   1 34567 16:25   17:45   KL1335   EQV        1H20M
26 Dec - 06 Jan   1 34567 18:15   19:35   KL1336   EQV        1H20M    07 Jan - 31 Jan   1234567 16:25   17:45   KL1335   EQV        1H20M
07 Jan - 31 Jan   1234567 18:15   19:35   KL1336   EQV        1H20M    01 Nov - 31 Jan   1234567 21:00   22:20   KL1337   73W        1H20M
01 Nov - 31 Jan   1234567 06:00   07:25   KL1328   73W        1H25M    01 Nov - 31 Jan   1234567 10:05   11:30   KL1333   73W        1H25M
01 Nov - 31 Jan   1234567 12:10   13:35   KL1334   73W        1H25M    01 Nov - 23 Dec 1234567 16:25     17:45   KL1335   EQV        1H20M
01 Nov - 23 Dec 1234567 18:15     19:35   KL1336   EQV        1H20M    26 Dec - 06 Jan   1 34567 16:25   17:45   KL1335   EQV        1H20M
26 Dec - 06 Jan   1 34567 18:15   19:35   KL1336   EQV        1H20M    07 Jan - 31 Jan   1234567 16:25   17:45   KL1335   EQV        1H20M
07 Jan - 31 Jan   1234567 18:15   19:35   KL1336   EQV        1H20M    01 Nov - 31 Jan   1234567 21:00   22:20   KL1337   73W        1H20M
01 Nov - 31 Jan   1234567 06:00   07:25   KL1328   73W        1H25M    01 Nov - 31 Jan   1234567 10:05   11:30   KL1333   73W        1H25M
01 Nov - 31 Jan   1234567 12:10   13:35   KL1334   73W        1H25M    01 Nov - 23 Dec 1234567 16:25     17:45   KL1335   EQV        1H20M
01 Nov - 23 Dec 1234567 18:15     19:35   KL1336   EQV        1H20M    26 Dec - 06 Jan   1 34567 16:25   17:45   KL1335   EQV        1H20M
26 Dec - 06 Jan   1 34567 18:15   19:35   KL1336   EQV        1H20M    07 Jan - 31 Jan   1234567 16:25   17:45   KL1335   EQV        1H20M
07 Jan - 31 Jan   1234567 18:15   19:35   KL1336   EQV        1H20M    01 Nov - 31 Jan   1234567 21:00   22:20   KL1337   73W        1H20M
01 Nov - 31 Jan   1234567 06:00   07:25   KL1328   73W        1H25M    01 Nov - 31 Jan   1234567 10:05   11:30   KL1333   73W        1H25M
01 Nov - 31 Jan   1234567 12:10   13:35   KL1334   73W        1H25M    01 Nov - 23 Dec 1234567 16:25     17:45   KL1335   EQV        1H20M
01 Nov - 23 Dec 1234567 18:15     19:35   KL1336   EQV        1H20M    26 Dec - 06 Jan   1 34567 16:25   17:45   KL1335   EQV        1H20M
26 Dec - 06 Jan   1 34567 18:15   19:35   KL1336   EQV        1H20M    07 Jan - 31 Jan   1234567 16:25   17:45   KL1335   EQV        1H20M
07 Jan - 31 Jan   1234567 18:15   19:35   KL1336   EQV        1H20M    01 Nov - 31 Jan   1234567 21:00   22:20   KL1337   73W        1H20M
01 Nov - 31 Jan   1234567 06:00   07:25   KL1328   73W        1H25M    01 Nov - 31 Jan   1234567 10:05   11:30   KL1333   73W        1H25M
"""

"""
have to search for patterns like in raw input rather than use readlines because somewhere \n is missing
"""

# %%
def city_country_code(m):
    city, code = m.group(1).strip(), m.group(2).strip()
    if ',' in city:
        city, country = city.split(',', 1)
        city = city.strip()
        country = country.strip()
    else:
        country = None
    return {
        "city": city,
        "country": country,
        "code": code
    }

# %%
schedule = []

with open(input_path, 'r', encoding='utf-8', errors='replace') as f:
    content = f.read()

    # first search for FROM -- is is a start of a new block
    from_matches = list(re.finditer(FROM_RE, content))
    to_matches = list(re.finditer(TO_RE, content))

    for i in range(1, len(from_matches), 2):
        from_ = [None, None]
        to_ = [None, None]
        from_[0], to_[0] = from_matches[i-1], to_matches[i-1]
        from_[1], to_[1] = from_matches[i], to_matches[i]

        # find the next FROM or end of content
        if i + 1 < len(from_matches):
            next_from_pos = from_matches[i + 1].start()
            block_content = content[to_[1].end():next_from_pos]
        else:
            block_content = content[to_[1].end():]

        from_[0] = city_country_code(from_[0])
        from_[1] = city_country_code(from_[1])
        to_[0] = city_country_code(to_[0])
        to_[1] = city_country_code(to_[1])

        rows = list(ROW_RE.finditer(block_content))
        for i in range(2):
            for row_match in rows[i::2]:
                validity_str = row_match.group(1)
                days_str = row_match.group(2)
                dep_time = row_match.group(3)
                arr_time = row_match.group(4)
                flight = row_match.group(5)
                aircraft = row_match.group(6)
                travel_time = row_match.group(7)

                validity_match = VALIDITY_RE.match(validity_str)
                if validity_match:
                    start_day, start_month, end_day, end_month = validity_match.groups()
                    start_month_num = MONTHS[start_month.lower()]
                    end_month_num = MONTHS[end_month.lower()]
                    validity_start = f"{start_day}-{start_month_num}"
                    validity_end = f"{end_day}-{end_month_num}"
                else:
                    validity_start = None
                    validity_end = None

                schedule.append({
                    'from_code': from_[i]["code"],
                    'from_city': from_[i]["city"],
                    'from_country': from_[i]["country"],
                    'to_code': to_[i]["code"],
                    'to_city': to_[i]["city"],
                    'to_country': to_[i]["country"],
                    'validity_start': validity_start,
                    'validity_end': validity_end,
                    'days': days_str.strip(),
                    'dep_time': dep_time,
                    'arr_time': arr_time,
                    'flight': flight,
                    'aircraft': aircraft,
                    'travel_time': travel_time,
                })


# %%
# Resulting CSV has too many lines, so, break down into several documents by the country of departure (from_country)
from collections import defaultdict
country_schedules = defaultdict(list)
for row in schedule:
    country_schedules[row['from_country']].append(row) 

# then write each country schedule to a separate CSV file
for country, rows in country_schedules.items():
    country_schedule_path = f"../../data/unified/skyteam-timetable/{country}.csv"
    with open(country_schedule_path, 'w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=[
            'from_code', 'from_city', 'from_country', 'to_code', 'to_city', 'to_country',
            'validity_start', 'validity_end', 'days',
            'dep_time', 'arr_time', 'flight', 'aircraft', 'travel_time'
        ])
        writer.writeheader()
        writer.writerows(rows)

# %%
# another option is to break down by the airport code of departure (from_code)
code_schedules = defaultdict(list)
for row in schedule:
    code_schedules[row['from_code']].append(row)

# then write each code schedule to a separate CSV file
for code, rows in code_schedules.items():
    code_schedule_path = f"../../data/unified/skyteam-timetable/{code}.csv"
    with open(code_schedule_path, 'w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=[
            'from_code', 'from_city', 'from_country', 'to_code', 'to_city', 'to_country',
            'validity_start', 'validity_end', 'days',
            'dep_time', 'arr_time', 'flight', 'aircraft', 'travel_time'
        ])
        writer.writeheader()
        writer.writerows(rows)

# %%
# also may break down by the airport code of arrival (to_code)
code_schedules = defaultdict(list)
for row in schedule:
    code_schedules[row['to_code']].append(row)

# then write each code schedule to a separate CSV file
for code, rows in code_schedules.items():
    code_schedule_path = f"../../data/unified/skyteam-timetable/{code}.csv"
    with open(code_schedule_path, 'w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=[
            'from_code', 'from_city', 'from_country', 'to_code', 'to_city', 'to_country',
            'validity_start', 'validity_end', 'days',
            'dep_time', 'arr_time', 'flight', 'aircraft', 'travel_time'
        ])
        writer.writeheader()
        writer.writerows(rows)
    
# %%
# write the full schedule to CSV
with open(schedule_path, 'w', encoding='utf-8', newline='') as f:
    writer = csv.DictWriter(f, fieldnames=[
        'from_code', 'from_city', 'from_country', 'to_code', 'to_city', 'to_country',
        'validity_start', 'validity_end', 'days',
        'dep_time', 'arr_time', 'flight', 'aircraft', 'travel_time'
    ])
    writer.writeheader()
    writer.writerows(schedule)