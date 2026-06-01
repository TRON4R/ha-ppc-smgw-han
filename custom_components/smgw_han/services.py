"""Export service for the SMGW HAN integration.

Registers a single integration-wide service ``smgw_han.export_readings`` that
fetches meter readings for an arbitrary time range from one SMGW device and
returns them as a response variable, optionally also writing CSV / XLSX / the
signed CMS original as downloadable files under ``<config>/www/``.
"""

from __future__ import annotations

import logging
import re
import secrets
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
)
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv, device_registry as dr
from homeassistant.helpers.network import NoURLAvailableError, get_url
from homeassistant.util import dt as dt_util

from .aggregation import DailySummary, build_daily_summary
from .const import (
    ATTR_DEVICE_ID,
    ATTR_DOWNLOAD_CMS,
    ATTR_FROM_DATETIME,
    ATTR_PERIOD,
    ATTR_TO_DATETIME,
    ATTR_WRITE_CSV,
    ATTR_WRITE_XLSX,
    DOMAIN,
    EXPORT_WWW_SUBDIR,
    SERVICE_EXPORT_READINGS,
    SMGW_HISTORY_DAYS,
)
from .export_files import write_readings_csv, write_xlsx
from .smgw_client import MeterReading

_LOGGER = logging.getLogger(__name__)

PERIOD_CUSTOM = "custom"
PERIOD_PRESETS = (
    "yesterday",
    "last_7_days",
    "last_30_days",
    "current_month",
    "last_month",
)

SERVICE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_DEVICE_ID): cv.string,
        vol.Optional(ATTR_PERIOD, default=PERIOD_CUSTOM): cv.string,
        vol.Optional(ATTR_FROM_DATETIME): cv.datetime,
        vol.Optional(ATTR_TO_DATETIME): cv.datetime,
        vol.Optional(ATTR_DOWNLOAD_CMS, default=False): cv.boolean,
        vol.Optional(ATTR_WRITE_CSV, default=False): cv.boolean,
        vol.Optional(ATTR_WRITE_XLSX, default=False): cv.boolean,
    }
)


def _period_range(period: str) -> tuple[datetime, datetime]:
    """Translate a preset into a (from, to) range in local naive time.

    The end is pushed 15 minutes past a midnight boundary so the closing
    00:00:01 reading of the last day is captured (same margin the nightly
    fetch uses), which is exactly what users tend to get wrong by hand.
    """
    now = dt_util.now().replace(tzinfo=None)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    margin = timedelta(minutes=15)
    if period == "yesterday":
        return today - timedelta(days=1), today + margin
    if period == "last_7_days":
        return today - timedelta(days=7), today + margin
    if period == "last_30_days":
        return today - timedelta(days=30), today + margin
    if period == "current_month":
        return today.replace(day=1), today + margin
    if period == "last_month":
        first_this_month = today.replace(day=1)
        first_prev_month = (first_this_month - timedelta(days=1)).replace(day=1)
        return first_prev_month, first_this_month + margin
    raise ServiceValidationError(f"Unknown period preset: {period}")


def _sanitize(name: str) -> str:
    """Reduce a string to a safe filename component."""
    return re.sub(r"[^A-Za-z0-9._-]", "_", name)


def _reading_to_dict(reading: MeterReading) -> dict[str, Any]:
    return {
        "timestamp": reading.timestamp.isoformat(),
        "obis_code": reading.obis_code,
        "value": reading.value,
        "unit": reading.unit,
        "quality": reading.quality,
    }


def _resolve_coordinator(hass: HomeAssistant, device_id: str):
    """Resolve a device_id to this integration's loaded coordinator."""
    device = dr.async_get(hass).async_get(device_id)
    if device is None:
        raise ServiceValidationError(f"Unknown device id: {device_id}")

    entry: ConfigEntry | None = None
    for entry_id in device.config_entries:
        candidate = hass.config_entries.async_get_entry(entry_id)
        if candidate and candidate.domain == DOMAIN:
            entry = candidate
            break

    if entry is None:
        raise ServiceValidationError(
            "Selected device does not belong to the SMGW HAN integration."
        )
    if entry.state is not ConfigEntryState.LOADED:
        raise ServiceValidationError(
            "The SMGW HAN entry for this device is not loaded."
        )
    return entry.runtime_data


def _validate_range(from_dt: datetime, to_dt: datetime) -> None:
    """Plausibility checks beyond the frontend's format/required validation."""
    if from_dt >= to_dt:
        raise ServiceValidationError(
            "'from_datetime' must be before 'to_datetime'."
        )
    now_local = dt_util.now().replace(tzinfo=None)
    if to_dt > now_local:
        raise ServiceValidationError(
            "'to_datetime' must not be in the future."
        )
    if from_dt < now_local - timedelta(days=SMGW_HISTORY_DAYS):
        raise ServiceValidationError(
            f"'from_datetime' is older than the SMGW's "
            f"{SMGW_HISTORY_DAYS}-day history limit."
        )


