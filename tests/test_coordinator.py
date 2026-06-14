"""Tests for SmgwTafCoordinator error mapping and the no-data repair issue."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    HomeAssistantError,
    ServiceValidationError,
)
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.update_coordinator import UpdateFailed
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.smgw_han.const import CONF_METER_ID, DOMAIN, SENSOR_DATE
from custom_components.smgw_han.coordinator import SmgwTafCoordinator
from custom_components.smgw_han.smgw_client import (
    DailyData,
    SmgwAuthError,
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
        self, target_date, tariff_switch_hour=5,
        tariff_switch_minute=0, target_meter_id=None,
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
        import_midnight=1000.0,
        import_tariff_switch=1002.5,
        import_next_midnight=1010.0,
        export_midnight=500.0,
        export_next_midnight=503.0,
        daily_import_total=10.0,
        daily_import_go=2.5,
        daily_import_standard=7.5,
        daily_export_total=3.0,
    )


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
