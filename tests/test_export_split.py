"""Long meter exports are fetched in parts of at most three months (3.9.0).

The gateway refused a whole year in one request (HTTP 500 after 48 s,
2026-10-03) and was unreachable afterwards, so ``run_export`` cuts a range
over 93 days into parts, fetches them one by one, and merges the readings.
These tests drive the split rule, the merge and the error rules against a
fake gateway that serves slices of one synthetic reading series, so a split
export can be compared with the same range computed in one piece.
"""

from __future__ import annotations

import zipfile
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.setup import async_setup_component
from yarl import URL

from custom_components.smgw_han import services
from custom_components.smgw_han.aggregation import build_daily_summary
from custom_components.smgw_han.cms_parser import parse_cms_readings
from custom_components.smgw_han.const import DOMAIN, OBIS_EXPORT, OBIS_IMPORT
from custom_components.smgw_han.services import (
    EXPORT_SINGLE_REQUEST_DAYS,
    _merge_and_aggregate,
    export_summary,
    run_export,
    split_export_range,
)
from custom_components.smgw_han.smgw_client import MeterReading

FIXTURES = Path(__file__).parent / "fixtures"
GO_ZONES = [(time(0, 0), "Go"), (time(5, 0), "Standard")]
BERLIN = ZoneInfo("Europe/Berlin")


@pytest.fixture(autouse=True)
def _enable(enable_custom_integrations):
    yield


@pytest.fixture(autouse=True)
def _no_pause():
    """The real pause between parts is 10 s; tests need none."""
    with patch.object(services, "EXPORT_PART_PAUSE_S", 0):
        yield


@pytest.fixture(autouse=True)
async def _http(hass: HomeAssistant):
    """Signing the download links needs the auth setup http brings along."""
    assert await async_setup_component(hass, "http", {})


# ----------------------------------------------------------------------
# The split rule
# ----------------------------------------------------------------------


def _days(part: tuple[datetime, datetime]) -> float:
    return (part[1] - part[0]) / timedelta(days=1)


def test_up_to_93_days_stays_one_request():
    start = datetime(2026, 7, 1)
    for end in (
        datetime(2026, 10, 1, 0, 15),  # a quarter with the closing margin
        start + timedelta(days=EXPORT_SINGLE_REQUEST_DAYS),
    ):
        assert split_export_range(start, end) == [(start, end)]


def test_odd_range_is_cut_on_the_start_day_of_the_month():
    """17.02.-19.07.: cut on the 17th, three months on, rest in the last part."""
    parts = split_export_range(datetime(2027, 2, 17), datetime(2027, 7, 19))
    assert parts == [
        (datetime(2027, 2, 17), datetime(2027, 5, 17, 0, 15)),
        (datetime(2027, 5, 17), datetime(2027, 7, 19)),
    ]
    assert [int(_days(p)) for p in parts] == [89, 63]


def test_a_year_gives_the_four_calendar_quarters():
    parts = split_export_range(datetime(2026, 1, 1), datetime(2027, 1, 1, 0, 15))
    assert parts == [
        (datetime(2026, 1, 1), datetime(2026, 4, 1, 0, 15)),
        (datetime(2026, 4, 1), datetime(2026, 7, 1, 0, 15)),
        (datetime(2026, 7, 1), datetime(2026, 10, 1, 0, 15)),
        (datetime(2026, 10, 1), datetime(2027, 1, 1, 0, 15)),
    ]


def test_month_end_start_does_not_drift():
    """31.01. -> 30.04. -> 31.07.: always counted from the start."""
    parts = split_export_range(datetime(2026, 1, 31), datetime(2026, 9, 30))
    assert [p[0].date() for p in parts] == [
        date(2026, 1, 31), date(2026, 4, 30), date(2026, 7, 31),
    ]


def test_short_rest_gets_its_own_part_and_tiny_rest_does_not():
    # 95 days: 92 + 3 days, a small extra request is harmless.
    parts = split_export_range(datetime(2026, 7, 1), datetime(2026, 10, 4))
    assert [p[0] for p in parts] == [datetime(2026, 7, 1), datetime(2026, 10, 1)]
    # A rest of at most a day stays with the part before it.
    parts = split_export_range(datetime(2026, 7, 1), datetime(2026, 10, 1, 12, 0))
    assert parts == [(datetime(2026, 7, 1), datetime(2026, 10, 1, 12, 0))]


def test_start_time_of_day_is_kept_and_cuts_stay_at_midnight():
    parts = split_export_range(
        datetime(2027, 2, 17, 10, 30), datetime(2027, 7, 19)
    )
    assert parts[0] == (datetime(2027, 2, 17, 10, 30), datetime(2027, 5, 17, 0, 15))


