#!/usr/bin/env python3
"""
Convert PointzAggregatorUsers XML file to CSV format.

Input XML structure:
- <PointzAggregatorUsers>
  - <user uid="...">
    - <name first="..." last="..."/>
    - <cards type="Airlines">
      - <card number="...">
        - <bonusprogramm>...</bonusprogramm>
        - <activities type="Airlines">
          - <activity type="Flight">
            - <Code>...</Code>
            - <Date>...</Date>
            - <Departure>...</Departure>
            - <Arrival>...</Arrival>
            - <Fare>...</Fare>
          </activity>
        </activities>
      </card>
    </cards>
  </user>
"""

import argparse
import csv
import sys
from xml.etree import ElementTree as ET


def parse_xml(xml_file: str) -> list[dict]:
    """Parse XML file and extract user flight data."""
    users_data = []
    
    # Parse the XML file using the standard approach
    try:
        tree = ET.parse(xml_file)
        root = tree.getroot()
    except ET.ParseError as e:
        print(f"Warning: XML parsing issue at line {e.lineno}: {e.msg}", file=sys.stderr)
        # Fallback to regex-based approach for malformed XML
        xml_string = ET.tostring(root, encoding='unicode')
        users_data = parse_xml_with_regex(xml_string)
    else:
        # Process the parsed tree
        data = process_xml_tree(root)
        users_data = data
    
    return users_data


def process_xml_tree(root):
    """Process the XML tree and extract user flight data."""
    users_data = []
    
    # Process each user
    for user_elem in root.findall('.//user'):
        uid = user_elem.get('uid')
        
        # Get name information
        name_elem = user_elem.find('name')
        first_name = name_elem.get('first') or ''
        last_name = name_elem.get('last') or ''
        
        # Get card information
        cards_elem = user_elem.find('cards')
        if cards_elem is not None:
            for card_elem in cards_elem.findall('card'):
                card_number = card_elem.get('number') or ''
                
                # Get bonus program
                bonus_elem = card_elem.find('bonusprogramm')
                bonus_program = bonus_elem.text or '' if bonus_elem is not None else ''
                
                # Get activities (flights)
                activities_elem = card_elem.find('activities')
                if activities_elem is not None:
                    for activity_elem in activities_elem.findall('activity'):
                        if activity_elem.get('type') == 'Flight':
                            flight_code = activity_elem.find('Code').text or ''
                            date = activity_elem.find('Date').text or ''
                            departure = activity_elem.find('Departure').text or ''
                            arrival = activity_elem.find('Arrival').text or ''
                            fare = activity_elem.find('Fare').text or ''
                            
                            # Create a record for each flight
                            users_data.append({
                                'uid': uid,
                                'first_name': first_name,
                                'last_name': last_name,
                                'card_number': card_number,
                                'bonus_program': bonus_program,
                                'flight_code': flight_code,
                                'date': date,
                                'departure': departure,
                                'arrival': arrival,
                                'fare': fare
                            })
    
    return users_data


def parse_xml_from_string(xml_string: str) -> list[dict]:
    """Parse XML string and extract user flight data using a more lenient approach."""
    # Use an alternative parser that's more tolerant of malformed XML  
    try:
        root = ET.fromstring(xml_string)
    except ET.ParseError as e:
        print(f"Warning: XML parsing issue: {e}", file=sys.stderr)
        # Fallback to regex-based approach for incomplete XML
        users_data = parse_xml_with_regex(xml_string)
    
    return users_data


