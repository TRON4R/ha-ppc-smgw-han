"""Options-flow tests for the log export and its period presets.

``run_log_export`` is patched at the config_flow boundary, so these tests
cover the dialog only: step order, validation, the progress screen and how
the outcome is reported. The download itself is covered by
test_log_export_client.py, the files by test_log_files.py.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from freezegun import freeze_time
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import ServiceValidationError
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.smgw_han.const import (
    CONF_INSTANCE_ID,
    CONF_METER_ID,
    CONF_PASSWORD,
    CONF_TARIFF_ZONES,
    CONF_UPDATE_TIME,
    CONF_URL,
    CONF_USERNAME,
    DOMAIN,
    ZONE_NAME,
    ZONE_TIME,
)
from custom_components.smgw_han.log_export import LOG_ALL_FROM, log_period_range

RUN = "custom_components.smgw_han.config_flow.run_log_export"
NOTIFY = (
    "custom_components.smgw_han.config_flow.persistent_notification.async_create"
)
LINKS = {
    "cms": "http://ha.local/local/smgw_han_exports/t/Logdaten.zip",
    "csv": "http://ha.local/local/smgw_han_exports/t/Logdaten.csv",
}


@pytest.fixture(autouse=True)
def _enable(enable_custom_integrations):
    yield


def _entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="M:user",
        version=2,
        data={
            CONF_URL: "https://192.168.100.100/cgi-bin/hanservice.cgi",
            CONF_USERNAME: "user",
            CONF_PASSWORD: "pw",
            CONF_METER_ID: "M",
            CONF_INSTANCE_ID: 1,
            CONF_TARIFF_ZONES: [
                {ZONE_TIME: "00:00", ZONE_NAME: "Go"},
                {ZONE_TIME: "05:00", ZONE_NAME: "Standard"},
            ],
            CONF_UPDATE_TIME: "00:15:00",
        },
    )
    entry.add_to_hass(hass)
    entry.runtime_data = object()  # the coordinator; run_log_export is patched
    return entry


async def _open_dates(hass: HomeAssistant, entry: MockConfigEntry, **outputs):
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert "log_export" in result["menu_options"]
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "log_export"}
    )
    assert result["step_id"] == "log_export"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "period": "last_month",
            "download_cms": outputs.get("download_cms", True),
            "write_csv": outputs.get("write_csv", True),
            "write_xlsx": outputs.get("write_xlsx", False),
        },
    )
    assert result["step_id"] == "log_export_dates"
    return result


async def _submit_dates(hass: HomeAssistant, result):
    """Submit the range and follow the progress screen to its end."""
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "from_datetime": datetime(2026, 3, 1, 0, 0, 0),
            "to_datetime": datetime(2026, 4, 1, 0, 0, 0),
        },
    )
    if result["type"] is FlowResultType.SHOW_PROGRESS:
        await hass.async_block_till_done()
        result = await hass.config_entries.options.async_configure(
            result["flow_id"]
        )
    return result


async def test_log_export_single_part(hass: HomeAssistant):
    entry = _entry(hass)
    result = await _open_dates(hass, entry)
    run = AsyncMock(
        return_value={"files": LINKS, "entry_count": 230, "parts": 1, "complete": True}
    )
    with patch(RUN, run), patch(NOTIFY) as notify:
        result = await _submit_dates(hass, result)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "log_export_done"
    assert result["description_placeholders"]["count"] == "230"
    assert 'target="_blank">CMS</a>' in result["description_placeholders"]["links"]
    args, kwargs = run.call_args
    assert args[2:] == (datetime(2026, 3, 1), datetime(2026, 4, 1))
    assert kwargs == {"download_cms": True, "do_csv": True, "do_xlsx": False}
    message = notify.call_args.args[1]
    assert message.startswith("230 Logeinträge.")
    assert "Teilabrufen" not in message


async def test_log_export_split_says_why(hass: HomeAssistant):
    entry = _entry(hass)
    result = await _open_dates(hass, entry)
    run = AsyncMock(
        return_value={"files": LINKS, "entry_count": 1260, "parts": 3, "complete": True}
    )
    with patch(RUN, run), patch(NOTIFY) as notify:
        result = await _submit_dates(hass, result)

    assert result["reason"] == "log_export_done_split"
    assert result["description_placeholders"]["parts"] == "3"
    assert "in 3 Teilabrufen geholt" in notify.call_args.args[1]


async def test_log_export_incomplete_is_flagged(hass: HomeAssistant):
    entry = _entry(hass)
    result = await _open_dates(hass, entry)
    run = AsyncMock(
        return_value={"files": LINKS, "entry_count": 90, "parts": 2, "complete": False}
    )
    with patch(RUN, run), patch(NOTIFY) as notify:
        result = await _submit_dates(hass, result)

    assert result["reason"] == "log_export_incomplete"
    assert "Vollständigkeitsprüfung" in notify.call_args.args[1]


async def test_log_export_shows_progress_while_downloading(hass: HomeAssistant):
    entry = _entry(hass)
    result = await _open_dates(hass, entry)
    release = asyncio.Event()

    async def slow_run(*_args, **_kwargs):
        await release.wait()
        return {"files": LINKS, "entry_count": 5, "parts": 1, "complete": True}

    with patch(RUN, side_effect=slow_run), patch(NOTIFY):
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {
                "from_datetime": datetime(2026, 3, 1),
                "to_datetime": datetime(2026, 4, 1),
            },
        )
        assert result["type"] is FlowResultType.SHOW_PROGRESS
        assert result["progress_action"] == "log_export"
        release.set()
        await hass.async_block_till_done()
        result = await hass.config_entries.options.async_configure(
            result["flow_id"]
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "log_export_done"


async def test_log_export_error_returns_to_dates(hass: HomeAssistant):
    entry = _entry(hass)
    result = await _open_dates(hass, entry)
    run = AsyncMock(
        side_effect=ServiceValidationError(
            translation_domain=DOMAIN, translation_key="log_no_entries"
        )
    )
    with patch(RUN, run), patch(NOTIFY) as notify:
        result = await _submit_dates(hass, result)

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "log_export_dates"
    assert result["errors"] == {"base": "log_no_entries"}
    notify.assert_not_called()


async def test_log_export_unexpected_error_is_generic(hass: HomeAssistant):
    entry = _entry(hass)
    result = await _open_dates(hass, entry)
    with patch(RUN, AsyncMock(side_effect=RuntimeError("boom"))), patch(NOTIFY):
        result = await _submit_dates(hass, result)
    assert result["step_id"] == "log_export_dates"
    assert result["errors"] == {"base": "log_export_failed"}


async def test_log_export_needs_an_output(hass: HomeAssistant):
    entry = _entry(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "log_export"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "period": "all",
            "download_cms": False,
            "write_csv": False,
            "write_xlsx": False,
        },
    )
    assert result["step_id"] == "log_export"
    assert result["errors"] == {"base": "no_outputs"}


async def test_log_export_rejects_future_end(hass: HomeAssistant):
    entry = _entry(hass)
    result = await _open_dates(hass, entry)
    with patch(RUN) as run:
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {
                "from_datetime": datetime(2026, 1, 1),
                "to_datetime": datetime(2099, 1, 1),
            },
        )
    assert result["errors"] == {"base": "to_in_future"}
    run.assert_not_called()


async def test_log_export_accepts_a_later_time_today(hass: HomeAssistant):
    # Deliberate (external review 2026-10-02, not adopted): only a future DAY
    # is refused. Tested on a real gateway, an end later today just returns
    # everything up to now, plus the export's own login entry.
    entry = _entry(hass)
    run = AsyncMock(
        return_value={"files": LINKS, "entry_count": 5, "parts": 1, "complete": True}
    )
    with freeze_time("2026-10-02 18:00:00"), patch(RUN, run), patch(NOTIFY):
        result = await _open_dates(hass, entry)
        now = dt_util.now().replace(tzinfo=None, microsecond=0)
        later_today = now + timedelta(hours=1)
        assert later_today.date() == now.date()
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {
                "from_datetime": now - timedelta(hours=2),
                "to_datetime": later_today,
            },
        )
        if result["type"] is FlowResultType.SHOW_PROGRESS:
            await hass.async_block_till_done()
            result = await hass.config_entries.options.async_configure(
                result["flow_id"]
            )

    assert result["type"] is FlowResultType.ABORT
    assert run.call_args.args[3] == later_today


def test_log_period_range():
    now = datetime(2026, 10, 1, 18, 5, 33)
    today = datetime(2026, 10, 1)
    assert log_period_range("yesterday", now) == (datetime(2026, 9, 30), today)
    assert log_period_range("last_7_days", now) == (datetime(2026, 9, 24), now)
    assert log_period_range("current_month", now) == (today, now)
    assert log_period_range("last_month", now) == (datetime(2026, 9, 1), today)
    # "all" starts at a date the gateway is known to accept and runs to now.
    assert log_period_range("all", now) == (LOG_ALL_FROM, now)
    with pytest.raises(ServiceValidationError):
        log_period_range("nonsense", now)
