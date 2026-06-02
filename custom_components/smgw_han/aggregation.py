"""Tolerant per-day aggregation of raw SMGW readings for the export service.

Unlike :func:`smgw_client.SmgwClient._process_readings` (which is strict and
raises when a required reading is missing for a single target day), this module
aggregates an arbitrary multi-day range and leaves missing values as ``None``
so a single incomplete day never aborts the whole export. The daily-end-value
and tariff-zone layout mirrors the standalone ``smgw_tagesendwerte_to_excel``
script the owner already uses.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from .const import OBIS_EXPORT, OBIS_IMPORT
from .smgw_client import MeterReading, find_closest_reading


@dataclass
class DailySummary:
    """Aggregated values for a single calendar day.

    The "end value" of day D is the first cumulative reading at 00:00 of the
    following day (D+1), which is the cleanest closing value for a cumulative
    register. Consumption splits follow the Octopus Go model
    (Go = start..switch, Standard = switch..next-day-midnight).
    """

    day: date
    start_timestamp: datetime | None  # actual time of the D 00:00 reading
    end_timestamp: datetime | None  # actual time of the D+1 00:00 reading
    import_end: float | None  # 1.8.0 cumulative reading at D+1 00:00
    export_end: float | None  # 2.8.0 cumulative reading at D+1 00:00
    import_start: float | None  # 1.8.0 at D 00:00
    import_switch: float | None  # 1.8.0 at tariff switch time on D
    consumption_go: float | None
    consumption_standard: float | None
    consumption_total: float | None
    feedin_total: float | None

    def to_dict(self) -> dict:
        """Serializable representation for the service response variable."""
        return {
            "day_of_summary": self.day.isoformat(),
            "start_timestamp": (
                self.start_timestamp.isoformat()
                if self.start_timestamp
                else None
            ),
            "end_timestamp": (
                self.end_timestamp.isoformat() if self.end_timestamp else None
            ),
            "import_end": self.import_end,
            "export_end": self.export_end,
            "import_start": self.import_start,
            "import_switch": self.import_switch,
            "consumption_go": self.consumption_go,
            "consumption_standard": self.consumption_standard,
            "consumption_total": self.consumption_total,
            "feedin_total": self.feedin_total,
        }


def _diff(a: float | None, b: float | None) -> float | None:
    """Return ``b - a`` rounded to 4 decimals, or ``None`` if either is missing."""
    if a is None or b is None:
        return None
    return round(b - a, 4)


def build_daily_summary(
    readings: list[MeterReading],
    tariff_switch_hour: int = 5,
    tariff_switch_minute: int = 0,
) -> list[DailySummary]:
    """Aggregate raw readings into one :class:`DailySummary` per calendar day.

    Days are taken from the span of timestamps present in ``readings``. A day
    is included only if at least one of its computed values is available.
    """
    if not readings:
        return []

    import_readings = [r for r in readings if r.obis_code == OBIS_IMPORT]
    export_readings = [r for r in readings if r.obis_code == OBIS_EXPORT]

    first_day = min(r.timestamp for r in readings).date()
    last_day = max(r.timestamp for r in readings).date()

    summaries: list[DailySummary] = []
    day = first_day
    while day <= last_day:
        next_day = day + timedelta(days=1)
        midnight_start = datetime(day.year, day.month, day.day, 0, 0, 1)
        tariff_switch = datetime(
            day.year, day.month, day.day,
            tariff_switch_hour, tariff_switch_minute, 1,
        )
        midnight_end = datetime(
            next_day.year, next_day.month, next_day.day, 0, 0, 1
        )

        import_start = find_closest_reading(import_readings, midnight_start)
        import_switch = find_closest_reading(import_readings, tariff_switch)
        import_end = find_closest_reading(import_readings, midnight_end)
        export_start = find_closest_reading(export_readings, midnight_start)
        export_end = find_closest_reading(export_readings, midnight_end)

        # Representative timestamps of the day's boundary readings.
        start_reading = import_start or export_start
        end_reading = import_end or export_end

        start_val = import_start.value if import_start else None
        switch_val = import_switch.value if import_switch else None
        end_val = import_end.value if import_end else None
        export_start_val = export_start.value if export_start else None
        export_end_val = export_end.value if export_end else None

        summary = DailySummary(
            day=day,
            start_timestamp=start_reading.timestamp if start_reading else None,
            end_timestamp=end_reading.timestamp if end_reading else None,
            import_end=end_val,
            export_end=export_end_val,
            import_start=start_val,
            import_switch=switch_val,
            consumption_go=_diff(start_val, switch_val),
            consumption_standard=_diff(switch_val, end_val),
            consumption_total=_diff(start_val, end_val),
            feedin_total=_diff(export_start_val, export_end_val),
        )

        # Only include days that actually have a closing (end) value. The last
        # day of a range typically has just a start reading and no end value
        # yet; such an incomplete boundary day would otherwise show up as a
        # confusing date-only row.
        if summary.import_end is not None or summary.export_end is not None:
            summaries.append(summary)

        day = next_day

    return summaries
