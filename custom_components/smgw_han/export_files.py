"""Synchronous CSV / XLSX writers for the export service.

Every function here performs blocking file I/O (and, for XLSX, imports
openpyxl), so they MUST be called from an executor thread via
``hass.async_add_executor_job`` — never directly on the event loop.

- CSV  -> the raw 15-minute reading dump (semicolon-separated, UTF-8 BOM,
  opens cleanly in German Excel).
- XLSX -> a multi-sheet workbook (raw data + daily end values + tariff zones
  + definitions), mirroring the standalone ``smgw_tagesendwerte_to_excel``
  layout the owner already uses.
"""

from __future__ import annotations

import csv
import zipfile
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from .aggregation import DailySummary
from .const import OBIS_EXPORT, OBIS_IMPORT
from .log_parser import LogEntry
from .smgw_client import MeterReading

# Wide-format column headers for the raw-dump CSV.
RAW_HEADERS = [
    "Zeitstempel",
    "1.8.0 Bezug (kWh)",
    "2.8.0 Einspeisung (kWh)",
    "Qualität",
]


def _fmt_dt(dt: datetime | None) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S") if dt else ""


def _safe_text(value: str) -> str:
    """Neutralize spreadsheet formula injection in cell-leading text.

    User-defined zone names and gateway-supplied strings (unit, quality) end
    up in cells; Excel (and openpyxl itself) interprets leading '=', '+',
    '-' or '@' as a formula. The conventional apostrophe prefix forces the
    cell to stay text. Only applied to text fields, never to numbers.
    """
    return "'" + value if value[:1] in ("=", "+", "-", "@") else value


def _pivot_by_timestamp(
    readings: list[MeterReading],
) -> list[tuple[datetime, float | None, float | None, str]]:
    """Pivot long readings into one row per timestamp.

    Returns ``(timestamp, import_value, export_value, quality)`` tuples sorted
    by timestamp. A missing OBIS code for a timestamp yields ``None`` (an empty
    cell) instead of a fabricated zero — so meters without feed-in simply leave
    the 2.8.0 column blank.
    """
    rows: dict[datetime, dict[str, object]] = {}
    for r in readings:
        row = rows.setdefault(
            r.timestamp, {"import": None, "export": None, "quality": None}
        )
        if r.obis_code == OBIS_IMPORT:
            row["import"] = r.value
            row["quality"] = r.quality
        elif r.obis_code == OBIS_EXPORT:
            row["export"] = r.value
            if row["quality"] is None:
                row["quality"] = r.quality
    return [
        (ts, rows[ts]["import"], rows[ts]["export"], rows[ts]["quality"] or "")
        for ts in sorted(rows)
    ]


def write_readings_csv(path: Path, readings: list[MeterReading]) -> None:
    """Write the raw reading dump as a semicolon CSV (Excel-friendly).

    Wide format: one row per timestamp with separate 1.8.0 / 2.8.0 columns.
    """
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f, delimiter=";")
        writer.writerow(RAW_HEADERS)
        for ts, imp, exp, quality in _pivot_by_timestamp(readings):
            writer.writerow(
                [
                    _fmt_dt(ts),
                    f"{imp:.4f}" if imp is not None else "",
                    f"{exp:.4f}" if exp is not None else "",
                    _safe_text(quality),
                ]
            )


