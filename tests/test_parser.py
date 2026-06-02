"""Tests for SmgwClient._parse_meter_values_table (pure HTML parsing)."""

from __future__ import annotations

from datetime import datetime

from custom_components.smgw_han.const import OBIS_EXPORT, OBIS_IMPORT
from custom_components.smgw_han.smgw_client import SmgwClient


def _client() -> SmgwClient:
    # The constructor performs no network I/O, so a throwaway instance is the
    # cheapest way to reach the (self-free) parsing method.
    return SmgwClient("https://smgw.local/cgi-bin/hanservice.cgi", "user", "pw")


def test_parses_import_export_pairs(fixture_text):
    readings = _client()._parse_meter_values_table(
        fixture_text("showmetervalues_single.html")
    )
    assert len(readings) == 8
    imports = [r for r in readings if r.obis_code == OBIS_IMPORT]
    exports = [r for r in readings if r.obis_code == OBIS_EXPORT]
    assert len(imports) == 4
    assert len(exports) == 4


def test_export_line_inherits_timestamp_and_valid_status(fixture_text):
    readings = _client()._parse_meter_values_table(
        fixture_text("showmetervalues_single.html")
    )
    # line2 (export) has an empty timestamp + empty istvalide cell, so it must
    # inherit both from the line1 (import) row above it.
    exp_c = next(
        r
        for r in readings
        if r.obis_code == OBIS_EXPORT
        and r.timestamp == datetime(2026, 5, 16, 0, 0, 1)
    )
    assert exp_c.value == 503.0
    assert exp_c.quality == "valid"


def test_istvalide_zero_marks_invalid_and_is_inherited(fixture_text):
    readings = _client()._parse_meter_values_table(
        fixture_text("showmetervalues_single.html")
    )
    ts = datetime(2026, 5, 15, 12, 0, 1)
    imp = next(
        r for r in readings if r.obis_code == OBIS_IMPORT and r.timestamp == ts
    )
    exp = next(
        r for r in readings if r.obis_code == OBIS_EXPORT and r.timestamp == ts
    )
    assert imp.quality == "invalid"  # istvalide=0
    assert exp.quality == "invalid"  # inherited onto the export line


def test_valid_status_resets_on_following_pair(fixture_text):
    readings = _client()._parse_meter_values_table(
        fixture_text("showmetervalues_single.html")
    )
    a = next(
        r
        for r in readings
        if r.obis_code == OBIS_IMPORT
        and r.timestamp == datetime(2026, 5, 15, 0, 0, 1)
    )
    assert a.value == 1000.0
    assert a.quality == "valid"


def test_missing_table_returns_empty():
    assert _client()._parse_meter_values_table("<html><body>x</body></html>") == []


def test_table_without_rows_returns_empty():
    assert _client()._parse_meter_values_table('<table id="metervalue"></table>') == []


def test_non_target_obis_rows_are_skipped():
    html = (
        '<table id="metervalue">'
        '<tr id="table_metervalues_line1">'
        '<td id="table_metervalues_col_timestamp">2026-05-15 00:00:01</td>'
        '<td id="table_metervalues_col_obis">1-0:1.7.0</td>'
        '<td id="table_metervalues_col_wert">5.0</td>'
        '<td id="table_metervalues_col_einheit">kW</td>'
        '<td id="table_metervalues_col_istvalide">1</td>'
        "</tr></table>"
    )
    assert _client()._parse_meter_values_table(html) == []
