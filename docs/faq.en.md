# FAQ – Frequently asked questions

<!--
Maintenance rules for the IDs:
- The IDs (E1, S1, ...) are identical in faq.md and faq.en.md, so a reference like "FAQ S1" works in both languages.
- IDs are never renumbered or reused, because issues and discussions refer to them.
- A new question gets the next free number in its section, is appended at the end of that section, and goes into both files.
- A removed question leaves its ID unused.
- Every question carries a short anchor <a id="..."></a> in its heading and is listed in the overview.
-->

These questions are drawn from this repo's [issues](https://github.com/TRON4R/ha-ppc-smgw-han/issues) and [discussions](https://github.com/TRON4R/ha-ppc-smgw-han/discussions). Every question has a fixed ID such as **S1** that you can link to directly: `https://github.com/TRON4R/ha-ppc-smgw-han/blob/main/docs/faq.en.md#s1`. The IDs are the same in the [German version](https://github.com/TRON4R/ha-ppc-smgw-han/blob/main/docs/faq.md).

If your question is missing, please read [H1 – Before you open an issue](#h1) first. That makes help a lot quicker.

Back to the [README](https://github.com/TRON4R/ha-ppc-smgw-han/blob/main/README.en.md).

## Overview

**Setup and connection**
- [E1](#e1) Home Assistant and the SMGW are on different networks – how do I connect them?
- [E2](#e2) Setup fails with an error message
- [E3](#e3) Pinging the SMGW fails
- [E4](#e4) My laptop doesn't get an IP address from the SMGW
- [E5](#e5) Where do I get the HAN credentials?
- [E6](#e6) How do I look at the data directly on the SMGW – in a browser or with TRuDI?
- [E7](#e7) Does the integration work with SMGWs from other manufacturers?
- [E8](#e8) Do I have to be an Octopus Energy customer?

**Sensors and values**
- [S1](#s1) Consumption arrives, but feed-in stays at 0
- [S2](#s2) Why are the values only updated once a day?
- [S3](#s3) After setup, the sensors show "Unknown"
- [S4](#s4) What do the notices under "Repairs" mean?
- [S5](#s5) The values appear one day late
- [S6](#s6) Can I bring in values from before the installation?
- [S7](#s7) Which sensors belong in the Energy dashboard?
- [S8](#s8) I have two meters
- [S9](#s9) My password changed or my meter was replaced

**Tariffs**
- [T1](#t1) My tariff has several time windows per price level
- [T2](#t2) How do I handle time-variable grid fees (Modul 3)?

**Data export**
- [X1](#x1) The download links lead nowhere or to the dashboard
- [X2](#x2) My export "until 23:59:59" is incomplete
- [X3](#x3) Can I automate the export?
- [X4](#x4) How do I download the SMGW log – and why do I get a ZIP?

**Dashboard card**
- [K1](#k1) The daily consumption history card shows no bars

**Getting help**
- [H1](#h1) Before you open an issue

---

## Setup and connection

### <a id="e1"></a>E1 · Home Assistant and the SMGW are on different networks – how do I connect them?

The SMGW has a fixed IP address on a network of its own, usually `192.168.100.100`, and it cannot be changed. Your home network typically uses a different range, e.g. `192.168.2.x`. Out of the box, Home Assistant and the SMGW therefore cannot reach each other.

The simplest solution needs no routes, no VLANs and no changes to your network:

1. Connect the SMGW's **HAN port** by LAN cable to the same switch the Home Assistant server is connected to.
2. Give **Home Assistant a second IP address** on the SMGW's network, e.g. `192.168.100.12`.

The step-by-step guide with a screenshot is in the [network setup guide](https://github.com/TRON4R/ha-ppc-smgw-han/blob/main/docs/network-setup.en.md).

Good to know:

- **If Home Assistant runs as a virtual machine** (e.g. on a NAS or on Proxmox), the host's IP address doesn't matter. The second address goes to the Home Assistant instance itself. The most robust choice is then a dedicated network interface for the SMGW ([Option B](https://github.com/TRON4R/ha-ppc-smgw-han/blob/main/docs/network-setup.en.md#option-b) of the network setup guide): the existing interface stays on "Automatic".
- **On HA Container (Docker) or HA Core** there are no network settings in Home Assistant. Set up the second address on the host system instead.
- **The URL in the integration stays the same.** It keeps the SMGW's address (e.g. `https://192.168.100.100/cgi-bin/hanservice.cgi`), not Home Assistant's new address.
- **A second router, or routing between the networks, does not help according to user reports:** the SMGW apparently does not answer packets that were forwarded by a router.
- **If your SMGW has a different address** than `192.168.100.100`, pick an address from its range for Home Assistant – for `192.168.1.200`, e.g. `192.168.1.12`.

### <a id="e2"></a>E2 · Setup fails with an error message – what's wrong?

The message usually points to the cause:

**"Cannot connect to the SMGW. Check the URL and network connection."**

Home Assistant cannot reach the SMGW. The most common reasons:

- **Wrong IP address in the URL.** Not every SMGW uses `192.168.100.100`. The issues also mention `192.168.1.200` and `10.11.120.2` – the address depends on your metering point operator (German: *Messstellenbetreiber*, MSB). If in doubt, ask them.
- **Home Assistant and the SMGW are not on the same network:** no second IP address in Home Assistant, the HAN port is not on the same switch, or the connection runs through a router. See [E1](#e1).
- **The URL is incomplete.** It must start with `https://` and include the path, e.g. `https://192.168.100.100/cgi-bin/hanservice.cgi`.

**"Invalid username or password."**

- **A full stop at the end of the password?** Octopus sometimes sends the HAN password by text message with a full stop right after it. The full stop is **not** part of the password.
- These are the **HAN credentials** of the smart meter gateway – not the login for your account with your electricity supplier or for your grid operator's portal.
- If your gateway is not a PPC device, the login fails as well, see [E7](#e7).

**"Could not parse the SMGW response. … wait up to 15 minutes and try again."**

- **The SMGW temporarily blocks logins** after several failed attempts in a short time. Wait a few minutes, then try **once** with credentials you are sure are correct – don't retry in quick succession.
- **Another session is still open.** The SMGW allows only one active session. If you are logged in via a browser or TRuDI at the same time, log out there or close the program.

**"This meter is already set up with this evaluation."**

There already is an entry for this meter. How to add a second meter, a second login or a second evaluation of the same meter is explained in the README under [Multiple SMGWs / multiple logins](https://github.com/TRON4R/ha-ppc-smgw-han/blob/main/README.en.md#multiple-smgws--multiple-logins).

### <a id="e3"></a>E3 · Pinging the SMGW fails – is it broken?

No. The SMGW never answers `ping`, so a failed ping says nothing about whether it can be reached. Suitable tests are setting up the integration itself or logging in directly via a browser or TRuDI ([E6](#e6)).

### <a id="e4"></a>E4 · My laptop doesn't get an IP address from the SMGW

The SMGW has no DHCP server. You have to give your computer a fixed IP address on the SMGW's network by hand – for an SMGW at `192.168.100.100`, e.g. `192.168.100.50` with the subnet mask `255.255.255.0`. The last number must differ from the SMGW's and from the one you already gave Home Assistant.

### <a id="e5"></a>E5 · Where do I get the HAN credentials?

From your **metering point operator** (German: *Messstellenbetreiber*, MSB) – not necessarily your electricity supplier. If Octopus installed your smart meter, that is usually Octopus Energy Metering.

Experience from the issues:

- Processing took anywhere from a few days to three months. Persistent follow-ups help.
- The username often came by email, the password by letter or text message (watch out for the full stop at the end, see [E2](#e2)).
- Some operators issue **separate credentials for import and feed-in**. Then you need both, see [S1](#s1).

### <a id="e6"></a>E6 · How do I look at the data directly on the SMGW – in a browser or with TRuDI?

You don't need this to run the integration. For troubleshooting, though, it is the best cross-check: you see with your own eyes, and **independently of this integration**, what your SMGW hands out for your credentials.

Both ways require your computer to have an IP address on the SMGW's network (see [E4](#e4) and the notes in the [network setup guide](https://github.com/TRON4R/ha-ppc-smgw-han/blob/main/docs/network-setup.en.md)).

**In a browser**

1. Open the URL you entered in the integration, e.g. `https://192.168.100.100/cgi-bin/hanservice.cgi`.
2. A **certificate warning** appears. That's normal, because the SMGW uses a self-signed certificate. Firefox makes it easiest to proceed: "Advanced…" → "Accept the Risk and Continue". Chrome is considerably more troublesome here.
3. Log in with the HAN credentials, pick the meter from the drop-down and display the meter readings for the period you want. Every value is shown with its OBIS code, e.g. `1-0:1.8.0` for import and `1-0:2.8.0` for feed-in.
4. **Log out afterwards.** As long as the session is open, the integration cannot log in.

**With TRuDI**

TRuDI is the official display software for smart meter gateways. You log in with the same HAN credentials and see the values read from the gateway, including their OBIS codes. There are no browser certificate warnings to deal with; some users found it easier than the browser. Here, too: close TRuDI afterwards so the session is free again.

### <a id="e7"></a>E7 · Does the integration work with SMGWs from other manufacturers?

No, only with gateways made by **PPC** (Power Plus Communications). The integration reads PPC's HAN web interface. Other manufacturers such as Theben, EMH or Sagemcom Dr. Neuhaus have their own interfaces – even TRuDI ships a separate adapter for each manufacturer. As I only own a PPC gateway, I can't test other manufacturers either.

The manufacturer is shown on the gateway's type plate.

### <a id="e8"></a>E8 · Do I have to be an Octopus Energy customer?

No. The smart meter gateway belongs to the metering point, not to your electricity contract. The integration works with any supplier as long as a PPC gateway is installed and you have the HAN credentials. The Octopus tariffs are merely offered as templates during setup; you can enter your own switch times freely.

---

## Sensors and values

### <a id="s1"></a>S1 · Consumption arrives, but feed-in stays at 0 – why?

If you don't have a PV system, 0 is correct. Otherwise: **the integration delivers exactly what the SMGW hands out for your credentials.** Some metering point operators issue a separate login for feed-in – with the import login, feed-in simply isn't visible.

You can check this in two ways. In both, the OBIS code is what counts: `1-0:1.8.0` stands for import (consumption), `1-0:2.8.0` for feed-in.

**a) Quick: with the integration's export**

1. **Settings → Devices & services → PPC SMGW HAN Daily Import → Configure** (gear) → "Export SMGW meter data for a custom time range".
2. Choose the period **"Yesterday"**, tick **"Create Excel (XLSX)"** and run the export.
3. In the Excel file, open the sheet **"Rohdaten"** (raw data – the sheet names are always German) and look for `1-0:2.8.0` in the "OBIS" column.

**b) With your own eyes: directly on the SMGW**

The export logs in to the SMGW exactly like the integration does. To rule out that the integration itself swallows the feed-in values, log in to the SMGW directly with the same HAN credentials – in a browser or with TRuDI (instructions in [E6](#e6)) – and display yesterday's meter readings. What you see there comes straight from the gateway, without going through this integration. A screenshot of it also makes good evidence when you request the feed-in credentials from your metering point operator.

**If `1-0:2.8.0` is missing**, the SMGW does not provide feed-in values for your login. No software can change that – request the feed-in credentials from your metering point operator. Then use them to add a **second entry** with a descriptive device name such as "SMGW feed-in" (details in the README under [Multiple SMGWs / multiple logins](https://github.com/TRON4R/ha-ppc-smgw-han/blob/main/README.en.md#multiple-smgws--multiple-logins)).

**If the SMGW shows `1-0:2.8.0` but the integration still shows 0**, the fault lies with the integration. In that case, please open an issue (see [H1](#h1)) and attach the screenshot from the SMGW.

> [!IMPORTANT]
> Your **grid operator's portal** is not the SMGW. Seeing feed-in values there doesn't mean your HAN login sees them too. What counts is what the gateway itself delivers.

### <a id="s2"></a>S2 · Why are the values only updated once a day? I set 15 minutes.

The **"Fetch time"** (default `00:15`) is a **time of day, not an interval**: the integration fetches the previous day's values once a day at 00:15. That's deliberate:

- The certified daily closing values are only final **after midnight**.
- Frequent queries risk the SMGW blocking access – and getting it unblocked by the metering point operator can take weeks.

You still get the 15-minute values: the [data export](https://github.com/TRON4R/ha-ppc-smgw-han/blob/main/README.en.md#-data-export-for-user-defined-time-ranges) delivers every single 15-minute value, retroactively for the whole period stored in the gateway.

For **live values** (e.g. surplus charging), the SMGW is fundamentally unsuitable. Use an optical reading head on the meter or your inverter's smart meter instead. This integration cannot process their data.

### <a id="s3"></a>S3 · After setup, the sensors show "Unknown"

Usually they don't: the integration fetches the previous day right during setup, so the sensors have values immediately. If they stay "Unknown", check **Settings → System → Repairs** for a notice from the integration (see [S4](#s4)). The most common case: the SMGW has no complete data for yesterday, for example because the metering point operator is currently reworking something on the meter or account. An export for "Yesterday" shows whether data is there.

### <a id="s4"></a>S4 · What do the notices under "Repairs" mean?

The integration reports problems with the nightly fetch instead of silently keeping old values:

| Notice | Meaning | What to do |
|---|---|---|
| **SMGW fetch failed - retrying** | The fetch failed. The integration retries automatically (after 15, 30 and 60 minutes, then every 2 hours). | Nothing. The notice disappears by itself as soon as a fetch succeeds. |
| **SMGW fetch gave up for one day** | All attempts failed before the next regular fetch was due. This day is permanently missing from the sensors. | Check the connection. The day's values can usually still be retrieved via the export. |
| **SMGW is not delivering current data** | Login and connection work, but the gateway has no complete values for the day – e.g. after a meter or account change or an outage at the metering point operator. | Use an export to check whether values exist for that day at all. If they are missing there too, the cause lies with the gateway or the operator. |

In addition, the notification **"SMGW daily data is missing"** appears when more than two days in a row are missing. It deliberately stays until you dismiss it, so a gap doesn't go unnoticed.

If your credentials change, none of these notices appears – Home Assistant asks you to log in again instead ("Re-authenticate SMGW").

### <a id="s5"></a>S5 · The values appear one day late – in the Energy dashboard, in ApexCharts or compared with other sources

The values themselves are correct; only their **date in Home Assistant** is shifted by one day. That's inherent to daily values:

The consumption of 26 September is only final after midnight and reaches Home Assistant at 00:15 on **27 September**. Home Assistant always books a value in the hour it arrives – here on 27 September between 00:00 and 01:00. Home Assistant does not allow sensor values to be booked retroactively on the previous day. The **"Daily date"** sensor tells you which day a value belongs to.

What this means in practice:

- **Energy dashboard:** the day view shows the previous day's consumption, all of it in the hour from 00:00 to 01:00. The monthly total includes the last day of the previous month, while the last day of the current month lands in the next month. The Energy dashboard cannot be shifted.
- **Checking your electricity bill:** use the [data export](https://github.com/TRON4R/ha-ppc-smgw-han/blob/main/README.en.md#-data-export-for-user-defined-time-ranges). The sheets "Tagesendwerte" (daily closing values) and "Tarifzonen" (tariff zones) are dated to the exact day.
- **ApexCharts cards using `statistics:`:** shift every series back by one day, and each bar lands on the correct date. The [dashboard card](https://github.com/TRON4R/ha-ppc-smgw-han/blob/main/README.en.md#dashboard-card-daily-consumption-history) from this repo already does this – if you added it before 28 September 2026, please use the current version. For your own cards:

  ```yaml
  series:
    - entity: sensor.your_smgw_daily_consumption_total
      type: column
      statistics:
        type: change
        period: day
        align: start
      offset: '+1d'
      show:
        offset_in_name: false
  ```

  `offset: '+1d'` pulls each following day's value to the right place; `offset_in_name: false` keeps "+1d" from being appended to the series name.

- **Meter reading sensors ("previous day closing")** are barely affected: they show the meter reading at midnight and are only about 15 minutes old when they arrive. If yours were 24 hours behind, you are running a version older than v2.6.0 – please update.

### <a id="s6"></a>S6 · Can I bring values from before the installation into Home Assistant?

Not into the sensors – Home Assistant only accepts sensor values at the current point in time. But the data isn't lost: the [data export](https://github.com/TRON4R/ha-ppc-smgw-han/blob/main/README.en.md#-data-export-for-user-defined-time-ranges) delivers it for any period the gateway has stored (15–24 months depending on the device) – as CSV, Excel or the signed CMS original.

### <a id="s7"></a>S7 · Which sensors belong in the Energy dashboard?

- **Grid consumption:** either **"Daily consumption total"** or – if you want to set a separate price per tariff zone – the individual **"Daily consumption *zone name*"** sensors. **Never both**, otherwise the dashboard counts the consumption twice.
- **Return to grid:** **"Daily feed-in total"**.
- Do not add the **meter reading sensors** as well.
- If you evaluate the same meter twice (e.g. by supplier tariff and by Modul 3 time windows), add only **one** of the two devices to the Energy dashboard.

Mind the one-day offset, see [S5](#s5).

### <a id="s8"></a>S8 · I have two meters (e.g. a separate generation meter)

If the second meter is read via the same SMGW, the integration detects this when you add a new entry and lets you pick the meter. The step-by-step description is in the README under [Multiple SMGWs / multiple logins](https://github.com/TRON4R/ha-ppc-smgw-han/blob/main/README.en.md#multiple-smgws--multiple-logins). A pure generation meter records no import – its consumption sensors then permanently show 0. That's correct; you can hide them in Home Assistant.

### <a id="s9"></a>S9 · My password changed or my meter was replaced

- **New password:** Home Assistant asks you to log in again by itself at the next fetch. Alternatively: **Configure → Change settings**.
- **Meter replacement:** update the credentials via **Configure → Change settings**. Entities and statistics are kept; details in the README under [Behaviour on meter replacement](https://github.com/TRON4R/ha-ppc-smgw-han/blob/main/README.en.md#behaviour-on-meter-replacement).

---

## Tariffs

### <a id="t1"></a>T1 · My tariff has several time windows per price level (e.g. Octopus Heat)

That works: enter each time window as its own switch point and give windows with the same price the same name – they are summed into one sensor. There is a ready-made template for Octopus Heat. Details in the README under [Configuration](https://github.com/TRON4R/ha-ppc-smgw-han/blob/main/README.en.md#configuration).

### <a id="t2"></a>T2 · How do I handle time-variable grid fees (§ 14a EnWG, Modul 3)?

Add the same meter a second time – once with your supplier tariff's time windows, once with your grid operator's. That way you check each bill against exactly the time windows it was calculated with. As grid operators usually change their windows at the turn of the year, you can store the new schedule in advance. Details in the README under [Multiple SMGWs / multiple logins](https://github.com/TRON4R/ha-ppc-smgw-han/blob/main/README.en.md#multiple-smgws--multiple-logins) (item "One meter, two sets of time windows") and [Scheduling a tariff-zone change in advance](https://github.com/TRON4R/ha-ppc-smgw-han/blob/main/README.en.md#scheduling-a-tariff-zone-change-in-advance).

---

## Data export

### <a id="x1"></a>X1 · The download links lead nowhere or to the dashboard

- **Install the latest version.** Older versions opened the links in the same tab, where Home Assistant intercepted them.
- **Check the Home Assistant URLs:** under **Settings → System → Network**, the URLs you use to reach Home Assistant must be correct. The links are built from them – a wrong URL gives wrong links.
- **Folder `config/www/` missing?** Create it once by hand and restart Home Assistant.

The links can be opened **without logging in**. Delete exports you no longer need from `config/www/smgw_han_exports/` now and then.

### <a id="x2"></a>X2 · My export "until 23:59:59" is incomplete

The SMGW only writes the meter reading for the last quarter-hour of a day at **00:00:01 on the following day**. If your range ends at 23:59:59, the last 15 minutes are missing – with a heat pump or wallbox running, that can be several kWh. Set the end to **00:15 on the following day** instead. The ready-made periods ("Yesterday", "Last month", …) do this automatically.

### <a id="x3"></a>X3 · Can I automate the export?

Yes, via the actions `smgw_han.export_readings` and `smgw_han.export_period`, e.g. in a monthly automation. Parameters and examples are in the README under [Data export for user-defined time ranges](https://github.com/TRON4R/ha-ppc-smgw-han/blob/main/README.en.md#-data-export-for-user-defined-time-ranges). Please don't call them in loops or at short intervals – each call opens a real session on the SMGW.

### <a id="x4"></a>X4 · How do I download the SMGW log – and why do I get a ZIP?

Via **Configure → "Export the SMGW log for a custom time range"**, details in the README under [Exporting the SMGW log](https://github.com/TRON4R/ha-ppc-smgw-han/blob/main/README.en.md#-exporting-the-smgw-log). The SMGW hands out at most 1000 entries per export and otherwise answers "Die Abfrage liefert … Datensätze zurück. Es sind nur 1000 erlaubt." ("the query returns … records; only 1000 are allowed"). If your range holds more, the integration splits it automatically. Each part is a separate original signed by the SMGW, and signed files cannot be merged without breaking the signature. That is why the parts come together in one ZIP file. CSV and Excel still contain all entries in one file each.

---

## Dashboard card

### <a id="k1"></a>K1 · The daily consumption history card shows no bars

- **ApexCharts Card installed?** The card needs the [ApexCharts Card](https://github.com/RomRider/apexcharts-card) from HACS. Reload the browser afterwards.
- **Entity IDs adjusted?** The IDs in the YAML file are examples and must be replaced with your own (**Settings → Devices & services → Entities**).
- **Only from the second fetch on:** Home Assistant uses a new sensor's first value as the starting point of its statistics. Bars therefore only appear after the second nightly fetch.

---

## Getting help

### <a id="h1"></a>H1 · Before you open an issue

With the following information I can usually help you right away instead of having to ask first. You're welcome to write in English or German.

1. **Latest version?** Check in HACS whether an update is available, and test with it.
2. **Versions:** version of the integration and of Home Assistant.
3. **Gateway:** manufacturer and type from the type plate, your metering point operator.
4. **Error message verbatim** or as a screenshot.
5. **What does the SMGW deliver?** The result of the export for "Yesterday" (which OBIS codes are in the "Rohdaten" sheet?) and – if possible – a screenshot of the direct login in a browser or with TRuDI. Instructions in [S1](#s1) and [E6](#e6).
6. **Diagnostics:** Settings → Devices & services → PPC SMGW HAN Daily Import → three-dot menu of the entry → "Download diagnostics". Username and password are removed automatically.
7. **Debug log:** three-dot menu of the entry → "Enable debug logging" → trigger an export for "Yesterday" (for problems with the nightly fetch, wait for the night instead) → "Disable debug logging". Home Assistant then offers the log for download. The relevant lines contain `custom_components.smgw_han`.

> [!CAUTION]
> **Never** post your HAN password – not even "just as an example". Look through logs and screenshots briefly before uploading them.
