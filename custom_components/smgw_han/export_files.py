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
from datetime import datetime
from pathlib import Path
from typing import Any

from .aggregation import DailySummary
from .smgw_client import MeterReading


def _fmt_dt(dt: datetime | None) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S") if dt else ""


def write_readings_csv(path: Path, readings: list[MeterReading]) -> None:
    """Write the raw reading dump as a semicolon CSV (Excel-friendly)."""
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f, delimiter=";")
        writer.writerow(["Zeitstempel", "OBIS", "Wert", "Einheit", "Qualitaet"])
        for r in readings:
            writer.writerow(
                [_fmt_dt(r.timestamp), r.obis_code, f"{r.value:.4f}",
                 r.unit, r.quality]
            )


def write_xlsx(
    path: Path,
    readings: list[MeterReading],
    daily_summary: list[DailySummary],
    meta: dict[str, Any],
) -> None:
    """Write a multi-sheet workbook. Imports openpyxl lazily."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font
    from openpyxl.utils import get_column_letter

    tariff_label = meta.get("tariff_switch", "05:00")

    wb = Workbook()

    # --- Sheet 1: raw readings ------------------------------------------
    raw = wb.active
    raw.title = "Rohdaten"
    raw.append(["Zeitstempel", "OBIS", "Wert (kWh)", "Einheit", "Qualitaet"])
    for r in readings:
        raw.append([_fmt_dt(r.timestamp), r.obis_code, r.value, r.unit,
                    r.quality])
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

    # --- Sheet 3: tariff zones ------------------------------------------
    tarif = wb.create_sheet("Tarifzonen")
    tarif.append([
        "Datum",
        "Bezug 00:00 (kWh)",
        f"Bezug {tariff_label} (kWh)",
        "Bezug Folgetag 00:00 (kWh)",
        f"Go-Verbrauch 00:00-{tariff_label} (kWh)",
        f"Standard-Verbrauch {tariff_label}-24:00 (kWh)",
        "Gesamtverbrauch 00:00-24:00 (kWh)",
        "Einspeisung gesamt (kWh)",
    ])
    for s in daily_summary:
        tarif.append([
            s.day.isoformat(), s.import_start, s.import_switch, s.import_end,
            s.consumption_go, s.consumption_standard, s.consumption_total,
            s.feedin_total,
        ])
    for row in tarif.iter_rows(min_row=2, min_col=2, max_col=8):
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
    info["A9"] = "Tarifzonen für Intelligent Octopus Go"
    info["A9"].font = bold
    info["A10"] = (
        f"Go-Zeit = 00:00 bis {tariff_label} lokaler Zeit des Kalendertags D."
    )
    info["A11"] = (
        f"Standardzeit = {tariff_label} bis 00:00 lokaler Zeit des "
        "Folgetags D+1."
    )
    info["A13"] = "Berechnung Bezug"
    info["A13"].font = bold
    info["A14"] = f"Go-Verbrauch = Bezug({tariff_label}) - Bezug(00:00)"
    info["A15"] = (
        f"Standard-Verbrauch = Bezug(Folgetag 00:00) - Bezug({tariff_label})"
    )
    info["A16"] = "Gesamtverbrauch = Bezug(Folgetag 00:00) - Bezug(00:00)"
    info["A18"] = "Wichtiger Hinweis"
    info["A18"].font = bold
    info["A19"] = (
        "Gesamtverbrauch wird direkt aus den beiden Tagesrand-Zählerständen "
        "berechnet. Er ist damit rechnerisch identisch zu Go-Verbrauch + "
        "Standard-Verbrauch, sofern alle drei Messpunkte vorhanden sind."
    )
    info["A20"] = (
        "Falls für einen Tag ein benötigter Messpunkt fehlt (00:00 oder "
        "Tarifwechsel), bleibt die berechnete Spalte leer."
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
