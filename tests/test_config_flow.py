"""Config-flow tests: meter selection, reauth, and the meter-swap guard.

The SMGW HTTP layer is patched at the ``async_validate_and_get_device_info``
boundary so no gateway is contacted; ``async_setup_entry`` is patched on the
create path so creating an entry does not kick off a real coordinator setup.
"""

from __future__ import annotations

from datetime import datetime
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
    """⭐ A single-meter hardware swap adopts the new meter id AND re-syncs the
    config-entry unique_id (since v2.3.0), while instance_id / entry_id stay
    put — history/entities ride on instance_id, not on the unique_id, so the
    unique_id can safely follow the new meter id."""
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
    assert entry.data[CONF_INSTANCE_ID] == 1  # unchanged -> history preserved
    assert entry.unique_id == "NEWMETER:user"  # now re-synced to the new meter
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


def _other_entry(meter_id: str, username: str, instance_id: int) -> MockConfigEntry:
    """A second entry with an explicit username (so its unique_id can differ)."""
    return MockConfigEntry(
        domain=DOMAIN,
        unique_id=f"{meter_id}:{username}",
        data={
            CONF_URL: URL,
            CONF_USERNAME: username,
            CONF_PASSWORD: "pw",
            CONF_METER_ID: meter_id,
            CONF_INSTANCE_ID: instance_id,
        },
    )


async def test_settings_username_change_syncs_unique_id(hass: HomeAssistant):
    """Changing the username in settings re-syncs the unique_id (meter:user)
    while instance_id stays put."""
    entry = _entry("M", instance_id=1)  # unique_id "M:user"
    entry.add_to_hass(hass)

    result = await _open_settings(hass, entry)
    with (
        patch(VALIDATE, return_value=_info("M", ["M"])),  # meter unchanged
        patch(CLOSE, return_value=None),
        patch.object(
            hass.config_entries, "async_reload", new_callable=AsyncMock
        ),
    ):
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {**USER_INPUT, CONF_USERNAME: "user2"}
        )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert entry.data[CONF_USERNAME] == "user2"
    assert entry.unique_id == "M:user2"  # re-synced
    assert entry.data[CONF_INSTANCE_ID] == 1  # unchanged


async def test_settings_unique_id_collision_refused(hass: HomeAssistant):
    """If the new (meter, username) identity already belongs to another entry,
    the settings step refuses with duplicate_login and changes nothing."""
    entry_a = _entry("M", instance_id=1)  # "M:user"
    entry_a.add_to_hass(hass)
    _other_entry("M", "user2", instance_id=2).add_to_hass(hass)  # owns "M:user2"

    result = await _open_settings(hass, entry_a)
    with (
        patch(VALIDATE, return_value=_info("M", ["M"])),
        patch(CLOSE, return_value=None),
        patch.object(
            hass.config_entries, "async_reload", new_callable=AsyncMock
        ),
    ):
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {**USER_INPUT, CONF_USERNAME: "user2"}
        )

    assert result["type"] == FlowResultType.FORM
    assert result["errors"]["base"] == "duplicate_login"
    assert entry_a.unique_id == "M:user"  # unchanged
    assert entry_a.data[CONF_USERNAME] == "user"  # nothing saved


async def test_reauth_syncs_unique_id(hass: HomeAssistant):
    """Reauth with a new username re-syncs the unique_id."""
    entry = _entry("M", instance_id=1)  # "M:user"
    entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={
            "source": config_entries.SOURCE_REAUTH,
            "entry_id": entry.entry_id,
        },
        data=entry.data,
    )
    assert result["step_id"] == "reauth_confirm"
    with (
        patch(VALIDATE, return_value=_info("M", ["M"])),
        patch(CLOSE, return_value=None),
        patch.object(
            hass.config_entries, "async_reload", new_callable=AsyncMock
        ),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_USERNAME: "user2", CONF_PASSWORD: "newpw"},
        )
        await hass.async_block_till_done()

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.unique_id == "M:user2"
    assert entry.data[CONF_USERNAME] == "user2"


async def test_reauth_collision_refused(hass: HomeAssistant):
    """Reauth that would collide with another entry's identity is refused."""
    entry_a = _entry("M", instance_id=1)  # "M:user"
    entry_a.add_to_hass(hass)
    _other_entry("M", "user2", instance_id=2).add_to_hass(hass)  # owns "M:user2"

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={
            "source": config_entries.SOURCE_REAUTH,
            "entry_id": entry_a.entry_id,
        },
        data=entry_a.data,
    )
    with (
        patch(VALIDATE, return_value=_info("M", ["M"])),
        patch(CLOSE, return_value=None),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_USERNAME: "user2", CONF_PASSWORD: "newpw"},
        )

    assert result["type"] == FlowResultType.FORM
    assert result["errors"]["base"] == "duplicate_login"
    assert entry_a.unique_id == "M:user"  # unchanged


async def _open_export_dates(hass: HomeAssistant, entry: MockConfigEntry):
    """Drive the options export flow to the date-range step."""
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "export"}
    )
    assert result["step_id"] == "export"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "period": "last_month",
            "download_cms": True,
            "write_csv": False,
            "write_xlsx": False,
        },
    )
    assert result["step_id"] == "export_dates"
    return result


async def test_options_export_rejects_future_day(hass: HomeAssistant):
    # The options flow now runs the same _validate_range as the service path.
    entry = _entry("M", instance_id=1)
    entry.add_to_hass(hass)
    result = await _open_export_dates(hass, entry)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "from_datetime": datetime(2026, 1, 1, 0, 0, 0),
            "to_datetime": datetime(2099, 1, 1, 0, 0, 0),
        },
    )
    assert result["type"] == FlowResultType.FORM
    assert result["errors"]["base"] == "to_in_future"


async def test_options_export_rejects_too_old(hass: HomeAssistant):
    entry = _entry("M", instance_id=1)
    entry.add_to_hass(hass)
    result = await _open_export_dates(hass, entry)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "from_datetime": datetime(2000, 1, 1, 0, 0, 0),
            "to_datetime": datetime(2026, 5, 1, 0, 0, 0),
        },
    )
    assert result["type"] == FlowResultType.FORM
    assert result["errors"]["base"] == "from_too_old"
