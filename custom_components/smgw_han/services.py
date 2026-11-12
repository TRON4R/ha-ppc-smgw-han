"""Export services for the SMGW HAN integration.

Two integration-wide services fetch meter readings for one SMGW device and
return them as a response variable, optionally also writing CSV / XLSX / the
signed CMS original as files under ``<config>/smgw_han_exports/``, offered
through short-lived signed download links (see ``downloads.py``):

- ``smgw_han.export_readings`` — explicit ``from_datetime`` / ``to_datetime``.
- ``smgw_han.export_period``   — a ready-made period preset (yesterday, last
  7/30 days, current/last month) whose range is computed automatically.

They are kept separate so each action form is unambiguous (no "which input
wins" between a preset and a custom range).

A range longer than three months is fetched in parts (see
:func:`split_export_range`): the gateway refused a whole year in one request
on 2026-10-03 and was unreachable afterwards. The signed parts go into one
ZIP unchanged; CSV and Excel are merged into one file each.
"""

from __future__ import annotations

import asyncio
import calendar
import contextlib
import html
import logging
import math
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from time import monotonic
from typing import Any, TypeVar

import voluptuous as vol
from homeassistant.components import persistent_notification
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
)
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv, device_registry as dr
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
    SERVICE_EXPORT_PERIOD,
    SERVICE_EXPORT_READINGS,
    SMGW_HISTORY_DAYS,
    ZONE_NAME,
    ZONE_TIME,
)
from .downloads import export_links, new_export_dir, valid_until_label
from .export_files import write_cms_zip, write_readings_csv, write_xlsx
from .smgw_client import MeterReading, TariffZones, _visible_text, utc_instant

_LOGGER = logging.getLogger(__name__)

_T = TypeVar("_T")

# --- Splitting long ranges (owner decision 2026-11-12) ---------------------
# Up to this many days the export stays ONE request, as it always was. Every
# export of up to 111 days worked; a whole year made the gateway answer HTTP
# 500 after 48 s and then become unreachable (2026-10-03, cause unproven).
EXPORT_SINGLE_REQUEST_DAYS = 93
# Longer ranges are cut every three calendar months, on the start's day of
# the month and counted from the start (a part holds at most 92 days).
EXPORT_PART_MONTHS = 3
# Each part runs this far past its cut, like the presets' closing margin, so
# every signed part also holds the closing reading of its own last day. The
# overlapping readings are dropped again when the parts are merged.
EXPORT_PART_OVERLAP = timedelta(minutes=15)
# A remainder up to this long joins the part before it instead of becoming a
# request of its own (keeps every request within EXPORT_SINGLE_REQUEST_DAYS).
EXPORT_MIN_LAST_PART = timedelta(days=1)
# Pause between two parts. Whether the gateway needs it is unknown; for a
# year it costs 30 seconds.
EXPORT_PART_PAUSE_S = 10.0
# A part starts at a midnight cut, and a gateway that already recorded then
# has its reading at 00:00:01-00:00:05 (the grid is 15 minutes). A first
# reading later than this indicates that its history begins inside the
# part - under the assumption described in _download_parts.
_HISTORY_START_TOLERANCE = timedelta(minutes=15)

# --- Progress ring of the options flow ---------------------------------------
# Share of the ring for downloading and parsing the parts; the rest is the
# daily summary and the files.
_DOWNLOAD_SHARE = 0.9
# While a part downloads its real progress is unknown (the gateway sends
# nothing until the whole file is assembled), so the ring creeps towards this
# fraction of the part's share and jumps to the full share when it arrives.
_PART_ESTIMATE_CAP = 0.85
_PART_TIME_CONSTANT_S = 10.0

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
    raise ServiceValidationError(
        translation_domain=DOMAIN,
        translation_key="unknown_period",
        translation_placeholders={"period": str(period)},
    )


