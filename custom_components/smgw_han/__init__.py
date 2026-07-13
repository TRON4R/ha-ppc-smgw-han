"""The SMGW HAN integration."""

from __future__ import annotations

import asyncio
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import (
    config_validation as cv,
    device_registry as dr,
    issue_registry as ir,
)
from homeassistant.helpers.typing import ConfigType

from .const import (
    CONF_INSTANCE_ID,
    CONF_PASSWORD,
    CONF_TARIFF_SWITCH_HOUR,
    CONF_TARIFF_SWITCH_MINUTE,
    CONF_TARIFF_ZONES,
    CONF_URL,
    CONF_USERNAME,
    DEFAULT_TARIFF_SWITCH_HOUR,
    DEFAULT_TARIFF_SWITCH_MINUTE,
    DOMAIN,
    ZONE_NAME,
    ZONE_TIME,
)
from .coordinator import SmgwTafCoordinator, no_data_issue_id
from .services import async_setup_services
from .smgw_client import SmgwClient

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR]

# This integration is configured exclusively via the config flow (config
# entries); it has no YAML configuration. Declaring the schema explicitly
# satisfies hassfest's CONFIG_SCHEMA requirement for integrations that
# implement async_setup (here only to register the export services).
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

type SmgwTafConfigEntry = ConfigEntry[SmgwTafCoordinator]


def gateway_lock(
    hass: HomeAssistant, base_url: str, username: str
) -> asyncio.Lock:
    """One shared lock per SMGW gateway + login, across all config entries.

    The SMGW allows only one active session per account. A per-client lock
    only serializes one config entry with itself — two entries on the same
    gateway (multi-meter SMGW, or separate import/feed-in logins pointing at
    the same account) would still log in concurrently and invalidate each
    other's session. Every ``SmgwClient`` for the same ``(URL, username)``
    must therefore share this lock; the config flow and reauth use it too.
    """
    locks: dict[tuple[str, str], asyncio.Lock] = hass.data.setdefault(
        DOMAIN, {}
    ).setdefault("gateway_locks", {})
    key = (base_url.rstrip("/"), username)
    if key not in locks:
        locks[key] = asyncio.Lock()
    return locks[key]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up integration-wide resources (the export service)."""
    async_setup_services(hass)
    return True


async def async_migrate_entry(
    hass: HomeAssistant, entry: SmgwTafConfigEntry
) -> bool:
    """Migrate old config entries to the current version.

    Version 1 -> 2 (v3.0.0): the single tariff switch time
    (CONF_TARIFF_SWITCH_HOUR/MINUTE) becomes the ordered tariff-zone list
    CONF_TARIFF_ZONES. The fallback zone names "Zeitfenster 1/2" reproduce
    the pre-3.0 entity display names, so migrated installations look
    unchanged. A degenerate legacy switch time of 00:00 yields two zones with
    equal boundaries — slot 1 then computes to 0 kWh, exactly as before.
    """
    if entry.version > 2:
        # Downgrade from a future version — refuse to load.
        return False

    if entry.version == 1:
        data = dict(entry.data)
        hour = int(
            data.pop(CONF_TARIFF_SWITCH_HOUR, DEFAULT_TARIFF_SWITCH_HOUR)
        )
        minute = int(
            data.pop(CONF_TARIFF_SWITCH_MINUTE, DEFAULT_TARIFF_SWITCH_MINUTE)
        )
        if CONF_TARIFF_ZONES not in data:
            data[CONF_TARIFF_ZONES] = [
                {ZONE_TIME: "00:00", ZONE_NAME: "Zeitfenster 1"},
                {ZONE_TIME: f"{hour:02d}:{minute:02d}", ZONE_NAME: "Zeitfenster 2"},
            ]
        hass.config_entries.async_update_entry(entry, data=data, version=2)
        _LOGGER.info(
            "Migrated config entry %s to version 2 (tariff zones: %s)",
            entry.entry_id,
            data[CONF_TARIFF_ZONES],
        )

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
        lock=gateway_lock(
            hass, entry.data[CONF_URL], entry.data[CONF_USERNAME]
        ),
    )

    coordinator = SmgwTafCoordinator(hass, entry, client)
    try:
        await coordinator.async_setup()
    except Exception:
        # Aborted setup (ConfigEntryNotReady -> HA retries with backoff,
        # ConfigEntryAuthFailed -> reauth): runtime_data is never set, so
        # nothing would ever unload this instance — clean up here so no time
        # listener or HTTP client outlives the failed attempt.
        await coordinator.async_unload()
        raise

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


async def async_remove_entry(
    hass: HomeAssistant, entry: SmgwTafConfigEntry
) -> None:
    """Clean up the per-entry repair issue when the entry is removed.

    The coordinator deletes the "no recent data" issue on a successful fetch,
    but if the entry is removed while that issue is still active, nothing else
    would clear it — leaving a stale repair entry behind. Deleting here (rather
    than in async_unload_entry, which also runs on every reload/shutdown) keeps
    an active issue intact across reloads while avoiding the orphan on removal.
    """
    ir.async_delete_issue(hass, DOMAIN, no_data_issue_id(entry.entry_id))


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