def write_xlsx(
    path: Path,
    readings: list[MeterReading],
    daily_summary: list[DailySummary],
    meta: dict[str, Any],
) -> None:
    """Write a multi-sheet workbook. Imports openpyxl lazily.

    ``meta["zones"]`` is the tariff-zone definition as ordered
    ``("HH:MM", name)`` pairs (the first at 00:00); it drives the dynamic
    columns of the "Tarifzonen" sheet and the "Definition" sheet.

    ``meta["zone_periods"]`` optionally lists every dated layout the exported
    range touches (``{"valid_from": "YYYY-MM-DD", "zones": [...]}``). A range
    crossing a scheduled zone change is split by more than one layout, so the
    workbook has to name all of them — otherwise half the rows are documented
    by windows they were not computed with.
    """
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font
    from openpyxl.utils import get_column_letter

    zones: list[tuple[str, str]] = [
        (t, name) for t, name in meta.get("zones", [])
    ]
    periods: list[dict[str, Any]] = meta.get("zone_periods") or [
        {"valid_from": "", "zones": zones}
    ]

    def _zone_windows(
        pairs: list[tuple[str, str]],
    ) -> tuple[list[str], dict[str, list[str]]]:
        """Names in order plus each name's daily windows ("06:00-12:00")."""
        names = list(dict.fromkeys(name for _t, name in pairs))
        ends = [*[t for t, _name in pairs[1:]], "24:00"]
        return names, {
            name: [
                f"{pairs[i][0]}-{ends[i]}"
                for i in range(len(pairs))
                if pairs[i][1] == name
            ]
            for name in names
        }

    inner_times = [t for t, _name in zones[1:]]
    # Columns span every layout in the range (plus anything the summaries
    # actually carry), so a zone that exists on only one side of a change
    # still gets its own column instead of silently dropping out.
    zone_names: list[str] = []
    period_windows: list[tuple[str, dict[str, list[str]]]] = []
    for period in periods:
        names, windows = _zone_windows(
            [(t, name) for t, name in period.get("zones", [])]
        )
        zone_names.extend(n for n in names if n not in zone_names)
        period_windows.append((period.get("valid_from", ""), windows))
    for summary in daily_summary:
        zone_names.extend(
            n for n in summary.zone_consumptions if n not in zone_names
        )

    wb = Workbook()

    # --- Sheet 1: raw readings (long: one row per reading) --------------
    raw = wb.active
    raw.title = "Rohdaten"
    raw.append(["Zeitstempel", "OBIS", "Wert (kWh)", "Einheit", "Qualitaet"])
    for r in readings:
        raw.append([_fmt_dt(r.timestamp), _safe_text(r.obis_code), r.value,
                    _safe_text(r.unit), _safe_text(r.quality)])
    for row in raw.iter_rows(min_row=2, min_col=3, max_col=3):
        for cell in row:
            cell.number_format = "0.0000"

    # --- Sheet 2: daily end values --------------------------------------
    ende = wb.create_sheet("Tagesendwerte")
    ende.append([
        "Datum", "verwendeter Zeitstempel",
        "1.8.0 Bezug (kWh)", "2.8.0 Einspeisung (kWh)",
    ])
    for s in daily_summary:
        ende.append([
            s.day.isoformat(), _fmt_dt(s.end_timestamp),
            s.import_end, s.export_end,
        ])
    for row in ende.iter_rows(min_row=2, min_col=3, max_col=4):
        for cell in row:
            cell.number_format = "0.0000"

    # --- Sheet 3: tariff zones (dynamic columns from the zone config) ----
    tarif = wb.create_sheet("Tarifzonen")
    tarif.append([
        "Datum",
        "Bezug 00:00 (kWh)",
        *[f"Bezug {t} (kWh)" for t in inner_times],
        "Bezug Folgetag 00:00 (kWh)",
        *[f"Verbrauch {name} (kWh)" for name in zone_names],
        "Gesamtverbrauch 00:00-24:00 (kWh)",
        "Einspeisung gesamt (kWh)",
    ])
    for s in daily_summary:
        tarif.append([
            s.day.isoformat(),
            s.import_start,
            *[s.import_switches.get(t) for t in inner_times],
            s.import_end,
            *[s.zone_consumptions.get(name) for name in zone_names],
            s.consumption_total,
            s.feedin_total,
        ])
    for row in tarif.iter_rows(min_row=2, min_col=2, max_col=tarif.max_column):
        for cell in row:
            cell.number_format = "0.0000"

    # --- Sheet 4: definitions (layout mirrors the standalone v6 script) --
    info = wb.create_sheet("Definition")
    bold = Font(bold=True)
    info["A1"] = "Definitionen"
    info["A1"].font = bold
    info["A3"] = "Export"
    info["A3"].font = bold
    info["A4"] = (
        f"Zähler: {meta.get('meter_id', '')}    "
        f"Zeitraum: {meta.get('from', '')} bis {meta.get('to', '')}"
    )
    info["A6"] = "Tagesendwert"
    info["A6"].font = bold
    info["A7"] = (
        "Der Tagesendwert eines Tages D ist der erste vorhandene kumulative "
        "Zählerstand am Folgetag D+1 um 00:00 Uhr lokaler Zeit "
        "(Europe/Berlin)."
    )
    info["A9"] = "Tarifzonen (konfiguriert, lokale Zeit des Kalendertags D)"
    info["A9"].font = bold
    row = 10
    for valid_from, windows in period_windows:
        if len(period_windows) > 1:
            info[f"A{row}"] = _safe_text(f"ab {valid_from}:")
            info[f"A{row}"].font = bold
            row += 1
        for name, segments in windows.items():
            info[f"A{row}"] = _safe_text(f"{name}: {' + '.join(segments)}")
            row += 1
    if len(period_windows) > 1:
        info[f"A{row}"] = (
            "Der Zeitraum umfasst mehrere Tarifzonen-Schemata. Jeder Tag "
            "wurde mit dem Schema berechnet, das an diesem Tag galt."
        )
        row += 1
    row += 1
    info[f"A{row}"] = "Berechnung Bezug"
    info[f"A{row}"].font = bold
    row += 1
    info[f"A{row}"] = (
        "Verbrauch eines Zeitfensters = Bezug(Fensterende) - "
        "Bezug(Fensterbeginn); 24:00 entspricht 00:00 des Folgetags D+1."
    )
    row += 1
    info[f"A{row}"] = (
        "Verbrauch einer Tarifzone = Summe der Verbräuche ihrer Zeitfenster."
    )
    row += 1
    info[f"A{row}"] = "Gesamtverbrauch = Bezug(Folgetag 00:00) - Bezug(00:00)"
    row += 2
    info[f"A{row}"] = "Wichtiger Hinweis"
    info[f"A{row}"].font = bold
    row += 1
    info[f"A{row}"] = (
        "Gesamtverbrauch wird direkt aus den beiden Tagesrand-Zählerständen "
        "berechnet. Er ist damit rechnerisch identisch zur Summe der "
        "Tarifzonen-Verbräuche, sofern alle Messpunkte vorhanden sind."
    )
    row += 1
    info[f"A{row}"] = (
        "Falls für einen Tag ein benötigter Messpunkt fehlt (00:00 oder "
        "eine Umschaltzeit), bleibt die betroffene berechnete Spalte leer."
    )
    info.column_dimensions["A"].width = 140

    # --- header styling + autosize for all data sheets ------------------
    for ws in (raw, ende, tarif):
        for cell in ws[1]:
            cell.font = Font(bold=True)
            cell.alignment = Alignment(
                horizontal="center", vertical="center", wrap_text=True
            )
        for col_idx, column_cells in enumerate(ws.columns, start=1):
            max_len = max(
                len("" if c.value is None else str(c.value))
                for c in column_cells
            )
            ws.column_dimensions[get_column_letter(col_idx)].width = min(
                max_len + 2, 40
            )
        ws.freeze_panes = "A2"
        ws.auto_filter.ref = ws.dimensions

    wb.save(path)


