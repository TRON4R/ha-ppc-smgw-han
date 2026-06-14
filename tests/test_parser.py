"""Tests for SmgwClient._parse_meter_values_table (pure HTML parsing)."""

from __future__ import annotations

from datetime import datetime

import pytest

from custom_components.smgw_han.const import OBIS_EXPORT, OBIS_IMPORT
from custom_components.smgw_han.smgw_client import SmgwClient, SmgwParseError


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


def test_istvalide_2_marks_invalid_and_is_inherited(fixture_text):
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
    assert imp.quality == "invalid"  # istvalide=2
    assert exp.quality == "invalid"  # inherited onto the export line


def test_istvalide_3_marks_not_present():
    # ist valide = 3 -> "not present" (a carried-forward gap placeholder); the
    # reading is still emitted and the status inherits onto the export line.
    html = (
        '<table id="metervalue">'
        '<tr id="table_metervalues_line1">'
        '<td id="table_metervalues_col_timestamp">2026-05-15 14:00:00</td>'
        '<td id="table_metervalues_col_obis">1-0:1.8.0</td>'
        '<td id="table_metervalues_col_wert">1005.0</td>'
        '<td id="table_metervalues_col_einheit">kWh</td>'
        '<td id="table_metervalues_col_istvalide">3</td>'
        "</tr>"
        '<tr id="table_metervalues_line2">'
        '<td id="table_metervalues_col_timestamp"></td>'
        '<td id="table_metervalues_col_obis">1-0:2.8.0</td>'
        '<td id="table_metervalues_col_wert">501.0</td>'
        '<td id="table_metervalues_col_einheit">kWh</td>'
        '<td id="table_metervalues_col_istvalide"></td>'
        "</tr></table>"
    )
    readings = _client()._parse_meter_values_table(html)
    assert len(readings) == 2
    assert all(r.quality == "not_present" for r in readings)


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


def test_missing_table_raises_parse_error():
    # No values table at all = broken/unexpected HTML, a real parse error -
    # NOT "no data for the day" (which is signalled by an empty list below).
    with pytest.raises(SmgwParseError):
        _client()._parse_meter_values_table("<html><body>x</body></html>")


def test_table_without_rows_returns_empty():
    # Table present but empty = a genuine "no readings for this day"; the empty
    # list lets the fetch raise SmgwNoDataError (-> self-healing repair issue).
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
