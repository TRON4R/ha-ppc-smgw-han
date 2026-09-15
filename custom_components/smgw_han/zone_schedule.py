"""Dated tariff-zone layouts: schedule a zone change ahead of time.

Tariff windows change on a date that is known weeks ahead: grid-fee windows
(section 14a EnWG, module 3) are published per calendar year, and a change of
supplier or tariff usually takes effect mid-year. Without this module the user
has to be at the computer on the day — and editing the zones ON the effective
date is *wrong*: the nightly run at 00:15 that morning still fetches the
previous day, which has to be split by the OLD windows.

So a scheduled change is not a flip of one value at midnight. The schedule
keeps every dated layout and answers the only question the rest of the
integration asks: which zones were valid on day X?

    [
      {"valid_from": null,         "zones": [...]},   # baseline, "since ever"
      {"valid_from": "2027-01-01", "zones": [...]},   # scheduled change
    ]

Entries are ordered ascending, the baseline first. An entry whose date has
passed is history and is kept — the export needs it to split old days by the
windows that were actually billed then.

``CONF_TARIFF_ZONES`` stays in the config entry as the materialized "valid
today" layout, so sensor setup, the store's change detection and the options
prefill keep reading one plain zone list and need no schedule awareness. The
schedule is the source of truth; that field is its projection onto today.

Pure logic, no Home Assistant imports — runnable (and testable) standalone.
"""

from __future__ import annotations

from datetime import date
from typing import Any

VALID_FROM = "valid_from"
ZONES = "zones"

Schedule = list[dict[str, Any]]
ZoneList = list[dict[str, str]]


def _parse_date(value: Any) -> date | None:
    """Parse a stored ``valid_from`` (ISO string, or None for the baseline)."""
    if value is None:
        return None
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def normalize(schedule: Any) -> Schedule:
    """Return ``schedule`` sorted ascending, baseline first, dates as ISO text.

    Tolerates the JSON shape the config entry round-trips through, a missing
    or malformed schedule (-> empty), and unsorted input.
    """
    if not isinstance(schedule, list):
        return []
    items: list[tuple[date | None, dict[str, Any]]] = []
    for raw in schedule:
        if not isinstance(raw, dict) or not raw.get(ZONES):
            continue
        try:
            parsed = _parse_date(raw.get(VALID_FROM))
        except ValueError:
            continue
        items.append(
            (
                parsed,
                {
                    VALID_FROM: parsed.isoformat() if parsed else None,
                    ZONES: [dict(zone) for zone in raw[ZONES]],
                },
            )
        )
    # date.min sorts the baseline (None) to the front; later entries win on a
    # duplicate date, which is what replacing a pending change relies on.
    items.sort(key=lambda item: item[0] or date.min)
    deduped: Schedule = []
    for parsed, item in items:
        if deduped and deduped[-1][VALID_FROM] == item[VALID_FROM]:
            deduped[-1] = item
        else:
            deduped.append(item)
    return deduped


def zones_for_day(
    schedule: Any, fallback: ZoneList, day: date
) -> ZoneList:
    """The zone layout valid on ``day``.

    ``fallback`` (the entry's ``CONF_TARIFF_ZONES``) is used when no schedule
    exists at all — the normal case for an installation that never scheduled
    a change.
    """
    best: ZoneList | None = None
    for item in normalize(schedule):
        valid_from = _parse_date(item[VALID_FROM])
        if valid_from is None or valid_from <= day:
            best = item[ZONES]
        else:
            break
    return [dict(zone) for zone in (best if best is not None else fallback)]


def pending_change(schedule: Any, today: date) -> dict[str, Any] | None:
    """The next scheduled change that has not taken effect yet, if any."""
    for item in normalize(schedule):
        valid_from = _parse_date(item[VALID_FROM])
        if valid_from is not None and valid_from > today:
            return item
    return None


def next_change_at(schedule: Any, today: date) -> date | None:
    """Date on which the next scheduled change takes effect, if any."""
    pending = pending_change(schedule, today)
    return _parse_date(pending[VALID_FROM]) if pending else None


def with_change(
    schedule: Any,
    current_zones: ZoneList,
    valid_from: date,
    zones: ZoneList,
) -> Schedule:
    """Add (or replace) the change taking effect on ``valid_from``.

    The first scheduled change also materializes a baseline from
    ``current_zones``, so the layout valid until then is preserved rather than
    being overwritten when the change is promoted later.
    """
    existing = normalize(schedule)
    if not existing:
        existing = [{VALID_FROM: None, ZONES: [dict(z) for z in current_zones]}]
    return normalize(
        existing
        + [
            {
                VALID_FROM: valid_from.isoformat(),
                ZONES: [dict(zone) for zone in zones],
            }
        ]
    )


def without_pending(schedule: Any, today: date) -> Schedule:
    """Drop every change that has not taken effect yet.

    A schedule left with nothing but its baseline is dropped entirely, so an
    entry that never had a scheduled change ends up exactly as it started.
    """
    kept = [
        item
        for item in normalize(schedule)
        if (_parse_date(item[VALID_FROM]) or date.min) <= today
    ]
    if len(kept) <= 1:
        return []
    return kept


def periods_in_range(
    schedule: Any, fallback: ZoneList, first_day: date, last_day: date
) -> list[dict[str, Any]]:
    """The dated layouts covering ``first_day``..``last_day``, in order.

    Used by the export so a range crossing a change is documented with both
    layouts instead of silently re-splitting old days by today's windows. The
    first entry's ``valid_from`` is clamped to ``first_day``.
    """
    periods = [
        {
            VALID_FROM: first_day.isoformat(),
            ZONES: zones_for_day(schedule, fallback, first_day),
        }
    ]
    for item in normalize(schedule):
        valid_from = _parse_date(item[VALID_FROM])
        if valid_from is None or valid_from <= first_day:
            continue
        if valid_from > last_day:
            break
        periods.append(
            {VALID_FROM: valid_from.isoformat(), ZONES: list(item[ZONES])}
        )
    return periods
