import argparse
import csv
import json
import os
import sys
import time
from datetime import datetime

import requests

# TAG_ID = "jU3ZsuQekszxvCkHQiV7l9i9jSzq-01"  # windows11 tag (now chosen from a menu at startup)
CSV_FILE = "devices.csv"  # Default input file, override with --csv
DEVICE_PAGE_SIZE = 1000  # Devices per page when listing attackSurfaceDevices
TAG_BATCH_SIZE = 1000  # Max mappings per tag assign/unassign request
MAX_RETRIES = 6  # Retries for 429 / 5xx / network errors before giving up
OUTPUT_ROOT = "output"  # Each run writes its result files to output/<timestamp>/

url_base = 'https://api.xdr.trendmicro.com'
device_api_path = '/v3.0/asrm/attackSurfaceDevices'
tag_assign_api_path = '/v3.0/tagManagement/customTags/assign'
tag_unassign_api_path = '/v3.0/tagManagement/customTags/unassign'
tag_list_api_path = '/v3.0/tagManagement/customTags'

RETRY_STATUSES = {429, 500, 502, 503, 504}
HEADER_NAMES = {'devicename', 'device name', 'name', 'hostname', 'device', 'ip'}
IP_FIELDS = ('ip', 'ipAddress', 'ipAddresses')  # device fields that may hold IP addresses

parser = argparse.ArgumentParser(description='Assign (or remove) a Vision One custom tag for devices listed in a CSV file.')
parser.add_argument('--csv', default=CSV_FILE, help=f'file of device names, one per line (default: {CSV_FILE})')
parser.add_argument('--remove', action='store_true', help='remove the selected tag from the listed devices instead of assigning it')
parser.add_argument('--dry-run', action='store_true', help='match devices and write result files, but do not change any tags')
parser.add_argument('--limit', type=int, help='only use the first N names from the CSV (for testing on a sample)')
args = parser.parse_args()

# Everything that differs between assigning and removing
tag_api_path = tag_unassign_api_path if args.remove else tag_assign_api_path
ACTION = 'remove' if args.remove else 'assign'
DONE_FILE = 'untagged.csv' if args.remove else 'tagged.csv'
RERUN = 'python3 batch-tagging.py' + (' --remove' if args.remove else '')

token = os.environ.get('TMV1_TOKEN')
if not token:
    sys.exit('TMV1_TOKEN environment variable is not set')

headers = {
    'Authorization': 'Bearer ' + token,
    'Content-Type': 'application/json;charset=utf-8'
}

session = requests.Session()
session.headers.update(headers)


def request_with_retry(method, url, **kwargs):
    """Send a request, retrying on rate limits, server errors and network errors.

    Waits for Retry-After when the API sends it, otherwise backs off exponentially.
    Returns the final response (which may still be an error status) or raises the
    last network error once retries run out.
    """
    kwargs.setdefault('timeout', 60)
    for attempt in range(MAX_RETRIES + 1):
        try:
            r = session.request(method, url, **kwargs)
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
            if attempt == MAX_RETRIES:
                raise
            wait = min(2 ** attempt * 2, 60)
            print(f'  … {type(e).__name__}, retrying in {wait}s ({attempt + 1}/{MAX_RETRIES})')
            time.sleep(wait)
            continue

        if r.status_code not in RETRY_STATUSES or attempt == MAX_RETRIES:
            return r

        retry_after = r.headers.get('Retry-After', '')
        wait = int(retry_after) if retry_after.isdigit() else min(2 ** attempt * 2, 60)
        print(f'  … HTTP {r.status_code}, retrying in {wait}s ({attempt + 1}/{MAX_RETRIES})')
        time.sleep(wait)


def describe_error(e):
    """Short, readable description of a request failure"""
    if isinstance(e, requests.exceptions.HTTPError) and e.response is not None:
        return f'HTTP {e.response.status_code} {e.response.reason}'
    return f'{type(e).__name__}: {e}'


