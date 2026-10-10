# %%
import json
import pandas as pd

filename = "../../data/Airlines/FrequentFlyerForum-Profiles.json"
profiles_file = "../../data/unified/flyer-forum-users.csv"
flights_file = "../../data/unified/flyer-forum-flights.csv"

# %%

with open(filename, "r", encoding="utf-8") as f:
    data = json.load(f)

data = data["Forum Profiles"]

# %%

# >>> data[0].keys()
# dict_keys(['Registered Flights', 'NickName', 'Travel Documents', 'Sex', 'Loyality Programm', 'Real Name'])

# >>> data[0]["Registered Flights"][0].keys()
# dict_keys(['Date', 'Codeshare', 'Arrival', 'Flight', 'Departure']) 

# all passports is none

# >>> data[0]["Loyality Programm"]
# [{'Status': 'Basic', 'programm': 'KE', 'Number': ' 889215424'}, {'Status': 'Elite+', 'programm': 'DT', 'Number': ' 484697244'}] 

# >>> asd = data[0].copy()
# >>> del asd["Registered Flights"]
# >>> asd
# {'NickName': 'FrequentFlyer0',
#  'Travel Documents': [{'Passports': None}],
#  'Sex': 'Male',
#  'Loyality Programm': [{'Status': 'Basic', 'programm': 'KE', 'Number': ' 889215424'},
#                        {'Status': 'Elite+', 'programm': 'DT', 'Number': ' 484697244'}],
#  'Real Name': {'Last Name': None, 'First Name': None}}

profiles = []
flights = []
for i, profile in enumerate(data):
    profile_data = {
        "NickName": profile["NickName"],
        "Sex": profile["Sex"],
        "First Name": profile['Real Name']['First Name'],
        "Last Name": profile['Real Name']['Last Name'],
    }
    for program in profile["Loyality Programm"]:
        profile_data[f"Programm {program['programm']} Status"] = program["Status"]
        profile_data[f"Programm {program['programm']} Number"] = program["Number"]
    for flight in profile["Registered Flights"]:
        # Arrival: {'City': 'Pittsburgh PA', 'Airport': 'PIT', 'Country': 'United States of'}
        # Departure: {'City': 'Pittsburgh PA', 'Airport': 'PIT', 'Country': 'United States of'}
        flight_data = {
            "NickName": profile["NickName"],
            "Date": flight["Date"],
            "Codeshare": flight["Codeshare"],
            "Flight": flight["Flight"],
            "Arrival City": flight["Arrival"]["City"],
            "Arrival Airport": flight["Arrival"]["Airport"],
            "Arrival Country": flight["Arrival"]["Country"],
            "Departure City": flight["Departure"]["City"],
            "Departure Airport": flight["Departure"]["Airport"],
            "Departure Country": flight["Departure"]["Country"],
        }
        flights.append(flight_data)
    print(f"Profile {i}: {profile_data}")
    profiles.append(profile_data)

# %%
profiles_df = pd.DataFrame(profiles)
profiles_df.to_csv(profiles_file, index=False)

flights_df = pd.DataFrame(flights)
flights_df.to_csv(flights_file, index=False)
