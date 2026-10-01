"""Parse the signed SMGW log export (``exportLogData``) into log entries.

The log export uses the same container as the meter-value export: a PKCS#7/CMS
envelope around a DKE ``profile_generic-1`` XML document. Instead of
``simple_data`` columns its buffer holds a ``logbook`` of entries, so the
embedded document is located with :func:`cms_parser.extract_embedded_xml` and
only the payload walk differs.

Each entry carries its human-readable message as an XML *comment* inside
``message_extensions`` — a template with ``[Placeholder]`` markers — followed
by one ``message_extension`` value per placeholder, in order. Filling the
template reproduces the text the SMGW web interface shows. ElementTree drops
comments by default, hence the ``insert_comments`` tree builder below.

The merge/check helpers at the end work on entries from several exports: the
gateway refuses ranges with more than 1000 entries, so a long range arrives as
several signed files that overlap at their boundaries. Every entry has a
gateway-assigned running ``record_number``, which deduplicates the overlap and
reveals entries that went missing between two files.

Pure module (no Home Assistant imports); call the parsers from an executor
thread.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from .cms_parser import CmsParseError, extract_embedded_xml

_NS = {
    "p": "urn:k461-dke-de:profile_generic-1",
    "e": "urn:k461-dke-de:extension-1",
}
_EXPECTED_ROOT = f"{{{_NS['p']}}}object"
_LOCAL_TZ = ZoneInfo("Europe/Berlin")
_PLACEHOLDER_RE = re.compile(r"\[[^\]]+\]")

# The SMGW web interface shows the outcome in German; the exports are German
# throughout (sheet names, headers), so the files use the same wording.
OUTCOME_LABELS = {"SUCCESS": "erfolgreich", "FAILURE": "fehlgeschlagen"}


@dataclass(frozen=True)
class LogEntry:
    """One entry of the SMGW consumer log."""

    record_number: int
    timestamp_utc: datetime  # aware, UTC — as signed by the gateway
    timestamp_local: datetime  # naive Europe/Berlin, as the web interface shows
    level: str  # INFO / WARNING / ERROR / FATAL
    event_id: str
    outcome: str  # SUCCESS / FAILURE, as delivered
    template: str  # message with [Placeholder] markers ("" if none was sent)
    message: str  # template with the values filled in

    @property
    def outcome_label(self) -> str:
        return OUTCOME_LABELS.get(self.outcome, self.outcome)


@dataclass
class ParsedLog:
    """Entries of one signed log export plus the gateway that signed it."""

    gateway_id: str
    entries: list[LogEntry]


def fill_template(template: str, values: list[str]) -> str:
    """Insert ``values`` into the ``[Placeholder]`` markers, in order.

    Placeholders without a value stay visible; values without a placeholder
    are appended in brackets, so nothing the gateway sent is dropped.
    """
    remaining = iter(values)
    used = 0

    def _next(match: re.Match[str]) -> str:
        nonlocal used
        value = next(remaining, None)
        if value is None:
            return match.group(0)
        used += 1
        return value

    text = _PLACEHOLDER_RE.sub(_next, template) if template else ""
    extra = [v for v in values[used:] if v]
    if extra:
        text = f"{text} ({'; '.join(extra)})" if text else "; ".join(extra)
    return text.strip()


def _parse_timestamp(raw: str) -> datetime:
    ts = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if ts.tzinfo is None:  # the gateway always signs UTC; be explicit anyway
        ts = ts.replace(tzinfo=UTC)
    return ts.astimezone(UTC)


def _parse_entry(entry: ET.Element) -> LogEntry:
    event = entry.find("e:event", _NS)
    if event is None:
        raise CmsParseError("Log entry without an event element.")
    record_text = entry.findtext("e:record_number", default="", namespaces=_NS)
    timestamp_text = event.findtext("e:timestamp", default="", namespaces=_NS)
    try:
        record_number = int(record_text.strip())
        ts_utc = _parse_timestamp(timestamp_text.strip())
    except ValueError as err:
        raise CmsParseError(f"Unparseable log entry: {err}") from err

    # The template comment sits inside message_extensions when there are
    # values, but AFTER the self-closing element when there are none
    # ('<message_extensions count="0"/><!--...-->'), so take the first comment
    # anywhere in the event.
    template = next(
        ((node.text or "").strip() for node in event.iter() if node.tag is ET.Comment),
        "",
    )
    values: list[tuple[int, str]] = []
    for ext in event.iterfind("e:message_extensions/e:message_extension", _NS):
        try:
            position = int(ext.attrib.get("id", "0"))
        except ValueError:
            position = 0
        values.append((position, (ext.text or "").strip()))
    values.sort(key=lambda pair: pair[0])

    return LogEntry(
        record_number=record_number,
        timestamp_utc=ts_utc,
        timestamp_local=ts_utc.astimezone(_LOCAL_TZ).replace(tzinfo=None),
        level=event.findtext("e:level", default="", namespaces=_NS).strip(),
        event_id=event.findtext(
            "e:type/e:event_id", default="", namespaces=_NS
        ).strip(),
        outcome=event.findtext("e:outcome", default="", namespaces=_NS).strip(),
        template=template,
        message=fill_template(template, [v for _pos, v in values]),
    )


def parse_log_cms(raw: bytes) -> ParsedLog:
    """Parse one signed log export. Entries come back oldest first."""
    xml_bytes = extract_embedded_xml(raw)
    parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True))
    try:
        root = ET.fromstring(xml_bytes, parser=parser)
    except ET.ParseError as err:
        raise CmsParseError(f"Invalid embedded log XML: {err}") from err
    if root.tag != _EXPECTED_ROOT:
        raise CmsParseError(f"Unsupported embedded XML root: {root.tag}")

    logbook = root.find("p:attributes/p:buffer/p:logbook", _NS)
    if logbook is None:
        raise CmsParseError("logbook not found — not a log export.")
    gateway_id = (
        root.findtext("p:attributes/e:logical_name", default="", namespaces=_NS)
        or root.attrib.get("id", "")
    ).strip()

    entries = [_parse_entry(e) for e in logbook.findall("p:entry", _NS)]
    entries.sort(key=lambda e: e.record_number)
    return ParsedLog(gateway_id=_short_gateway_id(gateway_id), entries=entries)


def _short_gateway_id(logical_name: str) -> str:
    """``0000636203ff.eppc0215990320.sm`` -> ``eppc0215990320``.

    The middle part is the gateway id the web interface and the log messages
    use ("Das SMGW mit der ID eppc..."); anything else is returned unchanged.
    """
    parts = logical_name.removesuffix(".sm").split(".")
    return parts[-1] if len(parts) > 1 else logical_name


def merge_entries(parts: list[list[LogEntry]]) -> list[LogEntry]:
    """Combine the entries of several exports, deduplicated, oldest first.

    Adjacent exports may both contain an entry that sits exactly on their
    shared boundary; the gateway's running number identifies it as one.
    """
    by_number: dict[int, LogEntry] = {}
    for entries in parts:
        for entry in entries:
            by_number.setdefault(entry.record_number, entry)
    return [by_number[n] for n in sorted(by_number)]


def find_number_gaps(entries: list[LogEntry]) -> list[tuple[int, int]]:
    """Missing running numbers between the first and the last entry.

    Returns inclusive ``(first_missing, last_missing)`` ranges. In every
    export examined so far the numbers of one consumer log are consecutive,
    so a gap means entries are missing from the result. Expects ``entries``
    sorted by record number (as :func:`merge_entries` returns them).
    """
    gaps: list[tuple[int, int]] = []
    for prev, cur in zip(entries, entries[1:]):
        if cur.record_number - prev.record_number > 1:
            gaps.append((prev.record_number + 1, cur.record_number - 1))
    return gaps


def count_shortfalls(
    entries: list[LogEntry],
    reported: list[tuple[datetime, datetime, int]],
) -> list[tuple[datetime, datetime, int, int]]:
    """Ranges the gateway reported more entries for than the result holds.

    ``reported`` lists the ranges the gateway refused as too large, with the
    count it named in the refusal. All of their entries must have arrived via
    the smaller exports they were split into. Returns
    ``(from, to, reported, found)`` for every range that came up short.
    Boundaries are compared inclusively: an entry exactly on a boundary may
    be counted by both neighbours, which can only make ``found`` larger, never
    hide a missing entry.
    """
    shortfalls = []
    for start, end, expected in reported:
        found = sum(1 for e in entries if start <= e.timestamp_local <= end)
        if found < expected:
            shortfalls.append((start, end, expected, found))
    return shortfalls
