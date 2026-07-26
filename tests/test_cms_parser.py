"""Tests for cms_parser.parse_cms_readings (signed-CMS export parsing)."""

from __future__ import annotations

from datetime import date, datetime, time
from pathlib import Path

import pytest

from custom_components.smgw_han.aggregation import build_daily_summary
from custom_components.smgw_han.cms_parser import (
    CmsParseError,
    extract_embedded_xml,
    parse_cms_readings,
)
from custom_components.smgw_han.const import OBIS_EXPORT, OBIS_IMPORT

FIXTURE = Path(__file__).parent / "fixtures" / "cms_sample.xml.cms"


def _readings():
    return parse_cms_readings(FIXTURE.read_bytes())


def test_extracts_only_mapped_obis():
    readings = _readings()
    # 3 import + 2 export; the third column (unmapped OBIS) is skipped.
    assert len(readings) == 5
    assert {r.obis_code for r in readings} == {OBIS_IMPORT, OBIS_EXPORT}


def test_values_units_and_naive_local_timestamps():
    readings = _readings()
    by_key = {(r.obis_code, r.timestamp): r for r in readings}

    # long64 * 10**scaler(-1) / 1000 (unit 30 = Wh) -> kWh.
    a = by_key[(OBIS_IMPORT, datetime(2026, 5, 15, 0, 0, 1))]
    assert a.value == 1000.0
    assert a.unit == "kWh"
    assert a.quality == "valid"
    assert a.timestamp.tzinfo is None  # naive Europe/Berlin

    assert by_key[(OBIS_IMPORT, datetime(2026, 5, 15, 5, 0, 1))].value == 1002.5
    assert by_key[(OBIS_IMPORT, datetime(2026, 5, 16, 0, 0, 1))].value == 1010.0
    assert by_key[(OBIS_EXPORT, datetime(2026, 5, 15, 0, 0, 1))].value == 500.0
    assert by_key[(OBIS_EXPORT, datetime(2026, 5, 16, 0, 0, 1))].value == 503.0


def test_utc_is_converted_to_berlin_local():
    # 2026-05-14T22:00:01Z is 2026-05-15 00:00:01 in CEST (+02:00).
    readings = _readings()
    assert any(
        r.timestamp == datetime(2026, 5, 15, 0, 0, 1) for r in readings
    )
    assert not any(
        r.timestamp == datetime(2026, 5, 14, 22, 0, 1) for r in readings
    )


def test_end_to_end_daily_summary():
    zones = [(time(0, 0), "Go"), (time(5, 0), "Standard")]
    summary = build_daily_summary(_readings(), zones)
    assert [s.day for s in summary] == [date(2026, 5, 15)]
    s = summary[0]
    assert s.zone_consumptions == {"Go": 2.5, "Standard": 7.5}
    assert s.consumption_total == 10.0
    assert s.feedin_total == 3.0


def test_missing_xml_raises():
    with pytest.raises(CmsParseError):
        parse_cms_readings(b"no xml in here at all")


