
# ha-ppc-smgw-han

<img src="custom_components/smgw_han/brand/icon.png" alt="SMGW Icon" width="128" align="left" style="margin-right: 16px;">

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=TRON4R&repository=ha-ppc-smgw-han)

**Home Assistant custom integration for reading __certified daily meter values__ from PPC Smart Meter Gateways via the HAN interface.**

<a href="README.md">Deutsche Version</a>

<br clear="left">

## What it does

This integration automatically connects to your PPC SMGW once per day and retrieves the official, calibration-grade daily meter readings from the Zählerstand (meter readings) endpoint. It provides:

- **Daily consumption (total)** - total electricity consumed
- **Daily consumption per tariff zone** - with **freely configurable tariff zones** (since v3.0.0): from simple two-zone tariffs like Octopus Go (default: 00:00–04:59 / 05:00–23:59) up to tariffs with several time windows per zone like Octopus Heat
- **Daily feed-in (total)** - total electricity fed back to grid
- **Energy Dashboard compatible** - all sensors can be used directly in the Home Assistant Energy Dashboard
- **Separate data export for arbitrary time ranges** - on demand and independent of the sensors: the data comes straight from the SMGW's own storage (15–24 months of history depending on the device, i.e. also from **before** the integration was installed) — conveniently from the Home Assistant UI, without cumbersome manual logins to the SMGW web interface. Output as CSV, Excel or the **signed CMS original**. See [Data export for user-defined time ranges](#-data-export-for-user-defined-time-ranges)

## How does this differ from other SMGW integrations?

Another SMGW integration polls current meter readings at fixed 10 minute intervals. Some users have reported being locked out of their SMGW as a result, because the frequency of requests was deemed as too high by the SMGW. So this integration takes a different approach:

- **One fetch per day** (5 HTTP requests total, at a configurable time - eliminating any risk of being locked out by the SMGW due to excessive polling)
- **Certified values** from the SMGW's Zählerstand endpoint (not live meter snapshots)
- **Accurate tariff split** using the second-precise meter readings at the configured tariff switch points
- **No timing issues** - values are based on the SMGW's official daily boundaries, not the local clock of the Home Assistant server
- **Multiple meters and SMGWs in parallel** - supports both several SMGWs and several meters on a single SMGW (Modul-2 setups, separate logins for import and feed-in). Details under [Multiple SMGWs / multiple logins](#multiple-smgws--multiple-logins).
- **Export of certified CMS files** - the integration allows exporting legally sound CMS files in their certified original straight from the SMGW (e.g. to prove billing errors by the electricity supplier), plus generating CSV and Excel files for further processing of the consumption and feed-in data. Details under [Data export for user-defined time ranges](#-data-export-for-user-defined-time-ranges).

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
   - **Tariff zones (switch points)**: one entry per switch point in the format `HH:MM zone name`, starting at `00:00`; the last entry runs until midnight (default: `00:00 Zeitfenster 1` and `05:00 Zeitfenster 2`, matching Octopus Go). Entries sharing a zone name are summed into **one** sensor — this also covers tariffs with several time windows per zone, e.g. Octopus Heat: `00:00 Standard`, `02:00 Heat`, `06:00 Standard`, `12:00 Heat`, `16:00 Standard`. If you prefer to **track every time window separately** — even when it carries the same name and price at your supplier — simply use distinct names (e.g. `Standard1`/`Standard2` or `Night-Low`/`Noon-Low`): each window then gets its own sensor. Switch times must sit on the SMGW's 15-minute measurement grid (minutes `00`, `15`, `30` or `45`) — other times are rejected with an error message right at input, nothing is rounded automatically
   - **Fetch time**: Time of the daily data fetch (default: 00:15)
   - **Device name** (optional, see next section)

> [!NOTE]
> **Upgrading from v2.x:** Existing entries are migrated to the tariff-zone model automatically on first start. That means your entities, names and the Energy Dashboard history are preserved. The migration is **one-way** (downgrading below 3.0.0 requires a backup or re-adding the entry).

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
| Daily consumption *{zone name}* | Consumption per configured tariff zone (one sensor per zone name; segments sharing a name are summed) | `energy` | `total` |
| Daily feed-in total | Yesterday's total feed-in | `energy` | `total` |
| Meter consumption previous day closing | Absolute reading at the end of the previous day (midnight) | `energy` | `total_increasing` |
| Meter consumption tariff switch *N* | Absolute reading at each inner switch point (one sensor per switch point) | `energy` | `total_increasing` |
| Meter feed-in previous day closing | Absolute export reading at the end of the previous day (midnight) | `energy` | `total_increasing` |
| Daily date | Date of the last fetched data | `date` | — |


## 📤 Data export for user-defined time ranges

Meter readings can be fetched for an **arbitrary time range** straight from Home Assistant — no detour via the SMGW web interface. Output as **CSV**, **Excel** and/or the **signed CMS original**.

**Three ways, easiest to most flexible** (details below):

- 🛠️ **Easiest – via the integration:** on the SMGW device click the **gear "Configure"** → **"Export SMGW data for a custom time range"**. A guided form, no prior knowledge and no helpers are necessary.
- 📊 **One click, presets only – dashboard tile:** buttons for "Yesterday", "Last month" etc.
- ⚙️ **Full control – Developer Tools → Actions:** any parameters, the response (incl. download links) is shown right there.

Under the hood there are **two actions/services**:

- **`smgw_han.export_readings`** — custom range via `from_datetime` / `to_datetime`.
- **`smgw_han.export_period`** — a ready-made **period preset** (`Yesterday`, `Last 7 days`, `Last 30 days`, `Current month`, `Last month`); `from`/`to` incl. the correct day-closing reading are computed automatically.

Both return the same result: a response variable with `readings` + `daily_summary`, and (if file switches are set) `files` with download links.

---

### Way 1: Via the integration ("Configure") – easiest

Without Developer Tools, helpers or a dashboard: **Settings → Devices & Services → your SMGW → gear "Configure"** → menu entry **"Export SMGW data for a custom time range"**. Pick a period, confirm/edit the **pre-filled** From/To fields in the next step, and after the export the download links appear directly in the final step **and** as a notification 🔔. The initial setup is unaffected by this.

### Way 2: Via a dashboard tile (quick select)

A ready-made tile with buttons for the period presets is available at [`dashboard/datenexport.yaml`](dashboard/datenexport.yaml). It calls a small script that posts a **notification with clickable links** after the export.

**a) Create the script** (Settings → Automations & Scenes → Scripts → "Edit in YAML") — yields the entity id `script.smgw_export_mit_benachrichtigung`:

```yaml
alias: SMGW export with notification
fields:
  period:
    selector:
      select:
        options: [yesterday, last_7_days, last_30_days, current_month, last_month]
sequence:
  - action: smgw_han.export_period
    data:
      # device_id is omitted with a single SMGW (auto-detected).
      # Multiple SMGWs? Add device_id: <your-device-id> here.
      period: "{{ period | default('last_month') }}"
      download_cms: true
      write_csv: true
      write_xlsx: true
    response_variable: result
  - action: persistent_notification.create
    data:
      title: SMGW export
      message: >-
        {{ result.reading_count }} values, {{ result.daily_summary | count }} days.
        {% set f = result.files | default({}) %}
        {% if f.cms %}[CMS]({{ f.cms }}) · {% endif %}
        {% if f.csv %}[CSV]({{ f.csv }}) · {% endif %}
        {% if f.xlsx %}[Excel]({{ f.xlsx }}){% endif %}
```

**b) Add the tile:** Dashboard → Add card → Manual card → paste the YAML from [`dashboard/datenexport.yaml`](dashboard/datenexport.yaml). **With a single SMGW there's nothing else to do** — the device is auto-detected. Clicking e.g. "Last month" creates the export and shows the links as a notification.

