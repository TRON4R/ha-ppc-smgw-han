
# ha-ppc-smgw-han

<img src="custom_components/smgw_han/brand/icon.png" alt="SMGW Icon" width="128" align="left" style="margin-right: 16px;">

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=TRON4R&repository=ha-ppc-smgw-han)

**Home Assistant custom integration for reading __certified daily meter values__ from PPC Smart Meter Gateways via the HAN interface.**

<a href="README.md">Deutsche Version</a>

<br clear="left">

## What it does

This integration connects to your PPC SMGW once per day and retrieves the official, calibration-grade daily meter readings from the Zählerstand (meter readings) endpoint. It calculates:

- **Daily consumption (total)** - total electricity consumed
- **Daily consumption (slot 1)** - consumption during the first tariff period (default: 00:00–04:59)
- **Daily consumption (slot 2)** - consumption during the second tariff period (default: 05:00–23:59)
- **Daily feed-in (total)** - total electricity fed back to grid

All sensors are compatible with the **Home Assistant Energy Dashboard**.

## How does this differ from ha-ppc-smgw?

The existing [ha-ppc-smgw](https://github.com/jannickfahlbusch/ha-ppc-smgw) integration polls current meter readings at fixed 10 minute intervals (ignoring the respective user setting during setup). Some users have reported being locked out of their SMGW, because the frequency of requests was deemed as too high by the SMGW. So this integration takes a different approach:

- **One fetch per day** (5 HTTP requests total, at a configurable time — eliminating any risk of being locked out by the SMGW due to excessive polling)
- **Certified values** from the SMGW's Zählerstand endpoint (not live meter snapshots)
- **Accurate tariff split** using the second-precise meter reading at the configured tariff switch time
- **No timing issues** - values are based on the SMGW's official daily boundaries, not the local clock of the Home Assistant server
- **Multiple meters and SMGWs in parallel** - supports both several SMGWs and several meters on a single SMGW (Modul-2 setups, separate logins for import and feed-in). Details under [Multiple SMGWs / multiple logins](#multiple-smgws--multiple-logins).

## Requirements

- PPC Smart Meter Gateway with HAN interface enabled
- HAN credentials (username + password) from your electricity provider (MSB)
- Your Home Assistant server and the SMGW must be able to reach each other via IP.

> [!TIP]
> **A SIMPLE SOLUTION FOR THE SMGW IP ROUTING PROBLEM:**  
> _(Making Home Assistant and the SMGW reachable in the same IP range)_
>
> The SMGW is permanently fixed at `192.168.100.100`, while Home Assistant typically runs on a local IP like `192.168.2.x` or similar.
> The [network setup guide](docs/network-setup.en.md) explains how to easily assign your HA server
> a second IP address in the `192.168.100.x` range to establish the connection.

## Installation

### HACS (recommended)

1. Open HACS in Home Assistant
2. Go to Integrations → three-dot menu → Custom repositories
3. Add `https://github.com/TRON4R/ha-ppc-smgw-han` as an Integration
4. Install "PPC SMGW HAN Daily Import"
5. Restart Home Assistant

### Manual

1. Copy `custom_components/smgw_han/` to your Home Assistant `custom_components/` directory
2. Restart Home Assistant

### Trying pre-release versions

If a pre-release is available and you want to try it:

1. HACS → Integrations → open "PPC SMGW HAN Daily Import"
2. Three-dot menu in the top right → **"Re-download"**
3. Expand **"Need a different version?"** in the dialog
4. From the **"Release"** dropdown, pick the desired version (with an orange `pre-release` label)
5. Click **"Download"**
6. Restart Home Assistant

Your existing configuration remains untouched — all entities and the Energy Dashboard history are preserved.

## Configuration

1. Go to Settings → Devices & Services → Add Integration
2. Search for "PPC SMGW"
3. Enter:
   - **URL**: Your SMGW HAN interface URL (default: `https://192.168.100.100/cgi-bin/hanservice.cgi`)
   - **Username** and **Password**: Your HAN credentials
   - **Standard tariff start time**: When the standard tariff begins (default: 05:00, configurable)
   - **Fetch time**: Time of the daily data fetch (default: 00:15)
   - **Device name** (optional, see next section)

## Multiple SMGWs / multiple logins

Since version 2.0, the integration can manage any number of SMGW instances in parallel. Just click "Add Integration" again and configure another login. Each entry gets its own set of entities and its own device in the device registry.

Typical use cases:

- **Two physical meters on the *same* SMGW** (e.g. a Modul-2 setup with an import meter and a separate PV-production meter on one SMGW): when adding a new entry, the integration auto-detects that the SMGW exposes multiple meters in its dropdown and inserts an extra step in which you pick which of those meters shall be assigned to the entry. To monitor the second meter as well, simply add another entry with the same credentials and pick the other meter there.
- **Two separate SMGWs** (e.g. two buildings or independent metering points): each SMGW is added as its own entry with its own credentials and, if applicable, its own IP address.
- **One SMGW, two logins**: some metering point operators issue separate HAN credentials for consumption (OBIS 1.8.0) and feed-in (OBIS 2.8.0). Both logins can be configured as two independent entries against the same SMGW. In this case use the optional **Device name** field and pick meaningful names like "SMGW Import" and "SMGW Export" so the two devices are clearly distinguishable in Home Assistant.

Leave **Device name** empty when you only run a single SMGW or your SMGWs target distinct physical meters — the default name "PPC SMGW" is sufficient, and Home Assistant automatically numbers devices with identical names.

### Behaviour on meter replacement

When your metering point operator swaps the physical meter in your basement, you can simply update the credentials via the options dialog of the existing entry — entities and statistics history remain untouched. Even if you instead delete the entry and add it back, the new entry will reuse the freed internal slot (e.g. `smgw_meter1` again), so long-term statistics in the Energy Dashboard continue seamlessly.

## Sensors

| Sensor | Description | Device Class | State Class |
|---|---|---|---|
| Daily consumption total | Yesterday's total consumption | `energy` | `total` |
| Daily consumption slot 1 | Consumption during slot 1 (midnight → tariff switch) | `energy` | `total` |
| Daily consumption slot 2 | Consumption during slot 2 (tariff switch → midnight) | `energy` | `total` |
| Daily feed-in total | Yesterday's total feed-in | `energy` | `total` |
| Meter consumption previous day closing | Absolute reading at start of day (00:00) | `energy` | `total_increasing` |
| Meter consumption tariff switch 1 | Absolute reading at tariff switch time | `energy` | `total_increasing` |
| Meter feed-in previous day closing | Absolute export reading at start of day (00:00) | `energy` | `total_increasing` |
| Daily date | Date of the last fetched data | `date` | — |


## Data export for arbitrary time ranges

The service **`smgw_han.export_readings`** fetches meter readings for a **custom time range** straight from Home Assistant — no detour via the SMGW web interface.

**Parameters:**

| Field | Required | Description |
|---|---|---|
| `device_id` | ✅ | The SMGW device to query |
| `from_datetime` | ✅ | Start (date + time) |
| `to_datetime` | ✅ | End (date + time) |
| `download_cms` | – | Also save the signed **CMS original** (tamper-evident, like the "Exportieren" button in the web interface) |
| `write_csv` | – | Also save the raw readings as **CSV** (semicolon-separated, Excel-friendly) |
| `write_xlsx` | – | Also save an **Excel workbook** (raw data, daily end values, tariff zones) |

**Return value (response variable):** The service always returns the raw readings (`readings`) and a daily summary (`daily_summary`) — visible directly in **Developer Tools → Actions** or usable in scripts via `response_variable`. If one or more file switches are set, the response additionally contains `files` with download links.

**Example call (script/automation):**

```yaml
action: smgw_han.export_readings
data:
  device_id: <your device id>
  from_datetime: "2026-05-01 00:00:00"
  to_datetime: "2026-05-08 00:00:00"
  write_csv: true
  write_xlsx: true
  download_cms: true
response_variable: smgw_export
```

> **Tip:** For a dashboard mini-UI, create two [`input_datetime`](https://www.home-assistant.io/integrations/input_datetime/) helpers (start/end) and wire a button to the `smgw_han.export_readings` action using those helpers as `from_datetime`/`to_datetime` — entirely with built-in HA features, no extra installs.

**Important notes:**

- **Be gentle with the SMGW:** Every call opens a real SMGW session. Do **not** call the service in loops — the SMGW allows only one active session and may briefly lock out on overload. The nightly fetch and a manual export block each other automatically (no conflict) but run sequentially.
- **Download links are unauthenticated:** Files are written to `config/www/smgw_han_exports/<random>/` and are reachable as `/local/…` links **without login**. Anyone who knows the link can fetch the file. The random path component makes guessing hard; delete export folders you no longer need from time to time.
- If the `/local/` links don't work on the very first export, create the `config/www/` folder once manually and restart HA (Home Assistant only mounts `www/` at startup).

## Dashboard card: Daily consumption history

**Prerequisite:** [ApexCharts Card](https://github.com/RomRider/apexcharts-card) (installable via HACS)

![Daily consumption history SMGW](dashboard/verbrauchshistorie_taeglich.png)

The card displays the last 30 days as a stacked bar chart:
- **Go** (blue): Consumption during the discounted tariff slot (slot 1)
- **Standard** (pink): Consumption during the standard tariff slot (slot 2)
- Tooltip (mouse-over): Individual values per tariff segment per day
- Header: Cumulative total per segment over the displayed period

### How to add it

1. Download [`dashboard/verbrauchshistorie_taeglich.yaml`](dashboard/verbrauchshistorie_taeglich.yaml)
2. In Home Assistant: Dashboard → Add card → Manual card
3. Paste the YAML and adjust the entity IDs to match yours:
   - `sensor.octopus_smgw_tagesverbrauch_zeitfenster_2` → your entity ID for slot 2
   - `sensor.octopus_smgw_tagesverbrauch_zeitfenster_1` → your entity ID for slot 1

You can find your entity IDs under **Settings → Devices & Services → Entities**.

## Intended use case

This integration was developed for the **Octopus Energy (Intelligent) Go tariff** in Germany, which offers a reduced electricity rate between **00:00 and 04:59:59** (Go tariff) and a standard rate from **05:00 to 23:59:59** (standard tariff).

The **tariff split time** can however be **freely adjusted** via the GUI for other tariffs.

If you are using a completely different tariff structure, please [open an issue](https://github.com/TRON4R/ha-ppc-smgw-han/issues) or ideally a [pull request](https://github.com/TRON4R/ha-ppc-smgw-han/pulls) to discuss how to make this work for your setup.

## License

MIT License — see [LICENSE](LICENSE) for details.
