import json
import os
import sys
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed

# TAG_ID = "jU3ZsuQekszxvCkHQiV7l9i9jSzq-01"  # windows11 tag (now chosen from a menu at startup)
CSV_FILE = "devices.csv"  # Change this to your CSV filename
MAX_WORKERS = 10  # Number of parallel API requests

url_base = 'https://api.xdr.trendmicro.com'
device_api_path = '/v3.0/asrm/attackSurfaceDevices'
tag_api_path = '/v3.0/tagManagement/customTags/assign'
tag_list_api_path = '/v3.0/tagManagement/customTags'

token = os.environ.get('TMV1_TOKEN')
if not token:
    sys.exit('TMV1_TOKEN environment variable is not set')

headers = {
    'Authorization': 'Bearer ' + token,
    'Content-Type': 'application/json;charset=utf-8'
}

# Step 0: Fetch available custom tags and let the user pick one
def fetch_tags():
    """Fetch all custom tags, following nextLink pagination"""
    tags = []
    url = url_base + tag_list_api_path
    params = {'top': 200}
    while url:
        r = requests.get(url, headers=headers, params=params, timeout=30)
        r.raise_for_status()
        data = r.json()
        tags.extend(data.get('items', []))
        url = data.get('nextLink')
        params = None  # nextLink already carries the query string
    return tags

def select_tag(tags):
    """Show a numbered menu of tags and return the chosen tag"""
    tags = sorted(tags, key=lambda t: (t.get('property', '').lower(), t.get('value', '').lower()))
    print('Available tags:')
    for i, tag in enumerate(tags, 1):
        print(f'  {i:>3}. {tag.get("property", "")}: {tag.get("value", "")}')

    while True:
        try:
            choice = input(f'\nSelect a tag [1-{len(tags)}] (q to quit): ').strip()
        except (EOFError, KeyboardInterrupt):
            sys.exit('\nCancelled')
        if choice.lower() == 'q':
            sys.exit('Cancelled')
        if choice.isdigit() and 1 <= int(choice) <= len(tags):
            return tags[int(choice) - 1]
        print('Invalid choice, try again.')

print('Fetching available tags...')
try:
    tags = fetch_tags()
except requests.exceptions.RequestException as e:
    sys.exit(f'Failed to fetch tags: {e}')
if not tags:
    sys.exit('No custom tags found')

selected_tag = select_tag(tags)
TAG_ID = selected_tag['id']
TAG_LABEL = f'{selected_tag.get("property", "")}: {selected_tag.get("value", "")}'
print(f'Selected tag: {TAG_LABEL} ({TAG_ID})\n')

# Step 1: Read device names from CSV file
print(f'Reading device names from {CSV_FILE}...')
names_to_tag = []
try:
    with open(CSV_FILE, 'r') as f:
        for line in f:
            name = line.strip()
            if name:  # Skip empty lines
                names_to_tag.append(name)
except FileNotFoundError:
    sys.exit(f'CSV file not found: {CSV_FILE}')

print(f'Found {len(names_to_tag)} device names to tag\n')

# Step 2: Search for each device using filter (parallel)
print(f'Searching for devices (using {MAX_WORKERS} parallel workers)...')
matched_devices = []
not_found = []
errors = []  # (device_name, error message) for lookups that failed, as opposed to not found

def search_device(device_name):
    """Search for a single device by name using filter.

    Returns the matched device, or None if the API returned no exact match.
    Raises on request/HTTP errors so they aren't mistaken for "not found".
    """
    # Use filter parameter with proper formatting
    filter_param = f"deviceName eq '{device_name}'"
    query_params = {'filter': filter_param}

    r = requests.get(url_base + device_api_path, params=query_params, headers=headers, timeout=30)
    r.raise_for_status()

    data = r.json()
    items = data.get('items', [])

    # Match the exact device name (case-insensitive)
    for device in items:
        if device.get('deviceName', '').lower() == device_name.lower():
            return {
                'assetId': device.get('id'),
                'deviceName': device.get('deviceName', device_name)
            }

    return None