def _add_months(day: date, months: int) -> date:
    """``day`` shifted by whole months; a missing day becomes the month end.

    31 January + 3 months is 30 April. Callers always count from the same
    start, so a short month never shifts the later cuts (31.01. -> 30.04. ->
    31.07., not 30.07.).
    """
    index = day.month - 1 + months
    year, month = day.year + index // 12, index % 12 + 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def split_export_range(
    from_dt: datetime, to_dt: datetime
) -> list[tuple[datetime, datetime]]:
    """The gateway requests one export is fetched with, in order.

    Up to :data:`EXPORT_SINGLE_REQUEST_DAYS` days: the range itself, one
    request. Longer: cut at midnight every three months on the start's day
    of the month (17.02. -> 17.05. -> 17.08. ...); the last part takes the
    rest, and a rest of at most :data:`EXPORT_MIN_LAST_PART` stays with the
    part before it. Each part but the last ends
    :data:`EXPORT_PART_OVERLAP` after its cut. Cuts at midnight never fall
    into the hour a DST change touches. The user's own from/to are kept.
    """
    if to_dt - from_dt <= timedelta(days=EXPORT_SINGLE_REQUEST_DAYS):
        return [(from_dt, to_dt)]
    cuts: list[datetime] = []
    step = 1
    while True:
        cut = datetime.combine(
            _add_months(from_dt.date(), EXPORT_PART_MONTHS * step), time()
        )
        if to_dt - cut <= EXPORT_MIN_LAST_PART:
            break
        cuts.append(cut)
        step += 1
    starts = [from_dt, *cuts]
    ends = [*(cut + EXPORT_PART_OVERLAP for cut in cuts), to_dt]
    return list(zip(starts, ends, strict=True))


# German umlauts are transliterated rather than blanked out by _sanitize: a
# device called "Zähler Süd" would otherwise land in the filename as
# "Z_hler_S_d", which is unreadable in a folder full of exports.
_UMLAUT_MAP = str.maketrans(
    {
        "ä": "ae", "ö": "oe", "ü": "ue",
        "Ä": "Ae", "Ö": "Oe", "Ü": "Ue",
        "ß": "ss",
    }
)

# Cap for the device-name part of a filename. The rest of the name already
# carries two timestamps and the meter id; a long device label should not be
# what pushes the result over a path limit.
DEVICE_NAME_MAX_CHARS = 24


def _sanitize(name: str) -> str:
    """Reduce a string to a safe filename component."""
    return re.sub(r"[^A-Za-z0-9._-]", "_", name.translate(_UMLAUT_MAP))


def _device_suffix(device_name: str | None) -> str:
    """Filename part identifying the device, or "" when it has no name.

    With two evaluations of the SAME meter the meter id no longer tells the
    exports apart — both carry it, because it is the same physical meter. The
    device name is then the only thing that does, so it goes into the filename
    whenever one is set. Appended at the end so exports still sort by period,
    which is the order in which two regimes get compared.
    """
    cleaned = _sanitize((device_name or "").strip()).strip("_")
    if not cleaned:
        return ""
    return f"_{cleaned[:DEVICE_NAME_MAX_CHARS].rstrip('_')}"


def _cms_name_with_device(cms_name: str | None, suffix: str) -> str | None:
    """Insert ``suffix`` into the gateway's own CMS filename.

    The gateway names the signed export after the meter, so for two
    evaluations of one meter the .cms file is the one export that still could
    not be told apart. Inserted before the extension rather than appended, so
    the gateway's naming stays recognisable:
    ``1lgz00.sm_data.xml.cms`` -> ``1lgz00_Modul_3_SMGW.sm_data.xml.cms``.
    """
    if not cms_name or not suffix:
        return cms_name
    stem, dot, extension = cms_name.partition(".")
    return f"{stem}{suffix}{dot}{extension}"


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
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="no_device"
            )
        if len(loaded) > 1:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="multiple_devices"
            )
        return loaded[0].runtime_data

    device = dr.async_get(hass).async_get(device_id)
    if device is None:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="unknown_device",
            translation_placeholders={"device_id": str(device_id)},
        )

    entry: ConfigEntry | None = None
    for entry_id in device.config_entries:
        candidate = hass.config_entries.async_get_entry(entry_id)
        if candidate and candidate.domain == DOMAIN:
            entry = candidate
            break

    if entry is None:
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="wrong_device"
        )
    if entry.state is not ConfigEntryState.LOADED:
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="device_not_loaded"
        )
    return entry.runtime_data


def _validate_range(from_dt: datetime, to_dt: datetime) -> None:
    """Plausibility checks beyond the frontend's format/required validation."""
    if from_dt >= to_dt:
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="from_after_to"
        )
    now_local = dt_util.now().replace(tzinfo=None)
    # Reject a genuinely future day (catches typos) but allow any time today —
    # in particular the presets' "today + 00:15" closing margin, even just after
    # midnight — so this check can also gate the options flow without false hits.
    if to_dt.date() > now_local.date():
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="to_in_future"
        )
    if from_dt < now_local - timedelta(days=SMGW_HISTORY_DAYS):
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="from_too_old"
        )