@pytest.mark.parametrize(
    ("start", "end"),
    [
        (datetime(2024, 10, 13), datetime(2026, 11, 12, 0, 15)),  # 760 days
        (datetime(2025, 3, 30, 17, 0), datetime(2026, 2, 27)),
        (datetime(2025, 11, 30), datetime(2026, 6, 1)),
    ],
)
def test_parts_cover_the_range_seamlessly(start, end):
    parts = split_export_range(start, end)
    assert parts[0][0] == start
    assert parts[-1][1] == end
    for (_s1, e1), (s2, _e2) in zip(parts, parts[1:]):
        assert e1 - s2 == timedelta(minutes=15)  # overlap, no gap
        assert s2.time() == time(0, 0)  # never in a DST change hour
    assert all(_days(p) <= EXPORT_SINGLE_REQUEST_DAYS for p in parts)


# ----------------------------------------------------------------------
# A fake gateway that serves slices of one reading series
# ----------------------------------------------------------------------


def _series(first: date, days: int) -> list[MeterReading]:
    """15-minute import/export readings with an uneven consumption profile.

    Stepped in UTC and shown in legal time like the CMS parser does, so a
    DST day has 92 or 100 readings and the repeated hour carries ``fold``.
    """
    readings: list[MeterReading] = []
    imp, exp = 1000.0, 500.0
    moment = datetime.combine(first, time(0, 0, 1), BERLIN).astimezone(UTC)
    for step in range(days * 96 + 1):
        local = moment.astimezone(BERLIN).replace(tzinfo=None)
        readings.append(MeterReading(local, OBIS_IMPORT, round(imp, 4), "kWh", "valid"))
        readings.append(MeterReading(local, OBIS_EXPORT, round(exp, 4), "kWh", "valid"))
        imp += 0.05 + (step % 7) * 0.013
        exp += 0.02 if 40 <= step % 96 <= 70 else 0.0
        moment += timedelta(minutes=15)
    return readings


class SliceGateway:
    """Coordinator stand-in: answers each request with its slice.

    The "CMS" is a token the patched parser resolves back to the readings
    inside the requested range, inclusive at both ends like the gateway.
    ``empty_before`` makes it answer ranges ending before that moment with an
    empty body; ``fail_at`` raises for the request with that index.
    """

    target_meter_id = "1lgz0072999211"
    device_name = None

    def __init__(self, readings, *, empty_before=None, fail_at=None, error=None):
        self.readings = readings
        self.empty_before = empty_before
        self.fail_at = fail_at
        self.error = error
        self.requests: list[tuple[datetime, datetime]] = []
        self.slices: dict[bytes, list[MeterReading]] = {}

    async def async_download_cms(self, from_dt, to_dt):
        index = len(self.requests)
        self.requests.append((from_dt, to_dt))
        if index == self.fail_at:
            raise self.error
        if self.empty_before and to_dt <= self.empty_before:
            return b"", None
        token = f"<?xml part {index}".encode()
        self.slices[token] = [
            r for r in self.readings if from_dt <= r.timestamp <= to_dt
        ]
        return token, f"{self.target_meter_id}.sm_data.xml.cms"

    def parse(self, content: bytes) -> list[MeterReading]:
        return list(self.slices[content])

    def zone_resolver(self):
        return lambda _day: GO_ZONES

    async def async_preload_holidays(self, years=None):
        return None

    def zone_day_types(self, first_day, last_day):
        return []

    def holidays_in_range(self, first_day, last_day):
        return []

    def zone_periods(self, first_day, last_day):
        return [
            {
                "valid_from": first_day.isoformat(),
                "zones": [
                    {"time": t.strftime("%H:%M"), "name": name}
                    for t, name in GO_ZONES
                ],
            }
        ]


async def _export(hass, gateway, start, end, **kwargs):
    with patch.object(services, "parse_cms_readings", gateway.parse):
        return await run_export(
            hass, gateway, start, end,
            download_cms=kwargs.pop("download_cms", True),
            do_csv=kwargs.pop("do_csv", True),
            do_xlsx=kwargs.pop("do_xlsx", True),
            **kwargs,
        )


def _export_dir(hass, response) -> Path:
    token = URL(response["files"]["csv"]).parts[-2]
    return Path(hass.config.config_dir) / "smgw_han_exports" / token


