"""Tests for SmgwClient._process_readings (N-zone tariff calculation)."""

from __future__ import annotations

import logging
from datetime import date, datetime, time

import pytest

from custom_components.smgw_han.const import OBIS_IMPORT
from custom_components.smgw_han.smgw_client import (
    MeterReading,
    SmgwClient,
    SmgwNoDataError,
)

# Two-zone definition equivalent to the historical Octopus-Go default.
GO_ZONES = [(time(0, 0), "Go"), (time(5, 0), "Standard")]

# Octopus Heat: 3 price zones spread over 7 daily segments (6 switch times).
HEAT_ZONES = [
    (time(0, 0), "Standard"),
    (time(2, 0), "Niedrig"),
    (time(6, 0), "Standard"),
    (time(12, 0), "Niedrig"),
    (time(16, 0), "Standard"),
    (time(18, 0), "Hoch"),
    (time(21, 0), "Standard"),
]


def _client() -> SmgwClient:
    return SmgwClient("https://smgw.local/cgi-bin/hanservice.cgi", "user", "pw")


def _mr(
    ts: datetime, obis: str, value: float, quality: str = "valid"
) -> MeterReading:
    return MeterReading(
        timestamp=ts, obis_code=obis, value=value, unit="kWh", quality=quality
    )


def test_happy_path_from_fixture(fixture_text):
    client = _client()
    readings = client._parse_meter_values_table(
        fixture_text("showmetervalues_single.html")
    )
    daily = client._process_readings(date(2026, 5, 15), readings, GO_ZONES)
    assert daily.zone_totals["Go"] == 2.5  # 1002.5 - 1000.0
    assert daily.zone_totals["Standard"] == 7.5  # 1010.0 - 1002.5
    assert daily.daily_import_total == 10.0  # 1010.0 - 1000.0
    assert daily.daily_export_total == 3.0  # 503.0 - 500.0
    assert daily.import_boundaries == [1000.0, 1002.5, 1010.0]


def test_heat_zones_sum_same_named_segments():
    # 7 segments -> 3 distinct zones; "Niedrig" and "Standard" each span
    # several windows and must be summed per zone name.
    boundary_values = [
        (time(0, 0), 1000.0),
        (time(2, 0), 1001.0),  # Standard 00-02: 1.0
        (time(6, 0), 1003.0),  # Niedrig  02-06: 2.0
        (time(12, 0), 1006.0),  # Standard 06-12: 3.0
        (time(16, 0), 1010.0),  # Niedrig  12-16: 4.0
        (time(18, 0), 1015.0),  # Standard 16-18: 5.0
        (time(21, 0), 1021.0),  # Hoch     18-21: 6.0
    ]
    readings = [
        _mr(datetime(2026, 5, 15, t.hour, t.minute, 1), OBIS_IMPORT, v)
        for t, v in boundary_values
    ]
    readings.append(
        _mr(datetime(2026, 5, 16, 0, 0, 1), OBIS_IMPORT, 1028.0)
    )  # Standard 21-24: 7.0

    daily = _client()._process_readings(date(2026, 5, 15), readings, HEAT_ZONES)

    # Zone order = first appearance in the config -> slot indices stay stable.
    assert list(daily.zone_totals) == ["Standard", "Niedrig", "Hoch"]
    assert daily.zone_totals["Standard"] == 16.0  # 1 + 3 + 5 + 7
    assert daily.zone_totals["Niedrig"] == 6.0  # 2 + 4
    assert daily.zone_totals["Hoch"] == 6.0
    assert daily.daily_import_total == 28.0  # 1028 - 1000
    # Reconciliation: the zone totals must add up to the daily total.
    assert sum(daily.zone_totals.values()) == daily.daily_import_total
    assert daily.import_boundaries[0] == 1000.0
    assert daily.import_boundaries[-1] == 1028.0
    assert len(daily.import_boundaries) == len(HEAT_ZONES) + 1


def test_export_only_meter_assumes_zero_consumption(fixture_text):
    client = _client()
    readings = client._parse_meter_values_table(
        fixture_text("showmetervalues_export_only.html")
    )
    daily = client._process_readings(date(2026, 5, 15), readings, GO_ZONES)
    assert daily.daily_import_total == 0.0
    assert daily.zone_totals == {"Go": 0.0, "Standard": 0.0}
    assert daily.daily_export_total == 3.0


