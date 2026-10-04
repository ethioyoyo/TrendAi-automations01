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

Assigns one custom tag to many devices at once, using a list of device names. It is built for large lists (100K+ names): it lists the tenant's devices once and matches them locally instead of making one API call per name.

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

2. **Read device names** from the CSV file. Duplicate names are removed, ignoring case.

3. **Scan all devices** in Attack Surface devices (`GET /v3.0/asrm/attackSurfaceDevices`), `DEVICE_PAGE_SIZE` devices per page, following `nextLink`. Each device is matched against the CSV names exactly, ignoring case. If several devices share a name, all of them are tagged.

4. **Assign the tag as it goes.** Every time 1000 devices have matched, the script sends them in one `POST /v3.0/tagManagement/customTags/assign` request. The last partial batch is sent when the scan ends, including when the scan fails or you press Ctrl+C, so work done so far isn't lost.

Every request is retried on `HTTP 429` (rate limited), `5xx` and network errors, up to `MAX_RETRIES` times. The script waits for `Retry-After` when the API sends it, and otherwise backs off exponentially (2s, 4s, 8s … up to 60s).

### Input file

The CSV has one device name per line, in the first column. Blank lines are skipped, and so is a header row if the first line is `deviceName`, `name`, `hostname` or `device`.

```
kebede-vm-02
JSmith-PC
LAPTOP-GRANGER0
```

### Usage

```bash
python3 batch-tagging.py
```

| Option | Purpose |
| --- | --- |
| `--csv FILE` | File of device names (default `devices.csv`) |
| `--dry-run` | Scan and match, write the result files, but don't assign the tag |
| `--limit N` | Only use the first N names from the CSV |

Before a large run, test on a sample first:

```bash
python3 batch-tagging.py --csv devices.csv --limit 500 --dry-run
```

A dry run still scans every device in the tenant, so it takes about as long as a real run. Only the tagging step is skipped.

### Output

While it runs, the script prints its progress after every page and the result of each tag batch. At the end it prints a summary.

Each run also writes its results to its own folder, `output/<YYYYmmdd-HHMMSS>/`, so earlier runs are never overwritten:

| File | Contents |
| --- | --- |
| `matched.csv` | Every matched device: CSV name, device name, asset ID |
| `tagged.csv` | Devices the tag was sent for, with batch number and HTTP status (`dry-run` in a dry run) |
| `not_found.csv` | Names that matched no device, after a complete scan |
| `unresolved.csv` | Names the scan never got to check because it stopped early. These may still exist. |
| `tag_errors.csv` | Devices whose tag batch failed, with the error |
| `retry.csv` | Names from failed tag batches |
| `operations.csv` | The `Operation-Location` link for each batch that returned `202 Accepted` |

`202 Accepted` means Vision One queued the assignment. The `Operation-Location` links can be polled for the final status. The script doesn't poll them.

`not_found.csv`, `unresolved.csv` and `retry.csv` use the same format as the input file, so you can feed them straight back in:

```bash
python3 batch-tagging.py --csv output/20261004-094133/retry.csv
```

The script exits with status `1` if the scan stopped early, you interrupted it, or any tag batch failed. Otherwise it exits with `0`.

### Settings

These are set at the top of the script:

| Setting | Default | Purpose |
| --- | --- | --- |
| `CSV_FILE` | `devices.csv` | Default input file when `--csv` isn't given |
| `DEVICE_PAGE_SIZE` | `1000` | Devices requested per page during the scan |
| `TAG_BATCH_SIZE` | `1000` | Devices per tag assign request |
| `MAX_RETRIES` | `6` | Retries per request before giving up |
| `OUTPUT_ROOT` | `output` | Folder that run results are written under |

The old hard-coded `TAG_ID` line is still in the script as a comment. The tag is now chosen from the menu each time the script runs.

### Before your first large run

These details haven't been confirmed against a live tenant yet:

- **Page size.** If the first device page fails with `HTTP 400`, the API may not accept `top=1000`. Lower `DEVICE_PAGE_SIZE`, for example to `200`.
- **Re-tagging.** Re-running `retry.csv` assumes that assigning a tag to a device that already has it does no harm.
- **Run time.** A run takes roughly one request per `DEVICE_PAGE_SIZE` devices in the tenant, plus one request per 1000 matches. Rate limiting slows it down but no longer causes failures.

Do a small sample run first (`--limit 500 --dry-run`) and check `matched.csv`.

## Other scripts

All of these use the same `TMV1_TOKEN` setup. IDs and tag values are set at the top of each script unless noted.

| Script | What it does | Endpoint |
| --- | --- | --- |
| `ASD.py` | Lists every Attack Surface device (following `nextLink`) with IP, custom tags, OS and asset ID | `GET /v3.0/asrm/attackSurfaceDevices` |
| `asd_host.py` | Shows the full record for one device by name: `python3 asd_host.py <host-name>` | `GET /v3.0/asrm/attackSurfaceDevices` with `TMV1-Filter` |
| `device-cve.py` | Lists vulnerable devices and how many CVEs each has (first 200 only) | `GET /v3.0/asrm/vulnerableDevices` |
| `ep.py` | Lists endpoints that need action (immediate action, unmanaged, sensor update or disabled, maintenance recommended) | `GET /v3.0/endpointSecurity/endpoints` |
| `tag-fetch.py` | Lists custom tags with their ID, name and property (first 100 only) | `GET /v3.0/tagManagement/customTags` |
| `tag.py` | Assigns one tag to one asset, printing the full response | `POST /v3.0/tagManagement/customTags/assign` |
| `get_devices.py` | Assigns one tag to one asset. Despite its name, it does the same job as `tag.py` and lists no devices. | `POST /v3.0/tagManagement/customTags/assign` |

`tag.py` doesn't run right now. Its last line has an unterminated string (`SyntaxError` at line 46). Use `get_devices.py` until it's fixed.