def _write_export_files(
    export_dir: Path,
    base: str,
    readings: list[MeterReading],
    daily_summary: list[DailySummary],
    meta: dict[str, Any],
    do_csv: bool,
    do_xlsx: bool,
    cms_bytes: bytes | None,
    cms_name: str | None,
) -> dict[str, str]:
    """Write the requested files (blocking) and return ``kind -> filename``."""
    export_dir.mkdir(parents=True, exist_ok=True)
    written: dict[str, str] = {}
    if do_csv:
        fname = f"{base}.csv"
        write_readings_csv(export_dir / fname, readings)
        written["csv"] = fname
    if do_xlsx:
        fname = f"{base}.xlsx"
        write_xlsx(export_dir / fname, readings, daily_summary, meta)
        written["xlsx"] = fname
    if cms_bytes is not None:
        fname = _sanitize(cms_name) if cms_name else f"{base}.sm_data.xml.cms"
        (export_dir / fname).write_bytes(cms_bytes)
        written["cms"] = fname
    return written


async def _async_handle_export(call: ServiceCall) -> ServiceResponse:
    """Handle ``smgw_han.export_readings``."""
    hass = call.hass
    period: str = call.data[ATTR_PERIOD]
    download_cms: bool = call.data[ATTR_DOWNLOAD_CMS]
    do_csv: bool = call.data[ATTR_WRITE_CSV]
    do_xlsx: bool = call.data[ATTR_WRITE_XLSX]

    if period and period != PERIOD_CUSTOM:
        # Preset ranges are computed to be valid by construction (the
        # closing-reading margin can put `to` a few minutes ahead of "now"
        # only between 00:00 and 00:15), so the custom-range checks are skipped.
        from_dt, to_dt = _period_range(period)
    else:
        from_dt = call.data.get(ATTR_FROM_DATETIME)
        to_dt = call.data.get(ATTR_TO_DATETIME)
        if from_dt is None or to_dt is None:
            raise ServiceValidationError(
                "Choose a period preset, or provide both 'from_datetime' "
                "and 'to_datetime'."
            )
        _validate_range(from_dt, to_dt)

    coordinator = _resolve_coordinator(hass, call.data[ATTR_DEVICE_ID])

    readings = await coordinator.async_export_readings(from_dt, to_dt)
    tariff_hour, tariff_minute = coordinator.tariff_switch
    daily_summary = build_daily_summary(readings, tariff_hour, tariff_minute)

    meter_id = coordinator.target_meter_id
    from_str = from_dt.strftime("%Y-%m-%d %H:%M:%S")
    to_str = to_dt.strftime("%Y-%m-%d %H:%M:%S")

    response: dict[str, Any] = {
        "meter_id": meter_id,
        "from": from_str,
        "to": to_str,
        "reading_count": len(readings),
        "readings": [_reading_to_dict(r) for r in readings],
        "daily_summary": [s.to_dict() for s in daily_summary],
    }

    if download_cms or do_csv or do_xlsx:
        token = secrets.token_urlsafe(8)
        export_dir = Path(hass.config.path("www", EXPORT_WWW_SUBDIR, token))
        base = _sanitize(
            f"Zaehlerstaende_{from_dt:%Y-%m-%d_%H%M%S}_bis_"
            f"{to_dt:%Y-%m-%d_%H%M%S}_{meter_id or 'meter'}"
        )
        meta = {
            "meter_id": meter_id or "",
            "from": from_str,
            "to": to_str,
            "tariff_switch": f"{tariff_hour:02d}:{tariff_minute:02d}",
        }

        cms_bytes: bytes | None = None
        cms_name: str | None = None
        if download_cms:
            cms_bytes, cms_name = await coordinator.async_download_cms(
                from_dt, to_dt
            )

        written = await hass.async_add_executor_job(
            _write_export_files,
            export_dir, base, readings, daily_summary, meta,
            do_csv, do_xlsx, cms_bytes, cms_name,
        )
        # Prefer an absolute URL so the link is directly usable (clickable in
        # markdown/notifications, copy-pasteable from Developer Tools). Falls
        # back to a relative /local path if no base URL is configured.
        try:
            base_url = get_url(hass).rstrip("/")
        except NoURLAvailableError:
            base_url = ""
        response["files"] = {
            kind: f"{base_url}/local/{EXPORT_WWW_SUBDIR}/{token}/{fname}"
            for kind, fname in written.items()
        }
        _LOGGER.info(
            "Export wrote %d file(s) for meter %s to %s",
            len(written), meter_id, export_dir,
        )

    return response


def async_setup_services(hass: HomeAssistant) -> None:
    """Register the export service once for the whole integration."""
    if hass.services.has_service(DOMAIN, SERVICE_EXPORT_READINGS):
        return
    hass.services.async_register(
        DOMAIN,
        SERVICE_EXPORT_READINGS,
        _async_handle_export,
        schema=SERVICE_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
