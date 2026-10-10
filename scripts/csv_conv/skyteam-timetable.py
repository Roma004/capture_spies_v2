# %%
# Для начала я экспортнул исходный файл через okular в txt.
import csv
import re

input_path = "../../data/tmp/asd"
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


# %%

"""
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

have to search for patterns like in raw input rather than use readlines because somewhere \n is missing
"""

schedule = []

with open(input_path, 'r', encoding='utf-8', errors='replace') as f:
    content = f.read()
    # first search for FROM -- is is a start of a new block
    from_matches = list(re.finditer(r'FROM:\s+([A-Za-z ,]+)\s+([A-Z]{3})\s+TO:\s+([A-Za-z ,]+)\s+([A-Z]{3})', content))
    for i, match in enumerate(from_matches):
        from_city = match.group(1).strip()
        from_code = match.group(2).strip()
        to_city = match.group(3).strip()
        to_code = match.group(4).strip()
        # find the next FROM or end of content
        if i + 1 < len(from_matches):
            next_from_pos = from_matches[i + 1].start()
            block_content = content[match.end():next_from_pos]
        else:
            block_content = content[match.end():]

        # break down from_city and to_city into city and country splitting by comma
        if ',' in from_city:
            from_city, from_country = from_city.split(',', 1)
            from_city = from_city.strip()
            from_country = from_country.strip()
        else:
            from_country = None

        if ',' in to_city:
            to_city, to_country = to_city.split(',', 1)
            to_city = to_city.strip()
            to_country = to_country.strip()
        else:
            to_country = None
        
        # Now parse the block_content for schedule rows
        for row_match in ROW_RE.finditer(block_content):
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
                'from_code': from_code,
                'from_city': from_city,
                'from_country': from_country,
                'to_code': to_code,
                'to_city': to_city,
                'to_country': to_country,
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
# write the full schedule to CSV
with open(schedule_path, 'w', encoding='utf-8', newline='') as f:
    writer = csv.DictWriter(f, fieldnames=[
        'from_code', 'from_city', 'from_country', 'to_code', 'to_city', 'to_country',
        'validity_start', 'validity_end', 'days',
        'dep_time', 'arr_time', 'flight', 'aircraft', 'travel_time'
    ])
    writer.writeheader()
    writer.writerows(schedule)