@dataclass
class _Part:
    """One gateway request of an export that returned readings."""

    from_dt: datetime
    to_dt: datetime
    readings: list[MeterReading]
    content: bytes | None  # the signed file; dropped unless it is to be saved
    name: str | None  # the gateway's filename for it


def _stamp(value: datetime) -> str:
    return value.strftime("%Y-%m-%d_%H%M%S")


def _write_export_files(
    export_dir: Path,
    base: str,
    readings: list[MeterReading],
    daily_summary: list[DailySummary],
    meta: dict[str, Any],
    do_csv: bool,
    do_xlsx: bool,
    cms_parts: list[_Part] | None,
    fallback_cms_name: str,
) -> dict[str, str]:
    """Write the requested files (blocking) and return ``kind -> filename``.

    Order follows the selection order in the action form (CMS, CSV, XLSX) so
    the response links come out in that same order. ``cms_parts`` is None
    when no CMS file was requested. One part is saved as the gateway named
    it; several go into one ZIP unchanged, because a signed file cannot be
    merged without breaking its signature.
    """
    export_dir.mkdir(parents=True, exist_ok=True)
    written: dict[str, str] = {}
    if cms_parts and len(cms_parts) == 1:
        part = cms_parts[0]
        fname = _sanitize(part.name) if part.name else fallback_cms_name
        (export_dir / fname).write_bytes(part.content or b"")
        written["cms"] = fname
    elif cms_parts:
        total = len(cms_parts)
        members = [
            (
                _sanitize(
                    f"Teil{index:02d}von{total:02d}_{_stamp(part.from_dt)}_bis_"
                    f"{_stamp(part.to_dt)}_{part.name or fallback_cms_name}"
                ),
                part.content or b"",
            )
            for index, part in enumerate(cms_parts, 1)
        ]
        fname = f"{base}_CMS.zip"
        write_cms_zip(export_dir / fname, members)
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


def _merge_and_aggregate(
    part_readings: list[list[MeterReading]],
    zones_for: Callable[[date], TariffZones],
) -> tuple[list[MeterReading], list[DailySummary]]:
    """Merge the parts' readings, build the daily summary (blocking).

    CPU-bound on large ranges, so it belongs off the event loop. Neighbouring
    parts overlap by :data:`EXPORT_PART_OVERLAP`; a reading both deliver is
    kept once, identified by its real moment (UTC) and register, so the two
    passes of the repeated autumn hour stay apart. The summary runs over the
    merged readings, so a day at a seam gets its boundaries from both sides.
    ``zones_for`` is the coordinator's snapshotted resolver, so each day is
    split by the layout that was valid on it even when the range crosses a
    scheduled change.
    """
    if len(part_readings) == 1:
        readings = part_readings[0]  # already in parser order, as before
    else:
        seen: dict[tuple[datetime, str], float] = {}
        readings = []
        for part in part_readings:
            for reading in part:
                key = (utc_instant(reading.timestamp), reading.obis_code)
                if key in seen:
                    if seen[key] != reading.value:
                        _LOGGER.warning(
                            "Two parts disagree on %s %s: %s vs %s; the "
                            "first is kept",
                            reading.obis_code, reading.timestamp,
                            seen[key], reading.value,
                        )
                    continue
                seen[key] = reading.value
                readings.append(reading)
        readings.sort(key=lambda r: (utc_instant(r.timestamp), r.obis_code))
    return readings, build_daily_summary(readings, zones_for)


class _Progress:
    """Feeds the options flow's progress ring (0..1); idle without a callback."""

    def __init__(self, report: Callable[[float], None] | None, parts: int) -> None:
        self._report = report
        self._share = _DOWNLOAD_SHARE / parts

    def set(self, value: float) -> None:
        if self._report is not None:
            self._report(min(max(value, 0.0), 1.0))

    def part_done(self, index: int) -> None:
        self.set(self._share * (index + 1))

    async def track(self, index: int, awaitable: Awaitable[_T]) -> _T:
        """Await one part's download while the ring creeps forward."""
        if self._report is None:
            return await awaitable
        base = self._share * index

        async def creep() -> None:
            started = monotonic()
            while True:
                await asyncio.sleep(1)
                elapsed = monotonic() - started
                self.set(
                    base
                    + self._share
                    * _PART_ESTIMATE_CAP
                    * (1 - math.exp(-elapsed / _PART_TIME_CONSTANT_S))
                )

        creeper = asyncio.create_task(creep())
        try:
            return await awaitable
        finally:
            creeper.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await creeper