def describe_error(e):
    """Short, readable description of a lookup failure"""
    if isinstance(e, requests.exceptions.HTTPError) and e.response is not None:
        return f'HTTP {e.response.status_code} {e.response.reason}'
    return f'{type(e).__name__}: {e}'

# Use ThreadPoolExecutor for parallel searches
with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
    futures = {executor.submit(search_device, name): name for name in names_to_tag}

    completed = 0
    for future in as_completed(futures):
        completed += 1
        device_name = futures[future]
        try:
            result = future.result()
            if result:
                matched_devices.append(result)
                print(f'[{completed}/{len(names_to_tag)}] ✓ Found: {device_name}')
            else:
                not_found.append(device_name)
                print(f'[{completed}/{len(names_to_tag)}] ✗ Not found: {device_name}')
        except Exception as e:
            errors.append((device_name, describe_error(e)))
            print(f'[{completed}/{len(names_to_tag)}] ! Error: {device_name} ({describe_error(e)})')

print(f'\n{"="*60}')
print(f'Results: Matched {len(matched_devices)}/{len(names_to_tag)} devices')
if errors:
    print(f'         {len(errors)} lookups failed with errors (not the same as not found)')
print(f'{"="*60}\n')

if matched_devices:
    print('Matched devices:')
    for device in matched_devices[:10]:
        print(f'  • {device["deviceName"]} ({device["assetId"]})')
    if len(matched_devices) > 10:
        print(f'  ... and {len(matched_devices) - 10} more')

# Step 3: Assign tags in batches (max 1000 per request)
if matched_devices:
    print(f'\nAssigning tag "{TAG_LABEL}" ({TAG_ID}) to {len(matched_devices)} devices...')
    print('-' * 60)

    batch_size = 1000
    total_assigned = 0

    for batch_num in range(0, len(matched_devices), batch_size):
        batch = matched_devices[batch_num:batch_num+batch_size]

        body = {
            'assignments': [
                {
                    'assetType': 'device',
                    'mappings': [
                        {
                            'tagId': TAG_ID,
                            'assetId': device['assetId']
                        }
                        for device in batch
                    ]
                }
            ]
        }

        try:
            r = requests.post(url_base + tag_api_path, headers=headers, json=body, timeout=30)

            if r.status_code == 202:  # Accepted (async)
                total_assigned += len(batch)
                print(f'Batch {batch_num//batch_size + 1}: ✓ Status {r.status_code} (Accepted)')
                if 'Operation-Location' in r.headers:
                    print(f'  → Poll: {r.headers["Operation-Location"]}\n')
            elif r.status_code >= 200 and r.status_code < 300:
                total_assigned += len(batch)
                print(f'Batch {batch_num//batch_size + 1}: ✓ Status {r.status_code} (Success)\n')
            else:
                print(f'Batch {batch_num//batch_size + 1}: ✗ Status {r.status_code}')
                if 'application/json' in r.headers.get('Content-Type', ''):
                    print(f'  Error: {json.dumps(r.json(), indent=2)}\n')

        except requests.exceptions.RequestException as e:
            print(f'Batch {batch_num//batch_size + 1}: ✗ Failed - {e}\n')

    print('='*60)
    print(f'✓ Assignment complete! Tagged {total_assigned} devices')
    print('='*60)

elif errors:
    print('✗ No devices matched. Lookups failed with errors, see below.')
else:
    print('✗ No devices matched. Check that device names are correct.')

if not_found:
    print(f'\n⚠️  {len(not_found)} devices not found in the system:')
    for name in not_found[:10]:
        print(f'  • {name}')
    if len(not_found) > 10:
        print(f'  ... and {len(not_found) - 10} more')

if errors:
    print(f'\n⚠️  {len(errors)} device lookups failed (these devices may exist, so re-run them):')
    for name, message in errors[:10]:
        print(f'  • {name}: {message}')
    if len(errors) > 10:
        print(f'  ... and {len(errors) - 10} more')
