"""Export the SMGW consumer log ("Logs" in the SMGW web interface).

Counterpart of ``services.run_export`` for the log instead of meter values,
reached from the options flow. The client fetches the range as one or more
signed CMS files (the gateway exports at most 1000 entries at a time); this
module parses and merges them, checks completeness, and writes the files the
user picked:

- CMS: the signed original — a single ``.cms``, or a ZIP holding every part
  unchanged when the range had to be split. Signed files cannot be merged
  without breaking their signatures.
- CSV / XLSX: one merged file each, however many parts there were.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .export_files import write_cms_zip, write_log_csv, write_log_xlsx
from .log_parser import (
    LogEntry,
    count_shortfalls,
    find_number_gaps,
    merge_entries,
    parse_log_cms,
)
from .services import (
    PERIOD_PRESETS,
    _device_suffix,
    _new_export_dir,
    _sanitize,
    export_links,
)
from .smgw_client import LogExportResult

_LOGGER = logging.getLogger(__name__)

LOG_PERIOD_ALL = "all"
LOG_PERIOD_PRESETS = (*PERIOD_PRESETS, LOG_PERIOD_ALL)
# Start of "everything the gateway still holds": the earliest start known to be
# accepted. Measured on a real gateway (2026-10-01): 2000-01-01 is accepted,
# 1970-01-01 (00:00 and 01:00) is refused with "Ungültige Zeitangabe 'von
# (Datum)'". Reaching back that far is not about the gateway's age: a clock
# that lost its time after a power cut stamps entries with a default date,
# and only a range that reaches back to it can return them.
# Measured on one gateway (PPC firmware 00950-34900) only. Should another
# firmware refuse 2000, "Alles" fails with the gateway's answer in the log;
# an adaptive fallback is only worth building once that happens.
LOG_ALL_FROM = datetime(2000, 1, 1)


def log_period_range(
    period: str, now: datetime | None = None
) -> tuple[datetime, datetime]:
    """Translate a preset into a (from, to) range in local naive time.

    Unlike the meter export there is no 00:15 closing margin — a log has no
    closing reading — and the open-ended presets run up to now, so today's
    entries are included.
    """
    now = (now or dt_util.now()).replace(tzinfo=None, microsecond=0)
    today = now.replace(hour=0, minute=0, second=0)
    if period == "yesterday":
        return today - timedelta(days=1), today
    if period == "last_7_days":
        return today - timedelta(days=7), now
    if period == "last_30_days":
        return today - timedelta(days=30), now
    if period == "current_month":
        return today.replace(day=1), now
    if period == "last_month":
        first_this_month = today.replace(day=1)
        first_prev_month = (first_this_month - timedelta(days=1)).replace(day=1)
        return first_prev_month, first_this_month
    if period == LOG_PERIOD_ALL:
        return LOG_ALL_FROM, now
    raise ServiceValidationError(
        translation_domain=DOMAIN,
        translation_key="unknown_period",
        translation_placeholders={"period": str(period)},
    )


@dataclass
class _ProcessedLog:
    gateway_id: str
    entries: list[LogEntry]
    # (from, to, content, entry_count) per signed file that holds entries
    parts: list[tuple[datetime, datetime, bytes, int]]
    gaps: list[tuple[int, int]]
    shortfalls: list[tuple[datetime, datetime, int, int]]


def _process(result: LogExportResult) -> _ProcessedLog:
    """Parse, merge and check the exported parts (blocking; run in executor)."""
    gateway_id = ""
    parts: list[tuple[datetime, datetime, bytes, int]] = []
    parsed_entries: list[list[LogEntry]] = []
    for chunk in result.chunks:
        parsed = parse_log_cms(chunk.content)
        gateway_id = gateway_id or parsed.gateway_id
        # A signed "nothing here" adds nothing but clutter to the ZIP.
        if parsed.entries:
            parts.append(
                (chunk.from_dt, chunk.to_dt, chunk.content, len(parsed.entries))
            )
            parsed_entries.append(parsed.entries)
    entries = merge_entries(parsed_entries)
    return _ProcessedLog(
        gateway_id=gateway_id,
        entries=entries,
        parts=parts,
        gaps=find_number_gaps(entries),
        shortfalls=count_shortfalls(entries, result.refused),
    )


def _stamp(value: datetime) -> str:
    return value.strftime("%Y-%m-%d_%H%M%S")


def _write_log_files(
    export_dir: Path,
    base: str,
    processed: _ProcessedLog,
    meta: dict[str, Any],
    download_cms: bool,
    do_csv: bool,
    do_xlsx: bool,
) -> dict[str, str]:
    """Write the requested files (blocking) and return ``kind -> filename``."""
    export_dir.mkdir(parents=True, exist_ok=True)
    written: dict[str, str] = {}
    if download_cms:
        if len(processed.parts) == 1:
            fname = f"{base}.xml.cms"
            (export_dir / fname).write_bytes(processed.parts[0][2])
        else:
            total = len(processed.parts)
            members = [
                (
                    f"Teil{index:02d}von{total:02d}_{_stamp(start)}_bis_"
                    f"{_stamp(end)}.xml.cms",
                    content,
                )
                for index, (start, end, content, _count) in enumerate(
                    processed.parts, 1
                )
            ]
            fname = f"{base}_CMS.zip"
            write_cms_zip(export_dir / fname, members)
        written["cms"] = fname
    if do_csv:
        fname = f"{base}.csv"
        write_log_csv(export_dir / fname, processed.entries)
        written["csv"] = fname
    if do_xlsx:
        fname = f"{base}.xlsx"
        write_log_xlsx(export_dir / fname, processed.entries, meta)
        written["xlsx"] = fname
    return written


async def run_log_export(
    hass: HomeAssistant,
    coordinator,
    from_dt: datetime,
    to_dt: datetime,
    *,
    download_cms: bool,
    do_csv: bool,
    do_xlsx: bool,
) -> dict[str, Any]:
    """Fetch the log for a range and write the selected files.

    Returns ``files`` (kind -> URL), ``entry_count``, ``parts`` (number of
    signed files) and ``complete`` (False if the running numbers have a gap
    or a refused range came up short).

    ``complete`` means "no irregularity detectable", not a guarantee: entries
    missing before the first or after the last one returned leave no trace in
    the running numbers, and a range the gateway accepted at once names no
    count to compare against.
    """
    result = await coordinator.async_export_log(from_dt, to_dt)
    processed = await hass.async_add_executor_job(_process, result)
    if not processed.entries:
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="log_no_entries"
        )
    if processed.gaps or processed.shortfalls:
        _LOGGER.warning(
            "Log export %s .. %s may be incomplete: running-number gaps %s, "
            "short ranges %s",
            from_dt, to_dt, processed.gaps, processed.shortfalls,
        )

    fmt = "%Y-%m-%d %H:%M:%S"
    meta = {
        "gateway_id": processed.gateway_id,
        "from": from_dt.strftime(fmt),
        "to": to_dt.strftime(fmt),
        "created": dt_util.now().strftime(fmt),
        "parts": [
            (start.strftime(fmt), end.strftime(fmt), count)
            for start, end, _content, count in processed.parts
        ],
        "gaps": processed.gaps,
        "refused": result.refused,
        "shortfalls": [
            (start.strftime(fmt), end.strftime(fmt), expected, found)
            for start, end, expected, found in processed.shortfalls
        ],
    }
    base = _sanitize(
        f"Logdaten_{_stamp(from_dt)}_bis_{_stamp(to_dt)}_"
        f"{processed.gateway_id or 'smgw'}"
    ) + _device_suffix(coordinator.device_name)
    token, export_dir = _new_export_dir(hass)
    written = await hass.async_add_executor_job(
        _write_log_files,
        export_dir, base, processed, meta, download_cms, do_csv, do_xlsx,
    )
    _LOGGER.info(
        "Log export wrote %d file(s) with %d entries from %d part(s) to %s",
        len(written), len(processed.entries), len(processed.parts), export_dir,
    )
    return {
        "files": export_links(hass, token, written),
        "entry_count": len(processed.entries),
        "parts": len(processed.parts),
        "complete": not (processed.gaps or processed.shortfalls),
    }
