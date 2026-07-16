"""Tests for SmgwTafCoordinator error mapping and the no-data repair issue."""

from __future__ import annotations

import asyncio
from datetime import date, datetime, time, timedelta

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryNotReady,
    HomeAssistantError,
    ServiceValidationError,
)
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.update_coordinator import UpdateFailed
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.smgw_han import async_remove_entry, gateway_lock
from custom_components.smgw_han.const import (
    CONF_METER_ID,
    CONF_TARIFF_ZONES,
    DOMAIN,
    SENSOR_DATE,
    SENSOR_METER_CONSUMPTION_PREV_DAY_CLOSE,
    SENSOR_METER_FEEDIN_PREV_DAY_CLOSE,
    STORE_VERSION,
)
from custom_components.smgw_han.coordinator import (
    SmgwTafCoordinator,
    no_data_issue_id,
)
from custom_components.smgw_han.smgw_client import (
    DailyData,
    SmgwAuthError,
    SmgwClient,
    SmgwClientError,
    SmgwConnectionError,
    SmgwNoDataError,
    SmgwServerError,
)


@pytest.fixture(autouse=True)
def _enable(enable_custom_integrations):
    yield


class _StubClient:
    """Client stub whose CMS download always raises the given exception."""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    async def async_download_cms(self, from_dt, to_dt, target_meter_id=None):
        raise self._exc

    async def close(self):
        pass


def _coordinator(hass: HomeAssistant, exc: Exception) -> SmgwTafCoordinator:
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_METER_ID: "M"})
    entry.add_to_hass(hass)
    return SmgwTafCoordinator(hass, entry, _StubClient(exc))


class _FetchStub:
    """Client stub for the daily fetch: returns ``result`` or raises ``exc``."""

    def __init__(self, *, result: DailyData | None = None,
                 exc: Exception | None = None) -> None:
        self._result = result
        self._exc = exc
        self.calls = 0

    async def async_fetch_daily_data(
        self, target_date, zones=None, target_meter_id=None,
    ):
        self.calls += 1
        if self._exc is not None:
            raise self._exc
        return self._result

    async def close(self):
        pass


def _fetch_coordinator(
    hass: HomeAssistant, stub: _FetchStub
) -> SmgwTafCoordinator:
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_METER_ID: "M"})
    entry.add_to_hass(hass)
    return SmgwTafCoordinator(hass, entry, stub)


def _daily_data(target_date) -> DailyData:
    return DailyData(
        date=target_date,
        import_boundaries=[1000.0, 1002.5, 1010.0],
        export_midnight=500.0,
        export_next_midnight=503.0,
        zone_totals={"Zeitfenster 1": 2.5, "Zeitfenster 2": 7.5},
        daily_import_total=10.0,
        daily_export_total=3.0,
    )


def test_daily_data_to_dict_generates_slot_and_switch_keys():
    # 3 zones over 4 segments (one name repeated): slot index follows first
    # appearance, inner boundaries become switch_{n}.
    dd = DailyData(
        date=datetime(2026, 5, 15).date(),
        import_boundaries=[1000.0, 1001.0, 1003.0, 1006.0, 1010.0],
        export_midnight=500.0,
        export_next_midnight=503.0,
        zone_totals={"Standard": 6.0, "Niedrig": 2.0, "Hoch": 2.0},
        daily_import_total=10.0,
        daily_export_total=3.0,
    )
    data = SmgwTafCoordinator._daily_data_to_dict(dd)
    assert data["daily_consumption_slot_1"] == 6.0  # Standard
    assert data["daily_consumption_slot_2"] == 2.0  # Niedrig
    assert data["daily_consumption_slot_3"] == 2.0  # Hoch
    assert "daily_consumption_slot_4" not in data
    assert data["meter_consumption_prev_day_close"] == 1010.0  # closing (#35)
    assert data["meter_consumption_switch_1"] == 1001.0
    assert data["meter_consumption_switch_2"] == 1003.0
    assert data["meter_consumption_switch_3"] == 1006.0
    assert "meter_consumption_switch_4" not in data
    assert data["daily_consumption_total"] == 10.0
    assert data["daily_feedin_total"] == 3.0


