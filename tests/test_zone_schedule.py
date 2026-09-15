"""Tests for zone_schedule: dated tariff-zone layouts.

Pure logic, no Home Assistant involved. The decisive case is the one the
feature exists for: on 1 January the nightly run fetches 31 December, which
must still be split by the previous year's windows even though the new ones
are already active.
"""

from __future__ import annotations

from datetime import date

import pytest

from custom_components.smgw_han import zone_schedule as zs

GO = [{"time": "00:00", "name": "Go"}, {"time": "05:00", "name": "Standard"}]
M3_2026 = [
    {"time": "00:00", "name": "NT"},
    {"time": "05:45", "name": "ST"},
    {"time": "17:00", "name": "HT"},
    {"time": "19:30", "name": "ST"},
    {"time": "23:45", "name": "NT"},
]
M3_2027 = [
    {"time": "00:00", "name": "NT"},
    {"time": "06:00", "name": "ST"},
    {"time": "16:30", "name": "HT"},
    {"time": "20:00", "name": "ST"},
    {"time": "23:30", "name": "NT"},
]


@pytest.fixture
def scheduled():
    """M3_2026 active, M3_2027 scheduled for 1 January 2027."""
    return zs.with_change([], M3_2026, date(2027, 1, 1), M3_2027)


def test_no_schedule_falls_back_to_the_entry_zones():
    assert zs.zones_for_day([], GO, date(2026, 5, 1)) == GO
    assert zs.pending_change([], date(2026, 12, 1)) is None
    assert zs.next_change_at([], date(2026, 12, 1)) is None


def test_first_change_materializes_a_baseline(scheduled):
    """Without the baseline the old layout would be lost on promotion."""
    assert [item[zs.VALID_FROM] for item in scheduled] == [
        None,
        "2027-01-01",
    ]
    assert scheduled[0][zs.ZONES] == M3_2026
    assert scheduled[1][zs.ZONES] == M3_2027


@pytest.mark.parametrize(
    ("day", "expected"),
    [
        (date(2026, 6, 15), M3_2026),
        # The whole point: fetched on 1 January, split by 2026 windows.
        (date(2026, 12, 31), M3_2026),
        (date(2027, 1, 1), M3_2027),
        (date(2027, 7, 1), M3_2027),
    ],
)
def test_every_day_gets_the_layout_valid_on_it(scheduled, day, expected):
    assert zs.zones_for_day(scheduled, M3_2027, day) == expected


def test_pending_until_the_date_is_reached(scheduled):
    assert zs.next_change_at(scheduled, date(2026, 12, 31)) == date(2027, 1, 1)
    assert zs.next_change_at(scheduled, date(2027, 1, 1)) is None
    assert zs.next_change_at(scheduled, date(2027, 6, 1)) is None


def test_rescheduling_the_same_date_replaces(scheduled):
    """Editing a pending change must not stack a second entry on one date."""
    again = zs.with_change(scheduled, M3_2026, date(2027, 1, 1), GO)
    assert len(again) == 2
    assert again[1][zs.ZONES] == GO


def test_discarding_a_pending_change_restores_the_plain_state(scheduled):
    assert zs.without_pending(scheduled, date(2026, 12, 1)) == []


def test_history_survives_once_it_has_taken_effect(scheduled):
    """An old layout is never dropped — the export needs it for old days."""
    kept = zs.without_pending(scheduled, date(2027, 3, 1))
    assert len(kept) == 2
    assert zs.zones_for_day(kept, GO, date(2026, 6, 1)) == M3_2026


def test_several_changes_stack_in_order(scheduled):
    three = zs.with_change(scheduled, M3_2027, date(2028, 1, 1), GO)
    assert len(three) == 3
    assert zs.zones_for_day(three, GO, date(2027, 7, 1)) == M3_2027
    assert zs.zones_for_day(three, GO, date(2028, 2, 1)) == GO


def test_normalize_sorts_and_drops_junk():
    messy = [
        {"valid_from": "2028-01-01", "zones": GO},
        {"valid_from": None, "zones": M3_2026},
        {"valid_from": "nonsense", "zones": GO},
        {"zones": []},
        "not a dict",
        {"valid_from": "2027-01-01", "zones": M3_2027},
    ]
    assert [item[zs.VALID_FROM] for item in zs.normalize(messy)] == [
        None,
        "2027-01-01",
        "2028-01-01",
    ]


def test_periods_in_range_documents_every_layout_it_touches(scheduled):
    """The export has to name both layouts when the range crosses a change."""
    crossing = zs.periods_in_range(
        scheduled, GO, date(2026, 12, 1), date(2027, 1, 31)
    )
    assert [p[zs.VALID_FROM] for p in crossing] == [
        "2026-12-01",
        "2027-01-01",
    ]
    assert crossing[0][zs.ZONES] == M3_2026
    assert crossing[1][zs.ZONES] == M3_2027

    inside = zs.periods_in_range(
        scheduled, GO, date(2026, 3, 1), date(2026, 4, 1)
    )
    assert [p[zs.VALID_FROM] for p in inside] == ["2026-03-01"]