def write_csv(path, header, rows):
    with open(path, 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(header)
        writer.writerows(rows)


# Step 0: Fetch available custom tags and let the user pick one
def fetch_tags():
    """Fetch all custom tags, following nextLink pagination"""
    tags = []
    url = url_base + tag_list_api_path
    params = {'top': 200}
    while url:
        r = request_with_retry('GET', url, params=params)
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
    sys.exit(f'Failed to fetch tags: {describe_error(e)}')
if not tags:
    sys.exit('No custom tags found')

selected_tag = select_tag(tags)
TAG_ID = selected_tag['id']
TAG_LABEL = f'{selected_tag.get("property", "")}: {selected_tag.get("value", "")}'
print(f'Selected tag: {TAG_LABEL} ({TAG_ID})')
if args.remove:
    print('MODE: REMOVE. The tag will be removed from the listed devices.')
print()

# Step 1: Read device names from CSV file (first column; header row and duplicates skipped)
print(f'Reading device names from {args.csv}...')
names_to_tag = {}  # lowercase name -> name as written in the CSV
try:
    with open(args.csv, 'r', newline='') as f:
        for i, row in enumerate(csv.reader(f)):
            name = row[0].strip() if row else ''
            if not name:
                continue
            if i == 0 and name.lower() in HEADER_NAMES:
                continue
            names_to_tag.setdefault(name.lower(), name)
except FileNotFoundError:
    sys.exit(f'CSV file not found: {args.csv}')

if args.limit:
    names_to_tag = dict(list(names_to_tag.items())[:args.limit])
if not names_to_tag:
    sys.exit('No device names found in the CSV file')

print(f'Found {len(names_to_tag)} unique device names')
if args.dry_run:
    print(f'DRY RUN: devices will be matched but the tag will NOT be {"removed" if args.remove else "assigned"}')
elif args.remove:
    try:
        answer = input(f'Remove "{TAG_LABEL}" from up to {len(names_to_tag)} devices? Type yes to continue: ')
    except (EOFError, KeyboardInterrupt):
        sys.exit('\nCancelled')
    if answer.strip().lower() != 'yes':
        sys.exit('Cancelled')

run_dir = os.path.join(OUTPUT_ROOT, datetime.now().strftime('%Y%m%d-%H%M%S'))
os.makedirs(run_dir, exist_ok=True)
print(f'Writing results to {run_dir}/\n')

# Results, filled in as the run goes
matched = []  # (csv name, deviceName, assetId)
matched_names = set()  # lowercase names/IPs that matched at least one device
duplicates = 0  # matched devices whose name/IP had already matched another device
pending = []  # matched devices waiting for the next tag batch
tagged = []  # (csv name, deviceName, assetId, batch number, status)
tag_errors = []  # (csv name, deviceName, assetId, error)
operations = []  # (batch number, Operation-Location)
batch_count = 0
listing_complete = False
listing_error = None
interrupted = False


def flush_batch():
    """Assign (or remove) the tag for everything in `pending` in one request"""
    global batch_count
    if not pending:
        return
    batch = pending[:]
    pending.clear()
    batch_count += 1

    if args.dry_run:
        tagged.extend((n, d, a, batch_count, 'dry-run') for n, d, a in batch)
        print(f'  Batch {batch_count}: {len(batch)} devices (dry run, not sent)')
        return

    body = {
        'assignments': [
            {
                'assetType': 'device',
                'mappings': [{'tagId': TAG_ID, 'assetId': a} for _, _, a in batch]
            }
        ]
    }
    try:
        r = request_with_retry('POST', url_base + tag_api_path, json=body)
    except requests.exceptions.RequestException as e:
        tag_errors.extend((n, d, a, describe_error(e)) for n, d, a in batch)
        print(f'  Batch {batch_count}: ✗ Failed - {describe_error(e)}')
        return

    if 200 <= r.status_code < 300:
        tagged.extend((n, d, a, batch_count, r.status_code) for n, d, a in batch)
        print(f'  Batch {batch_count}: ✓ {len(batch)} devices, status {r.status_code}')
        if 'Operation-Location' in r.headers:
            operations.append((batch_count, r.headers['Operation-Location']))
    else:
        message = f'HTTP {r.status_code}'
        if 'application/json' in r.headers.get('Content-Type', ''):
            message += ' ' + json.dumps(r.json())
        tag_errors.extend((n, d, a, message) for n, d, a in batch)
        print(f'  Batch {batch_count}: ✗ {message}')


def device_keys(device):
    """Lowercase name and IP addresses a CSV entry can match for this device.

    Devices discovered only by IP may have no deviceName (or a null one), so the
    IP fields are matched too. Field names follow ASD.py.
    """
    keys = []
    name = device.get('deviceName')
    if name:
        keys.append(name.strip().lower())
    for field in IP_FIELDS:
        value = device.get(field)
        for ip in (value if isinstance(value, list) else [value]):
            if isinstance(ip, str) and ip.strip():
                keys.append(ip.strip().lower())
    return keys


# Step 2: List all devices once and match locally, tagging each full batch as it fills
print(f'Scanning devices and {"removing" if args.remove else "assigning"} the tag as matches are found...')
start = time.time()
scanned = 0
url = url_base + device_api_path
params = {'top': DEVICE_PAGE_SIZE}
try:
    while url:
        r = request_with_retry('GET', url, params=params)
        r.raise_for_status()
        data = r.json()
        url = data.get('nextLink')
        params = None  # nextLink already carries the query string

        for device in data.get('items', []):
            scanned += 1
            hits = [k for k in device_keys(device) if k in names_to_tag]
            if hits:
                # One entry per device, even if both its name and IP are in the CSV
                entry = (names_to_tag[hits[0]], device.get('deviceName') or '', device.get('id'))
                if all(k in matched_names for k in hits):
                    duplicates += 1
                matched.append(entry)
                matched_names.update(hits)
                pending.append(entry)
                if len(pending) >= TAG_BATCH_SIZE:
                    flush_batch()

        print(f'  Scanned {scanned} devices, matched {len(matched_names)}/{len(names_to_tag)} names '
              f'({time.time() - start:.0f}s)')
    listing_complete = True
except requests.exceptions.RequestException as e:
    listing_error = describe_error(e)
    print(f'\n✗ Device listing stopped: {listing_error}')
except KeyboardInterrupt:
    interrupted = True
    print('\n✗ Interrupted, sending matches found so far...')
finally:
    # Tag whatever matched before the listing ended, unless the user pressed Ctrl+C again
    try:
        flush_batch()
    except KeyboardInterrupt:
        interrupted = True
        tag_errors.extend((n, d, a, 'interrupted, not submitted') for n, d, a in pending)
        pending.clear()

# Step 3: Write result files
unmatched = [names_to_tag[k] for k in names_to_tag if k not in matched_names]
write_csv(os.path.join(run_dir, 'matched.csv'), ['csvName', 'deviceName', 'assetId'], matched)
write_csv(os.path.join(run_dir, DONE_FILE), ['csvName', 'deviceName', 'assetId', 'batch', 'status'], tagged)
if tag_errors:
    write_csv(os.path.join(run_dir, 'tag_errors.csv'), ['csvName', 'deviceName', 'assetId', 'error'], tag_errors)
if operations:
    write_csv(os.path.join(run_dir, 'operations.csv'), ['batch', 'operationLocation'], operations)
# Names the scan never reached are "unresolved", not "not found": they may still exist
unmatched_file = 'not_found.csv' if listing_complete else 'unresolved.csv'
if unmatched:
    with open(os.path.join(run_dir, unmatched_file), 'w', newline='') as f:
        csv.writer(f).writerows([n] for n in unmatched)
# Names whose tag request failed, in the same format as the input so they can be re-run
if tag_errors:
    with open(os.path.join(run_dir, 'retry.csv'), 'w', newline='') as f:
        csv.writer(f).writerows([n] for n in dict.fromkeys(n for n, _, _, _ in tag_errors))

# Summary
print(f'\n{"="*60}')
print(f'Tag:        {TAG_LABEL} ({TAG_ID}){" [REMOVE]" if args.remove else ""}')
print(f'Scanned:    {scanned} devices{"" if listing_complete else " (scan did not finish)"}')
print(f'Matched:    {len(matched_names)}/{len(names_to_tag)} names, {len(matched)} devices'
      + (f' ({duplicates} extra devices share a name)' if duplicates else ''))
done_label = {('assign', False): 'Tagged:', ('assign', True): 'Would tag:',
              ('remove', False): 'Untagged:', ('remove', True): 'Would untag:'}[(ACTION, args.dry_run)]
print(f'{done_label:<11} {len(tagged)} devices in {batch_count} batches → {DONE_FILE}')
if tag_errors:
    print(f'Failed:     {len(tag_errors)} devices → retry.csv, tag_errors.csv')
if unmatched:
    label = 'Not found' if listing_complete else 'Unresolved'
    print(f'{label + ":":<11} {len(unmatched)} names → {unmatched_file}')
print(f'Results:    {run_dir}/')
print(f'{"="*60}')

if not listing_complete:
    print('\nThe device scan did not finish, so some names were never checked.')
    print(f'Re-run them with:  {RERUN} --csv {os.path.join(run_dir, unmatched_file)}')
if tag_errors:
    print(f'\nRe-run failed tag batches with:  {RERUN} --csv {os.path.join(run_dir, "retry.csv")}')

if interrupted or listing_error or tag_errors:
    sys.exit(1)