async def test_prev_day_close_sensors_report_day_end(hass: HomeAssistant):
    """Endstand Vortag = closing reading of the fetched day, not opening.

    Regression test for issue #35: the sensors published the opening
    boundary (midnight at the START of the fetched day = close of the day
    before), making the value 24 h staler than the entity name promises.
    """
    yesterday = dt_util.now().date() - timedelta(days=1)
    coord = _fetch_coordinator(
        hass, _FetchStub(result=_daily_data(yesterday))
    )

    await coord._async_do_daily_fetch()

    assert coord.data[SENSOR_METER_CONSUMPTION_PREV_DAY_CLOSE] == 1010.0
    assert coord.data[SENSOR_METER_FEEDIN_PREV_DAY_CLOSE] == 503.0
    # The tariff-switch reading belongs to the fetched day itself and was
    # never affected — it must stay the inner boundary.
    assert coord.data["meter_consumption_switch_1"] == 1002.5


async def test_no_data_creates_repair_issue_and_keeps_data(hass: HomeAssistant):
    coord = _fetch_coordinator(hass, _FetchStub(exc=SmgwNoDataError("empty")))
    # Existing (older) data must survive a no-data fetch untouched.
    old = {SENSOR_DATE: "2000-01-01", "daily_consumption_total": 1.23}
    coord.async_set_updated_data(dict(old))

    await coord._async_do_daily_fetch()  # must NOT raise

    reg = ir.async_get(hass)
    assert reg.async_get_issue(DOMAIN, coord._no_data_issue_id) is not None
    assert coord.data == old  # sensor values untouched


async def test_successful_fetch_clears_repair_issue(hass: HomeAssistant):
    yesterday = dt_util.now().date() - timedelta(days=1)
    coord = _fetch_coordinator(
        hass, _FetchStub(result=_daily_data(yesterday))
    )
    # Pre-existing issue from an earlier no-data night.
    ir.async_create_issue(
        hass, DOMAIN, coord._no_data_issue_id,
        is_fixable=False, severity=ir.IssueSeverity.WARNING,
        translation_key="no_recent_data",
        translation_placeholders={"date": "x", "last_date": "y"},
    )

    await coord._async_do_daily_fetch()

    reg = ir.async_get(hass)
    assert reg.async_get_issue(DOMAIN, coord._no_data_issue_id) is None
    assert coord.data[SENSOR_DATE] == yesterday.isoformat()


async def test_auth_error_raises_config_entry_auth_failed(hass: HomeAssistant):
    coord = _fetch_coordinator(hass, _FetchStub(exc=SmgwAuthError("bad")))
    with pytest.raises(ConfigEntryAuthFailed):
        await coord._async_do_daily_fetch()
    reg = ir.async_get(hass)
    assert reg.async_get_issue(DOMAIN, coord._no_data_issue_id) is None


async def test_connection_error_raises_update_failed(hass: HomeAssistant):
    coord = _fetch_coordinator(
        hass, _FetchStub(exc=SmgwConnectionError("down"))
    )
    with pytest.raises(UpdateFailed):
        await coord._async_do_daily_fetch()
    reg = ir.async_get(hass)
    assert reg.async_get_issue(DOMAIN, coord._no_data_issue_id) is None


async def test_remove_entry_clears_repair_issue(hass: HomeAssistant):
    # An active no-data issue must not survive removal of the config entry.
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_METER_ID: "M"})
    entry.add_to_hass(hass)
    issue_id = no_data_issue_id(entry.entry_id)
    ir.async_create_issue(
        hass, DOMAIN, issue_id,
        is_fixable=False, severity=ir.IssueSeverity.WARNING,
        translation_key="no_recent_data",
        translation_placeholders={"date": "x", "last_date": "y"},
    )
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None

    await async_remove_entry(hass, entry)

    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None


# ---------------------------------------------------------------------------
# Zone-change cache handling (async_setup) + startup retry semantics
# ---------------------------------------------------------------------------

OLD_ZONES = [
    {"time": "00:00", "name": "Go"},
    {"time": "05:00", "name": "Standard"},
]
NEW_ZONES = [
    {"time": "00:00", "name": "Nacht"},
    {"time": "06:00", "name": "Tag"},
]