# ---------------------------------------------------------------------------
# SMGW consumer log
# ---------------------------------------------------------------------------

# Same columns in CSV and XLSX; the long message text goes last so the short
# columns stay readable side by side.
LOG_HEADERS = [
    "Zeitpunkt (Ortszeit)",
    "Zeitpunkt (UTC)",
    "Level",
    "Status",
    "ID",
    "Lfd. Nr.",
    "Meldungstext",
]
_LOG_LEVELS = ("INFO", "WARNING", "ERROR", "FATAL")


def write_log_csv(path: Path, entries: list[LogEntry]) -> None:
    """Write the log as a plain semicolon CSV, one entry per row, oldest first."""
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f, delimiter=";")
        writer.writerow(LOG_HEADERS)
        for e in entries:
            writer.writerow(
                [
                    _fmt_dt(e.timestamp_local),
                    _fmt_dt(e.timestamp_utc.replace(tzinfo=None)),
                    _safe_text(e.level),
                    _safe_text(e.outcome_label),
                    _safe_text(e.event_id),
                    e.record_number,
                    _safe_text(e.message),
                ]
            )


def write_cms_zip(path: Path, members: list[tuple[str, bytes]]) -> None:
    """Bundle several signed CMS files unchanged into one ZIP archive."""
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, content in members:
            zf.writestr(name, content)


