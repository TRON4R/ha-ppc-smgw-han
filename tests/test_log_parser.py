"""Tests for log_parser: signed log exports -> LogEntry, merge and checks."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from custom_components.smgw_han.cms_parser import CmsParseError
from custom_components.smgw_han.log_parser import (
    count_shortfalls,
    fill_template,
    find_number_gaps,
    merge_entries,
    parse_log_cms,
)

METER_CMS = Path(__file__).parent / "fixtures" / "cms_sample.xml.cms"


def test_parses_entries_oldest_first_with_filled_text(log_cms, log_row):
    raw = log_cms(
        [
            log_row(101, datetime(2026, 1, 15, 0, 20, 5)),
            log_row(
                102,
                datetime(2026, 1, 15, 0, 21, 0),
                level="ERROR",
                event_id="10041",
                outcome="FAILURE",
                template="Das SMGW mit der ID [Id] war zu lange stromlos.",
                values=("eppc0000000001",),
            ),
        ]
    )
    parsed = parse_log_cms(raw)

    assert parsed.gateway_id == "eppc0000000001"
    # The gateway lists newest first; the parser returns oldest first.
    assert [e.record_number for e in parsed.entries] == [101, 102]
    first, second = parsed.entries
    assert first.message == (
        "Der Endbenutzer mit der ID ecpr0000000001.sm hat sich auf dem SMGW "
        "eingeloggt. Dabei sind folgende Fehler aufgetreten:"
    )
    assert second.message == "Das SMGW mit der ID eppc0000000001 war zu lange stromlos."
    assert second.template == "Das SMGW mit der ID [Id] war zu lange stromlos."
    assert (second.level, second.event_id, second.outcome) == (
        "ERROR", "10041", "FAILURE",
    )
    assert second.outcome_label == "fehlgeschlagen"
    assert first.outcome_label == "erfolgreich"


def test_template_after_self_closing_extensions(log_cms, log_row):
    # count="0": the comment follows the self-closing element instead of
    # sitting inside it (seen in real exports for 'Der Messbetrieb wird
    # eingestellt.').
    raw = log_cms(
        [
            log_row(
                7,
                datetime(2026, 4, 18, 15, 53, 30),
                level="ERROR",
                event_id="10260",
                outcome="FAILURE",
                template="Der Messbetrieb wird eingestellt.",
                values=(),
            )
        ]
    )
    (entry,) = parse_log_cms(raw).entries
    assert entry.message == "Der Messbetrieb wird eingestellt."


def test_timestamps_utc_and_local_across_dst(log_cms, log_row):
    raw = log_cms(
        [
            log_row(1, datetime(2026, 1, 10, 12, 0, 0)),  # CET, UTC+1
            log_row(2, datetime(2026, 7, 10, 12, 0, 0)),  # CEST, UTC+2
        ]
    )
    winter, summer = parse_log_cms(raw).entries
    assert winter.timestamp_utc == datetime(2026, 1, 10, 11, 0, tzinfo=UTC)
    assert winter.timestamp_local == datetime(2026, 1, 10, 12, 0)
    assert summer.timestamp_utc == datetime(2026, 7, 10, 10, 0, tzinfo=UTC)
    assert summer.timestamp_local == datetime(2026, 7, 10, 12, 0)


def test_meter_value_export_is_rejected():
    # Same container, but no logbook: a meter-value CMS must not be read as
    # an empty log.
    with pytest.raises(CmsParseError, match="logbook"):
        parse_log_cms(METER_CMS.read_bytes())


def test_fill_template_keeps_unmatched_parts():
    assert fill_template("A [x] B [y]", ["1"]) == "A 1 B [y]"
    assert fill_template("A [x]", ["1", "2"]) == "A 1 (2)"
    assert fill_template("", ["v"]) == "v"
    assert fill_template("Fehler: [Fehler]", [""]) == "Fehler:"


def test_merge_deduplicates_boundary_entries(log_cms, log_row):
    # Two adjacent exports both hold the entry sitting on their boundary.
    first = parse_log_cms(
        log_cms([log_row(n, datetime(2026, 3, 1, 0, n)) for n in (1, 2, 3)])
    ).entries
    second = parse_log_cms(
        log_cms([log_row(n, datetime(2026, 3, 1, 0, n)) for n in (3, 4)])
    ).entries
    merged = merge_entries([first, second])
    assert [e.record_number for e in merged] == [1, 2, 3, 4]
    assert find_number_gaps(merged) == []


def test_number_gaps_are_reported(log_cms, log_row):
    entries = parse_log_cms(
        log_cms([log_row(n, datetime(2026, 3, 1, 0, n)) for n in (1, 2, 5, 7)])
    ).entries
    assert find_number_gaps(entries) == [(3, 4), (6, 6)]


def test_count_shortfalls(log_cms, log_row):
    entries = parse_log_cms(
        log_cms([log_row(n, datetime(2026, 3, 1, 0, n)) for n in range(1, 6)])
    ).entries
    start, end = datetime(2026, 3, 1, 0, 1), datetime(2026, 3, 1, 0, 5)
    # Boundaries count inclusively: all five entries are inside.
    assert count_shortfalls(entries, [(start, end, 5)]) == []
    assert count_shortfalls(entries, [(start, end, 7)]) == [(start, end, 7, 5)]