def _stored_day(zones: list[dict], day: str) -> dict:
    return {
        SENSOR_DATE: day,
        "daily_consumption_total": 10.0,
        "daily_consumption_slot_1": 2.5,
        "daily_consumption_slot_2": 7.5,
        "daily_feedin_total": 3.0,
        "meter_consumption_prev_day_close": 1000.0,
        "meter_consumption_switch_1": 1002.5,
        "meter_feedin_prev_day_close": 500.0,
        "_tariff_zones": zones,
    }


def _coordinator_with_store(
    hass: HomeAssistant,
    hass_storage: dict,
    stub: _FetchStub,
    entry_zones: list[dict],
    stored: dict | None,
) -> SmgwTafCoordinator:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_METER_ID: "M", CONF_TARIFF_ZONES: entry_zones},
    )
    entry.add_to_hass(hass)
    if stored is not None:
        key = f"{DOMAIN}_{entry.entry_id}"
        hass_storage[key] = {
            "version": STORE_VERSION,
            "minor_version": 1,
            "key": key,
            "data": stored,
        }
    return SmgwTafCoordinator(hass, entry, stub)


async def test_zone_change_withholds_stale_zone_values_on_no_data(
    hass: HomeAssistant, hass_storage: dict
):
    # Cached day was computed with OLD zones, entry now has NEW zones, and
    # the refetch yields no data: the per-zone values must NOT be published
    # under the new zone names; zone-independent values are kept.
    yesterday = (dt_util.now().date() - timedelta(days=1)).isoformat()
    stub = _FetchStub(exc=SmgwNoDataError("empty"))
    coord = _coordinator_with_store(
        hass, hass_storage, stub, NEW_ZONES, _stored_day(OLD_ZONES, yesterday)
    )

    await coord.async_setup()

    assert "daily_consumption_slot_1" not in coord.data
    assert "daily_consumption_slot_2" not in coord.data
    assert "meter_consumption_switch_1" not in coord.data
    assert coord.data["daily_consumption_total"] == 10.0
    assert coord.data["meter_consumption_prev_day_close"] == 1000.0
    reg = ir.async_get(hass)
    assert reg.async_get_issue(DOMAIN, coord._no_data_issue_id) is not None
    await coord.async_unload()


async def test_zone_change_withholds_stale_zone_values_on_connection_error(
    hass: HomeAssistant, hass_storage: dict
):
    yesterday = (dt_util.now().date() - timedelta(days=1)).isoformat()
    stub = _FetchStub(exc=SmgwConnectionError("down"))
    coord = _coordinator_with_store(
        hass, hass_storage, stub, NEW_ZONES, _stored_day(OLD_ZONES, yesterday)
    )

    # Cached (stripped) data exists -> setup must NOT raise; the nightly
    # fetch retries.
    await coord.async_setup()

    assert "daily_consumption_slot_1" not in coord.data
    assert coord.data["daily_consumption_total"] == 10.0
    await coord.async_unload()


async def test_zone_change_withholds_stale_zone_values_on_auth_error(
    hass: HomeAssistant, hass_storage: dict
):
    yesterday = (dt_util.now().date() - timedelta(days=1)).isoformat()
    stub = _FetchStub(exc=SmgwAuthError("bad"))
    coord = _coordinator_with_store(
        hass, hass_storage, stub, NEW_ZONES, _stored_day(OLD_ZONES, yesterday)
    )

    with pytest.raises(ConfigEntryAuthFailed):
        await coord.async_setup()

    # The stripped cache was published before the fetch attempt.
    assert "daily_consumption_slot_1" not in coord.data
    assert coord.data["daily_consumption_total"] == 10.0
    # Aborted setup must not leave a daily time listener behind.
    assert coord._unsub_time_listener is None


async def test_matching_zones_keep_cached_slot_values(
    hass: HomeAssistant, hass_storage: dict
):
    yesterday = (dt_util.now().date() - timedelta(days=1)).isoformat()
    stub = _FetchStub(result=None)
    coord = _coordinator_with_store(
        hass, hass_storage, stub, OLD_ZONES, _stored_day(OLD_ZONES, yesterday)
    )

    await coord.async_setup()

    assert coord.data["daily_consumption_slot_1"] == 2.5
    assert coord.data["daily_consumption_slot_2"] == 7.5
    assert stub.calls == 0  # nothing changed -> no fetch
    assert coord._unsub_time_listener is not None  # successful setup schedules
    await coord.async_unload()