def test_import_only_assumes_zero_feedin():
    readings = [
        _mr(datetime(2026, 5, 15, 0, 0, 1), OBIS_IMPORT, 1000.0),
        _mr(datetime(2026, 5, 15, 5, 0, 1), OBIS_IMPORT, 1002.5),
        _mr(datetime(2026, 5, 16, 0, 0, 1), OBIS_IMPORT, 1010.0),
    ]
    daily = _client()._process_readings(date(2026, 5, 15), readings, GO_ZONES)
    assert daily.daily_export_total == 0.0
    assert daily.daily_import_total == 10.0


def test_missing_required_import_reading_raises():
    # Import present at 00:00 and the tariff switch, but the mandatory
    # next-day-midnight closing reading is absent. This is an incomplete
    # day (no usable data), not broken HTML -> SmgwNoDataError.
    readings = [
        _mr(datetime(2026, 5, 15, 0, 0, 1), OBIS_IMPORT, 1000.0),
        _mr(datetime(2026, 5, 15, 5, 0, 1), OBIS_IMPORT, 1002.5),
    ]
    with pytest.raises(SmgwNoDataError):
        _client()._process_readings(date(2026, 5, 15), readings, GO_ZONES)


def test_missing_inner_boundary_raises_and_names_it():
    # A Heat-style config where one inner switch reading (12:00) is missing:
    # the error must name the missing boundary.
    readings = [
        _mr(datetime(2026, 5, 15, 0, 0, 1), OBIS_IMPORT, 1000.0),
        _mr(datetime(2026, 5, 15, 2, 0, 1), OBIS_IMPORT, 1001.0),
        _mr(datetime(2026, 5, 15, 6, 0, 1), OBIS_IMPORT, 1003.0),
        # 12:00 missing
        _mr(datetime(2026, 5, 15, 16, 0, 1), OBIS_IMPORT, 1010.0),
        _mr(datetime(2026, 5, 15, 18, 0, 1), OBIS_IMPORT, 1015.0),
        _mr(datetime(2026, 5, 15, 21, 0, 1), OBIS_IMPORT, 1021.0),
        _mr(datetime(2026, 5, 16, 0, 0, 1), OBIS_IMPORT, 1028.0),
    ]
    with pytest.raises(SmgwNoDataError) as ei:
        _client()._process_readings(date(2026, 5, 15), readings, HEAT_ZONES)
    assert "12:00" in str(ei.value)


def test_no_readings_at_all_raises():
    with pytest.raises(SmgwNoDataError):
        _client()._process_readings(date(2026, 5, 15), [], GO_ZONES)


def test_invalid_anchor_is_logged_but_value_still_used(caplog):
    # The next-day-midnight closing reading carries the gateway's "invalid"
    # flag. Observe-first: a WARNING is logged, but the value is still used, so
    # the daily total is unchanged (no behaviour change, no NoData).
    readings = [
        _mr(datetime(2026, 5, 15, 0, 0, 1), OBIS_IMPORT, 1000.0),
        _mr(datetime(2026, 5, 15, 5, 0, 1), OBIS_IMPORT, 1002.5),
        _mr(datetime(2026, 5, 16, 0, 0, 1), OBIS_IMPORT, 1010.0, "invalid"),
    ]
    with caplog.at_level(logging.WARNING):
        daily = _client()._process_readings(
            date(2026, 5, 15), readings, GO_ZONES
        )
    assert daily.daily_import_total == 10.0  # 1010.0 - 1000.0, value still used
    assert "invalid" in caplog.text.lower()


def test_invalid_inner_boundary_is_logged(caplog):
    # The generalized invalid check must also cover inner switch boundaries.
    readings = [
        _mr(datetime(2026, 5, 15, 0, 0, 1), OBIS_IMPORT, 1000.0),
        _mr(datetime(2026, 5, 15, 5, 0, 1), OBIS_IMPORT, 1002.5, "invalid"),
        _mr(datetime(2026, 5, 16, 0, 0, 1), OBIS_IMPORT, 1010.0),
    ]
    with caplog.at_level(logging.WARNING):
        daily = _client()._process_readings(
            date(2026, 5, 15), readings, GO_ZONES
        )
    assert daily.zone_totals["Go"] == 2.5  # value still used
    assert "05:00" in caplog.text
    assert "invalid" in caplog.text.lower()