async def _fetch_part(
    coordinator, from_dt: datetime, to_dt: datetime
) -> tuple[bytes | None, str | None]:
    """One gateway request; ``(None, None)`` when the gateway reports no data.

    "No data" arrives as an HTTP error (mapped to ``no_data_in_range`` by the
    coordinator), an empty answer, or the text "Keine Daten". Any other answer
    without a signed payload, such as a login or error page, is a failure:
    taking it for an empty range would let it pass as a gap in the history.

    An HTTP error alone does NOT prove that the range is empty, though. An
    external review (2026-11-12) proposed telling "no data" from a real
    failure by the gateway's answer. That does not work: measured on a real
    gateway the same day, a range without data is answered with HTTP 500 and
    a generic 369-byte page "500 - Internal Server Error" within 0.1 s -
    the same size as the page that came after 48 s when a gateway failed on
    a whole year (2026-10-03). The caller therefore checks against the data
    itself whether a skipped part can really have been empty
    (see :func:`_download_parts`).
    """
    try:
        content, name = await coordinator.async_download_cms(from_dt, to_dt)
    except ServiceValidationError as err:
        if err.translation_key != "no_data_in_range":
            raise
        return None, None
    if content and b"<?xml" in content:
        return content, name
    text = html.unescape((content or b"").decode("utf-8", "replace"))
    if not text.strip() or "Keine Daten" in text:
        return None, None
    _LOGGER.warning(
        "SMGW answered the export %s .. %s with neither a signed file nor "
        "'no data': %s", from_dt, to_dt, _visible_text(content),
    )
    raise HomeAssistantError(
        translation_domain=DOMAIN, translation_key="cms_download_failed"
    )


def _fmt_moment(hass: HomeAssistant, value: datetime) -> str:
    """A part's start or end the way the reader's language writes it."""
    if (hass.config.language or "").lower().startswith("de"):
        return value.strftime("%d.%m.%Y %H:%M")
    return value.strftime("%Y-%m-%d %H:%M")


def _part_placeholders(
    hass: HomeAssistant, ranges: list[tuple[datetime, datetime]], index: int
) -> dict[str, str]:
    start, end = ranges[index]
    return {
        "part": str(index + 1),
        "parts": str(len(ranges)),
        "range": f"{_fmt_moment(hass, start)} – {_fmt_moment(hass, end)}",
    }


def _part_error(
    hass: HomeAssistant,
    err: HomeAssistantError,
    ranges: list[tuple[datetime, datetime]],
    index: int,
) -> HomeAssistantError:
    """Name the failed part in the message, so the user knows what is missing."""
    if len(ranges) == 1 or err.translation_key == "cms_auth_failed":
        return err  # one request, or a login problem that no part can fix
    placeholders = _part_placeholders(hass, ranges, index)
    if err.translation_key == "cms_download_timeout":
        return HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="cms_part_timeout",
            translation_placeholders={
                **placeholders,
                **(err.translation_placeholders or {}),
            },
        )
    return HomeAssistantError(
        translation_domain=DOMAIN,
        translation_key="cms_part_failed",
        translation_placeholders=placeholders,
    )


