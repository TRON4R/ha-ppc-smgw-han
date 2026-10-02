"""Tariff boundaries on the two DST change days.

The two fixtures are real signed CMS exports of a PPC gateway around the
spring change (29.03.2026) and the autumn change (26.10.2025). The expected
zone values were computed independently from the CMS's UTC timestamps, not
with this code: in spring a 02:00 boundary takes effect at the jump (03:00
CEST), in autumn the first pass of the repeated hour ("2A") wins.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from custom_components.smgw_han.aggregation import build_daily_summary
from custom_components.smgw_han.cms_parser import parse_cms_readings
from custom_components.smgw_han.const import OBIS_EXPORT, OBIS_IMPORT
from custom_components.smgw_han.smgw_client import (
    MeterReading,
    SmgwClient,
    SmgwNoDataError,
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


def _as_html_table(
    readings: list[MeterReading], start: datetime, end: datetime
) -> str:
    """The readings as the gateway's showMeterValues table renders them.

    The nightly fetch reads this table, not the CMS. Its layout was checked
    against the real table for 2025-10-26 (2026-10-02): newest first, BOTH
    passes of the repeated hour listed, the second pass above the first, the
    import row carrying timestamp and validity, the export row inheriting.
    """
    berlin = ZoneInfo("Europe/Berlin")
    flags = {"valid": "1", "invalid": "2", "not_present": "3"}
    by_instant: dict[datetime, dict[str, MeterReading]] = {}
    for r in readings:
        if start <= r.timestamp <= end:
            # The CMS parser keeps `fold`, so this is the true instant.
            instant = r.timestamp.replace(tzinfo=berlin).astimezone(UTC)
            by_instant.setdefault(instant, {})[r.obis_code] = r
    rows: list[str] = []
    for instant in sorted(by_instant, reverse=True):
        pair = by_instant[instant]
        first = True
        for obis in (OBIS_IMPORT, OBIS_EXPORT):
            r = pair.get(obis)
            if r is None:
                continue
            ts = r.timestamp.strftime("%Y-%m-%d %H:%M:%S") if first else ""
            valid = flags[r.quality] if first else ""
            rows.append(
                f'<tr id="table_metervalues_line{len(rows) + 1}">'
                f'<td id="table_metervalues_col_timestamp">{ts}</td>'
                f'<td id="table_metervalues_col_obis">{obis}</td>'
                f'<td id="table_metervalues_col_wert">{r.value:.4f}</td>'
                '<td id="table_metervalues_col_einheit">kWh</td>'
                f'<td id="table_metervalues_col_istvalide">{valid}</td></tr>'
            )
            first = False
    return (
        '<html><body><table id="metervalue">' + "".join(rows)
        + "</table></body></html>"
    )


def _without_first_pass_0200(readings: list[MeterReading]) -> list[MeterReading]:
    """The autumn data with the 02:00 reading of the FIRST pass removed."""
    return [
        r for r in readings
        if not (r.obis_code == OBIS_IMPORT and abs(r.value - 1778.2671) < 1e-6)
    ]


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


def test_nightly_html_table_resolves_the_repeated_hour(autumn):
    # Same data, but through the nightly path: the HTML table and its parser.
    html = _as_html_table(
        autumn, datetime(2025, 10, 26), datetime(2025, 10, 27, 0, 15)
    )
    readings = _client()._parse_meter_values_table(html)
    at_two = [
        r.value for r in readings
        if r.obis_code == OBIS_IMPORT and r.timestamp == datetime(2025, 10, 26, 2, 0, 1)
    ]
    # Both passes, the second listed first - as on the real gateway.
    assert at_two == [1785.0904, 1778.2671]
    daily = _client()._process_readings(AUTUMN, readings, HEAT_ZONES)
    assert daily.import_boundaries[1] == pytest.approx(1778.2671, abs=1e-4)
    assert daily.zone_totals == pytest.approx(HEAT_AUTUMN, abs=1e-4)


def test_nightly_rejects_the_day_when_one_pass_is_missing(autumn):
    # One 02:00 reading cannot be assigned to a pass: no guessed zone split.
    with pytest.raises(SmgwNoDataError, match="Import at 02:00 on 2025-10-26"):
        _client()._process_readings(
            AUTUMN, _without_first_pass_0200(autumn), HEAT_ZONES
        )


def test_export_leaves_the_affected_zones_empty_when_one_pass_is_missing(autumn):
    row = next(
        r for r in build_daily_summary(
            _without_first_pass_0200(autumn), lambda _d: HEAT_ZONES
        )
        if r.day == AUTUMN
    )
    # Standard (00:00-02:00) and Niedrig (02:00-06:00) touch the boundary;
    # Hoch and the day total do not and stay usable.
    assert row.zone_consumptions["Standard"] is None
    assert row.zone_consumptions["Niedrig"] is None
    assert row.zone_consumptions["Hoch"] == pytest.approx(HEAT_AUTUMN["Hoch"], abs=1e-4)
    assert row.consumption_total == pytest.approx(68.476, abs=1e-4)


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


def test_single_reading_in_repeated_hour_counts_as_missing(caplog):
    # Until v3.6.0 this reading was used as is; it may be the wrong pass.
    only = _mr(datetime(2025, 10, 26, 2, 0, 1), 10.0)
    with caplog.at_level(logging.WARNING):
        result = find_boundary_reading([only], datetime(2025, 10, 26, 2, 0, 1))
    assert result is None
    assert "treated as missing" in caplog.text
