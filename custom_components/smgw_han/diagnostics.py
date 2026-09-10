"""Diagnostics support for SMGW HAN integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import SmgwConfigEntry
from .const import CONF_PASSWORD, CONF_USERNAME

TO_REDACT = {CONF_USERNAME, CONF_PASSWORD}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: SmgwConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = entry.runtime_data

    return {
        "config_entry_data": async_redact_data(dict(entry.data), TO_REDACT),
        "coordinator_data": coordinator.data,
    }
