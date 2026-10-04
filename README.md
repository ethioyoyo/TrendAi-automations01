# TrendAi-automations01

Scripts for automating Trend Vision One tasks through the v3.0 API.

## Setup

Every script reads your API key from the `TMV1_TOKEN` environment variable and uses the `requests` library.

```bash
pip install requests
```

```bash
export TMV1_TOKEN='your-vision-one-api-key'
```

The API key needs permission to read Attack Surface devices and to read and assign custom tags.

## batch-tagging.py

Assigns one custom tag to many devices at once, using a list of device names.

### How it works

1. **Pick a tag.** The script fetches every custom tag (`GET /v3.0/tagManagement/customTags`, following `nextLink` pagination) and shows a numbered menu:

   ```
   Available tags:
       1. Department: Finance
       2. OS: windows11
   ...
   Select a tag [1-2] (q to quit):
   ```

   Enter the number of the tag you want, or `q` to quit. The script looks up the ID of the tag you chose, so you don't need to know tag IDs.

2. **Read device names** from `devices.csv` (set by `CSV_FILE`).

3. **Look up each device** in Attack Surface devices (`GET /v3.0/asrm/attackSurfaceDevices`), running `MAX_WORKERS` lookups in parallel (10 by default). Names are matched exactly, ignoring case.

4. **Assign the tag** to every matched device (`POST /v3.0/tagManagement/customTags/assign`), sending up to 1000 devices per request.

### Input file

`devices.csv` holds one device name per line. There is no header row, and blank lines are skipped.

```
kebede-vm-02
JSmith-PC
LAPTOP-GRANGER0
```

### Usage

```bash
python3 batch-tagging.py
```

### Output

Each device lookup prints one of three results:

| Marker | Meaning |
| --- | --- |
| `✓ Found` | The device exists and will be tagged. |
| `✗ Not found` | The API answered, but no device has that exact name. |
| `! Error` | The lookup itself failed, for example with `HTTP 401` (bad token), `HTTP 429` (rate limited) or a timeout. The device might still exist. |

When the script finishes, it prints a summary with:

- the matched devices
- the result of each tag batch (`202 Accepted` means Vision One queued the assignment; the `Operation-Location` link can be polled for the final status)
- the devices that weren't found
- the lookups that failed with errors, so you can re-run them

### Settings

These are set at the top of the script:

| Setting | Default | Purpose |
| --- | --- | --- |
| `CSV_FILE` | `devices.csv` | File of device names to tag |
| `MAX_WORKERS` | `10` | How many device lookups run in parallel. Lower it if you see `HTTP 429` errors. |

The old hard-coded `TAG_ID` line is still in the script as a comment. The tag is now chosen from the menu each time the script runs.

## Other scripts

| Script | Purpose |
| --- | --- |
| `tag-fetch.py` | List custom tags with their IDs |
| `tag.py` | Assign one tag to a single asset ID |
| `get_devices.py`, `device-cve.py`, `ep.py`, `ASD.py`, `asd_host.py` | Other device and endpoint queries |
