"""The SMGW HAN integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.typing import ConfigType

from .const import (
    CONF_INSTANCE_ID,
    CONF_PASSWORD,
    CONF_URL,
    CONF_USERNAME,
)
from .coordinator import SmgwTafCoordinator
from .services import async_setup_services
from .smgw_client import SmgwClient

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR]

type SmgwTafConfigEntry = ConfigEntry[SmgwTafCoordinator]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up integration-wide resources (the export service)."""
    async_setup_services(hass)
    return True


async def async_setup_entry(
    hass: HomeAssistant, entry: SmgwTafConfigEntry
) -> bool:
    """Set up SMGW HAN from a config entry."""
    # One-time backfill for entries created with pre-2.0 versions:
    # - instance_id was introduced in 2.0; absent => entry predates multi-device
    #   support and is the sole instance, so it takes id 1 (matches its
    #   historical hardcoded entity slug "smgw_meter1_*", preserving history).
    # - unique_id was bare meter_id; new format is "meter_id:username" so the
    #   same physical SMGW can be added once per distinct login.
    new_data = dict(entry.data)
    new_unique_id = entry.unique_id
    needs_update = False
    if CONF_INSTANCE_ID not in new_data:
        new_data[CONF_INSTANCE_ID] = 1
        needs_update = True
    if entry.unique_id and ":" not in entry.unique_id:
        username = entry.data.get(CONF_USERNAME)
        if username:
            new_unique_id = f"{entry.unique_id}:{username}"
            needs_update = True
    if needs_update:
        hass.config_entries.async_update_entry(
            entry, data=new_data, unique_id=new_unique_id
        )

    client = SmgwClient(
        base_url=entry.data[CONF_URL],
        username=entry.data[CONF_USERNAME],
        password=entry.data[CONF_PASSWORD],
    )

    coordinator = SmgwTafCoordinator(hass, entry, client)
    await coordinator.async_setup()

    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_remove_config_entry_device(
    hass: HomeAssistant,
    config_entry: SmgwTafConfigEntry,
    device_entry: dr.DeviceEntry,
) -> bool:
    """Allow removal of a device from the UI."""
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: SmgwTafConfigEntry
) -> bool:
    """Unload a config entry.

    Platforms are unloaded first, then the coordinator is cleaned up.
    """
    unload_ok = await hass.config_entries.async_unload_platforms(
        entry, PLATFORMS
    )

    if unload_ok:
        await entry.runtime_data.async_unload()

    return unload_ok
