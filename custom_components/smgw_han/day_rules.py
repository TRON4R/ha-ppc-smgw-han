"""Day types: Monday-Friday, Saturday, Sunday and public holidays.

A tariff layout (see zone_schedule.py) says which zone windows apply on which
KIND of day:

    {"zones": [...Mon-Fri...], "saturday": [...], "sunday": [...],
     "holidays": "off" | "national" | "<state code>"}

An empty ``saturday`` / ``sunday`` list means "same as Mon-Fri". Public
holidays use the Sunday layout (precedence over Saturday, so 3 October on a
Saturday is a Sunday day) -- the holiday rule has three settings:

  off       holidays are ordinary days (default; the safe choice)
  national  the holidays common to every German state
  BY, NW..  those plus the holidays of one federal state

Which setting is right depends on the grid operator's price sheet, which is
why it is a choice and not a built-in assumption.

The ``holidays`` library loads its country data lazily on first use. That is
blocking file I/O, so it must never happen on Home Assistant's event loop:
``preload_holidays`` is run in the import executor at setup, and everything
else only reads the cache it fills.

Pure logic apart from that lazy import -- runnable (and testable) standalone.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date
from typing import Any

from .const import (
    CONF_HOLIDAYS,
    CONF_HOLIDAYS_AS_SUNDAY,
    CONF_TARIFF_ZONES,
    CONF_TARIFF_ZONES_SATURDAY,
    CONF_TARIFF_ZONES_SUNDAY,
    DEFAULT_TARIFF_ZONES,
    HOLIDAY_STATES,
    HOLIDAYS_NATIONAL,
    HOLIDAYS_OFF,
    ZONE_NAME,
)
from .zone_schedule import HOLIDAYS, SATURDAY, SUNDAY, ZONES

_holiday_cache: dict[tuple[str, int], frozenset[date]] = {}

# For the export's documentation (German, like the rest of the export text).
HOLIDAY_STATE_NAMES = {
    "bw": "Baden-Württemberg",
    "by": "Bayern",
    "be": "Berlin",
    "bb": "Brandenburg",
    "hb": "Bremen",
    "hh": "Hamburg",
    "he": "Hessen",
    "mv": "Mecklenburg-Vorpommern",
    "ni": "Niedersachsen",
    "nw": "Nordrhein-Westfalen",
    "rp": "Rheinland-Pfalz",
    "sl": "Saarland",
    "sn": "Sachsen",
    "st": "Sachsen-Anhalt",
    "sh": "Schleswig-Holstein",
    "th": "Thüringen",
}


def normalize_holiday_mode(value: Any) -> str:
    """A valid holiday setting: anything unknown means "off"."""
    # State codes are stored lowercase (the selector keys must be, for
    # hassfest); upper case from hand-edited data is accepted as well.
    text = str(value).strip().lower() if value is not None else ""
    if text == HOLIDAYS_NATIONAL or text in HOLIDAY_STATES:
        return text
    return HOLIDAYS_OFF


def holiday_label(mode: str) -> str:
    """Short German name of a holiday setting ("bundeseinheitlich", "Bayern")."""
    mode = normalize_holiday_mode(mode)
    if mode == HOLIDAYS_NATIONAL:
        return "bundeseinheitlich"
    return HOLIDAY_STATE_NAMES.get(mode, mode)


def holiday_mode(entry_data: Any) -> str:
    """The holiday setting stored in a config entry's data."""
    mode = entry_data.get(CONF_HOLIDAYS)
    if mode is None and entry_data.get(CONF_HOLIDAYS_AS_SUNDAY):
        # Early pre-release builds stored a boolean "nationwide as Sunday".
        return HOLIDAYS_NATIONAL
    return normalize_holiday_mode(mode)


def entry_layout(entry_data: Any) -> dict[str, Any]:
    """The layout valid TODAY, as stored (flat) in a config entry's data."""
    return {
        ZONES: [
            dict(zone)
            for zone in entry_data.get(CONF_TARIFF_ZONES, DEFAULT_TARIFF_ZONES)
        ],
        SATURDAY: [
            dict(zone) for zone in entry_data.get(CONF_TARIFF_ZONES_SATURDAY) or []
        ],
        SUNDAY: [
            dict(zone) for zone in entry_data.get(CONF_TARIFF_ZONES_SUNDAY) or []
        ],
        HOLIDAYS: holiday_mode(entry_data),
    }


