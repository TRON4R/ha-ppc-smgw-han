"""Diagnostics support for SMGW HAN integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import SmgwConfigEntry
from .const import CONF_METER_ID, CONF_PASSWORD, CONF_USERNAME

TO_REDACT = {CONF_USERNAME, CONF_PASSWORD}

# Leading characters of the meter id that stay readable. They name the energy
# type and the manufacturer (e.g. "1lgz" = electricity, Landis+Gyr), which helps
# when reading a diagnostics file; the rest identifies the individual device.
METER_ID_VISIBLE_CHARS = 4


def _mask_meter_id(meter_id: str) -> str:
    """Keep the first characters of the meter id and mask the rest."""
    visible = meter_id[:METER_ID_VISIBLE_CHARS]
    return visible + "*" * (len(meter_id) - len(visible))


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: SmgwConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = entry.runtime_data

    config_entry_data = async_redact_data(dict(entry.data), TO_REDACT)
    meter_id = config_entry_data.get(CONF_METER_ID)
    if isinstance(meter_id, str):
        config_entry_data[CONF_METER_ID] = _mask_meter_id(meter_id)

    return {
        "config_entry_data": config_entry_data,
        "coordinator_data": coordinator.data,
    }