async def test_split_export_equals_the_unsplit_computation(hass: HomeAssistant):
    """Merged parts give exactly what one request would have given."""
    series = _series(date(2026, 1, 1), 200)
    start, end = datetime(2026, 1, 1), datetime(2026, 7, 20, 0, 15)
    gateway = SliceGateway(series)

    response = await _export(hass, gateway, start, end)

    assert gateway.requests == split_export_range(start, end)
    assert response["parts"] == 3
    assert response["parts_without_data"] == 0
    whole = [r for r in series if start <= r.timestamp <= end]
    assert response["reading_count"] == len(whole)
    expected = [s.to_dict() for s in build_daily_summary(whole, lambda _d: GO_ZONES)]
    assert response["daily_summary"] == expected
    # Every day of the range, the seams included, is complete.
    assert all(day["consumption_total"] is not None for day in expected)

    # The signed parts go into one ZIP unchanged and in order.
    folder = _export_dir(hass, response)
    archive = folder / URL(response["files"]["cms"]).name
    assert archive.name.endswith("_CMS.zip")
    with zipfile.ZipFile(archive) as zf:
        names = zf.namelist()
        assert names[0].startswith("Teil01von03_2026-01-01_000000_bis_2026-04-01_001500")
        assert names[2].startswith("Teil03von03_2026-07-01_000000_bis_2026-07-20_001500")
        assert all(n.endswith("1lgz0072999211.sm_data.xml.cms") for n in names)
        assert zf.read(names[1]) == b"<?xml part 1"

    # The workbook says how the range was fetched.
    from openpyxl import load_workbook

    sheet = load_workbook(folder / URL(response["files"]["xlsx"]).name)["Definition"]
    texts = [c.value for c in sheet["A"] if c.value]
    assert "Abruf in Teilen" in texts
    assert any(str(t).startswith("Teil 3: 2026-07-01 00:00:00 bis") for t in texts)


async def test_one_request_keeps_the_gateway_file_name(hass: HomeAssistant):
    series = _series(date(2026, 5, 1), 20)
    gateway = SliceGateway(series)
    response = await _export(
        hass, gateway, datetime(2026, 5, 1), datetime(2026, 5, 21, 0, 15)
    )
    assert len(gateway.requests) == 1
    assert response["parts"] == 1
    assert URL(response["files"]["cms"]).name == "1lgz0072999211.sm_data.xml.cms"


async def test_leading_parts_without_data_are_skipped(hass: HomeAssistant):
    """A gateway younger than the range: the early parts have nothing."""
    await hass.config.async_set_time_zone("Europe/Berlin")
    hass.config.language = "de"
    series = _series(date(2026, 8, 15), 60)
    gateway = SliceGateway(series, empty_before=datetime(2026, 7, 2))
    response = await _export(
        hass, gateway, datetime(2026, 1, 1), datetime(2026, 10, 14, 0, 15)
    )

    assert len(gateway.requests) == 4
    assert response["parts"] == 2  # only the parts that returned readings
    assert response["parts_without_data"] == 2
    assert response["readings"][0]["timestamp"].startswith("2026-08-15")
    text = export_summary(hass, response)
    assert "In 2 Teilabrufen geholt" in text
    assert "die Werte beginnen am 15.08.2026" in text


async def test_leading_http_error_counts_as_no_data(hass: HomeAssistant):
    """The gateway answers a range without data with HTTP 500 too."""
    series = _series(date(2026, 5, 1), 60)

    class Gateway(SliceGateway):
        async def async_download_cms(self, from_dt, to_dt):
            if to_dt <= datetime(2026, 4, 2):
                self.requests.append((from_dt, to_dt))
                raise ServiceValidationError(
                    translation_domain=DOMAIN, translation_key="no_data_in_range"
                )
            return await super().async_download_cms(from_dt, to_dt)

    gateway = Gateway(series)
    response = await _export(
        hass, gateway, datetime(2026, 1, 1), datetime(2026, 6, 30), do_xlsx=False
    )
    assert response["parts_without_data"] == 1
    assert response["parts"] == 1


class _Http500Before(SliceGateway):
    """Answers ranges ending before ``cut`` the way a real gateway does.

    Measured 2026-11-12: a range without data gets HTTP 500 with a generic
    error page - the same answer a gateway gave when it failed on a year -
    which the coordinator maps to ``no_data_in_range``.
    """

    def __init__(self, readings, cut):
        super().__init__(readings)
        self.cut = cut

    async def async_download_cms(self, from_dt, to_dt):
        if to_dt <= self.cut:
            self.requests.append((from_dt, to_dt))
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="no_data_in_range"
            )
        return await super().async_download_cms(from_dt, to_dt)


