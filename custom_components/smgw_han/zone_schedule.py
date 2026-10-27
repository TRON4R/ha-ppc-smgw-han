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

A dated entry may also carry the rules for the other day types -- the
Saturday and Sunday zones and which public holidays count as Sunday:

    {"valid_from": "2027-01-01", "zones": [...],
     "saturday": [...], "sunday": [...], "holidays": "off"}

They belong to the SAME date as the weekday zones: otherwise a later change of
the Saturday times would silently re-split every earlier Saturday. A rule key
that is missing from an entry (entries written before day types existed)
inherits the entry's current value; a key that is present, even empty, is
authoritative ("no Saturday override from this date on").

``CONF_TARIFF_ZONES`` (and the Saturday / Sunday / holiday keys) stay in the
config entry as the materialized "valid today" layout, so sensor setup, the
store's change detection and the options prefill keep reading plain values and
need no schedule awareness. The schedule is the source of truth; those fields
are its projection onto today.

Pure logic, no Home Assistant imports — runnable (and testable) standalone.
"""

from __future__ import annotations

from datetime import date
from typing import Any

VALID_FROM = "valid_from"
ZONES = "zones"
# Optional day-type rules of a dated layout (see module docstring).
SATURDAY = "saturday"
SUNDAY = "sunday"
HOLIDAYS = "holidays"
ZONE_RULE_KEYS = (SATURDAY, SUNDAY)  # lists of zones
NO_HOLIDAYS = "off"

Schedule = list[dict[str, Any]]
ZoneList = list[dict[str, str]]


def _empty_rules() -> dict[str, Any]:
    """Day-type rules meaning "no difference to Mon-Fri"."""
    return {SATURDAY: [], SUNDAY: [], HOLIDAYS: NO_HOLIDAYS}


def _copy_rules(rules: dict[str, Any] | None) -> dict[str, Any]:
    """A defensive, complete copy of ``rules`` (missing keys -> empty)."""
    result = _empty_rules()
    for key in ZONE_RULE_KEYS:
        value = (rules or {}).get(key)
        if isinstance(value, list):
            result[key] = [dict(zone) for zone in value]
    holidays = (rules or {}).get(HOLIDAYS)
    if isinstance(holidays, str) and holidays.strip():
        # State codes are stored lowercase (see day_rules.normalize_holiday_mode).
        result[HOLIDAYS] = holidays.strip().lower()
    return result


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
        item: dict[str, Any] = {
            VALID_FROM: parsed.isoformat() if parsed else None,
            ZONES: [dict(zone) for zone in raw[ZONES]],
        }
        # Rule keys are optional; keep exactly those that are present, so
        # "missing" (inherit) stays distinguishable from "empty" (none).
        for key in ZONE_RULE_KEYS:
            if isinstance(raw.get(key), list):
                item[key] = [dict(zone) for zone in raw[key]]
        if isinstance(raw.get(HOLIDAYS), str) and raw[HOLIDAYS].strip():
            item[HOLIDAYS] = raw[HOLIDAYS].strip().lower()
        items.append((parsed, item))
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


def layout_for_day(
    schedule: Any, fallback: dict[str, Any], day: date
) -> dict[str, Any]:
    """The complete layout valid on ``day``: weekday zones plus day-type rules.

    ``fallback`` is the entry's current layout (``{"zones": ..., "saturday":
    ..., "sunday": ..., "holidays": ...}``). It answers for an installation
    that never scheduled a change, and supplies every rule key a dated entry
    does not carry itself.
    """
    best: dict[str, Any] | None = None
    for item in normalize(schedule):
        valid_from = _parse_date(item[VALID_FROM])
        if valid_from is None or valid_from <= day:
            best = item
        else:
            break
    fallback_rules = _copy_rules(fallback)
    if best is None:
        return {ZONES: [dict(zone) for zone in fallback[ZONES]], **fallback_rules}
    result = {ZONES: [dict(zone) for zone in best[ZONES]]}
    for key in (*ZONE_RULE_KEYS, HOLIDAYS):
        result[key] = (
            _copy_rules(best)[key] if key in best else fallback_rules[key]
        )
    return result


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
    *,
    current_rules: dict[str, Any] | None = None,
    rules: dict[str, Any] | None = None,
) -> Schedule:
    """Add (or replace) the change taking effect on ``valid_from``.

    The first scheduled change also materializes a baseline from
    ``current_zones``, so the layout valid until then is preserved rather than
    being overwritten when the change is promoted later.

    ``current_rules`` / ``rules`` are the day-type rules (Saturday, Sunday,
    holidays) of the baseline and of the new entry. When ``rules`` is given
    the new entry carries all of them explicitly, so it fully replaces what
    was valid before; without it the entry stays weekday-only and inherits.
    """
    existing = normalize(schedule)
    if not existing:
        baseline: dict[str, Any] = {
            VALID_FROM: None,
            ZONES: [dict(z) for z in current_zones],
        }
        if current_rules is not None:
            baseline.update(_copy_rules(current_rules))
        existing = [baseline]
    elif rules is not None and current_rules is not None:
        # Entries without rule keys inherit the rules of the entry data. Once
        # the new entry carries other rules, that inheritance would rewrite
        # the history, so every OLDER entry gets the rules that applied to it
        # written down now. A LATER entry without rule keys is a pure zone
        # change on top of the new entry: it keeps inheriting, from the new
        # rules, not from the ones that are being replaced.
        before = _copy_rules(current_rules)
        after = _copy_rules(rules)
        for item in existing:
            item_from = _parse_date(item[VALID_FROM])
            older = item_from is None or item_from < valid_from
            filled = before if older else after
            for key in (*ZONE_RULE_KEYS, HOLIDAYS):
                item.setdefault(
                    key,
                    [dict(z) for z in filled[key]]
                    if isinstance(filled[key], list)
                    else filled[key],
                )
    entry: dict[str, Any] = {
        VALID_FROM: valid_from.isoformat(),
        ZONES: [dict(zone) for zone in zones],
    }
    if rules is not None:
        entry.update(_copy_rules(rules))
    return normalize(existing + [entry])


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
    schedule: Any,
    fallback: ZoneList,
    first_day: date,
    last_day: date,
    *,
    fallback_rules: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """The dated layouts covering ``first_day``..``last_day``, in order.

    Used by the export so a range crossing a change is documented with both
    layouts instead of silently re-splitting old days by today's windows. The
    first entry's ``valid_from`` is clamped to ``first_day``. Besides the
    weekday ``zones`` every period carries the day-type rules in force then.
    """
    base = {ZONES: [dict(zone) for zone in fallback], **_copy_rules(fallback_rules)}
    periods = [
        {
            VALID_FROM: first_day.isoformat(),
            **layout_for_day(schedule, base, first_day),
        }
    ]
    for item in normalize(schedule):
        valid_from = _parse_date(item[VALID_FROM])
        if valid_from is None or valid_from <= first_day:
            continue
        if valid_from > last_day:
            break
        periods.append(
            {
                VALID_FROM: valid_from.isoformat(),
                **layout_for_day(schedule, base, valid_from),
            }
        )
    return periods
