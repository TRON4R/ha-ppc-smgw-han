"""Tests for SmgwClient._process_readings (A-B-C tariff calculation)."""

from __future__ import annotations

from datetime import date, datetime

import pytest

from custom_components.smgw_han.const import OBIS_IMPORT
from custom_components.smgw_han.smgw_client import (
    MeterReading,
    SmgwClient,
    SmgwNoDataError,
)


def _client() -> SmgwClient:
    return SmgwClient("https://smgw.local/cgi-bin/hanservice.cgi", "user", "pw")


def _mr(ts: datetime, obis: str, value: float) -> MeterReading:
    return MeterReading(
        timestamp=ts, obis_code=obis, value=value, unit="kWh", quality="valid"
    )


def test_happy_path_from_fixture(fixture_text):
    client = _client()
    readings = client._parse_meter_values_table(
        fixture_text("showmetervalues_single.html")
    )
    daily = client._process_readings(date(2026, 5, 15), readings, 5, 0)
    assert daily.daily_import_go == 2.5  # 1002.5 - 1000.0
    assert daily.daily_import_standard == 7.5  # 1010.0 - 1002.5
    assert daily.daily_import_total == 10.0  # 1010.0 - 1000.0
    assert daily.daily_export_total == 3.0  # 503.0 - 500.0
    assert daily.import_midnight == 1000.0


def test_export_only_meter_assumes_zero_consumption(fixture_text):
    client = _client()
    readings = client._parse_meter_values_table(
        fixture_text("showmetervalues_export_only.html")
    )
    daily = client._process_readings(date(2026, 5, 15), readings, 5, 0)
    assert daily.daily_import_total == 0.0
    assert daily.daily_import_go == 0.0
    assert daily.daily_export_total == 3.0


def test_import_only_assumes_zero_feedin():
    readings = [
        _mr(datetime(2026, 5, 15, 0, 0, 1), OBIS_IMPORT, 1000.0),
        _mr(datetime(2026, 5, 15, 5, 0, 1), OBIS_IMPORT, 1002.5),
        _mr(datetime(2026, 5, 16, 0, 0, 1), OBIS_IMPORT, 1010.0),
    ]
    daily = _client()._process_readings(date(2026, 5, 15), readings, 5, 0)
    assert daily.daily_export_total == 0.0
    assert daily.daily_import_total == 10.0


def test_missing_required_import_reading_raises():
    # Import present at 00:00 and the tariff switch, but the mandatory
    # next-day-midnight closing reading (C) is absent. This is an incomplete
    # day (no usable data), not broken HTML -> SmgwNoDataError.
    readings = [
        _mr(datetime(2026, 5, 15, 0, 0, 1), OBIS_IMPORT, 1000.0),
        _mr(datetime(2026, 5, 15, 5, 0, 1), OBIS_IMPORT, 1002.5),
    ]
    with pytest.raises(SmgwNoDataError):
        _client()._process_readings(date(2026, 5, 15), readings, 5, 0)


def test_no_readings_at_all_raises():
    with pytest.raises(SmgwNoDataError):
        _client()._process_readings(date(2026, 5, 15), [], 5, 0)


def test_custom_tariff_switch_time():
    readings = [
        _mr(datetime(2026, 5, 15, 0, 0, 1), OBIS_IMPORT, 1000.0),
        _mr(datetime(2026, 5, 15, 7, 30, 1), OBIS_IMPORT, 1004.0),
        _mr(datetime(2026, 5, 16, 0, 0, 1), OBIS_IMPORT, 1010.0),
    ]
    daily = _client()._process_readings(date(2026, 5, 15), readings, 7, 30)
    assert daily.daily_import_go == 4.0  # up to 07:30
    assert daily.daily_import_standard == 6.0  # after 07:30