async def test_http_500_first_part_with_data_right_after_aborts(
    hass: HomeAssistant,
):
    """The review's scenario: part 1 fails with HTTP 500, part 2 has data.

    Part 2 has a reading right at its start, so the gateway recorded before
    that too: the HTTP 500 of part 1 was a failure, not an empty range, and
    the export must not pass as complete without January to March.
    """
    hass.config.language = "de"
    series = _series(date(2025, 6, 1), 400)  # data long before the range
    gateway = _Http500Before(series, cut=datetime(2026, 4, 2))
    with pytest.raises(HomeAssistantError) as err:
        await _export(hass, gateway, datetime(2026, 1, 1), datetime(2026, 9, 1))
    assert err.value.translation_key == "cms_part_no_data"
    assert err.value.translation_placeholders["part"] == "1"
    assert err.value.translation_placeholders["range"].startswith("01.01.2026")
    assert len(gateway.requests) == 2  # stops after the part that proves it


async def test_history_starting_exactly_at_a_cut_aborts_on_the_safe_side(
    hass: HomeAssistant,
):
    """Indistinguishable from the failure above, so it aborts as well."""
    series = _series(date(2026, 4, 1), 200)  # first reading 01.04. 00:00:01
    gateway = SliceGateway(series, empty_before=datetime(2026, 4, 2))
    with pytest.raises(HomeAssistantError) as err:
        await _export(hass, gateway, datetime(2026, 1, 1), datetime(2026, 9, 1))
    assert err.value.translation_key == "cms_part_no_data"


async def test_history_starting_inside_the_part_proves_the_skip(
    hass: HomeAssistant,
):
    """The live case of 2026-11-12: data from 11.11. 12:15, part from 01.10."""
    series = _series(date(2026, 11, 11), 1)[98:]  # from 11.11. 12:15:01 on
    gateway = _Http500Before(series, cut=datetime(2026, 10, 2))
    response = await _export(
        hass, gateway, datetime(2026, 1, 1), datetime(2026, 11, 12, 0, 15),
        do_xlsx=False,
    )
    assert len(gateway.requests) == 4
    assert response["parts_without_data"] == 3
    assert response["readings"][0]["timestamp"].startswith("2026-11-11T12:")


@pytest.mark.parametrize(
    ("answer", "outcome"),
    [
        (b"", "skipped"),
        (b"Keine Daten\n", "skipped"),
        (b"<p>Keine Daten vorhanden.</p>", "skipped"),
        (b"<html><body>Bitte anmelden</body></html>", "cms_part_failed"),
    ],
)
async def test_an_unexpected_answer_is_no_empty_range(
    hass: HomeAssistant, answer, outcome
):
    """Only an empty answer or 'Keine Daten' counts as no data.

    A login or error page with HTTP 200 must not pass as a gap in the
    history.
    """
    series = _series(date(2026, 5, 10), 120)

    class Gateway(SliceGateway):
        async def async_download_cms(self, from_dt, to_dt):
            if to_dt <= datetime(2026, 4, 2):
                self.requests.append((from_dt, to_dt))
                return answer, None
            return await super().async_download_cms(from_dt, to_dt)

    gateway = Gateway(series)
    if outcome == "skipped":
        response = await _export(
            hass, gateway, datetime(2026, 1, 1), datetime(2026, 9, 1), do_xlsx=False
        )
        assert response["parts_without_data"] == 1
    else:
        with pytest.raises(HomeAssistantError) as err:
            await _export(hass, gateway, datetime(2026, 1, 1), datetime(2026, 9, 1))
        assert err.value.translation_key == outcome
        assert err.value.translation_placeholders["part"] == "1"


async def test_an_unexpected_answer_to_one_request_is_a_failure(
    hass: HomeAssistant,
):
    class Gateway(SliceGateway):
        async def async_download_cms(self, from_dt, to_dt):
            return b"<html>Fehler</html>", None

    with pytest.raises(HomeAssistantError) as err:
        await _export(
            hass, Gateway([]), datetime(2026, 5, 1), datetime(2026, 6, 1)
        )
    assert not isinstance(err.value, ServiceValidationError)
    assert err.value.translation_key == "cms_download_failed"


async def test_an_unreadable_part_is_named(hass: HomeAssistant):
    series = _series(date(2026, 1, 1), 250)
    gateway = SliceGateway(series)

    def parse(content):
        if content == b"<?xml part 1":
            raise ValueError("broken XML")
        return gateway.parse(content)

    with (
        patch.object(services, "parse_cms_readings", parse),
        pytest.raises(HomeAssistantError) as err,
    ):
        await run_export(
            hass, gateway, datetime(2026, 1, 1), datetime(2026, 9, 1),
            download_cms=False, do_csv=False, do_xlsx=False,
        )
    assert err.value.translation_key == "cms_part_failed"
    assert err.value.translation_placeholders["part"] == "2"