def _is_alert(entry: LogEntry) -> bool:
    return entry.level in ("ERROR", "FATAL") or entry.outcome == "FAILURE"


def write_log_xlsx(
    path: Path, entries: list[LogEntry], meta: dict[str, Any]
) -> None:
    """Write the log workbook: Logbuch, Übersicht, Info. Imports openpyxl lazily.

    ``meta`` keys: ``gateway_id``, ``from``, ``to``, ``created`` (strings),
    ``parts`` (``(from, to, entry_count)`` per signed file), ``gaps``
    (missing running-number ranges), ``refused`` (``(from, to, count)`` the
    gateway named when refusing a range) and ``shortfalls`` (refused ranges
    whose entries did not all arrive).
    """
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill

    bold = Font(bold=True)
    alert_fill = PatternFill("solid", start_color="FFC7CE")
    warning_fill = PatternFill("solid", start_color="FFEB9C")
    datetime_format = "yyyy-mm-dd hh:mm:ss"

    def _event_id(value: str) -> int | str:
        return int(value) if value.isdigit() else _safe_text(value)

    wb = Workbook()

    # --- Sheet 1: the log itself ------------------------------------------
    log = wb.active
    log.title = "Logbuch"
    log.append(LOG_HEADERS)
    for cell in log[1]:
        cell.font = bold
    for e in entries:
        log.append(
            [
                e.timestamp_local,
                e.timestamp_utc.replace(tzinfo=None),
                _safe_text(e.level),
                _safe_text(e.outcome_label),
                _event_id(e.event_id),
                e.record_number,
                _safe_text(e.message),
            ]
        )
        row = log.max_row
        log.cell(row=row, column=1).number_format = datetime_format
        log.cell(row=row, column=2).number_format = datetime_format
        if _is_alert(e):
            fill = alert_fill
        elif e.level == "WARNING":
            fill = warning_fill
        else:
            continue
        for cell in log[row]:
            cell.fill = fill
    for column, width in zip("ABCDEFG", (20, 20, 10, 15, 8, 10, 120)):
        log.column_dimensions[column].width = width
    log.freeze_panes = "A2"
    log.auto_filter.ref = log.dimensions

    # --- Sheet 2: where the entries are and what they are ------------------
    summary = wb.create_sheet("Übersicht")
    summary.append(["Einträge je Monat (Ortszeit)"])
    summary.cell(row=1, column=1).font = bold
    summary.append(["Monat", *_LOG_LEVELS, "Gesamt", "davon fehlgeschlagen"])
    for cell in summary[2]:
        cell.font = bold
    per_month: dict[str, Counter[str]] = defaultdict(Counter)
    for e in entries:
        counts = per_month[e.timestamp_local.strftime("%Y-%m")]
        counts[e.level] += 1
        counts["_total"] += 1
        if e.outcome == "FAILURE":
            counts["_failed"] += 1
    for month in sorted(per_month):
        counts = per_month[month]
        summary.append(
            [
                month,
                *[counts[level] for level in _LOG_LEVELS],
                counts["_total"],
                counts["_failed"],
            ]
        )

    summary.append([])
    summary.append(["Einträge je Meldungstyp"])
    summary.cell(row=summary.max_row, column=1).font = bold
    summary.append(
        ["ID", "Anzahl", "Erster Eintrag", "Letzter Eintrag", "Level",
         "Meldung (Vorlage)"]
    )
    for cell in summary[summary.max_row]:
        cell.font = bold
    by_type: dict[str, list[LogEntry]] = defaultdict(list)
    for e in entries:
        by_type[e.event_id].append(e)
    for event_id, group in sorted(
        by_type.items(), key=lambda item: (-len(item[1]), item[0])
    ):
        summary.append(
            [
                _event_id(event_id),
                len(group),
                group[0].timestamp_local,
                group[-1].timestamp_local,
                _safe_text(", ".join(sorted({e.level for e in group}))),
                _safe_text(group[0].template or group[0].message),
            ]
        )
        row = summary.max_row
        summary.cell(row=row, column=3).number_format = datetime_format
        summary.cell(row=row, column=4).number_format = datetime_format
    for column, width in zip("ABCDEFGH", (12, 10, 20, 20, 16, 120, 10, 22)):
        summary.column_dimensions[column].width = width

    # --- Sheet 3: provenance and completeness -------------------------------
    info = wb.create_sheet("Info")
    lines: list[tuple[str, bool]] = [
        ("SMGW-Logdaten", True),
        (f"Gateway: {meta.get('gateway_id', '')}", False),
        (
            f"Zeitraum: {meta.get('from', '')} bis {meta.get('to', '')} "
            "(Ortszeit)",
            False,
        ),
        (f"Erstellt: {meta.get('created', '')}", False),
        (f"Einträge: {len(entries)}", False),
        ("", False),
        ("Abruf", True),
    ]
    parts = meta.get("parts", [])
    if len(parts) <= 1:
        lines.append(("Das SMGW hat den Zeitraum in einem Abruf geliefert.", False))
    else:
        lines.append(
            (
                "Das SMGW gibt höchstens 1000 Einträge pro Abruf heraus. Der "
                f"Zeitraum wurde deshalb in {len(parts)} Teilabrufe zerlegt; "
                "jeder Teil ist eine eigene, einzeln signierte CMS-Datei im "
                "ZIP-Archiv.",
                False,
            )
        )
        lines += [
            (f"Teil {index}: {start} bis {end}: {count} Einträge", False)
            for index, (start, end, count) in enumerate(parts, 1)
        ]

    lines += [("", False), ("Vollständigkeitsprüfung", True)]
    gaps = meta.get("gaps", [])
    if entries and not gaps:
        lines.append(
            (
                f"Laufende Nummern {entries[0].record_number} bis "
                f"{entries[-1].record_number}: lückenlos.",
                False,
            )
        )
    for first, last in gaps:
        missing = f"{first}" if first == last else f"{first} bis {last}"
        lines.append((f"Lücke in den laufenden Nummern: {missing}", False))
    if gaps:
        lines.append(
            (
                "Eine Lücke bedeutet, dass im Ergebnis Einträge fehlen können. "
                "Bitte den betroffenen Zeitraum erneut exportieren.",
                False,
            )
        )
    shortfalls = meta.get("shortfalls", [])
    if meta.get("refused") and not shortfalls:
        lines.append(
            (
                "Für jeden Bereich, den das SMGW als zu groß abgelehnt hat, "
                "sind mindestens so viele Einträge angekommen, wie das SMGW "
                "dort gemeldet hat.",
                False,
            )
        )
    lines += [
        (
            f"{start} bis {end}: vom SMGW gemeldet {expected} Einträge, "
            f"angekommen {found}.",
            False,
        )
        for start, end, expected, found in shortfalls
    ]

    lines += [
        ("", False),
        ("Hinweise", True),
        (
            "Zeitpunkt (UTC) ist der vom SMGW signierte Zeitstempel. Zeitpunkt "
            "(Ortszeit) ist derselbe Zeitpunkt in deutscher Zeit (MEZ/MESZ), "
            "wie ihn die SMGW-Oberfläche zeigt.",
            False,
        ),
        (
            "Zeilen mit Level WARNING sind gelb hinterlegt, mit ERROR oder "
            "FATAL bzw. Status „fehlgeschlagen“ rot.",
            False,
        ),
        (
            "Signiert sind nur die CMS-Dateien. CSV und Excel sind daraus "
            "abgeleitet.",
            False,
        ),
    ]
    for row, (text, is_heading) in enumerate(lines, 1):
        cell = info.cell(row=row, column=1, value=_safe_text(text))
        if is_heading:
            cell.font = bold
    info.column_dimensions["A"].width = 140

    wb.save(path)