> **Multiple SMGWs?** Add a `device_id: <your-device-id>` line under `data:` in the script (or each tile button). The **"Device ID"** diagnostic entity on each SMGW device shows its `device_id` (the entity state is the device id to copy).

### Way 3: Via Developer Tools → Actions (full control)

In **Developer Tools → Actions** both services can be called with any parameters; the response (incl. download links) is shown right there. Parameters:

| Field | Action | Description |
|---|---|---|
| `device_id` | both | The SMGW device to query (optional with a single SMGW) |
| `from_datetime` / `to_datetime` | `export_readings` | Start/end of the range (date + time) |
| `period` | `export_period` | Ready-made range (dropdown) |
| `download_cms` | both | Also save the signed **CMS original** (tamper-evident, like the "Exportieren" button in the web interface) |
| `write_csv` | both | Also save the raw readings as **CSV** (semicolon-separated, Excel-friendly) |
| `write_xlsx` | both | Also save an **Excel workbook** (raw data, daily end values, tariff zones) |

> **Tip:** "Last month" (in `export_period`) automatically returns the **complete** previous month including the closing meter reading at midnight on the 1st — the value that's easy to miss by hand. With `export_readings`, remember to set `to` to **00:15 of the following day** to include the last day's closing reading.

**Example calls (to embed in a script or an automation):**

```yaml
# Custom range
action: smgw_han.export_readings
data:
  device_id: <your device id>   # omit with a single SMGW
  from_datetime: "2026-05-01 00:00:00"
  to_datetime: "2026-06-01 00:15:00"
  write_csv: true
  write_xlsx: true
  download_cms: true
response_variable: smgw_export
```

```yaml
# Ready-made preset (no date guessing)
action: smgw_han.export_period
data:
  period: last_month
  write_csv: true
  write_xlsx: true
  download_cms: true
response_variable: smgw_export
```

To make the links **clickable**, consume the response in a follow-up step — e.g. the notification script from Way 2.

> [!CAUTION]
> **Important notes**
>
> - **Be gentle with the SMGW:** Every call opens a real SMGW session. Do **not** call the service in loops — the SMGW allows only one active session and may briefly lock out on overload. The nightly fetch and a manual export block each other automatically (no conflict) but run sequentially.
> - **Download links are unauthenticated:** Files are written to `config/www/smgw_han_exports/<random>/` and are reachable as `/local/…` links **without login**. Anyone who knows the link can fetch the file. The random path component makes guessing hard; delete export folders you no longer need from time to time.
> - If the `/local/` links don't work on the very first export, create the `config/www/` folder once manually and restart HA (Home Assistant only mounts `www/` at startup).

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

This integration was initially developed for the **Octopus Energy (Intelligent) Go tariff** in Germany, which offers a reduced electricity rate between **00:00 and 04:59:59** (Go tariff) and a standard rate from **05:00 to 23:59:59** (standard tariff). It has since been extended, so it can now also model e.g. the **Octopus Heat tariff** — and of course tariffs from other electricity suppliers as well.

The **tariff zones** are **freely configurable** for other tariffs — any number of switch points per day, straight from the GUI.

If you are using a completely different tariff structure, please [open an issue](https://github.com/TRON4R/ha-ppc-smgw-han/issues) or ideally a [pull request](https://github.com/TRON4R/ha-ppc-smgw-han/pulls) to discuss how to make this work for your setup.

## License

MIT License — see [LICENSE](LICENSE) for details.