def test_not_present_anchor_does_not_warn(caplog):
    # "not_present" is a carried-forward placeholder on a cumulative register
    # and must stay silent (deliberately distinct from "invalid").
    readings = [
        _mr(datetime(2026, 5, 15, 0, 0, 1), OBIS_IMPORT, 1000.0),
        _mr(datetime(2026, 5, 15, 5, 0, 1), OBIS_IMPORT, 1002.5),
        _mr(datetime(2026, 5, 16, 0, 0, 1), OBIS_IMPORT, 1010.0, "not_present"),
    ]
    with caplog.at_level(logging.WARNING):
        daily = _client()._process_readings(
            date(2026, 5, 15), readings, GO_ZONES
        )
    assert daily.daily_import_total == 10.0
    assert "invalid" not in caplog.text.lower()


def test_material_negative_delta_rejects_day():
    # A meter swap: the new register starts near 0, so C - A is hugely
    # negative. The day must be rejected (SmgwNoDataError -> repair issue,
    # previous sensor values kept) instead of publishing nonsense into the
    # long-term statistics.
    readings = [
        _mr(datetime(2026, 5, 15, 0, 0, 1), OBIS_IMPORT, 8000.0),
        _mr(datetime(2026, 5, 15, 5, 0, 1), OBIS_IMPORT, 8001.0),
        _mr(datetime(2026, 5, 16, 0, 0, 1), OBIS_IMPORT, 3.0),  # new meter
    ]
    with pytest.raises(SmgwNoDataError) as ei:
        _client()._process_readings(date(2026, 5, 15), readings, GO_ZONES)
    assert "inconsistent" in str(ei.value)


def test_tiny_negative_delta_is_clamped_to_zero():
    # Rounding artifacts within the tolerance band become exactly 0 instead
    # of a cosmetic -0.0002 in the sensor (and instead of rejecting the day).
    readings = [
        _mr(datetime(2026, 5, 15, 0, 0, 1), OBIS_IMPORT, 1000.0002),
        _mr(datetime(2026, 5, 15, 5, 0, 1), OBIS_IMPORT, 1000.0),
        _mr(datetime(2026, 5, 16, 0, 0, 1), OBIS_IMPORT, 1010.0),
    ]
    daily = _client()._process_readings(date(2026, 5, 15), readings, GO_ZONES)
    assert daily.zone_totals["Go"] == 0.0  # -0.0002 clamped
    assert daily.zone_totals["Standard"] == 10.0


def test_degenerate_midnight_switch_computes_like_v2():
    # Regression for the v1->v2 migration of a legacy 00:00 switch time: two
    # zones with the same 00:00 boundary are degenerate but must compute the
    # historical v2 behaviour (slot 1 = 0, slot 2 = whole day) without error.
    zones = [(time(0, 0), "Zeitfenster 1"), (time(0, 0), "Zeitfenster 2")]
    readings = [
        _mr(datetime(2026, 5, 15, 0, 0, 1), OBIS_IMPORT, 1000.0),
        _mr(datetime(2026, 5, 16, 0, 0, 1), OBIS_IMPORT, 1010.0),
    ]
    daily = _client()._process_readings(date(2026, 5, 15), readings, zones)
    assert daily.zone_totals == {"Zeitfenster 1": 0.0, "Zeitfenster 2": 10.0}
    assert daily.daily_import_total == 10.0


def test_custom_tariff_switch_time():
    readings = [
        _mr(datetime(2026, 5, 15, 0, 0, 1), OBIS_IMPORT, 1000.0),
        _mr(datetime(2026, 5, 15, 7, 30, 1), OBIS_IMPORT, 1004.0),
        _mr(datetime(2026, 5, 16, 0, 0, 1), OBIS_IMPORT, 1010.0),
    ]
    zones = [(time(0, 0), "Go"), (time(7, 30), "Standard")]
    daily = _client()._process_readings(date(2026, 5, 15), readings, zones)
    assert daily.zone_totals["Go"] == 4.0  # up to 07:30
    assert daily.zone_totals["Standard"] == 6.0  # after 07:30
