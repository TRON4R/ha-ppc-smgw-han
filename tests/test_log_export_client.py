"""Tests for the client side of the log export: classification and splitting.

A fake gateway stands in for the HTTP layer (``_send``). It behaves like the
real one as observed on 2026-10-01: ``exportLogData`` with more than 1000
entries in the range answers with the double-escaped refusal text naming the
count, an empty range answers "Keine Daten vorhanden.", anything else gets a
signed CMS. Boundaries are inclusive on both ends, so adjacent slices can
share an entry — the worst case for the merge.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

import httpx
import pytest

from custom_components.smgw_han import smgw_client
from custom_components.smgw_han.log_parser import (
    count_shortfalls,
    find_number_gaps,
    merge_entries,
    parse_log_cms,
)
from custom_components.smgw_han.smgw_client import (
    SmgwClient,
    SmgwClientError,
    SmgwLogLimitError,
    classify_log_export,
)

REFUSAL = (
    "Die Abfrage liefert {n} Datens&auml;tze zur&uuml;ck. Es sind nur 1000 "
    "erlaubt. Schr&auml;nken Sie die Abfrageparameter ein."
)
EMPTY_PAGE = (
    "<html><body><form name='menu'><input type='hidden' name='tkn' "
    "value='SESSIONTOKEN123'></form><div class='content'>Von 2020-01-01 "
    "00:00:00 bis 2020-02-01 00:00:00, Seite 1<br/>Keine Daten vorhanden."
    "</div></body></html>"
)


class FakeGateway(SmgwClient):
    """SmgwClient whose HTTP layer is an in-memory gateway."""

    def __init__(
        self, rows, build_cms, garbage_answers: int = 0,
        reject_dst_times: bool = False,
    ) -> None:
        super().__init__("https://gw.invalid/cgi-bin/hanservice.cgi", "u", "p")
        self.rows = rows
        self.build_cms = build_cms
        self.garbage_answers = garbage_answers
        # How PPC reads a wall-clock time that the DST change skips or
        # repeats is unknown; this gateway assumes the worst and refuses it,
        # with the answer the real one gave for a date it would not take.
        self.reject_dst_times = reject_dst_times
        self.logins = 0
        self.logouts = 0
        self.ranges: list[tuple[datetime, datetime]] = []
        # What the real gateway's exportLogData sends for an empty range.
        self.empty_answer = "Keine Daten\n"

    async def _login(self) -> str:
        self.logins += 1
        self._token = "tkn"
        return ""

    async def _logout(self) -> None:
        self.logouts += 1
        self._token = None

    async def _send(self, data: dict) -> httpx.Response:
        assert data["action"] == "exportLogData"
        start = datetime.strptime(data["from"], "%Y-%m-%d %H:%M:%S")
        end = datetime.strptime(data["to"], "%Y-%m-%d %H:%M:%S")
        self.ranges.append((start, end))
        if self.reject_dst_times and any(map(_in_dst_change_hour, (start, end))):
            return httpx.Response(
                200, text="Ung&uuml;ltige Zeitangabe 'von (Datum)'\n"
            )
        if self.garbage_answers:
            self.garbage_answers -= 1
            return httpx.Response(200, text="<html>Invalide Session</html>")
        inside = [r for r in self.rows if start <= r.local <= end]
        if len(inside) > smgw_client.LOG_EXPORT_MAX_ENTRIES:
            return httpx.Response(200, text=REFUSAL.format(n=len(inside)))
        if not inside:
            return httpx.Response(200, text=self.empty_answer)
        return httpx.Response(200, content=self.build_cms(inside))


def _in_dst_change_hour(local: datetime) -> bool:
    return smgw_client._is_nonexistent(local) or smgw_client._is_repeated(local)


# ---------------------------------------------------------------------------
# classify_log_export
# ---------------------------------------------------------------------------


def test_classify_refusal_double_and_single_escaped():
    assert classify_log_export(REFUSAL.format(n=1260).encode()) == (
        "too_many", 1260,
    )
    single = "Die Abfrage liefert 2400 Datensätze zurück. Es sind nur 1000 erlaubt."
    assert classify_log_export(single.encode("utf-8")) == ("too_many", 2400)
    doubly = REFUSAL.format(n=1500).replace("&", "&amp;")
    assert classify_log_export(doubly.encode()) == ("too_many", 1500)


def test_classify_cms_empty_and_unknown(log_cms, log_row):
    cms = log_cms([log_row(1, datetime(2026, 3, 1))])
    assert classify_log_export(cms) == ("cms", None)
    assert classify_log_export(EMPTY_PAGE.encode()) == ("empty", None)
    assert classify_log_export(b"") == ("empty", None)
    # The export's own answer for an empty range, as the real gateway sent it
    # for 2020-01-01 .. 2022-04-01 (beta.1 failed on it).
    assert classify_log_export(b"Keine Daten\n") == ("empty", None)
    assert classify_log_export(b"<html>Invalide Session</html>") == (
        "unknown", None,
    )


# ---------------------------------------------------------------------------
# Splitting
# ---------------------------------------------------------------------------


def _spam_month(log_row):
    """Background entries every 6 h for half a year plus a spam burst.

    2500 entries one minute apart on 25 March — the burst alone exceeds the
    limit twice over, and it is concentrated in two days of a six-month range.
    """
    rows = []
    t = datetime(2026, 1, 1)
    while t < datetime(2026, 7, 1):
        rows.append(t)
        t += timedelta(hours=6)
    rows += [datetime(2026, 3, 25, 6, 0) + timedelta(minutes=i) for i in range(2500)]
    rows.sort()
    return [log_row(n, local) for n, local in enumerate(rows, 1000)]


async def test_split_fetches_everything_once(log_cms, log_row):
    rows = _spam_month(log_row)
    gateway = FakeGateway(rows, log_cms)

    result = await gateway.async_export_log(
        datetime(2020, 1, 1), datetime(2026, 7, 1)
    )

    # One session for the whole export: every login writes a log entry.
    assert (gateway.logins, gateway.logouts) == (1, 1)
    parts = [parse_log_cms(chunk.content).entries for chunk in result.chunks]
    assert all(len(p) <= smgw_client.LOG_EXPORT_MAX_ENTRIES for p in parts)
    merged = merge_entries(parts)
    assert [e.record_number for e in merged] == [r.record_number for r in rows]
    assert find_number_gaps(merged) == []
    # The top-level refusal named the full count; all of it arrived.
    assert result.refused[0] == (
        datetime(2020, 1, 1), datetime(2026, 7, 1), len(rows)
    )
    assert count_shortfalls(merged, result.refused) == []
    assert result.requests == len(gateway.ranges) < 60
    # Chunks come back in chronological order (ZIP part numbering).
    starts = [chunk.from_dt for chunk in result.chunks]
    assert starts == sorted(starts)


async def test_single_request_when_under_the_limit(log_cms, log_row):
    rows = [log_row(n, datetime(2026, 9, 1) + timedelta(hours=n)) for n in range(50)]
    gateway = FakeGateway(rows, log_cms)
    result = await gateway.async_export_log(datetime(2026, 9, 1), datetime(2026, 10, 1))
    assert result.requests == 1
    assert len(result.chunks) == 1
    assert result.refused == []


async def test_empty_range_yields_no_chunks(log_cms, log_row):
    gateway = FakeGateway([], log_cms)
    result = await gateway.async_export_log(datetime(2020, 1, 1), datetime(2020, 2, 1))
    assert result.chunks == []
    assert gateway.logouts == 1


async def test_unsplittable_second_raises(log_cms, log_row):
    # More than 1000 entries inside one second can never be exported.
    stamp = datetime(2026, 3, 25, 6, 0, 0)
    rows = [log_row(n, stamp) for n in range(1001)]
    gateway = FakeGateway(rows, log_cms)
    with pytest.raises(SmgwLogLimitError):
        await gateway.async_export_log(stamp, stamp + timedelta(seconds=1))
    assert gateway.logouts == 1  # the session is closed on failure too


async def test_request_cap(log_cms, log_row, monkeypatch):
    monkeypatch.setattr(smgw_client, "LOG_EXPORT_MAX_REQUESTS", 3)
    gateway = FakeGateway(_spam_month(log_row), log_cms)
    with pytest.raises(SmgwLogLimitError):
        await gateway.async_export_log(datetime(2020, 1, 1), datetime(2026, 7, 1))
    assert len(gateway.ranges) == 3


@pytest.mark.parametrize(
    ("day", "skip_missing_hour"),
    [
        (datetime(2026, 10, 25), False),  # autumn: 02:00-02:59 occurs twice
        (datetime(2027, 3, 28), True),  # spring: 02:00-02:59 does not exist
    ],
)
async def test_split_points_avoid_the_dst_change_hour(
    log_cms, log_row, day, skip_missing_hour
):
    # 1800 entries in 00:00-04:00 split into two halves - exactly at 02:00,
    # a wall-clock time that is ambiguous (autumn) or does not exist (spring).
    # (External review 2026-10-02.)
    if skip_missing_hour:  # every 6 s for 4 h, minus the hour that never was
        stamps = [day + timedelta(seconds=6 * i) for i in range(2400)]
        stamps = [t for t in stamps if t.hour != 2]
    else:  # every 8 s for 4 h
        stamps = [day + timedelta(seconds=8 * i) for i in range(1800)]
    assert len(stamps) == 1800
    rows = [log_row(n, t) for n, t in enumerate(stamps, 1)]
    gateway = FakeGateway(rows, log_cms, reject_dst_times=True)

    result = await gateway.async_export_log(day, day + timedelta(hours=4))

    sent = [t for pair in gateway.ranges for t in pair]
    assert not any(map(_in_dst_change_hour, sent))
    assert day + timedelta(hours=3) in sent  # the split moved to 03:00
    merged = merge_entries(
        [parse_log_cms(chunk.content).entries for chunk in result.chunks]
    )
    assert [e.record_number for e in merged] == [r.record_number for r in rows]


def test_split_points_on_ordinary_days_stay_where_they_are():
    start = datetime(2026, 5, 15)
    points = [datetime(2026, 5, 15, 2, 0), datetime(2026, 5, 15, 2, 30)]
    assert smgw_client._clear_of_dst_change(
        points, start, start + timedelta(hours=4)
    ) == points


async def test_unexpected_answer_gets_one_fresh_login(log_cms, log_row):
    rows = [log_row(1, datetime(2026, 9, 2))]
    gateway = FakeGateway(rows, log_cms, garbage_answers=1)
    result = await gateway.async_export_log(datetime(2026, 9, 1), datetime(2026, 10, 1))
    assert gateway.logins == 2
    assert len(result.chunks) == 1


async def test_non_cms_answers_are_logged_without_token(log_cms, log_row, caplog):
    # How the gateway answers an empty range was never observed: every
    # answer that is not a signed file goes to the log at INFO, readable,
    # and without the session token the page carries in a hidden field.
    rows = [log_row(n, datetime(2026, 3, 25, 6, 0) + timedelta(minutes=n))
            for n in range(1200)]
    gateway = FakeGateway(rows, log_cms)
    gateway.empty_answer = EMPTY_PAGE  # an HTML answer carrying the token
    with caplog.at_level(logging.INFO, logger=smgw_client.__name__):
        await gateway.async_export_log(datetime(2020, 1, 1), datetime(2026, 7, 1))

    infos = [r.getMessage() for r in caplog.records if r.levelno == logging.INFO]
    assert any(
        "-> too_many 1200 (HTTP 200" in m
        and "Die Abfrage liefert 1200 Datensätze zurück" in m
        for m in infos
    )
    assert any("-> empty (HTTP 200" in m and "Keine Daten vorhanden." in m
               for m in infos)
    assert not any("-> cms" in m for m in infos)  # signed files stay at DEBUG
    assert "SESSIONTOKEN123" not in caplog.text


async def test_unexpected_answer_twice_fails(log_cms, log_row):
    gateway = FakeGateway([], log_cms, garbage_answers=2)
    with pytest.raises(SmgwClientError, match="Unexpected answer"):
        await gateway.async_export_log(datetime(2026, 9, 1), datetime(2026, 10, 1))
    assert gateway.logouts == 1
