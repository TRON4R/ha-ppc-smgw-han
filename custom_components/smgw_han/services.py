"""Export services for the SMGW HAN integration.

Two integration-wide services fetch meter readings for one SMGW device and
return them as a response variable, optionally also writing CSV / XLSX / the
signed CMS original as downloadable files under ``<config>/www/``:

- ``smgw_han.export_readings`` — explicit ``from_datetime`` / ``to_datetime``.
- ``smgw_han.export_period``   — a ready-made period preset (yesterday, last
  7/30 days, current/last month) whose range is computed automatically.

They are kept separate so each action form is unambiguous (no "which input
wins" between a preset and a custom range).
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
from .cms_parser import parse_cms_readings
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
    SERVICE_EXPORT_PERIOD,
    SERVICE_EXPORT_READINGS,
    SMGW_HISTORY_DAYS,
)
from .export_files import write_readings_csv, write_xlsx
from .smgw_client import MeterReading

_LOGGER = logging.getLogger(__name__)

PERIOD_PRESETS = (
    "yesterday",
    "last_7_days",
    "last_30_days",
    "current_month",
    "last_month",
)

_FILE_FIELDS = {
    vol.Optional(ATTR_DOWNLOAD_CMS, default=False): cv.boolean,
    vol.Optional(ATTR_WRITE_CSV, default=False): cv.boolean,
    vol.Optional(ATTR_WRITE_XLSX, default=False): cv.boolean,
}

READINGS_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_DEVICE_ID): cv.string,
        vol.Required(ATTR_FROM_DATETIME): cv.datetime,
        vol.Required(ATTR_TO_DATETIME): cv.datetime,
        **_FILE_FIELDS,
    }
)

PERIOD_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_DEVICE_ID): cv.string,
        vol.Optional(ATTR_PERIOD, default="last_month"): vol.In(PERIOD_PRESETS),
        **_FILE_FIELDS,
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


def _resolve_coordinator(hass: HomeAssistant, device_id: str | None):
    """Resolve a device_id to this integration's loaded coordinator.

    If ``device_id`` is omitted, the single loaded SMGW HAN entry is used
    automatically — so scripts and dashboards don't need a device id unless
    more than one SMGW is configured.
    """
    if not device_id:
        loaded = [
            entry
            for entry in hass.config_entries.async_entries(DOMAIN)
            if entry.state is ConfigEntryState.LOADED
        ]
        if not loaded:
            raise ServiceValidationError("No loaded SMGW HAN device found.")
        if len(loaded) > 1:
            raise ServiceValidationError(
                "Multiple SMGW HAN devices are configured; please specify "
                "'device_id'."
            )
        return loaded[0].runtime_data

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
    """Write the requested files (blocking) and return ``kind -> filename``.

    Order follows the selection order in the action form (CMS, CSV, XLSX) so
    the response links come out in that same order.
    """
    export_dir.mkdir(parents=True, exist_ok=True)
    written: dict[str, str] = {}
    if cms_bytes is not None:
        fname = _sanitize(cms_name) if cms_name else f"{base}.sm_data.xml.cms"
        (export_dir / fname).write_bytes(cms_bytes)
        written["cms"] = fname
    if do_csv:
        fname = f"{base}.csv"
        write_readings_csv(export_dir / fname, readings)
        written["csv"] = fname
    if do_xlsx:
        fname = f"{base}.xlsx"
        write_xlsx(export_dir / fname, readings, daily_summary, meta)
        written["xlsx"] = fname
    return written


def _parse_and_aggregate(
    cms_bytes: bytes, tariff_hour: int, tariff_minute: int
) -> tuple[list[MeterReading], list[DailySummary]]:
    """Parse CMS bytes and build the daily summary (blocking; run in executor).

    Both steps are CPU-bound on large ranges (multi-MB XML, per-day reading
    scans), so they belong off the event loop.
    """
    readings = parse_cms_readings(cms_bytes)
    daily_summary = build_daily_summary(readings, tariff_hour, tariff_minute)
    return readings, daily_summary


async def run_export(
    hass: HomeAssistant,
    coordinator,
    from_dt: datetime,
    to_dt: datetime,
    *,
    download_cms: bool,
    do_csv: bool,
    do_xlsx: bool,
) -> dict[str, Any]:
    """Shared export core, reused by the services and the options flow.

    ``coordinator`` is a loaded :class:`SmgwTafCoordinator`. Returns the
    response dict (download links first, then meter data).
    """
    tariff_hour, tariff_minute = coordinator.tariff_switch

    # The signed CMS export delivers the whole range in a single request and is
    # the authoritative source. Download once, then parse + aggregate off the
    # event loop. The same bytes are reused below if the user asked to keep the
    # .cms file (so there is never a second download).
    cms_bytes, cms_name = await coordinator.async_download_cms(from_dt, to_dt)
    readings, daily_summary = await hass.async_add_executor_job(
        _parse_and_aggregate, cms_bytes, tariff_hour, tariff_minute
    )

    meter_id = coordinator.target_meter_id
    from_str = from_dt.strftime("%Y-%m-%d %H:%M:%S")
    to_str = to_dt.strftime("%Y-%m-%d %H:%M:%S")

    files: dict[str, str] = {}
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

        # The CMS bytes are already in hand; save the file only if requested.
        written = await hass.async_add_executor_job(
            _write_export_files,
            export_dir, base, readings, daily_summary, meta,
            do_csv, do_xlsx,
            cms_bytes if download_cms else None, cms_name,
        )
        # Prefer an absolute URL so the link is directly usable (clickable in
        # markdown/notifications, copy-pasteable from Developer Tools). Falls
        # back to a relative /local path if no base URL is configured.
        try:
            base_url = get_url(hass).rstrip("/")
        except NoURLAvailableError:
            base_url = ""
        files = {
            kind: f"{base_url}/local/{EXPORT_WWW_SUBDIR}/{token}/{fname}"
            for kind, fname in written.items()
        }
        _LOGGER.info(
            "Export wrote %d file(s) for meter %s to %s",
            len(written), meter_id, export_dir,
        )

    # Build the response with the download links first (followed by the
    # potentially very long readings list), in CMS -> CSV -> XLSX order.
    response: dict[str, Any] = {}
    if files:
        response["files"] = files
    response["meter_id"] = meter_id
    response["from"] = from_str
    response["to"] = to_str
    response["reading_count"] = len(readings)
    response["readings"] = [_reading_to_dict(r) for r in readings]
    response["daily_summary"] = [s.to_dict() for s in daily_summary]
    return response


async def _async_handle_export_readings(call: ServiceCall) -> ServiceResponse:
    """Handle ``smgw_han.export_readings`` (explicit from/to)."""
    from_dt: datetime = call.data[ATTR_FROM_DATETIME]
    to_dt: datetime = call.data[ATTR_TO_DATETIME]
    _validate_range(from_dt, to_dt)
    coordinator = _resolve_coordinator(call.hass, call.data.get(ATTR_DEVICE_ID))
    return await run_export(
        call.hass, coordinator, from_dt, to_dt,
        download_cms=call.data[ATTR_DOWNLOAD_CMS],
        do_csv=call.data[ATTR_WRITE_CSV],
        do_xlsx=call.data[ATTR_WRITE_XLSX],
    )


async def _async_handle_export_period(call: ServiceCall) -> ServiceResponse:
    """Handle ``smgw_han.export_period`` (preset range)."""
    from_dt, to_dt = _period_range(call.data[ATTR_PERIOD])
    coordinator = _resolve_coordinator(call.hass, call.data.get(ATTR_DEVICE_ID))
    return await run_export(
        call.hass, coordinator, from_dt, to_dt,
        download_cms=call.data[ATTR_DOWNLOAD_CMS],
        do_csv=call.data[ATTR_WRITE_CSV],
        do_xlsx=call.data[ATTR_WRITE_XLSX],
    )


def async_setup_services(hass: HomeAssistant) -> None:
    """Register the export services once for the whole integration."""
    if not hass.services.has_service(DOMAIN, SERVICE_EXPORT_READINGS):
        hass.services.async_register(
            DOMAIN,
            SERVICE_EXPORT_READINGS,
            _async_handle_export_readings,
            schema=READINGS_SCHEMA,
            supports_response=SupportsResponse.OPTIONAL,
        )
    if not hass.services.has_service(DOMAIN, SERVICE_EXPORT_PERIOD):
        hass.services.async_register(
            DOMAIN,
            SERVICE_EXPORT_PERIOD,
            _async_handle_export_period,
            schema=PERIOD_SCHEMA,
            supports_response=SupportsResponse.OPTIONAL,
        )
