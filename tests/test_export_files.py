"""Tests for the CSV / XLSX export writers."""

from __future__ import annotations

from datetime import datetime

from openpyxl import load_workbook

from custom_components.smgw_han.aggregation import build_daily_summary
from custom_components.smgw_han.const import OBIS_EXPORT, OBIS_IMPORT
from custom_components.smgw_han.export_files import (
    _pivot_by_timestamp,
    write_readings_csv,
    write_xlsx,
)
from custom_components.smgw_han.smgw_client import MeterReading


def _imp(ts: datetime, value: float) -> MeterReading:
    return MeterReading(ts, OBIS_IMPORT, value, "kWh", "valid")


def _exp(ts: datetime, value: float) -> MeterReading:
    return MeterReading(ts, OBIS_EXPORT, value, "kWh", "valid")


# A 05:00 import reading without a matching export reading exercises the
# "missing OBIS code -> empty cell, never a fabricated zero" behaviour.
READINGS = [
    _imp(datetime(2026, 5, 15, 0, 0, 1), 1000.0),
    _exp(datetime(2026, 5, 15, 0, 0, 1), 500.0),
    _imp(datetime(2026, 5, 15, 5, 0, 1), 1002.5),
    _imp(datetime(2026, 5, 16, 0, 0, 1), 1010.0),
    _exp(datetime(2026, 5, 16, 0, 0, 1), 503.0),
]


def test_pivot_leaves_missing_export_as_none():
    rows = {ts: (imp, exp, q) for ts, imp, exp, q in _pivot_by_timestamp(READINGS)}
    imp, exp, _q = rows[datetime(2026, 5, 15, 5, 0, 1)]
    assert imp == 1002.5
    assert exp is None  # -> rendered as an empty cell, not 0


def test_write_csv_wide_format(tmp_path):
    path = tmp_path / "out.csv"
    write_readings_csv(path, READINGS)
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    assert lines[0] == (
        "Zeitstempel;1.8.0 Bezug (kWh);2.8.0 Einspeisung (kWh);Qualität"
    )
    row = next(line for line in lines if line.startswith("2026-05-15 05:00:01"))
    # Columns: timestamp;import;export;quality -> export must be blank.
    assert row.split(";")[2] == ""


def test_write_xlsx_sheets_and_rowcount(tmp_path):
    path = tmp_path / "out.xlsx"
    summary = build_daily_summary(READINGS, 5, 0)
    meta = {
        "meter_id": "1lgz0072999211",
        "from": "2026-05-15 00:00:00",
        "to": "2026-05-16 00:15:00",
        "tariff_switch": "05:00",
    }
    write_xlsx(path, READINGS, summary, meta)

    wb = load_workbook(path)
    assert set(wb.sheetnames) == {
        "Rohdaten",
        "Tagesendwerte",
        "Tarifzonen",
        "Definition",
    }
    # Rohdaten is the long dump: one header row + one row per reading.
    assert wb["Rohdaten"].max_row == 1 + len(READINGS)
    # Tagesendwerte: header + one row per summarized day.
    assert wb["Tagesendwerte"].max_row == 1 + len(summary)
