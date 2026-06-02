"""Parse the signed CMS export into MeterReadings.

The SMGW's CMS download (``exportMeterValues``) wraps a plain DKE
``profile_generic-1`` XML payload — the gateway-signed Zaehlerstandsgang. A
single CMS response covers the **whole** requested range (verified: one file
held 85 days x 15-min x 2 OBIS), so the export reads from this in one request
instead of looping the paginated HTML table day by day.

The XML lives as readable text inside the PKCS#7 container, so no ASN.1/crypto
handling is needed — locate ``<?xml`` and trim to the closing root tag. Logic
adapted from the owner's standalone ``smgw_tagesendwerte_to_excel`` script.

Pure module (no Home Assistant imports); call ``parse_cms_readings`` from an
executor thread — parsing a multi-MB XML is CPU-bound.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from .const import OBIS_EXPORT, OBIS_IMPORT
from .smgw_client import MeterReading

# DKE profile namespaces. Matched by URI, so the document's ns1/ns2 prefixes
# are irrelevant.
_NS = {
    "p": "urn:k461-dke-de:profile_generic-1",
    "e": "urn:k461-dke-de:extension-1",
}
_ROOT_CLOSE = "</ns1:object>"
# The SMGW reports local German wall-clock readings; the CMS stores them in UTC.
# Convert back to naive Europe/Berlin so timestamps match what the HTML parser
# produces and the existing aggregation / find_closest logic keeps working.
_LOCAL_TZ = ZoneInfo("Europe/Berlin")
_UNIT_WATT_HOURS = 30  # DLMS unit code; values in Wh are scaled to kWh.

# Hex OBIS (capture_object logical_name) -> the integration's OBIS code.
_OBIS_HEX_TO_CODE = {
    "0100010800ff": OBIS_IMPORT,  # 1.8.0 consumption
    "0100020800ff": OBIS_EXPORT,  # 2.8.0 feed-in
}


class CmsParseError(Exception):
    """Raised when the embedded XML cannot be located or parsed."""


def extract_embedded_xml(raw: bytes) -> str:
    """Return the embedded XML text from the CMS byte stream."""
    start = raw.find(b"<?xml")
    if start < 0:
        raise CmsParseError("No XML start ('<?xml') found in the CMS data.")
    decoded = raw[start:].decode("utf-8", errors="ignore")
    end = decoded.rfind(_ROOT_CLOSE)
    if end < 0:
        raise CmsParseError(f"XML end ({_ROOT_CLOSE}) not found in the CMS data.")
    return decoded[: end + len(_ROOT_CLOSE)]


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
        try:
            value = Decimal(long64) * (Decimal(10) ** int(scaler_text or "0"))
            ts_utc = datetime.fromisoformat(
                capture_time.replace("Z", "+00:00")
            )
        except (ValueError, ArithmeticError) as err:
            raise CmsParseError(f"Unparseable CMS entry: {err}") from err
        if unit_text and int(unit_text) == _UNIT_WATT_HOURS:
            value = value / Decimal(1000)
        ts_local = ts_utc.astimezone(_LOCAL_TZ).replace(tzinfo=None)
        readings.append(
            MeterReading(
                timestamp=ts_local,
                obis_code=obis_code,
                value=float(value),
                unit="kWh",
                quality="valid",
            )
        )
    return readings


def parse_cms_readings(raw: bytes) -> list[MeterReading]:
    """Parse signed CMS bytes into MeterReadings (1.8.0 import + 2.8.0 feed-in).

    Non-target OBIS series are skipped. Returns readings sorted by
    ``(timestamp, obis_code)`` (matching the HTML export path).
    """
    root = ET.fromstring(extract_embedded_xml(raw))
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