async def _download_parts(
    hass: HomeAssistant,
    coordinator,
    ranges: list[tuple[datetime, datetime]],
    progress: _Progress,
    keep_cms: bool,
) -> tuple[list[_Part], int]:
    """Fetch and parse every part; returns the parts with data + skipped ones.

    Parts without data at the START may be skipped: usually the gateway
    simply has no data that far back (younger than the range, or older
    data deleted). Its answer cannot tell that from a failure (see
    :func:`_fetch_part`), so the data is used as evidence instead: if the
    first part with data begins clearly after its own start, the history
    most likely begins there. If it begins right at its start, the gateway
    had data before as well, and the empty part before it was a failure.

    This is evidence, not proof. It ASSUMES the gateway records without
    gaps from its first reading on. A real failure of one part together
    with missing readings right at the start of the next part would still
    be taken for an empty range (pointed out by the external review of
    2026-11-12, accepted as residual risk: never observed, and no answer
    of the gateway can rule it out). Skipped parts are therefore always
    reported as "returned no data" - in the dialog, the notification and
    the workbook - never as "the gateway had no data". That aborts the
    export, as do a part without data AFTER one with data (a hole in the
    middle) and any part that fails: a merged file with a quarter missing
    would look complete at first glance. In the rare case that a gateway's
    history begins exactly at a cut, the export aborts too - the safe side.
    There is no automatic retry, to keep the load on the gateway
    predictable.
    """
    parts: list[_Part] = []
    skipped = 0
    for index, (start, end) in enumerate(ranges):
        if index:
            await asyncio.sleep(EXPORT_PART_PAUSE_S)
        try:
            content, name = await progress.track(
                index, _fetch_part(coordinator, start, end)
            )
        except HomeAssistantError as err:
            raise _part_error(hass, err, ranges, index) from err
        try:
            readings = (
                await hass.async_add_executor_job(parse_cms_readings, content)
                if content
                else []
            )
        except Exception as err:
            if len(ranges) == 1:
                raise
            # A signed file that cannot be read: name the part it belongs to.
            _LOGGER.exception(
                "Export part %d/%d %s .. %s: the signed file could not be read",
                index + 1, len(ranges), start, end,
            )
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="cms_part_failed",
                translation_placeholders=_part_placeholders(hass, ranges, index),
            ) from err
        progress.part_done(index)
        _LOGGER.info(
            "Export part %d/%d %s .. %s: %d readings",
            index + 1, len(ranges), start, end, len(readings),
        )
        if not readings:
            if parts:
                raise _gap_error(hass, ranges, index)
            skipped += 1
            continue
        if skipped and not parts:
            first = min(r.timestamp for r in readings)
            if first <= start + _HISTORY_START_TOLERANCE:
                _LOGGER.warning(
                    "Export part %d/%d returned no data, but the next part "
                    "has readings from its very start (%s): the gateway had "
                    "data then, so the empty answer was a failure",
                    index, len(ranges), first,
                )
                raise _gap_error(hass, ranges, index - 1)
        parts.append(
            _Part(start, end, readings, content if keep_cms else None, name)
        )
    return parts, skipped


def _gap_error(
    hass: HomeAssistant, ranges: list[tuple[datetime, datetime]], index: int
) -> HomeAssistantError:
    """A part that returned nothing although its neighbours show data."""
    return HomeAssistantError(
        translation_domain=DOMAIN,
        translation_key="cms_part_no_data",
        translation_placeholders=_part_placeholders(hass, ranges, index),
    )