def parse_xml_with_regex(xml_string: str) -> list[dict]:
    """Parse XML string using regex-based approach for malformed/incomplete XML."""
    import re
    
    # Initialize data list
    users_data = []
    
    # Build a mapping of uid to name
    user_id_map = {}
    for name_match in re.findall(r'<name[^>]*first="([^"]*)"[^>]*last="([^"]*)"', xml_string):
        # Find the associated user id by looking between <user...> and </user> tags
        uid_match = re.search(
            r'<user[^>]*uid="([^"]*)"[^>]*>.+?<name[^>]*first="[^\"]*"[^>]*last="[^\"]*">', 
            xml_string,
            re.DOTALL
        )
        if uid_match:
            user_id_map[uid_match.group(1)] = {
                'first_name': name_match[0].strip(),
                'last_name': name_match[1].strip()
            }
    
    # Parse all user elements using regex to extract uid, then get names
    for user_match in re.findall(r'<user[^>]*uid="([^"]*)"[^>]*>', xml_string):
        uid = user_match
        
        # Find name info for this specific user
        name_info = {'first_name': '', 'last_name': ''}
        name_pattern = r'<user[^>]*uid="' + re.escape(uid) + r'.*?>.+?<name[^>]*first="([^"]*)"[^>]*last="([^"]*)".*?</user>'
        name_match = re.search(name_pattern, xml_string, re.DOTALL)
        if name_match:
            name_info['first_name'] = name_match.group(1).strip()
            name_info['last_name'] = name_match.group(2).strip()
        
        # Get all cards within this user's section using regex
        try:
            card_pattern = r'(?:.+?)(<card[^>]*number="([^"]*)".*?</card>)'
            cards = re.findall(card_pattern, xml_string, re.DOTALL)
        except Exception:
            # Last resort - extract all card numbers from the XML
            all_cards = re.findall(r'<card[^>]*number="([^"]*)".*?</card>', xml_string, re.DOTALL)
            if all_cards:
                cards = [(user_match[0], all_cards[0][1])]
            else:
                continue
        
        for card_number, card_content in cards:
            # Extract bonus program from this card's content
            bonus_matches = re.findall(r'<bonusprogramm>([^<]+)</bonusprogramm>', card_content)
            bonus_program = bonus_matches[0].strip() if bonus_matches else ''
            
            # Extract activities (flights) from this card's content
            activity_pattern = r'<activity[^>]*type="Flight"[^>]*>(.*?)</activity>'
            activities = re.findall(activity_pattern, card_content, re.DOTALL)
            
            for activity_match in activities:
                # Extract flight code
                flight_code_matches = re.findall(r'<Code>([^<]+)</Code>', activity_match)
                flight_code = flight_code_matches[0].strip() if flight_code_matches else ''
                
                # Extract date
                date_matches = re.findall(r'<Date>([^<]+)</Date>', activity_match)
                date = date_matches[0].strip() if date_matches else ''
                
                # Extract departure
                departure_matches = re.findall(r'<Departure>([^<]+)</Departure>', activity_match)
                departure = departure_matches[0].strip() if departure_matches else ''
                
                # Extract arrival
                arrival_matches = re.findall(r'<Arrival>([^<]+)</Arrival>', activity_match)
                arrival = arrival_matches[0].strip() if arrival_matches else ''
                
                # Extract fare
                fare_matches = re.findall(r'<Fare>([^<]+)</Fare>', activity_match)
                fare = fare_matches[0].strip() if fare_matches else ''
                
                users_data.append({
                    'uid': uid,
                    'first_name': name_info['first_name'],
                    'last_name': name_info['last_name'],
                    'card_number': card_number,
                    'bonus_program': bonus_program,
                    'flight_code': flight_code,
                    'date': date,
                    'departure': departure,
                    'arrival': arrival,
                    'fare': fare
                })
    
    return users_data


def write_csv(data: list[dict], output_file: str) -> None:
    """Write data to CSV file."""
    # Define the field order
    fields = [
        'uid',
        'first_name', 
        'last_name',
        'card_number',
        'bonus_program',
        'flight_code',
        'date',
        'departure',
        'arrival',
        'fare'
    ]
    
    with open(output_file, 'w', newline='', encoding='utf-8') as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=fields)
        
        # Write header
        writer.writeheader()
        
        # Write data rows
        for record in data:
            writer.writerow(record)


def main():
    parser = argparse.ArgumentParser(
        description='Convert PointzAggregatorUsers XML file to CSV format'
    )
    parser.add_argument(
        'input_xml',
        help='Input XML file path'
    )
    parser.add_argument(
        'output_csv',
        help='Output CSV file path'
    )
    
    args = parser.parse_args()
    
    try:
        # Parse XML and extract data
        data = parse_xml(args.input_xml)
        
        # Write to CSV
        write_csv(data, args.output_csv)
        
        print(f"Successfully converted {len(data)} records to {args.output_csv}")
        
    except ET.ParseError as e:
        print(f"Error parsing XML: {e}", file=sys.stderr)
        sys.exit(1)
    except FileNotFoundError as e:
        print(f"Input file not found: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error during conversion: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
