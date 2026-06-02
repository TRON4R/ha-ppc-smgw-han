"""Tests for SmgwTafCoordinator.async_download_cms error mapping."""

from __future__ import annotations

from datetime import datetime

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.smgw_han.const import CONF_METER_ID, DOMAIN
from custom_components.smgw_han.coordinator import SmgwTafCoordinator
from custom_components.smgw_han.smgw_client import (
    SmgwAuthError,
    SmgwClientError,
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
