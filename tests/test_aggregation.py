"""Tests for aggregation.build_daily_summary (tolerant range aggregation)."""

from __future__ import annotations

from datetime import date, datetime, time

from custom_components.smgw_han.aggregation import _diff, build_daily_summary
from custom_components.smgw_han.const import OBIS_EXPORT, OBIS_IMPORT
from custom_components.smgw_han.smgw_client import MeterReading

GO_ZONES = [(time(0, 0), "Go"), (time(5, 0), "Standard")]

HEAT_ZONES = [
    (time(0, 0), "Standard"),
    (time(2, 0), "Niedrig"),
    (time(6, 0), "Standard"),
    (time(12, 0), "Niedrig"),
    (time(16, 0), "Standard"),
    (time(18, 0), "Hoch"),
    (time(21, 0), "Standard"),
]


def _imp(ts: datetime, value: float) -> MeterReading:
    return MeterReading(ts, OBIS_IMPORT, value, "kWh", "valid")


def _exp(ts: datetime, value: float) -> MeterReading:
    return MeterReading(ts, OBIS_EXPORT, value, "kWh", "valid")


def test_empty_returns_empty():
    assert build_daily_summary([], GO_ZONES) == []


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
    out = build_daily_summary(readings, GO_ZONES)
    # Day 15 has a closing value (the 16th 00:00 reading); day 16 has only a
    # start reading and no end value, so it must be dropped.
    assert [s.day for s in out] == [date(2026, 5, 15)]
    s = out[0]
    assert s.zone_consumptions == {"Go": 2.5, "Standard": 7.5}
    assert s.import_switches == {"05:00": 1002.5}
    assert s.consumption_total == 10.0
    assert s.feedin_total == 3.0
    assert s.import_end == 1010.0


def test_heat_zones_summed_per_name():
    boundary_values = [
        (time(0, 0), 1000.0),
        (time(2, 0), 1001.0),
        (time(6, 0), 1003.0),
        (time(12, 0), 1006.0),
        (time(16, 0), 1010.0),
        (time(18, 0), 1015.0),
        (time(21, 0), 1021.0),
    ]
    readings = [
        _imp(datetime(2026, 5, 15, t.hour, t.minute, 1), v)
        for t, v in boundary_values
    ]
    readings.append(_imp(datetime(2026, 5, 16, 0, 0, 1), 1028.0))

    out = build_daily_summary(readings, HEAT_ZONES)
    assert len(out) == 1
    s = out[0]
    assert s.zone_consumptions == {
        "Standard": 16.0,
        "Niedrig": 6.0,
        "Hoch": 6.0,
    }
    assert s.consumption_total == 28.0
    assert s.import_switches == {
        "02:00": 1001.0,
        "06:00": 1003.0,
        "12:00": 1006.0,
        "16:00": 1010.0,
        "18:00": 1015.0,
        "21:00": 1021.0,
    }


def test_to_dict_keys_and_boundary_timestamps():
    readings = [
        _imp(datetime(2026, 5, 15, 0, 0, 1), 1000.0),
        _imp(datetime(2026, 5, 15, 5, 0, 1), 1002.5),
        _imp(datetime(2026, 5, 16, 0, 0, 1), 1010.0),
    ]
    d = build_daily_summary(readings, GO_ZONES)[0].to_dict()
    assert d["day_of_summary"] == "2026-05-15"  # renamed from "date"
    assert "date" not in d
    assert d["start_timestamp"] == "2026-05-15T00:00:01"  # D 00:00 reading
    assert d["end_timestamp"] == "2026-05-16T00:00:01"  # D+1 00:00 reading
    assert d["zones"] == {"Go": 2.5, "Standard": 7.5}
    assert d["import_switches"] == {"05:00": 1002.5}


def test_day_without_end_value_is_excluded():
    # Only a start reading -> no closing value -> no date-only row emitted.
    readings = [_imp(datetime(2026, 5, 15, 0, 0, 1), 1000.0)]
    assert build_daily_summary(readings, GO_ZONES) == []


def test_missing_tariff_switch_leaves_partial_values_none():
    # Start + end present but no reading near the tariff switch time.
    readings = [
        _imp(datetime(2026, 5, 15, 0, 0, 1), 1000.0),
        _imp(datetime(2026, 5, 16, 0, 0, 1), 1010.0),
    ]
    out = build_daily_summary(readings, GO_ZONES)
    assert len(out) == 1
    s = out[0]
    assert s.consumption_total == 10.0  # start..end still computable
    assert s.zone_consumptions == {"Go": None, "Standard": None}


def test_missing_inner_boundary_affects_only_adjacent_zones():
    # Heat config with the 12:00 reading missing: only the zones touching
    # that boundary (Niedrig 12-16 and Standard 06-12) become None; "Hoch"
    # and the total stay computable.
    boundary_values = [
        (time(0, 0), 1000.0),
        (time(2, 0), 1001.0),
        (time(6, 0), 1003.0),
        # 12:00 missing
        (time(16, 0), 1010.0),
        (time(18, 0), 1015.0),
        (time(21, 0), 1021.0),
    ]
    readings = [
        _imp(datetime(2026, 5, 15, t.hour, t.minute, 1), v)
        for t, v in boundary_values
    ]
    readings.append(_imp(datetime(2026, 5, 16, 0, 0, 1), 1028.0))

    s = build_daily_summary(readings, HEAT_ZONES)[0]
    assert s.zone_consumptions["Hoch"] == 6.0
    assert s.zone_consumptions["Standard"] is None  # 06-12 segment broken
    assert s.zone_consumptions["Niedrig"] is None  # 12-16 segment broken
    assert s.consumption_total == 28.0
    assert s.import_switches["12:00"] is None
