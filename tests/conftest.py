"""Shared pytest fixtures for the SMGW HAN test suite."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

FIXTURE_DIR = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> str:
    """Return the text content of a file under tests/fixtures/."""
    return (FIXTURE_DIR / name).read_text(encoding="utf-8")


@pytest.fixture
def fixture_text():
    """Provide the load_fixture helper as a fixture."""
    return load_fixture


@dataclass
class LogRow:
    """One synthetic SMGW log entry for :func:`build_log_cms`."""

    record_number: int
    local: datetime  # naive Europe/Berlin, as the gateway filters by
    level: str = "INFO"
    event_id: str = "10228"
    outcome: str = "SUCCESS"
    template: str = (
        "Der Endbenutzer mit der ID [Kommunikationsprofil-Id] hat sich auf dem "
        "SMGW eingeloggt. Dabei sind folgende Fehler aufgetreten: [Fehler]"
    )
    values: tuple[str, ...] = ("ecpr0000000001.sm", "")


def build_log_cms(
    rows: list[LogRow], gateway: str = "0000636203ff.eppc0000000001.sm"
) -> bytes:
    """A synthetic signed log export with made-up ids.

    Mirrors the structure of real ``exportLogData`` files: DER header bytes,
    the DKE profile_generic-1 XML with a logbook (newest entry first, like
    the gateway), the template as a comment inside message_extensions — or
    after the self-closing element when there are no values — and a binary
    signature trailer. Only the structure matters; nothing is signed.
    """
    berlin = ZoneInfo("Europe/Berlin")
    parts = []
    for index, row in enumerate(
        sorted(rows, key=lambda r: r.record_number, reverse=True), 1
    ):
        ts = row.local.replace(tzinfo=berlin).astimezone(UTC)
        values = "".join(
            f'<ns2:message_extension id="{i}">{value}</ns2:message_extension>'
            for i, value in enumerate(row.values, 1)
        )
        if row.values:
            extensions = (
                f'<ns2:message_extensions count="{len(row.values)}">'
                f"<!--{row.template}-->{values}</ns2:message_extensions>"
            )
        else:
            extensions = (
                '<ns2:message_extensions count="0"/>'
                f"<!--{row.template}-->"
            )
        parts.append(
            f'<ns1:entry id="{index}">'
            f"<ns2:record_number>{row.record_number}</ns2:record_number>"
            f"<ns2:parent_record_number>{row.record_number}"
            "</ns2:parent_record_number>"
            "<ns2:repetition_counter>1</ns2:repetition_counter>"
            "<ns2:event><ns2:seconds_index>0</ns2:seconds_index>"
            f"<ns2:timestamp>{ts:%Y-%m-%dT%H:%M:%SZ}</ns2:timestamp>"
            f"<ns2:level>{row.level}</ns2:level>"
            "<ns2:type><ns2:version>1</ns2:version>"
            "<ns2:vendor_id>PPC</ns2:vendor_id>"
            f"<ns2:event_id>{row.event_id}</ns2:event_id>"
            "<ns2:event_sub_id>0</ns2:event_sub_id></ns2:type>"
            f"<ns2:outcome>{row.outcome}</ns2:outcome>"
            "<ns2:subject_identity>0</ns2:subject_identity>"
            f"{extensions}</ns2:event></ns1:entry>"
        )
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<ns1:object class_id="7" class_version="1" id="{gateway}" '
        'xmlns:ns1="urn:k461-dke-de:profile_generic-1" '
        'xmlns:ns2="urn:k461-dke-de:extension-1">'
        '<ns1:attributes count="3">'
        f'<ns2:logical_name id="1">{gateway}</ns2:logical_name>'
        '<ns1:buffer id="3">'
        f'<ns1:logbook count="{len(rows)}">{"".join(parts)}</ns1:logbook>'
        "</ns1:buffer></ns1:attributes></ns1:object>"
    )
    return b"\x30\x83\x06\x1a\xc7\x06\x09\x2a" + xml.encode("utf-8") + b"\xa0\x82<sig>\x00"


@pytest.fixture
def log_cms():
    """Provide the synthetic log-export builder (see build_log_cms)."""
    return build_log_cms


@pytest.fixture
def log_row():
    """Provide the LogRow type for building synthetic log entries."""
    return LogRow


# Note: `enable_custom_integrations` (which requires the async `hass` fixture)
# is opted into per-module via a local autouse fixture in the HA-harness test
# files (test_config_flow.py, test_services.py). It is intentionally NOT global
# so the pure-logic tests stay synchronous and hass-free.
