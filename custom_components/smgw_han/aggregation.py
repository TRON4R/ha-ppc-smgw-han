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
from .smgw_client import MeterReading, TariffZones, find_closest_reading


@dataclass
class DailySummary:
    """Aggregated values for a single calendar day.

    The "end value" of day D is the first cumulative reading at 00:00 of the
    following day (D+1), which is the cleanest closing value for a cumulative
    register. Consumption splits follow the configured tariff zones: segments
    sharing a zone name are summed into one value; a zone with any missing
    boundary reading is ``None``.
    """

    day: date
    start_timestamp: datetime | None  # actual time of the D 00:00 reading
    end_timestamp: datetime | None  # actual time of the D+1 00:00 reading
    import_end: float | None  # 1.8.0 cumulative reading at D+1 00:00
    export_end: float | None  # 2.8.0 cumulative reading at D+1 00:00
    import_start: float | None  # 1.8.0 at D 00:00
    import_switches: dict[str, float | None]  # 1.8.0 at each inner boundary ("HH:MM")
    zone_consumptions: dict[str, float | None]  # zone name -> kWh (or None)
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
            "import_switches": dict(self.import_switches),
            "zones": dict(self.zone_consumptions),
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
    zones: TariffZones,
) -> list[DailySummary]:
    """Aggregate raw readings into one :class:`DailySummary` per calendar day.

    ``zones`` is the parsed tariff-zone definition (ordered ``(time, name)``
    pairs, the first at 00:00). Days are taken from the span of timestamps
    present in ``readings``. A day is included only if at least one of its
    computed values is available.
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
        # Segment boundaries: each configured segment start (second :01, like
        # the strict daily processing) plus next-day midnight as the closer.
        boundary_times = [
            datetime(day.year, day.month, day.day, t.hour, t.minute, 1)
            for t, _name in zones
        ]
        boundary_times.append(
            datetime(next_day.year, next_day.month, next_day.day, 0, 0, 1)
        )

        boundary_rs = [
            find_closest_reading(import_readings, bt) for bt in boundary_times
        ]
        boundary_vals = [r.value if r else None for r in boundary_rs]
        export_start = find_closest_reading(export_readings, boundary_times[0])
        export_end = find_closest_reading(export_readings, boundary_times[-1])

        # Sum segment diffs per zone name; any missing boundary makes the
        # affected zone(s) None while the other zones stay computable.
        zone_consumptions: dict[str, float | None] = {}
        for i, (_t, name) in enumerate(zones):
            seg = _diff(boundary_vals[i], boundary_vals[i + 1])
            if name in zone_consumptions:
                prev = zone_consumptions[name]
                zone_consumptions[name] = (
                    None
                    if prev is None or seg is None
                    else round(prev + seg, 4)
                )
            else:
                zone_consumptions[name] = seg

        import_switches = {
            t.strftime("%H:%M"): boundary_vals[i]
            for i, (t, _name) in enumerate(zones)
            if i > 0
        }

        # Representative timestamps of the day's boundary readings.
        start_reading = boundary_rs[0] or export_start
        end_reading = boundary_rs[-1] or export_end

        export_start_val = export_start.value if export_start else None
        export_end_val = export_end.value if export_end else None

        summary = DailySummary(
            day=day,
            start_timestamp=start_reading.timestamp if start_reading else None,
            end_timestamp=end_reading.timestamp if end_reading else None,
            import_end=boundary_vals[-1],
            export_end=export_end_val,
            import_start=boundary_vals[0],
            import_switches=import_switches,
            zone_consumptions=zone_consumptions,
            consumption_total=_diff(boundary_vals[0], boundary_vals[-1]),
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
