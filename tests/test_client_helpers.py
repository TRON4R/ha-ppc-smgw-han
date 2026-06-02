"""Tests for the small pure parsing helpers on SmgwClient.

Includes the meter-dropdown parsing (_list_meter_options): the HTTP layer
(_post) is replaced with an AsyncMock returning a saved meterform fixture, so
no real gateway is contacted. Those two are async tests (run on the managed
event loop) rather than using asyncio.run(), which would close the loop and
break pytest-homeassistant-custom-component's autouse event-loop fixture for
every subsequent test.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from custom_components.smgw_han.smgw_client import SmgwClient


def _client() -> SmgwClient:
    return SmgwClient("https://smgw.local/cgi-bin/hanservice.cgi", "user", "pw")


def test_extract_meter_id():
    assert (
        SmgwClient._extract_meter_id("01005e318002.1lgz0072999211.sm")
        == "1lgz0072999211"
    )


def test_parse_content_disposition_plain():
    assert (
        SmgwClient._parse_content_disposition_filename(
            'attachment; filename="export.sm_data.xml.cms"'
        )
        == "export.sm_data.xml.cms"
    )


def test_parse_content_disposition_rfc5987():
    assert (
        SmgwClient._parse_content_disposition_filename(
            "attachment; filename*=UTF-8''my%20export.cms"
        )
        == "my export.cms"
    )


def test_parse_content_disposition_none():
    assert SmgwClient._parse_content_disposition_filename(None) is None


def test_parse_token_from_input():
    html = '<form><input name="tkn" value="abc123" /></form>'
    assert _client()._parse_token(html) == "abc123"


def test_parse_token_missing():
    assert _client()._parse_token("<form></form>") is None


def test_parse_firmware():
    html = '<p id="div_fwversion">00861-34788</p>'
    assert SmgwClient._parse_firmware(html) == "00861-34788"


def test_parse_firmware_missing_defaults_unknown():
    assert SmgwClient._parse_firmware("<html></html>") == "unknown"


def test_parse_host_from_url():
    assert (
        SmgwClient.parse_host_from_url(
            "https://192.168.100.100/cgi-bin/hanservice.cgi"
        )
        == "192.168.100.100"
    )


async def test_list_meter_options_single(fixture_text):
    client = _client()
    html = fixture_text("meterform_single.html")
    with patch.object(client, "_post", new=AsyncMock(return_value=html)):
        options = await client._list_meter_options()
    assert options == [("mid-aaa-0001", "1lgz0072999211")]


async def test_list_meter_options_multi(fixture_text):
    client = _client()
    html = fixture_text("meterform_multi.html")
    with patch.object(client, "_post", new=AsyncMock(return_value=html)):
        options = await client._list_meter_options()
    assert options == [
        ("mid-aaa-0001", "1lgz0072999211"),
        ("mid-bbb-0002", "1lgz0088888888"),
    ]