# One import column with three entries: status 0 (valid), 4 (invalid),
# 3 (not present / carried-forward gap placeholder).
_STATUS_CMS = (
    b"PKCS7-WRAPPER\n"
    b'<?xml version="1.0" encoding="UTF-8"?>\n'
    b'<ns1:object class_id="7" id="x.1lgz.sm"'
    b' xmlns:ns1="urn:k461-dke-de:profile_generic-1"'
    b' xmlns:ns2="urn:k461-dke-de:extension-1">'
    b'<ns1:attributes count="3">'
    b'<ns1:capture_objects id="2" count="1">'
    b'<ns1:capture_object id="1">'
    b"<ns2:logical_name>0100010800ff.1lgz.sm</ns2:logical_name>"
    b"</ns1:capture_object></ns1:capture_objects>"
    b'<ns1:buffer id="3"><ns1:simple_data count="1">'
    b'<ns1:column count="3" id="1">'
    b'<ns1:entry_gateway_signed id="1"><ns2:value><ns2:long64>10000000'
    b"</ns2:long64></ns2:value><ns2:scaler>-1</ns2:scaler><ns2:unit>30"
    b"</ns2:unit><ns2:status><ns2:unsigned>0</ns2:unsigned></ns2:status>"
    b"<ns2:capture_time>2026-05-14T22:00:01Z</ns2:capture_time>"
    b"</ns1:entry_gateway_signed>"
    b'<ns1:entry_gateway_signed id="2"><ns2:value><ns2:long64>10010000'
    b"</ns2:long64></ns2:value><ns2:scaler>-1</ns2:scaler><ns2:unit>30"
    b"</ns2:unit><ns2:status><ns2:unsigned>4</ns2:unsigned></ns2:status>"
    b"<ns2:capture_time>2026-05-14T23:00:01Z</ns2:capture_time>"
    b"</ns1:entry_gateway_signed>"
    b'<ns1:entry_gateway_signed id="3"><ns2:value><ns2:long64>10010000'
    b"</ns2:long64></ns2:value><ns2:scaler>-1</ns2:scaler><ns2:unit>30"
    b"</ns2:unit><ns2:status><ns2:unsigned>3</ns2:unsigned></ns2:status>"
    b"<ns2:capture_time>2026-05-15T00:00:00Z</ns2:capture_time>"
    b"</ns1:entry_gateway_signed>"
    b"</ns1:column></ns1:simple_data></ns1:buffer></ns1:attributes>"
    b"</ns1:object>\nSIGNATURE-TRAILER"
)


def test_status_maps_to_quality_and_keeps_all():
    readings = parse_cms_readings(_STATUS_CMS)
    # All three entries are kept (nothing dropped), labelled by their status.
    assert len(readings) == 3
    assert sorted(r.quality for r in readings) == [
        "invalid",
        "not_present",
        "valid",
    ]


# --------------------------------------------------------------------------
# Structural XML extraction (prefix-independent end detection).
#
# The embedded document's end used to be found by searching for the literal
# "</ns1:object>". These tests pin the structural replacement: any namespace
# prefix works, and the binary CMS signature trailer is never included.
# --------------------------------------------------------------------------

# A realistic trailer: non-UTF-8 bytes (as a real signature would be) plus an
# XML-lookalike token that must NOT be mistaken for the document end.
_BIN_TRAILER = b"\x30\x82\x04\xa0\xff\xfe</fake>\x00\x81\xde\xad"

_MINIMAL_BODY = (
    b'<?xml version="1.0" encoding="UTF-8"?>\n'
    b'<%(open)s class_id="7" %(xmlns)s>\n'
    b'  <%(p)sattributes count="1"><%(p)sbuffer/></%(p)sattributes>\n'
    b"</%(close)s>"
)


def _doc(open_tag: bytes, xmlns: bytes, prefix: bytes, close_tag: bytes) -> bytes:
    """Build a minimal profile_generic-1 document with the given prefix style."""
    return _MINIMAL_BODY % {
        b"open": open_tag,
        b"xmlns": xmlns,
        b"p": prefix,
        b"close": close_tag,
    }


_PG_NS = b'xmlns:pg="urn:k461-dke-de:profile_generic-1"'
_DEFAULT_NS = b'xmlns="urn:k461-dke-de:profile_generic-1"'


@pytest.mark.parametrize(
    ("doc", "expected_end"),
    [
        # Alternative prefix.
        (_doc(b"pg:object", _PG_NS, b"pg:", b"pg:object"), b"</pg:object>"),
        # Default namespace (no prefix at all).
        (_doc(b"object", _DEFAULT_NS, b"", b"object"), b"</object>"),
        # Closing tag with whitespace before '>' — valid XML, so the cut must
        # not be computed from len("</name>").
        (
            _doc(b"pg:object", _PG_NS, b"pg:", b"pg:object   "),
            b"</pg:object   >",
        ),
    ],
    ids=["alt-prefix", "default-namespace", "whitespace-in-close-tag"],
)
def test_extraction_is_prefix_independent_and_drops_trailer(doc, expected_end):
    xml = extract_embedded_xml(doc + _BIN_TRAILER)

    assert xml.endswith(expected_end)
    assert b"</fake>" not in xml  # trailer lookalike ignored
    assert b"\xff\xfe" not in xml  # no binary trailer bytes leaked
    assert xml == doc  # byte-exact: exactly the document, nothing more