async def run_export(
    hass: HomeAssistant,
    coordinator,
    from_dt: datetime,
    to_dt: datetime,
    *,
    download_cms: bool,
    do_csv: bool,
    do_xlsx: bool,
    progress: Callable[[float], None] | None = None,
) -> dict[str, Any]:
    """Shared export core, reused by the services and the options flow.

    ``coordinator`` is a loaded :class:`SmgwCoordinator`. Returns the
    response dict (download links first, then meter data). ``progress``
    receives values from 0 to 1 for the options flow's progress ring.
    """
    # A holiday rule needs the library's data loaded off the event loop; the
    # export may reach back years, so cover its whole range.
    await coordinator.async_preload_holidays(
        range(from_dt.year, to_dt.year + 1)
    )
    zones_for = coordinator.zone_resolver()

    # The signed CMS export is the authoritative source. Each part is
    # downloaded once and parsed off the event loop; its bytes are kept only
    # if the user asked for the CMS file (so there is never a second
    # download).
    ranges = split_export_range(from_dt, to_dt)
    tracker = _Progress(progress, len(ranges))
    parts, skipped = await _download_parts(
        hass, coordinator, ranges, tracker, keep_cms=download_cms
    )
    # A range entirely before the meter's first reading comes back without a
    # CMS payload — surface that as a clean "no data" message, not a raw
    # parse error.
    if not parts:
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="no_data_in_range"
        )
    readings, daily_summary = await hass.async_add_executor_job(
        _merge_and_aggregate, [part.readings for part in parts], zones_for
    )
    tracker.set(_DOWNLOAD_SHARE + (1 - _DOWNLOAD_SHARE) / 2)

    meter_id = coordinator.target_meter_id
    from_str = from_dt.strftime("%Y-%m-%d %H:%M:%S")
    to_str = to_dt.strftime("%Y-%m-%d %H:%M:%S")

    files: dict[str, str] = {}
    valid_until = ""
    if download_cms or do_csv or do_xlsx:
        # An unnamed device yields an empty suffix, so a single-device
        # installation keeps exactly the filenames it had before.
        device_suffix = _device_suffix(coordinator.device_name)
        base = (
            _sanitize(
                f"Zaehlerstaende_{from_dt:%Y-%m-%d_%H%M%S}_bis_"
                f"{to_dt:%Y-%m-%d_%H%M%S}_{meter_id or 'meter'}"
            )
            + device_suffix
        )
        for part in parts:
            part.name = _cms_name_with_device(part.name, device_suffix)
        token, export_dir = new_export_dir(hass)
        # Document every layout the range touches, not just the current one:
        # an export crossing a scheduled change is split by two of them, and
        # a workbook that names only one would misrepresent half its rows.
        periods = coordinator.zone_periods(
            from_dt.date(), to_dt.date()
        )
        meta = {
            "meter_id": meter_id or "",
            "from": from_str,
            "to": to_str,
            "zones": [
                (t.strftime("%H:%M"), name)
                for t, name in zones_for(from_dt.date())
            ],
            "day_type_zones": [
                {
                    "label": label,
                    "zones": [
                        (zone[ZONE_TIME], zone[ZONE_NAME]) for zone in zones
                    ],
                }
                for label, zones in coordinator.zone_day_types(
                    from_dt.date(), to_dt.date()
                )
            ],
            "holiday_days": [
                day.isoformat()
                for day in coordinator.holidays_in_range(
                    from_dt.date(), to_dt.date()
                )
            ],
            "zone_periods": [
                {
                    "valid_from": period["valid_from"],
                    "zones": [
                        (zone[ZONE_TIME], zone[ZONE_NAME])
                        for zone in period["zones"]
                    ],
                }
                for period in periods
            ],
            # How the range was fetched, for the workbook (one request: []).
            "parts": [
                {
                    "from": start.strftime("%Y-%m-%d %H:%M:%S"),
                    "to": end.strftime("%Y-%m-%d %H:%M:%S"),
                    "has_data": index >= skipped,
                }
                for index, (start, end) in enumerate(ranges)
            ]
            if len(ranges) > 1
            else [],
        }

        # The CMS bytes are already in hand; save the file only if requested.
        written = await hass.async_add_executor_job(
            _write_export_files,
            export_dir, base, readings, daily_summary, meta,
            do_csv, do_xlsx,
            parts if download_cms else None, f"{base}.sm_data.xml.cms",
        )
        files, valid_until = export_links(hass, token, written)
        _LOGGER.info(
            "Export wrote %d file(s) for meter %s to %s",
            len(written), meter_id, export_dir,
        )

    # Build the response with the download links first (followed by the
    # potentially very long readings list), in CMS -> CSV -> XLSX order.
    response: dict[str, Any] = {}
    if files:
        response["files"] = files
        response["files_valid_until"] = valid_until
    response["meter_id"] = meter_id
    response["from"] = from_str
    response["to"] = to_str
    # Gateway requests that returned data (= signed files), and the requests
    # at the start of the range for which the gateway returned none.
    response["parts"] = len(parts)
    response["parts_without_data"] = skipped
    response["reading_count"] = len(readings)
    response["readings"] = [_reading_to_dict(r) for r in readings]
    response["daily_summary"] = [s.to_dict() for s in daily_summary]
    tracker.set(1.0)
    return response


