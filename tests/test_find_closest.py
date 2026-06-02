"""Tests for the module-level find_closest_reading / find_closest_value."""

from __future__ import annotations

from datetime import datetime

from custom_components.smgw_han.const import OBIS_IMPORT
from custom_components.smgw_han.smgw_client import (
    MeterReading,
    find_closest_reading,
    find_closest_value,
)


def _r(minute: int, value: float) -> MeterReading:
    return MeterReading(
        timestamp=datetime(2026, 5, 15, 0, minute, 0),
        obis_code=OBIS_IMPORT,
        value=value,
        unit="kWh",
        quality="valid",
    )


def test_exact_match():
    assert find_closest_value([_r(0, 1.0)], datetime(2026, 5, 15, 0, 0, 0)) == 1.0


def test_picks_closest_within_window():
    readings = [_r(0, 1.0), _r(10, 2.0)]
    # 00:06 is 6 min from 00:00 and 4 min from 00:10 -> the latter wins.
    assert find_closest_value(readings, datetime(2026, 5, 15, 0, 6, 0)) == 2.0


def test_seven_minute_boundary_is_inclusive():
    # Exactly 7 minutes away still matches (the comparison uses <=).
    assert find_closest_value([_r(0, 1.0)], datetime(2026, 5, 15, 0, 7, 0)) == 1.0


def test_outside_tolerance_returns_none():
    target = datetime(2026, 5, 15, 0, 8, 0)  # 8 minutes > 7
    assert find_closest_value([_r(0, 1.0)], target) is None
    assert find_closest_reading([_r(0, 1.0)], target) is None


def test_empty_returns_none():
    assert find_closest_value([], datetime(2026, 5, 15, 0, 0, 0)) is None
    assert find_closest_reading([], datetime(2026, 5, 15, 0, 0, 0)) is None
