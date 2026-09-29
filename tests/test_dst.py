"""Tariff boundaries on the two DST change days.

The two fixtures are real signed CMS exports of a PPC gateway around the
spring change (29.03.2026) and the autumn change (26.10.2025). The expected
zone values were computed independently from the CMS's UTC timestamps, not
with this code: in spring a 02:00 boundary takes effect at the jump (03:00
CEST), in autumn the first pass of the repeated hour ("2A") wins.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, time
from pathlib import Path

import pytest

from custom_components.smgw_han.aggregation import build_daily_summary
from custom_components.smgw_han.cms_parser import parse_cms_readings
from custom_components.smgw_han.const import OBIS_IMPORT
from custom_components.smgw_han.smgw_client import (
    MeterReading,
    SmgwClient,
    find_boundary_reading,
    find_closest_reading,
)

FIXTURES = Path(__file__).parent / "fixtures"
SPRING = date(2026, 3, 29)
AUTUMN = date(2025, 10, 26)

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

HEAT_SPRING = {"Standard": 17.6098, "Niedrig": 6.7839, "Hoch": 0.622}
HEAT_AUTUMN = {"Standard": 31.8576, "Niedrig": 26.8279, "Hoch": 9.7905}


@pytest.fixture(scope="module")
def spring() -> list[MeterReading]:
    return parse_cms_readings(
        (FIXTURES / "cms_dst_spring_2026-03-29.xml.cms").read_bytes()
    )


@pytest.fixture(scope="module")
def autumn() -> list[MeterReading]:
    return parse_cms_readings(
        (FIXTURES / "cms_dst_autumn_2025-10-26.xml.cms").read_bytes()
    )


def _client() -> SmgwClient:
    return SmgwClient("https://smgw.local/cgi-bin/hanservice.cgi", "user", "pw")


def _mr(ts: datetime, value: float) -> MeterReading:
    return MeterReading(
        timestamp=ts, obis_code=OBIS_IMPORT, value=value, unit="kWh",
        quality="valid",
    )


def _import_count(readings: list[MeterReading], day: date) -> int:
    return sum(
        1 for r in readings
        if r.obis_code == OBIS_IMPORT and r.timestamp.date() == day
    )


# --- the real data ---------------------------------------------------------


def test_dst_days_have_92_and_100_quarter_hours(spring, autumn):
    # BDEW market rules: the change days have 92 / 100 quarter-hour values.
    assert _import_count(spring, SPRING) == 92
    assert _import_count(autumn, AUTUMN) == 100


def test_spring_boundary_in_skipped_hour_takes_effect_at_the_jump(spring):
    # Before the fix this day was rejected ("Import at 02:00" missing).
    daily = _client()._process_readings(SPRING, spring, HEAT_ZONES)
    assert daily.zone_totals == pytest.approx(HEAT_SPRING, abs=1e-4)
    assert daily.daily_import_total == pytest.approx(25.0157, abs=1e-4)
    # 02:00 resolves to the reading at 03:00:01 CEST.
    assert daily.import_boundaries[1] == pytest.approx(11495.2085, abs=1e-4)


def test_autumn_boundary_in_repeated_hour_uses_first_pass(autumn):
    daily = _client()._process_readings(AUTUMN, autumn, HEAT_ZONES)
    assert daily.zone_totals == pytest.approx(HEAT_AUTUMN, abs=1e-4)
    assert daily.daily_import_total == pytest.approx(68.476, abs=1e-4)
    # 02:00 CEST (first pass), not 02:00 CET (1785.0904, 6.8 kWh later).
    assert daily.import_boundaries[1] == pytest.approx(1778.2671, abs=1e-4)


def test_autumn_result_does_not_depend_on_row_order(autumn):
    # The CMS lists newest first; the HTML table's order is not guaranteed.
    forward = _client()._process_readings(AUTUMN, autumn, HEAT_ZONES)
    backward = _client()._process_readings(
        AUTUMN, list(reversed(autumn)), HEAT_ZONES
    )
    assert backward.zone_totals == forward.zone_totals


def test_boundaries_outside_the_changed_hour_are_unaffected(spring, autumn):
    # Go switches at 00:00 and 05:00: its window simply had 4 (spring) and
    # 6 (autumn) real hours. These values match the pre-fix computation.
    spring_go = _client()._process_readings(SPRING, spring, GO_ZONES)
    assert spring_go.zone_totals == pytest.approx(
        {"Go": 10.4838, "Standard": 14.5319}, abs=1e-4
    )
    autumn_go = _client()._process_readings(AUTUMN, autumn, GO_ZONES)
    assert autumn_go.zone_totals == pytest.approx(
        {"Go": 35.4746, "Standard": 33.0014}, abs=1e-4
    )


@pytest.mark.parametrize(
    ("which", "day", "expected"),
    [("spring", SPRING, HEAT_SPRING), ("autumn", AUTUMN, HEAT_AUTUMN)],
)
def test_export_summary_matches_the_nightly_values(request, which, day, expected):
    readings = request.getfixturevalue(which)
    row = next(
        r for r in build_daily_summary(readings, lambda _d: HEAT_ZONES)
        if r.day == day
    )
    assert row.zone_consumptions == pytest.approx(expected, abs=1e-4)


# --- the rule in isolation -------------------------------------------------


def test_ordinary_day_behaves_like_find_closest_reading():
    readings = [
        _mr(datetime(2026, 5, 15, 1, 45, 1), 1.0),
        _mr(datetime(2026, 5, 15, 2, 0, 1), 2.0),
        _mr(datetime(2026, 5, 15, 2, 15, 1), 3.0),
    ]
    target = datetime(2026, 5, 15, 2, 0, 1)
    assert find_boundary_reading(readings, target) is find_closest_reading(
        readings, target
    )


@pytest.mark.parametrize("minute", [0, 15, 30, 45])
def test_every_time_in_the_skipped_hour_maps_to_the_jump(minute):
    readings = [
        _mr(datetime(2026, 3, 29, 1, 45, 1), 1.0),
        _mr(datetime(2026, 3, 29, 3, 0, 1), 2.0),
        _mr(datetime(2026, 3, 29, 3, 15, 1), 3.0),
    ]
    target = datetime(2026, 3, 29, 2, minute, 1)
    assert find_boundary_reading(readings, target).value == 2.0


@pytest.mark.parametrize("reverse", [False, True])
def test_repeated_hour_picks_the_lower_register_value(reverse):
    first = _mr(datetime(2025, 10, 26, 2, 30, 1), 10.0)
    second = _mr(datetime(2025, 10, 26, 2, 30, 1), 12.0)
    readings = [second, first] if reverse else [first, second]
    target = datetime(2025, 10, 26, 2, 30, 1)
    assert find_boundary_reading(readings, target) is first


def test_single_reading_in_repeated_hour_is_used_with_a_warning(caplog):
    only = _mr(datetime(2025, 10, 26, 2, 0, 1), 10.0)
    with caplog.at_level(logging.WARNING):
        result = find_boundary_reading([only], datetime(2025, 10, 26, 2, 0, 1))
    assert result is only
    assert "occurs twice" in caplog.text
