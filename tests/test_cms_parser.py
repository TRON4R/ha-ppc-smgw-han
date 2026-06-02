"""Tests for cms_parser.parse_cms_readings (signed-CMS export parsing)."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import pytest

from custom_components.smgw_han.aggregation import build_daily_summary
from custom_components.smgw_han.cms_parser import (
    CmsParseError,
    parse_cms_readings,
)
from custom_components.smgw_han.const import OBIS_EXPORT, OBIS_IMPORT

FIXTURE = Path(__file__).parent / "fixtures" / "cms_sample.xml.cms"


def _readings():
    return parse_cms_readings(FIXTURE.read_bytes())


def test_extracts_only_mapped_obis():
    readings = _readings()
    # 3 import + 2 export; the third column (unmapped OBIS) is skipped.
    assert len(readings) == 5
    assert {r.obis_code for r in readings} == {OBIS_IMPORT, OBIS_EXPORT}


def test_values_units_and_naive_local_timestamps():
    readings = _readings()
    by_key = {(r.obis_code, r.timestamp): r for r in readings}

    # long64 * 10**scaler(-1) / 1000 (unit 30 = Wh) -> kWh.
    a = by_key[(OBIS_IMPORT, datetime(2026, 5, 15, 0, 0, 1))]
    assert a.value == 1000.0
    assert a.unit == "kWh"
    assert a.quality == "valid"
    assert a.timestamp.tzinfo is None  # naive Europe/Berlin

    assert by_key[(OBIS_IMPORT, datetime(2026, 5, 15, 5, 0, 1))].value == 1002.5
    assert by_key[(OBIS_IMPORT, datetime(2026, 5, 16, 0, 0, 1))].value == 1010.0
    assert by_key[(OBIS_EXPORT, datetime(2026, 5, 15, 0, 0, 1))].value == 500.0
    assert by_key[(OBIS_EXPORT, datetime(2026, 5, 16, 0, 0, 1))].value == 503.0


def test_utc_is_converted_to_berlin_local():
    # 2026-05-14T22:00:01Z is 2026-05-15 00:00:01 in CEST (+02:00).
    readings = _readings()
    assert any(
        r.timestamp == datetime(2026, 5, 15, 0, 0, 1) for r in readings
    )
    assert not any(
        r.timestamp == datetime(2026, 5, 14, 22, 0, 1) for r in readings
    )


def test_end_to_end_daily_summary():
    summary = build_daily_summary(_readings(), 5, 0)
    assert [s.day for s in summary] == [date(2026, 5, 15)]
    s = summary[0]
    assert s.consumption_go == 2.5
    assert s.consumption_standard == 7.5
    assert s.consumption_total == 10.0
    assert s.feedin_total == 3.0


def test_missing_xml_raises():
    with pytest.raises(CmsParseError):
        parse_cms_readings(b"no xml in here at all")