async def test_no_cache_and_connection_error_raises_not_ready(
    hass: HomeAssistant, hass_storage: dict
):
    # Fresh install, SMGW temporarily unreachable: fail setup so HA retries
    # with backoff instead of leaving empty sensors until the nightly fetch.
    stub = _FetchStub(exc=SmgwConnectionError("down"))
    coord = _coordinator_with_store(hass, hass_storage, stub, OLD_ZONES, None)

    with pytest.raises(ConfigEntryNotReady):
        await coord.async_setup()
    # Aborted setup must not leave a daily time listener behind (HA retries
    # would otherwise pile up orphaned listeners on discarded coordinators).
    assert coord._unsub_time_listener is None


async def test_no_cache_and_no_data_loads_with_repair_issue(
    hass: HomeAssistant, hass_storage: dict
):
    # Fresh install against a gateway without daily values: NOT a setup
    # failure — the integration loads and the repair issue explains why.
    stub = _FetchStub(exc=SmgwNoDataError("empty"))
    coord = _coordinator_with_store(hass, hass_storage, stub, OLD_ZONES, None)

    await coord.async_setup()

    reg = ir.async_get(hass)
    assert reg.async_get_issue(DOMAIN, coord._no_data_issue_id) is not None
    await coord.async_unload()


# ---------------------------------------------------------------------------
# Gateway-wide session lock
# ---------------------------------------------------------------------------


async def test_gateway_lock_shared_per_gateway_and_login(hass: HomeAssistant):
    lock_a = gateway_lock(hass, "https://gw/cgi-bin/hanservice.cgi", "user")
    lock_b = gateway_lock(hass, "https://gw/cgi-bin/hanservice.cgi/", "user")
    lock_c = gateway_lock(hass, "https://gw/cgi-bin/hanservice.cgi", "other")
    assert lock_a is lock_b  # trailing slash normalized -> same gateway
    assert lock_a is not lock_c  # different login -> own lock


async def test_shared_lock_serializes_two_clients():
    # Two config entries on the same gateway share one injected lock: their
    # SMGW sessions must never overlap.
    lock = asyncio.Lock()
    client_1 = SmgwClient("https://gw", "user", "pw", lock=lock)
    client_2 = SmgwClient("https://gw", "user", "pw", lock=lock)

    active = 0
    overlap = False

    async def fake_locked(*args, **kwargs):
        nonlocal active, overlap
        active += 1
        if active > 1:
            overlap = True
        await asyncio.sleep(0)
        active -= 1
        return None

    client_1._fetch_daily_data_locked = fake_locked
    client_2._fetch_daily_data_locked = fake_locked

    zones = [(time(0, 0), "Go"), (time(5, 0), "Standard")]
    await asyncio.gather(
        client_1.async_fetch_daily_data(date(2026, 5, 15), zones),
        client_2.async_fetch_daily_data(date(2026, 5, 15), zones),
    )
    assert not overlap


async def test_server_error_maps_to_no_data(hass: HomeAssistant):
    coord = _coordinator(hass, SmgwServerError("HTTP error: 500"))
    with pytest.raises(ServiceValidationError) as ei:
        await coord.async_download_cms(
            datetime(2025, 1, 1), datetime(2025, 1, 2)
        )
    assert ei.value.translation_key == "no_data_in_range"


async def test_auth_error_maps_to_home_assistant_error(hass: HomeAssistant):
    coord = _coordinator(hass, SmgwAuthError("bad"))
    with pytest.raises(HomeAssistantError) as ei:
        await coord.async_download_cms(
            datetime(2025, 1, 1), datetime(2025, 1, 2)
        )
    assert not isinstance(ei.value, ServiceValidationError)
    assert ei.value.translation_key == "cms_auth_failed"


async def test_other_client_error_maps_to_home_assistant_error(
    hass: HomeAssistant,
):
    coord = _coordinator(hass, SmgwClientError("boom"))
    with pytest.raises(HomeAssistantError) as ei:
        await coord.async_download_cms(
            datetime(2025, 1, 1), datetime(2025, 1, 2)
        )
    assert not isinstance(ei.value, ServiceValidationError)
    assert ei.value.translation_key == "cms_download_failed"
