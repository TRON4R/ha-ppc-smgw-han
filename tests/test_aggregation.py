"""Tests for aggregation.build_daily_summary (tolerant range aggregation)."""

from __future__ import annotations

from datetime import date, datetime

from custom_components.smgw_han.aggregation import _diff, build_daily_summary
from custom_components.smgw_han.const import OBIS_EXPORT, OBIS_IMPORT
from custom_components.smgw_han.smgw_client import MeterReading


def _imp(ts: datetime, value: float) -> MeterReading:
    return MeterReading(ts, OBIS_IMPORT, value, "kWh", "valid")


def _exp(ts: datetime, value: float) -> MeterReading:
    return MeterReading(ts, OBIS_EXPORT, value, "kWh", "valid")


def test_empty_returns_empty():
    assert build_daily_summary([]) == []


def test_diff_none_propagation():
    assert _diff(None, 5.0) is None
    assert _diff(5.0, None) is None
    assert _diff(1.0, 3.5) == 2.5


def test_single_complete_day_is_summarized():
    readings = [
        _imp(datetime(2026, 5, 15, 0, 0, 1), 1000.0),
        _imp(datetime(2026, 5, 15, 5, 0, 1), 1002.5),
        _imp(datetime(2026, 5, 16, 0, 0, 1), 1010.0),
        _exp(datetime(2026, 5, 15, 0, 0, 1), 500.0),
        _exp(datetime(2026, 5, 16, 0, 0, 1), 503.0),
    ]
    out = build_daily_summary(readings, 5, 0)
    # Day 15 has a closing value (the 16th 00:00 reading); day 16 has only a
    # start reading and no end value, so it must be dropped.
    assert [s.day for s in out] == [date(2026, 5, 15)]
    s = out[0]
    assert s.consumption_go == 2.5
    assert s.consumption_standard == 7.5
    assert s.consumption_total == 10.0
    assert s.feedin_total == 3.0
    assert s.import_end == 1010.0


def test_day_without_end_value_is_excluded():
    # Only a start reading -> no closing value -> no date-only row emitted.
    readings = [_imp(datetime(2026, 5, 15, 0, 0, 1), 1000.0)]
    assert build_daily_summary(readings, 5, 0) == []


def test_missing_tariff_switch_leaves_partial_values_none():
    # Start + end present but no reading near the tariff switch time.
    readings = [
        _imp(datetime(2026, 5, 15, 0, 0, 1), 1000.0),
        _imp(datetime(2026, 5, 16, 0, 0, 1), 1010.0),
    ]
    out = build_daily_summary(readings, 5, 0)
    assert len(out) == 1
    s = out[0]
    assert s.consumption_total == 10.0  # start..end still computable
    assert s.consumption_go is None  # needs the (missing) switch reading
    assert s.consumption_standard is None
