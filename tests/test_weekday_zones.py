"""Tests for Saturday/Sunday zone overrides and nationwide holidays."""

from __future__ import annotations

from datetime import date, datetime, time

import pytest
from freezegun import freeze_time
from homeassistant.core import HomeAssistant
from openpyxl import load_workbook
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.smgw_han.aggregation import build_daily_summary
from custom_components.smgw_han.config_flow import (
    ZoneDefinitionError,
    _parse_optional_tariff_zones,
)
from custom_components.smgw_han.const import (
    CONF_HOLIDAYS_AS_SUNDAY,
    CONF_METER_ID,
    CONF_TARIFF_ZONES,
    CONF_TARIFF_ZONES_SATURDAY,
    CONF_TARIFF_ZONES_SUNDAY,
    CONF_ZONE_SCHEDULE,
    DOMAIN,
    OBIS_IMPORT,
    STORE_VERSION,
    TARIFF_TEMPLATE_BAYERNWERK,
    TARIFF_TEMPLATES_WEEKDAY,
    day_zones_config,
    distinct_zone_names,
    is_national_holiday,
)
from custom_components.smgw_han.coordinator import SmgwCoordinator
from custom_components.smgw_han.export_files import write_xlsx
from custom_components.smgw_han.sensor import _dynamic_descriptions
from custom_components.smgw_han.smgw_client import DailyData, MeterReading



@pytest.fixture(autouse=True)
def _enable(enable_custom_integrations):
    yield


BW = TARIFF_TEMPLATES_WEEKDAY[TARIFF_TEMPLATE_BAYERNWERK]
WEEKDAY = BW[CONF_TARIFF_ZONES]  # NT 00-06, HT 06-22, NT 22-24
SATURDAY = BW[CONF_TARIFF_ZONES_SATURDAY]  # NT 00-06, HT 06-13, NT 13-24
SUNDAY = BW[CONF_TARIFF_ZONES_SUNDAY]  # NT all day

# 2026-10-05 Mon, 10-03 Sat (Tag der Deutschen Einheit), 10-04 Sun
MONDAY_D, SATURDAY_D, SUNDAY_D = date(2026, 10, 5), date(2026, 10, 10), date(2026, 10, 11)
HOLIDAY_ON_SATURDAY = date(2026, 10, 3)
HOLIDAY_ON_WEEKDAY = date(2026, 12, 25)  # Friday


def _data(holidays: bool = True, **extra) -> dict:
    return {
        CONF_TARIFF_ZONES: WEEKDAY,
        CONF_TARIFF_ZONES_SATURDAY: SATURDAY,
        CONF_TARIFF_ZONES_SUNDAY: SUNDAY,
        CONF_HOLIDAYS_AS_SUNDAY: holidays,
        **extra,
    }


# --- day-type resolution -------------------------------------------------


def test_weekday_uses_base_layout():
    assert day_zones_config(_data(), WEEKDAY, MONDAY_D) == WEEKDAY


def test_saturday_and_sunday_use_their_override():
    assert day_zones_config(_data(), WEEKDAY, SATURDAY_D) == SATURDAY
    assert day_zones_config(_data(), WEEKDAY, SUNDAY_D) == SUNDAY


def test_empty_override_falls_back_to_base():
    data = {CONF_TARIFF_ZONES: WEEKDAY}
    assert day_zones_config(data, WEEKDAY, SATURDAY_D) == WEEKDAY
    assert day_zones_config(data, WEEKDAY, SUNDAY_D) == WEEKDAY


def test_holiday_on_weekday_is_sunday_when_enabled():
    assert is_national_holiday(HOLIDAY_ON_WEEKDAY)
    assert day_zones_config(_data(True), WEEKDAY, HOLIDAY_ON_WEEKDAY) == SUNDAY


def test_holiday_ignored_when_switch_is_off():
    assert day_zones_config(_data(False), WEEKDAY, HOLIDAY_ON_WEEKDAY) == WEEKDAY


def test_holiday_beats_saturday():
    assert HOLIDAY_ON_SATURDAY.weekday() == 5
    assert day_zones_config(_data(True), WEEKDAY, HOLIDAY_ON_SATURDAY) == SUNDAY
    assert day_zones_config(_data(False), WEEKDAY, HOLIDAY_ON_SATURDAY) == SATURDAY