async def test_no_data_anywhere_is_the_familiar_message(hass: HomeAssistant):
    gateway = SliceGateway([], empty_before=datetime(2030, 1, 1))
    with pytest.raises(ServiceValidationError) as err:
        await _export(hass, gateway, datetime(2026, 1, 1), datetime(2026, 9, 1))
    assert err.value.translation_key == "no_data_in_range"
    assert len(gateway.requests) == 3


async def test_a_hole_after_data_aborts(hass: HomeAssistant):
    """No merged file may silently miss a quarter in the middle."""
    hass.config.language = "de"
    series = [
        r for r in _series(date(2026, 1, 1), 250)
        if not datetime(2026, 4, 1) <= r.timestamp <= datetime(2026, 7, 1, 0, 15)
    ]
    gateway = SliceGateway(series)
    with pytest.raises(HomeAssistantError) as err:
        await _export(hass, gateway, datetime(2026, 1, 1), datetime(2026, 9, 1))
    assert err.value.translation_key == "cms_part_no_data"
    assert err.value.translation_placeholders == {
        "part": "2",
        "parts": "3",
        "range": "01.04.2026 00:00 – 01.07.2026 00:15",
    }
    assert len(gateway.requests) == 2  # stops right there


@pytest.mark.parametrize(
    ("raised", "expected_key"),
    [
        ("cms_download_failed", "cms_part_failed"),
        ("cms_download_timeout", "cms_part_timeout"),
        ("cms_auth_failed", "cms_auth_failed"),  # a login problem stays one
    ],
)
async def test_a_failed_part_is_named(hass: HomeAssistant, raised, expected_key):
    hass.config.language = "en"
    error = HomeAssistantError(
        translation_domain=DOMAIN,
        translation_key=raised,
        translation_placeholders={"minutes": "2"}
        if raised == "cms_download_timeout"
        else None,
    )
    gateway = SliceGateway(_series(date(2026, 1, 1), 250), fail_at=1, error=error)
    with pytest.raises(HomeAssistantError) as err:
        await _export(hass, gateway, datetime(2026, 1, 1), datetime(2026, 9, 1))
    assert err.value.translation_key == expected_key
    if expected_key != "cms_auth_failed":
        placeholders = err.value.translation_placeholders
        assert placeholders["part"] == "2"
        assert placeholders["parts"] == "3"
        assert placeholders["range"] == "2026-04-01 00:00 – 2026-07-01 00:15"
        if expected_key == "cms_part_timeout":
            assert placeholders["minutes"] == "2"
    assert len(gateway.requests) == 2


async def test_one_request_failure_keeps_its_own_message(hass: HomeAssistant):
    error = HomeAssistantError(
        translation_domain=DOMAIN,
        translation_key="cms_download_timeout",
        translation_placeholders={"minutes": "2"},
    )
    gateway = SliceGateway([], fail_at=0, error=error)
    with pytest.raises(HomeAssistantError) as err:
        await _export(hass, gateway, datetime(2026, 5, 1), datetime(2026, 6, 1))
    assert err.value is error


async def test_progress_runs_up_to_one(hass: HomeAssistant):
    seen: list[float] = []
    gateway = SliceGateway(_series(date(2026, 1, 1), 200))
    await _export(
        hass, gateway, datetime(2026, 1, 1), datetime(2026, 7, 20),
        download_cms=False, do_csv=False, do_xlsx=False,
        progress=seen.append,
    )
    assert seen == sorted(seen)
    assert seen[-1] == 1.0
    assert pytest.approx(seen[0]) == 0.3  # first of three parts done


def test_merge_keeps_both_passes_of_the_repeated_autumn_hour():
    """Overlapping parts across the DST change hour lose nothing.

    The real autumn export holds 02:00-02:59 twice, told apart only by the
    UTC moment. Two overlapping halves of it must merge back into exactly
    the original series.
    """
    readings = parse_cms_readings(
        (FIXTURES / "cms_dst_autumn_2025-10-26.xml.cms").read_bytes()
    )
    middle = len(readings) // 2
    first, second = readings[: middle + 40], readings[middle - 40 :]
    merged, summary = _merge_and_aggregate([first, second], lambda _d: GO_ZONES)
    assert merged == readings
    assert [s.to_dict() for s in summary] == [
        s.to_dict() for s in build_daily_summary(readings, lambda _d: GO_ZONES)
    ]