def layout_data(layout: dict[str, Any]) -> dict[str, Any]:
    """A layout as the flat config-entry keys (the inverse of entry_layout)."""
    return {
        CONF_TARIFF_ZONES: [dict(zone) for zone in layout[ZONES]],
        CONF_TARIFF_ZONES_SATURDAY: [dict(z) for z in layout.get(SATURDAY) or []],
        CONF_TARIFF_ZONES_SUNDAY: [dict(z) for z in layout.get(SUNDAY) or []],
        CONF_HOLIDAYS: normalize_holiday_mode(layout.get(HOLIDAYS)),
    }


def has_day_rules(layout: dict[str, Any]) -> bool:
    """True if the layout differs from "Mon-Fri applies to every day"."""
    return bool(
        layout.get(SATURDAY)
        or layout.get(SUNDAY)
        or normalize_holiday_mode(layout.get(HOLIDAYS)) != HOLIDAYS_OFF
    )


def _compute_holidays(mode: str, year: int) -> frozenset[date]:
    import holidays  # noqa: PLC0415 - lazy: only needed when a rule is on

    if mode == HOLIDAYS_NATIONAL:
        return frozenset(holidays.Germany(years=year).keys())
    if mode == "by":
        # Mariä Himmelfahrt (15 Aug) is a Bavarian holiday in most
        # municipalities; the library files it under "catholic".
        return frozenset(
            holidays.Germany(
                years=year, subdiv="BY", categories=("public", "catholic")
            ).keys()
        )
    return frozenset(holidays.Germany(years=year, subdiv=mode.upper()).keys())


def holiday_dates(mode: str, year: int) -> frozenset[date]:
    """The public holidays of ``year`` under ``mode`` (cached)."""
    mode = normalize_holiday_mode(mode)
    if mode == HOLIDAYS_OFF:
        return frozenset()
    key = (mode, year)
    if key not in _holiday_cache:
        _holiday_cache[key] = _compute_holidays(mode, year)
    return _holiday_cache[key]


def is_holiday(mode: str, day: date) -> bool:
    """True if ``day`` is a public holiday under the holiday setting ``mode``."""
    return day in holiday_dates(mode, day.year)


def holidays_cached(modes: Iterable[str], years: Iterable[int]) -> bool:
    """True if nothing is left to load for these settings and years."""
    return all(
        (mode, year) in _holiday_cache
        for mode in {normalize_holiday_mode(m) for m in modes} - {HOLIDAYS_OFF}
        for year in years
    )


def preload_holidays(modes: Iterable[str], years: Iterable[int]) -> None:
    """Fill the cache (blocking!): call it from an executor, not the loop."""
    years = list(years)
    for mode in {normalize_holiday_mode(m) for m in modes} - {HOLIDAYS_OFF}:
        for year in years:
            holiday_dates(mode, year)


def zones_for_day(layout: dict[str, Any], day: date) -> list[dict[str, str]]:
    """The zone windows of ``day``: weekday, Saturday, Sunday or holiday.

    A holiday uses the Sunday layout (precedence over Saturday); an empty
    Saturday / Sunday list falls back to the Mon-Fri zones.
    """
    base = layout[ZONES]
    sunday = layout.get(SUNDAY) or base
    if is_holiday(layout.get(HOLIDAYS, HOLIDAYS_OFF), day):
        return sunday
    weekday = day.weekday()
    if weekday == 5:
        return layout.get(SATURDAY) or base
    if weekday == 6:
        return sunday
    return base


def distinct_zone_names(*zone_lists: list[dict[str, str]]) -> list[str]:
    """Zone names in order of first appearance across all given layouts.

    The position in this list is the sensor slot number, so every layout
    (Mon-Fri, Saturday, Sunday) feeds the same sensors by NAME.
    """
    return list(
        dict.fromkeys(zone[ZONE_NAME] for zones in zone_lists for zone in zones)
    )