def test_holiday_without_sunday_override_uses_base():
    data = {CONF_TARIFF_ZONES: WEEKDAY, CONF_HOLIDAYS_AS_SUNDAY: True}
    assert day_zones_config(data, WEEKDAY, HOLIDAY_ON_WEEKDAY) == WEEKDAY


def test_state_specific_holiday_is_not_national():
    assert not is_national_holiday(date(2026, 6, 4))  # Fronleichnam


def test_distinct_zone_names_orders_by_first_appearance():
    assert distinct_zone_names(WEEKDAY, SATURDAY, SUNDAY) == ["NT", "HT"]
    assert distinct_zone_names(SUNDAY, WEEKDAY) == ["NT", "HT"]


# --- form validation ---------------------------------------------------------


def test_optional_zones_accept_empty_and_single_entry():
    assert _parse_optional_tariff_zones([]) == []
    assert _parse_optional_tariff_zones(["00:00 NT"]) == [
        {"time": "00:00", "name": "NT"}
    ]


def test_optional_zones_still_validate_format():
    with pytest.raises(ZoneDefinitionError):
        _parse_optional_tariff_zones(["06:00 NT"])  # must start at 00:00


# --- sensors -------------------------------------------------------------------


def test_sensors_cover_zone_names_and_switches_of_all_layouts():
    descs = _dynamic_descriptions(WEEKDAY, SATURDAY, SUNDAY)
    slots = [d for d in descs if d.key.startswith("daily_consumption_slot_")]
    switches = [d for d in descs if d.key.startswith("meter_consumption_switch_")]
    assert [d.translation_placeholders["zone_name"] for d in slots] == ["NT", "HT"]
    assert len(switches) == 2  # longest layout has 3 entries -> 2 inner switches


def test_zone_missing_on_the_day_is_zero_not_unknown():
    """Sunday is all NT: HT consumed 0 kWh, the HT sensor must not be unknown."""
    dd = DailyData(
        date=SUNDAY_D,
        import_boundaries=[1000.0, 1004.0],
        export_midnight=0.0,
        export_next_midnight=0.0,
        zone_totals={"NT": 4.0},
        daily_import_total=4.0,
        daily_export_total=0.0,
    )
    data = SmgwCoordinator._daily_data_to_dict(dd, ["NT", "HT"])
    assert data["daily_consumption_slot_1"] == 4.0
    assert data["daily_consumption_slot_2"] == 0.0


def test_daily_data_to_dict_without_names_keeps_old_behaviour():
    dd = DailyData(
        date=MONDAY_D,
        import_boundaries=[1000.0, 1001.0, 1010.0],
        export_midnight=0.0,
        export_next_midnight=0.0,
        zone_totals={"NT": 1.0, "HT": 9.0},
        daily_import_total=10.0,
        daily_export_total=0.0,
    )
    data = SmgwCoordinator._daily_data_to_dict(dd)
    assert data["daily_consumption_slot_2"] == 9.0


# --- coordinator ---------------------------------------------------------------


async def _coordinator(hass: HomeAssistant, stub, **data) -> SmgwCoordinator:
    await hass.config.async_set_time_zone("Europe/Berlin")
    entry = MockConfigEntry(
        domain=DOMAIN, data={CONF_METER_ID: "M", **_data(), **data}
    )
    entry.add_to_hass(hass)
    return SmgwCoordinator(hass, entry, stub)


def _dd(day: date, totals: dict[str, float]) -> DailyData:
    total = sum(totals.values())
    return DailyData(
        date=day,
        import_boundaries=[1000.0, 1000.0 + total],
        export_midnight=0.0,
        export_next_midnight=0.0,
        zone_totals=totals,
        daily_import_total=total,
        daily_export_total=0.0,
    )


class _FetchStub:
    """Client stub for the daily fetch; remembers the zones it was given."""

    zones = None

    def __init__(self, *, result=None):
        self._result = result

    async def close(self):
        pass


    async def async_fetch_daily_data(self, target_date, zones=None, target_meter_id=None):
        self.zones = zones
        return self._result


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        ("2026-10-06 00:15:00", WEEKDAY),  # fetches Monday 10-05
        ("2026-10-11 00:15:00", SATURDAY),  # fetches Saturday 10-10
        ("2026-10-12 00:15:00", SUNDAY),  # fetches Sunday 10-11
        ("2026-10-04 00:15:00", SUNDAY),  # fetches 10-03: Saturday AND holiday
    ],
)
async def test_fetch_splits_each_day_by_its_own_layout(hass, now, expected):
    stub = _FetchStub(result=_dd(date(2026, 1, 1), {"NT": 1.0}))
    coord = await _coordinator(hass, stub)
    with freeze_time(now):
        await coord._async_do_daily_fetch()
    assert [(t.strftime("%H:%M"), n) for t, n in stub.zones] == [
        (z["time"], z["name"]) for z in expected
    ]


