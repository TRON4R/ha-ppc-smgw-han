"""Config-flow tests: meter selection, reauth, and the meter-swap guard.

The SMGW HTTP layer is patched at the ``async_validate_and_get_device_info``
boundary so no gateway is contacted; ``async_setup_entry`` is patched on the
create path so creating an entry does not kick off a real coordinator setup.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.smgw_han.const import (
    CONF_INSTANCE_ID,
    CONF_METER_ID,
    CONF_PASSWORD,
    CONF_TARIFF_SWITCH_HOUR,
    CONF_TARIFF_SWITCH_MINUTE,
    CONF_UPDATE_TIME,
    CONF_URL,
    CONF_USERNAME,
    DOMAIN,
)
from custom_components.smgw_han.smgw_client import SmgwAuthError, SmgwDeviceInfo

VALIDATE = (
    "custom_components.smgw_han.config_flow.SmgwClient"
    ".async_validate_and_get_device_info"
)
CLOSE = "custom_components.smgw_han.config_flow.SmgwClient.close"
SETUP_ENTRY = "custom_components.smgw_han.async_setup_entry"

URL = "https://192.168.100.100/cgi-bin/hanservice.cgi"
USER_INPUT = {
    CONF_URL: URL,
    CONF_USERNAME: "user",
    CONF_PASSWORD: "pw",
    CONF_TARIFF_SWITCH_HOUR: "5",
    CONF_TARIFF_SWITCH_MINUTE: "0",
    CONF_UPDATE_TIME: "00:15:00",
}


@pytest.fixture(autouse=True)
def _enable(enable_custom_integrations):
    yield


def _info(meter_id: str, available: list[str]) -> SmgwDeviceInfo:
    return SmgwDeviceInfo(
        meter_id=meter_id,
        firmware_version="00861-34788",
        available_meter_ids=available,
    )


def _entry(meter_id: str, instance_id: int = 1, **extra) -> MockConfigEntry:
    data = {
        CONF_URL: URL,
        CONF_USERNAME: "user",
        CONF_PASSWORD: "pw",
        CONF_METER_ID: meter_id,
        CONF_INSTANCE_ID: instance_id,
        CONF_TARIFF_SWITCH_HOUR: 5,
        CONF_TARIFF_SWITCH_MINUTE: 0,
        CONF_UPDATE_TIME: "00:15:00",
        **extra,
    }
    return MockConfigEntry(
        domain=DOMAIN, unique_id=f"{meter_id}:user", data=data
    )


async def test_user_flow_single_meter_creates_entry(hass: HomeAssistant):
    with (
        patch(VALIDATE, return_value=_info("1lgz0072999211", ["1lgz0072999211"])),
        patch(CLOSE, return_value=None),
        patch(SETUP_ENTRY, return_value=True),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        assert result["type"] == FlowResultType.FORM
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], dict(USER_INPUT)
        )
        await hass.async_block_till_done()

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_METER_ID] == "1lgz0072999211"
    assert result["data"][CONF_INSTANCE_ID] == 1


async def test_user_flow_multi_meter_goes_through_select_step(
    hass: HomeAssistant,
):
    with (
        patch(
            VALIDATE,
            return_value=_info(
                "1lgz0072999211", ["1lgz0072999211", "1lgz0088888888"]
            ),
        ),
        patch(CLOSE, return_value=None),
        patch(SETUP_ENTRY, return_value=True),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], dict(USER_INPUT)
        )
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "select_meter"
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_METER_ID: "1lgz0088888888"}
        )
        await hass.async_block_till_done()

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_METER_ID] == "1lgz0088888888"


async def test_user_flow_duplicate_aborts(hass: HomeAssistant):
    _entry("1lgz0072999211").add_to_hass(hass)
    with (
        patch(VALIDATE, return_value=_info("1lgz0072999211", ["1lgz0072999211"])),
        patch(CLOSE, return_value=None),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], dict(USER_INPUT)
        )

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_user_flow_invalid_auth(hass: HomeAssistant):
    with (
        patch(VALIDATE, side_effect=SmgwAuthError("bad creds")),
        patch(CLOSE, return_value=None),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], dict(USER_INPUT)
        )

    assert result["type"] == FlowResultType.FORM
    assert result["errors"]["base"] == "invalid_auth"


async def _open_settings(hass: HomeAssistant, entry: MockConfigEntry):
    """Open the options flow and navigate to the 'settings' step."""
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == FlowResultType.MENU
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "settings"}
    )
    assert result["step_id"] == "settings"
    return result


async def test_meter_swap_keeps_identity(hass: HomeAssistant):
    """⭐ A single-meter hardware swap updates the stored meter_id but must
    leave instance_id / unique_id / entry_id untouched — history continuity
    rides on instance_id, not on the config-entry unique_id. This is the
    precondition for the later unique_id-sync work."""
    entry = _entry("OLDMETER", instance_id=1)
    entry.add_to_hass(hass)
    original_entry_id = entry.entry_id

    result = await _open_settings(hass, entry)
    with (
        patch(VALIDATE, return_value=_info("NEWMETER", ["NEWMETER"])),
        patch(CLOSE, return_value=None),
        patch.object(
            hass.config_entries, "async_reload", new_callable=AsyncMock
        ),
    ):
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], dict(USER_INPUT)
        )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert entry.data[CONF_METER_ID] == "NEWMETER"  # swap adopted
    assert entry.data[CONF_INSTANCE_ID] == 1  # unchanged
    assert entry.unique_id == "OLDMETER:user"  # unchanged (known gap)
    assert entry.entry_id == original_entry_id  # same device/entities


async def test_meter_swap_refused_on_multimeter_setup(hass: HomeAssistant):
    """With a sibling entry on the same SMGW, the 'only one meter left' case
    must NOT auto-repoint the entry — it surfaces an error instead."""
    entry = _entry("OLDMETER", instance_id=1)
    entry.add_to_hass(hass)
    _entry("SIBLING", instance_id=2).add_to_hass(hass)

    result = await _open_settings(hass, entry)
    with (
        patch(VALIDATE, return_value=_info("NEWMETER", ["NEWMETER"])),
        patch(CLOSE, return_value=None),
    ):
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], dict(USER_INPUT)
        )

    assert result["type"] == FlowResultType.FORM
    assert result["errors"]["base"] == "configured_meter_missing"
    assert entry.data[CONF_METER_ID] == "OLDMETER"  # left untouched