def build_links_markdown(files: dict[str, str]) -> str:
    """Download links (CMS · CSV · Excel) as explicit ``target="_blank"`` anchors.

    Rendered as raw ``<a target="_blank">`` anchors, NOT markdown ``[text](url)``:
    Home Assistant's frontend only auto-adds ``target="_blank"`` to cross-origin
    links (``node.host !== document.location.host``). Once the export URL is the
    same host the user is currently browsing (which is the norm since v2.5.3
    prefers the external URL), a plain markdown link is same-origin, so the SPA
    router intercepts the click and lands on the default dashboard instead of
    downloading. An explicit ``target="_blank"`` survives HA's markdown sanitizer
    (js-xss whitelists ``a[target]``) and opens the file in a new tab, which
    downloads it — origin-independent. Verified live against a real instance.

    ``href``/label are safe unescaped: the token is ``token_urlsafe``, the
    filename is ``_sanitize``d and the ``authSig`` signature is a JWT, i.e.
    base64url segments joined by dots — none contains HTML metacharacters.
    """
    labels = (("cms", "CMS"), ("csv", "CSV"), ("xlsx", "Excel"))
    parts = [
        f'<a href="{files[key]}" target="_blank">{label}</a>'
        for key, label in labels
        if files.get(key)
    ]
    return " · ".join(parts)


def _notify_export_files(hass: HomeAssistant, response: dict[str, Any]) -> None:
    """Post a persistent notification with clickable download links.

    Only fires when the export actually wrote files, so a pure response-variable
    call (e.g. from an automation) stays silent. The options flow posts its own
    notification, so this lives in the service handlers, not in ``run_export``.
    """
    files = response.get("files")
    if not files:
        return
    message = (
        f"{export_summary(hass, response)}\n\n"
        f"{build_links_markdown(files)}"
        f"{validity_note(response.get('files_valid_until'))}"
    )
    persistent_notification.async_create(
        hass,
        message,
        title="SMGW Export",
        notification_id="smgw_export_service",
    )


def data_from_label(hass: HomeAssistant, response: dict[str, Any]) -> str:
    """The day of the export's first reading, in the reader's notation."""
    readings = response.get("readings") or []
    if not readings:
        return "–"
    first = datetime.fromisoformat(readings[0]["timestamp"])
    if (hass.config.language or "").lower().startswith("de"):
        return first.strftime("%d.%m.%Y")
    return first.date().isoformat()


def export_summary(hass: HomeAssistant, response: dict[str, Any]) -> str:
    """The notification's first lines: what came back, and how it was fetched."""
    text = (
        f"{response['reading_count']} Werte, "
        f"{len(response['daily_summary'])} Tage."
    )
    parts = response.get("parts", 1)
    if parts > 1:
        text += (
            f" In {parts} Teilabrufen geholt, weil die Integration Zeiträume "
            "über drei Monate aufteilt, um das SMGW nicht zu überlasten."
        )
        if (response.get("files") or {}).get("cms"):
            text += " Die signierten CMS-Originale liegen einzeln in der ZIP-Datei."
    if response.get("parts_without_data"):
        text += (
            " Für den Anfang des Zeitraums hat das SMGW keine Daten "
            f"geliefert; die Werte beginnen am {data_from_label(hass, response)}."
        )
    return text


def validity_note(valid_until: str | None) -> str:
    """The notification line saying until when the links work ("" if none).

    A notification stays in the bell list long after its links have expired;
    without this line, a click there would end on a bare "401: Unauthorized".
    """
    label = valid_until_label(valid_until)
    return f"\n\nDie Links sind bis {label} Uhr gültig." if label else ""


async def _run_export_call(
    call: ServiceCall, from_dt: datetime, to_dt: datetime
) -> ServiceResponse:
    """Resolve the coordinator, run the export, and notify if files were made."""
    coordinator = _resolve_coordinator(call.hass, call.data.get(ATTR_DEVICE_ID))
    response = await run_export(
        call.hass, coordinator, from_dt, to_dt,
        download_cms=call.data[ATTR_DOWNLOAD_CMS],
        do_csv=call.data[ATTR_WRITE_CSV],
        do_xlsx=call.data[ATTR_WRITE_XLSX],
    )
    _notify_export_files(call.hass, response)
    return response


async def _async_handle_export_readings(call: ServiceCall) -> ServiceResponse:
    """Handle ``smgw_han.export_readings`` (explicit from/to)."""
    from_dt: datetime = call.data[ATTR_FROM_DATETIME]
    to_dt: datetime = call.data[ATTR_TO_DATETIME]
    _validate_range(from_dt, to_dt)
    return await _run_export_call(call, from_dt, to_dt)


async def _async_handle_export_period(call: ServiceCall) -> ServiceResponse:
    """Handle ``smgw_han.export_period`` (preset range)."""
    from_dt, to_dt = _period_range(call.data[ATTR_PERIOD])
    return await _run_export_call(call, from_dt, to_dt)


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
