import json
import os
import sys
import requests

TAG_ID="your_tag_id_here"
ASSET_ID="your_asset_id_here"

url_base = 'https://api.xdr.trendmicro.com'
url_path = '/v3.0/tagManagement/customTags'

token = os.environ.get('TMV1_TOKEN')
if not token:
    sys.exit('TMV1_TOKEN environment variable is not set')

headers = {
    'Authorization': 'Bearer ' + token,
    'Content-Type': 'application/json'
}

query_params = {
    'top': 100
}



try:
    r = requests.get(url_base + url_path, headers=headers, params=query_params, timeout=30)
    r.raise_for_status()
except requests.exceptions.RequestException as e:
    sys.exit(f'Request failed: {e}')    


#print(f'Successfully fetched tags. Response: {r.json()}')

items = r.json().get('items', [])
for item in items:
    tag_id = item.get('id')
    tag_property = item.get('property')
    tag_name = item.get('value')
    print(f'Tag ID: {tag_id}, Tag Name: {tag_name}, Description: {tag_property}')