async def test_sunday_fetch_publishes_ht_as_zero(hass: HomeAssistant):
    stub = _FetchStub(result=_dd(date(2026, 10, 11), {"NT": 3.0}))
    coord = await _coordinator(hass, stub)
    with freeze_time("2026-10-12 00:15:00"):
        await coord._async_do_daily_fetch()
    assert coord.data["daily_consumption_slot_1"] == 3.0
    assert coord.data["daily_consumption_slot_2"] == 0.0


async def test_zone_resolver_combines_schedule_and_day_types(hass: HomeAssistant):
    """Dated Mon-Fri change from 2027 on; Saturday override stays; holiday = Sunday."""
    new_week = [
        {"time": "00:00", "name": "NT"},
        {"time": "07:00", "name": "HT"},
        {"time": "21:00", "name": "NT"},
    ]
    schedule = [
        {"valid_from": None, "zones": WEEKDAY},
        {"valid_from": "2027-01-01", "zones": new_week},
    ]
    coord = await _coordinator(
        hass, _FetchStub(result=None), **{CONF_ZONE_SCHEDULE: schedule}
    )
    resolve = coord.zone_resolver()

    def layout(day: date) -> list[tuple[str, str]]:
        return [(t.strftime("%H:%M"), n) for t, n in resolve(day)]

    as_pairs = lambda zones: [(z["time"], z["name"]) for z in zones]  # noqa: E731
    assert layout(date(2026, 12, 30)) == as_pairs(WEEKDAY)  # Wed, old layout
    assert layout(date(2027, 1, 5)) == as_pairs(new_week)  # Tue, new layout
    assert layout(date(2027, 1, 9)) == as_pairs(SATURDAY)  # Sat override
    assert layout(date(2027, 1, 6)) == as_pairs(new_week)  # Epiphany: state holiday only
    assert layout(date(2027, 5, 3)) == as_pairs(new_week)  # Monday
    assert layout(date(2027, 5, 1)) == as_pairs(SUNDAY)  # Labour Day (Saturday)


async def test_publication_guard_uses_all_layouts(hass: HomeAssistant):
    coord = await _coordinator(hass, _FetchStub(result=None))
    snap = coord._zones_config_all
    assert snap["saturday"] == SATURDAY
    assert snap["sunday"] == SUNDAY
    assert snap["holidays_as_sunday"] is True
    assert coord._slot_zone_names == ["NT", "HT"]


async def test_day_types_listed_for_export(hass: HomeAssistant):
    coord = await _coordinator(hass, _FetchStub(result=None))
    labels = [label for label, _ in coord.zone_day_types()]
    assert labels == ["Samstag", "Sonntag", "Feiertag (bundeseinheitlich)"]
    plain = await _coordinator(
        hass,
        _FetchStub(result=None),
        **{CONF_TARIFF_ZONES_SATURDAY: [], CONF_TARIFF_ZONES_SUNDAY: [],
           CONF_HOLIDAYS_AS_SUNDAY: False},
    )
    assert plain.zone_day_types() == []


# --- aggregation + export ------------------------------------------------------


def _readings() -> list[MeterReading]:
    """Sat 2026-10-10 (HT 06-13) and Sun 2026-10-11 (all NT)."""
    def imp(d, h, m, v):
        return MeterReading(datetime(2026, 10, d, h, m, 1), OBIS_IMPORT, v, "kWh", "valid")

    return [
        imp(10, 0, 0, 1000.0),
        imp(10, 6, 0, 1001.0),
        imp(10, 13, 0, 1004.0),
        imp(11, 0, 0, 1005.0),
        imp(12, 0, 0, 1007.0),
    ]


def _resolver(day: date):
    data = _data()
    base = [{"time": z["time"], "name": z["name"]} for z in WEEKDAY]
    return [
        (time.fromisoformat(z["time"]), z["name"])
        for z in day_zones_config(data, base, day)
    ]