@pytest.mark.parametrize(
    "trailer",
    [
        _BIN_TRAILER,
        # Trailers starting immediately with '<' — including one that is byte
        # for byte the root's own closing tag. Any attempt to locate the
        # document end from the *trailing* bytes gets fooled by these.
        b"</fake>\xff\xfe",
        b"</pg:object>\x00\x81",
        b"<pg:object>\x00\x81",
    ],
    ids=["binary", "lookalike-close", "exact-close-tag", "lookalike-open"],
)
@pytest.mark.parametrize(
    "root",
    [b"<pg:object " + _PG_NS + b"/>", b"<pg:object " + _PG_NS + b"></pg:object>"],
    ids=["self-closing", "empty-element"],
)
def test_childless_root_is_rejected(root, trailer):
    """A childless root is refused instead of guessed at.

    ``profile_generic-1`` requires attributes/capture_objects and
    attributes/buffer, so a root without children can never be a valid payload.
    Rejecting it also removes the only case where expat reports the end position
    *past* the element — an element with children cannot be self-closing, so the
    remaining cut is exact rather than heuristic.
    """
    doc = b'<?xml version="1.0"?>\n' + root
    with pytest.raises(CmsParseError, match="no child elements"):
        extract_embedded_xml(doc + trailer)


@pytest.mark.parametrize(
    "trailer",
    [_BIN_TRAILER, b"</fake>\xff\xfe", b"</pg:object>\x00\x81"],
    ids=["binary", "lookalike-close", "exact-close-tag"],
)
def test_nested_root_cut_is_exact_whatever_follows(trailer):
    """With children present the cut must not depend on the trailer at all."""
    doc = (
        b'<?xml version="1.0"?>\n<pg:object ' + _PG_NS + b">"
        b"<a><b/></a></pg:object   >"
    )
    assert extract_embedded_xml(doc + trailer) == doc


def test_extraction_of_real_fixture_ends_at_root():
    raw = FIXTURE.read_bytes()
    xml = extract_embedded_xml(raw)

    assert isinstance(xml, bytes)  # not decoded text
    assert xml.startswith(b'<?xml version="1.0"')
    assert xml.endswith(b"</ns1:object>")
    # Nothing before the declaration and nothing of the fixture's trailer.
    assert xml == raw[raw.find(b"<?xml") : raw.find(b"</ns1:object>") + 13]
    assert b"TRAILING" not in xml


def test_truncated_root_raises():
    truncated = _doc(b"pg:object", _PG_NS, b"pg:", b"pg:object")[:-6]
    with pytest.raises(CmsParseError):
        extract_embedded_xml(truncated + _BIN_TRAILER)


def test_malformed_xml_raises_cms_parse_error():
    broken = (
        b'<?xml version="1.0"?>\n<pg:object ' + _PG_NS + b"><a></b></pg:object>"
    )
    with pytest.raises(CmsParseError):
        extract_embedded_xml(broken)


def test_foreign_root_schema_is_rejected_with_clear_message():
    # Well-formed XML, but a container schema this parser does not support.
    foreign = (
        b'<?xml version="1.0"?>\n'
        b'<khw:container xmlns:khw="urn:example:khw"><khw:x/></khw:container>'
    ) + _BIN_TRAILER
    with pytest.raises(CmsParseError, match="Unsupported embedded XML root"):
        parse_cms_readings(foreign)


def test_default_namespace_document_parses_end_to_end():
    """Same payload as the fixture but with a default namespace."""
    doc = FIXTURE.read_bytes()
    xml = extract_embedded_xml(doc).decode("utf-8")
    renamed = (
        xml.replace('xmlns:ns1="urn:k461-dke-de:profile_generic-1"',
                    'xmlns="urn:k461-dke-de:profile_generic-1"')
        .replace("<ns1:", "<")
        .replace("</ns1:", "</")
    )
    readings = parse_cms_readings(renamed.encode("utf-8") + _BIN_TRAILER)
    assert len(readings) == 5
    assert {r.obis_code for r in readings} == {OBIS_IMPORT, OBIS_EXPORT}
