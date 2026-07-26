"""Parse the signed CMS export into MeterReadings.

The SMGW's CMS download (``exportMeterValues``) wraps a plain DKE
``profile_generic-1`` XML payload — the gateway-signed Zaehlerstandsgang. A
single CMS response covers the **whole** requested range (verified: one file
held 85 days x 15-min x 2 OBIS), so the export reads from this in one request
instead of looping the paginated HTML table day by day.

The XML lives as readable text inside the PKCS#7 container, so no ASN.1/crypto
handling is needed: locate ``<?xml`` and let a streaming parser determine where
the root element closes. That end detection is structural, so any namespace
prefix works (``ns1:``, ``pg:``, or a default namespace) and the binary CMS
signature trailer following the document is reliably excluded. Logic adapted
from the owner's standalone ``smgw_tagesendwerte_to_excel`` script.

Scope is deliberately narrow: only the DKE ``profile_generic-1`` payload is
supported. Other container/TAF schemas are rejected with a clear error rather
than silently half-parsed.

Pure module (no Home Assistant imports); call ``parse_cms_readings`` from an
executor thread — parsing a multi-MB XML is CPU-bound.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import datetime
from decimal import Decimal
from xml.parsers import expat
from zoneinfo import ZoneInfo

from .const import OBIS_EXPORT, OBIS_IMPORT
from .smgw_client import MeterReading

# DKE profile namespaces. Everything is matched by URI — including the root
# element check below — so the document's prefixes (ns1:/ns2:, pg:, or a
# default namespace) are irrelevant.
_NS = {
    "p": "urn:k461-dke-de:profile_generic-1",
    "e": "urn:k461-dke-de:extension-1",
}
# Expanded name of the only supported root element (prefix-independent).
_EXPECTED_ROOT = f"{{{_NS['p']}}}object"
# The SMGW reports local German wall-clock readings; the CMS stores them in UTC.
# Convert back to naive Europe/Berlin so timestamps match what the HTML parser
# produces and the existing aggregation / find_closest logic keeps working.
_LOCAL_TZ = ZoneInfo("Europe/Berlin")
_UNIT_WATT_HOURS = 30  # DLMS unit code; values in Wh are scaled to kWh.

# CMS measurement status (<status><unsigned>): 0=valid, 4=invalid, 3=not present
# (a carried-forward placeholder for a missing 15-min slot). Readings are kept
# regardless and labelled accordingly; nothing is dropped.
_CMS_STATUS = {"0": "valid", "4": "invalid", "3": "not_present"}

# Hex OBIS (capture_object logical_name) -> the integration's OBIS code.
_OBIS_HEX_TO_CODE = {
    "0100010800ff": OBIS_IMPORT,  # 1.8.0 consumption
    "0100020800ff": OBIS_EXPORT,  # 2.8.0 feed-in
}


class CmsParseError(Exception):
    """Raised when the embedded XML cannot be located or parsed."""


class _RootComplete(Exception):
    """Internal signal: the embedded document's root element was closed.

    Raised from the expat end-element handler to stop parsing right at the
    document end, so the CMS signature bytes that follow are never fed to the
    parser. Only this exception means success — a real ``ExpatError`` still
    indicates broken XML.
    """


def extract_embedded_xml(raw: bytes) -> bytes:
    """Return the embedded XML document from the CMS byte stream.

    The document is assumed to **start** at the first ``<?xml`` marker: the DKE
    payload always carries an XML declaration (the declaration is optional in
    XML generally, so this is a deliberate, documented assumption).

    The **end** is determined structurally by parsing until the root element
    closes. That makes the extraction independent of the namespace prefix and
    of any XML-lookalike bytes inside the trailing CMS signature.

    Returns raw bytes (not text) so the XML parser can honour the encoding
    declared in the document itself.
    """
    start = raw.find(b"<?xml")
    if start < 0:
        raise CmsParseError("No XML start ('<?xml') found in the CMS data.")
    # Every offset below is relative to `payload` — never mix in `start`.
    payload = raw[start:]

    parser = expat.ParserCreate()
    depth = 0
    max_depth = 0
    root_close_start = -1

    def _on_start(_name: str, _attrs: dict[str, str]) -> None:
        nonlocal depth, max_depth
        depth += 1
        max_depth = max(max_depth, depth)

    def _on_end(_name: str) -> None:
        nonlocal depth, root_close_start
        depth -= 1
        if depth == 0:
            # CurrentByteIndex points at the '<' of the root's closing tag
            # (verified). It only points *past* the element for a childless
            # root, which is rejected below — so the cut stays unambiguous.
            root_close_start = parser.CurrentByteIndex
            raise _RootComplete

    parser.StartElementHandler = _on_start
    parser.EndElementHandler = _on_end

    try:
        parser.Parse(payload, True)
    except _RootComplete:
        pass
    except expat.ExpatError as err:
        raise CmsParseError(f"Embedded XML is not well-formed: {err}") from err

    if root_close_start < 0:
        raise CmsParseError("Embedded XML ended before its root was closed.")

    # A childless root ('<object/>' or '<object></object>') can never be a valid
    # profile_generic-1 payload — that schema requires attributes/capture_objects
    # and attributes/buffer. Rejecting it here is not just a scope check: for a
    # self-closing element expat reports the position *past* it, so the document
    # end would have to be guessed from the trailing bytes. Since an element
    # WITH children cannot be self-closing, excluding this case makes the cut
    # below exact instead of heuristic. (The previous implementation likewise
    # failed on such a document, just with a less helpful message.)
    if max_depth < 2:
        raise CmsParseError(
            "Embedded XML root has no child elements — not a profile_generic-1 "
            "payload."
        )

    # Guaranteed a real closing tag now. It may carry whitespace
    # ('</p:object >'), so the end is the tag's own '>', never a length computed
    # from the element name.
    if not payload.startswith(b"</", root_close_start):
        raise CmsParseError("Root closing tag not found where expected.")
    close_end = payload.find(b">", root_close_start)
    if close_end < 0:
        raise CmsParseError("Unterminated root closing tag in the CMS data.")
    return payload[: close_end + 1]


def _column_obis_map(root: ET.Element) -> dict[str, str]:
    """Map each column id to its hex OBIS code via the capture_objects block."""
    capture_objects = root.find("p:attributes/p:capture_objects", _NS)
    if capture_objects is None:
        raise CmsParseError("capture_objects not found in CMS XML.")
    mapping: dict[str, str] = {}
    for obj in capture_objects.findall("p:capture_object", _NS):
        obj_id = obj.attrib.get("id")
        logical_name = obj.findtext(
            "e:logical_name", default="", namespaces=_NS
        ).strip()
        if obj_id and logical_name:
            mapping[obj_id] = logical_name.split(".")[0].lower()
    return mapping


def _parse_entries(column: ET.Element, obis_code: str) -> list[MeterReading]:
    readings: list[MeterReading] = []
    for entry in column.findall("p:entry_gateway_signed", _NS):
        capture_time = entry.findtext(
            "e:capture_time", default="", namespaces=_NS
        ).strip()
        long64 = entry.findtext(
            "e:value/e:long64", default="", namespaces=_NS
        ).strip()
        if not capture_time or not long64:
            continue
        scaler_text = entry.findtext(
            "e:scaler", default="0", namespaces=_NS
        ).strip()
        unit_text = entry.findtext("e:unit", default="", namespaces=_NS).strip()
        status_text = entry.findtext(
            "e:status/e:unsigned", default="", namespaces=_NS
        ).strip()
        try:
            value = Decimal(long64) * (Decimal(10) ** int(scaler_text or "0"))
            ts_utc = datetime.fromisoformat(
                capture_time.replace("Z", "+00:00")
            )
        except (ValueError, ArithmeticError) as err:
            raise CmsParseError(f"Unparseable CMS entry: {err}") from err
        if unit_text and int(unit_text) == _UNIT_WATT_HOURS:
            value = value / Decimal(1000)
        quality = (
            _CMS_STATUS.get(status_text, status_text) if status_text else "valid"
        )
        ts_local = ts_utc.astimezone(_LOCAL_TZ).replace(tzinfo=None)
        readings.append(
            MeterReading(
                timestamp=ts_local,
                obis_code=obis_code,
                value=float(value),
                unit="kWh",
                quality=quality,
            )
        )
    return readings


def parse_cms_readings(raw: bytes) -> list[MeterReading]:
    """Parse signed CMS bytes into MeterReadings (1.8.0 import + 2.8.0 feed-in).

    Non-target OBIS series are skipped. Returns readings sorted by
    ``(timestamp, obis_code)`` (matching the HTML export path).
    """
    xml_bytes = extract_embedded_xml(raw)
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as err:
        # Defensive: extract_embedded_xml already parsed the document, so a
        # malformed payload normally fails there first. Kept so callers only
        # ever have to handle CmsParseError.
        raise CmsParseError(f"Invalid embedded CMS XML: {err}") from err
    if root.tag != _EXPECTED_ROOT:
        raise CmsParseError(f"Unsupported embedded XML root: {root.tag}")

    column_obis = _column_obis_map(root)
    simple_data = root.find("p:attributes/p:buffer/p:simple_data", _NS)
    if simple_data is None:
        raise CmsParseError("simple_data not found in CMS XML.")

    readings: list[MeterReading] = []
    for column in simple_data.findall("p:column", _NS):
        hex_obis = column_obis.get(column.attrib.get("id", ""))
        obis_code = _OBIS_HEX_TO_CODE.get(hex_obis or "")
        if obis_code is None:
            continue  # not a 1.8.0 / 2.8.0 series
        readings.extend(_parse_entries(column, obis_code))

    readings.sort(key=lambda r: (r.timestamp, r.obis_code))
    return readings