def test_aggregation_splits_weekend_days_by_their_layout():
    saturday, sunday = build_daily_summary(_readings(), _resolver)
    assert saturday.zone_consumptions == {"NT": 2.0, "HT": 3.0}
    assert sunday.zone_consumptions == {"NT": 2.0}
    assert sunday.consumption_total == 2.0


def test_xlsx_has_union_columns_and_documents_day_types(tmp_path):
    summary = build_daily_summary(_readings(), _resolver)
    meta = {
        "meter_id": "M",
        "from": "2026-10-10",
        "to": "2026-10-12",
        "zones": [(z["time"], z["name"]) for z in WEEKDAY],
        "day_type_zones": [
            {"label": "Samstag", "zones": [(z["time"], z["name"]) for z in SATURDAY]},
            {"label": "Sonntag", "zones": [(z["time"], z["name"]) for z in SUNDAY]},
        ],
    }
    path = tmp_path / "x.xlsx"
    write_xlsx(path, _readings(), summary, meta)
    wb = load_workbook(path)
    headers = [c.value for c in wb["Tarifzonen"][1]]
    # 06:00, 13:00 and 22:00 all occur in some layout -> three Bezug columns
    for t in ("06:00", "13:00", "22:00"):
        assert f"Bezug {t} (kWh)" in headers
    info = "\n".join(
        str(row[0].value) for row in wb["Definition"].iter_rows() if row[0].value
    )
    assert "Samstag (abweichend)" in info
    assert "Sonntag (abweichend)" in info


# --- restart with a cached day -----------------------------------------------


def _store(hass_storage: dict, entry: MockConfigEntry, data: dict) -> None:
    key = f"{DOMAIN}_{entry.entry_id}"
    hass_storage[key] = {"version": STORE_VERSION, "minor_version": 1, "key": key, "data": data}


def _cached(day: str, zones: list[dict], **extra) -> dict:
    return {
        "date": day,
        "daily_consumption_total": 3.0,
        "daily_feedin_total": 0.0,
        "meter_consumption_prev_day_close": 1003.0,
        "meter_feedin_prev_day_close": 0.0,
        "daily_consumption_slot_1": 3.0,
        "daily_consumption_slot_2": 0.0,
        "_tariff_zones": zones,
        **extra,
    }


async def _restart(hass, hass_storage, cached: dict, now: str) -> SmgwCoordinator:
    await hass.config.async_set_time_zone("Europe/Berlin")
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_METER_ID: "M", **_data()})
    entry.add_to_hass(hass)
    _store(hass_storage, entry, cached)
    coord = SmgwCoordinator(hass, entry, _FetchStub(result=None))
    with freeze_time(now):
        await coord.async_setup()
    return coord


async def test_restart_keeps_sunday_values_published(hass, hass_storage):
    """Sunday's cache must survive a restart on Monday (layouts unchanged)."""
    entry_all = {
        "weekday": WEEKDAY, "saturday": SATURDAY, "sunday": SUNDAY,
        "holidays_as_sunday": True,
    }
    coord = await _restart(
        hass, hass_storage,
        _cached("2026-10-11", SUNDAY, _tariff_zones_all=entry_all),
        "2026-10-12 09:00:00",
    )
    assert coord.data["daily_consumption_slot_1"] == 3.0
    assert coord.data["daily_consumption_slot_2"] == 0.0
    await coord.async_unload()


async def test_restart_with_legacy_cache_keeps_values(hass, hass_storage):
    """A cache written before day types existed (no _tariff_zones_all)."""
    coord = await _restart(
        hass, hass_storage,
        _cached("2026-10-05", WEEKDAY),  # Monday, computed with Mon-Fri zones
        "2026-10-06 09:00:00",
    )
    assert coord.data["daily_consumption_slot_1"] == 3.0
    await coord.async_unload()


async def test_changed_saturday_layout_withholds_cached_zone_values(
    hass, hass_storage
):
    """If the layouts differ from the cache's snapshot, per-zone values go."""
    stale_all = {
        "weekday": WEEKDAY, "saturday": [], "sunday": SUNDAY,
        "holidays_as_sunday": True,
    }
    coord = await _restart(
        hass, hass_storage,
        _cached("2026-10-11", SUNDAY, _tariff_zones_all=stale_all),
        "2026-10-12 09:00:00",
    )
    assert "daily_consumption_slot_1" not in coord.data
    assert coord.data["daily_consumption_total"] == 3.0
    await coord.async_unload()
