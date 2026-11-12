"""The meter export's own time limit and how its failures surface (3.9.0).

Before, every request shared the client's 30 s, and a nine-month export
failed with a generic "download failed" while the gateway was still
assembling the file. Now only ``exportMeterValues`` may stay silent for
two minutes, a silent gateway gets a message of its own, and an HTTP error
answer carries the gateway's text into the log.
"""

from __future__ import annotations

from datetime import datetime

import httpx
import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.smgw_han.const import CONF_METER_ID, DOMAIN
from custom_components.smgw_han.coordinator import SmgwCoordinator
from custom_components.smgw_han.smgw_client import (
    CMS_EXPORT_READ_TIMEOUT_S,
    SmgwClient,
    SmgwConnectionError,
    SmgwServerError,
    SmgwTimeoutError,
)


@pytest.fixture(autouse=True)
def _enable(enable_custom_integrations):
    yield


def _client(handler) -> SmgwClient:
    client = SmgwClient("https://smgw.local/cgi-bin/hanservice.cgi", "u", "p")
    client._token = "tkn"
    # Same default limit as SmgwClient._get_client, with a fake transport.
    client._client = httpx.AsyncClient(
        timeout=30.0, transport=httpx.MockTransport(handler)
    )
    return client


async def test_export_request_gets_the_long_read_limit():
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.extensions["timeout"])
        return httpx.Response(200, content=b"\x30<?xml signed")

    client = _client(handler)
    content, _name = await client._post_binary(
        {"action": "exportMeterValues"},
        timeout=httpx.Timeout(30.0, read=CMS_EXPORT_READ_TIMEOUT_S),
    )
    await client._send({"action": "meterform"})
    await client.close()

    assert content == b"\x30<?xml signed"
    assert seen[0]["read"] == CMS_EXPORT_READ_TIMEOUT_S == 120.0
    assert seen[0]["connect"] == 30.0
    # Everything else keeps the client's 30 s.
    assert seen[1]["read"] == 30.0


async def test_silent_gateway_is_a_timeout_not_a_lost_connection():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("no answer", request=request)

    client = _client(handler)
    with pytest.raises(SmgwTimeoutError) as err:
        await client._post_binary({"action": "exportMeterValues"})
    await client.close()
    # Still a connection error for every caller that only knows that one.
    assert isinstance(err.value, SmgwConnectionError)


async def test_unreachable_gateway_stays_a_connection_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("no route", request=request)

    client = _client(handler)
    with pytest.raises(SmgwConnectionError) as err:
        await client._post_binary({"action": "exportMeterValues"})
    await client.close()
    assert not isinstance(err.value, SmgwTimeoutError)


async def test_http_error_carries_the_gateway_text():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            500,
            content=b"<html><body><p>Interner Fehler &auml;</p>"
            b'<input name="tkn" value="secret"></body></html>',
        )

    client = _client(handler)
    with pytest.raises(SmgwServerError) as err:
        await client._post_binary({"action": "exportMeterValues"})
    await client.close()
    message = str(err.value)
    assert "500" in message
    assert "Interner Fehler ä" in message
    assert "secret" not in message  # the session token never reaches the log


class _Raises:
    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    async def async_download_cms(self, from_dt, to_dt, target_meter_id=None):
        raise self._exc

    async def close(self):
        pass


def _coordinator(hass: HomeAssistant, exc: Exception) -> SmgwCoordinator:
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_METER_ID: "M"})
    entry.add_to_hass(hass)
    return SmgwCoordinator(hass, entry, _Raises(exc))


async def test_timeout_maps_to_its_own_message(hass: HomeAssistant):
    coord = _coordinator(hass, SmgwTimeoutError("No answer within 120 s"))
    with pytest.raises(HomeAssistantError) as err:
        await coord.async_download_cms(datetime(2026, 1, 1), datetime(2026, 4, 1))
    assert not isinstance(err.value, ServiceValidationError)
    assert err.value.translation_key == "cms_download_timeout"
    assert err.value.translation_placeholders == {"minutes": "2"}


async def test_http_error_is_logged_with_the_gateway_text(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
):
    coord = _coordinator(
        hass, SmgwServerError("HTTP error: 500: 'Keine Daten vorhanden'")
    )
    with pytest.raises(ServiceValidationError) as err:
        await coord.async_download_cms(datetime(2026, 1, 1), datetime(2026, 4, 1))
    assert err.value.translation_key == "no_data_in_range"
    assert "Keine Daten vorhanden" in caplog.text
