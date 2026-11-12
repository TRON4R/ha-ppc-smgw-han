"""Options-flow tests for the meter export's progress screen (3.9.0).

``run_export`` is patched at the config_flow boundary, so these tests cover
the dialog only: the progress screen, how a split or late-starting export is
reported, and how failures return to the date form. The download itself is
covered by test_export_split.py.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
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

RUN = "custom_components.smgw_han.config_flow.run_export"
NOTIFY = (
    "custom_components.smgw_han.config_flow.persistent_notification.async_create"
)
LINKS = {
    "cms": "http://ha.local/api/smgw_han/export/t/Zaehlerstaende_CMS.zip?authSig=s",
    "csv": "http://ha.local/api/smgw_han/export/t/Zaehlerstaende.csv?authSig=s",
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
    entry.runtime_data = object()  # the coordinator; run_export is patched
    return entry


def _result(**extra) -> dict:
    return {
        "files": LINKS,
        "files_valid_until": "2026-11-13T10:00:00+01:00",
        "reading_count": 70080,
        "daily_summary": [{}] * 365,
        "readings": [{"timestamp": "2026-01-01T00:00:01"}],
        "parts": 1,
        "parts_without_data": 0,
        **extra,
    }


async def _open_dates(hass: HomeAssistant, entry: MockConfigEntry):
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "export"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "period": "last_month",
            "download_cms": True,
            "write_csv": True,
            "write_xlsx": False,
        },
    )
    assert result["step_id"] == "export_dates"
    return result


async def _submit_dates(hass: HomeAssistant, result):
    """Submit a year and follow the progress screen to its end."""
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "from_datetime": datetime(2026, 1, 1, 0, 0, 0),
            "to_datetime": datetime(2026, 11, 1, 0, 15, 0),
        },
    )
    if result["type"] is FlowResultType.SHOW_PROGRESS:
        await hass.async_block_till_done()
        result = await hass.config_entries.options.async_configure(
            result["flow_id"]
        )
    return result


async def test_export_shows_progress_and_reports_the_ring(hass: HomeAssistant):
    entry = _entry(hass)
    result = await _open_dates(hass, entry)
    release = asyncio.Event()
    reports: list[float] = []

    async def slow_run(*_args, progress=None, **_kwargs):
        progress(0.3)
        reports.append(0.3)
        await release.wait()
        return _result()

    with patch(RUN, side_effect=slow_run), patch(NOTIFY):
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {
                "from_datetime": datetime(2026, 1, 1),
                "to_datetime": datetime(2026, 11, 1),
            },
        )
        assert result["type"] is FlowResultType.SHOW_PROGRESS
        assert result["progress_action"] == "export"
        release.set()
        await hass.async_block_till_done()
        result = await hass.config_entries.options.async_configure(
            result["flow_id"]
        )

    assert reports == [0.3]
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "export_done"


async def test_split_export_says_why(hass: HomeAssistant):
    hass.config.language = "de"
    entry = _entry(hass)
    result = await _open_dates(hass, entry)
    with patch(RUN, AsyncMock(return_value=_result(parts=4))), patch(NOTIFY) as notify:
        result = await _submit_dates(hass, result)

    assert result["reason"] == "export_done_split"
    assert result["description_placeholders"]["parts"] == "4"
    message = notify.call_args.args[1]
    assert message.startswith("70080 Werte, 365 Tage. In 4 Teilabrufen geholt")
    assert "ZIP-Datei" in message


@pytest.mark.parametrize(
    ("parts", "reason"),
    [(1, "export_done_late"), (2, "export_done_split_late")],
)
async def test_late_start_names_the_first_day(hass: HomeAssistant, parts, reason):
    """A gateway younger than the range: the reader learns where data starts."""
    hass.config.language = "de"
    entry = _entry(hass)
    result = await _open_dates(hass, entry)
    run = AsyncMock(
        return_value=_result(
            parts=parts,
            parts_without_data=3,
            readings=[{"timestamp": "2026-11-11T12:15:01"}],
        )
    )
    with patch(RUN, run), patch(NOTIFY) as notify:
        result = await _submit_dates(hass, result)

    assert result["reason"] == reason
    assert result["description_placeholders"]["data_from"] == "11.11.2026"
    assert "die Werte beginnen am 11.11.2026" in notify.call_args.args[1]


async def test_failed_part_returns_to_the_form_naming_it(hass: HomeAssistant):
    entry = _entry(hass)
    result = await _open_dates(hass, entry)
    error = HomeAssistantError(
        translation_domain=DOMAIN,
        translation_key="cms_part_failed",
        translation_placeholders={
            "part": "3", "parts": "4", "range": "01.07.2026 00:00 – 01.10.2026 00:15",
        },
    )
    with patch(RUN, AsyncMock(side_effect=error)), patch(NOTIFY) as notify:
        result = await _submit_dates(hass, result)

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "export_dates"
    assert result["errors"] == {"base": "cms_part_failed"}
    assert result["description_placeholders"]["part"] == "3"
    assert result["description_placeholders"]["range"].startswith("01.07.2026")
    notify.assert_not_called()


async def test_timeout_has_its_own_message(hass: HomeAssistant):
    entry = _entry(hass)
    result = await _open_dates(hass, entry)
    error = HomeAssistantError(
        translation_domain=DOMAIN,
        translation_key="cms_download_timeout",
        translation_placeholders={"minutes": "2"},
    )
    with patch(RUN, AsyncMock(side_effect=error)), patch(NOTIFY):
        result = await _submit_dates(hass, result)
    assert result["errors"] == {"base": "cms_download_timeout"}
    assert result["description_placeholders"] == {"minutes": "2"}


async def test_no_data_is_a_form_error(hass: HomeAssistant):
    entry = _entry(hass)
    result = await _open_dates(hass, entry)
    error = ServiceValidationError(
        translation_domain=DOMAIN, translation_key="no_data_in_range"
    )
    with patch(RUN, AsyncMock(side_effect=error)), patch(NOTIFY):
        result = await _submit_dates(hass, result)
    assert result["errors"] == {"base": "no_data_in_range"}


@pytest.mark.parametrize(
    "error",
    [
        RuntimeError("boom"),
        HomeAssistantError(translation_domain=DOMAIN, translation_key="cms_auth_failed"),
    ],
)
async def test_other_failures_are_generic(hass: HomeAssistant, error):
    entry = _entry(hass)
    result = await _open_dates(hass, entry)
    with patch(RUN, AsyncMock(side_effect=error)), patch(NOTIFY):
        result = await _submit_dates(hass, result)
    assert result["step_id"] == "export_dates"
    assert result["errors"] == {"base": "export_failed"}


async def test_form_errors_exist_in_every_language():
    """Every key the flow can show has a text in all three files."""
    import json
    from pathlib import Path

    from custom_components.smgw_han import config_flow

    base = Path(config_flow.__file__).parent
    for path in (
        base / "strings.json",
        base / "translations" / "de.json",
        base / "translations" / "en.json",
    ):
        data = json.loads(path.read_text(encoding="utf-8"))
        errors = data["options"]["error"]
        exceptions = data["exceptions"]
        for key in config_flow._EXPORT_FORM_ERRORS:
            assert key in errors, (path.name, key)
            assert key in exceptions, (path.name, key)
        for reason in (
            "export_done", "export_done_split", "export_done_late",
            "export_done_split_late",
        ):
            assert reason in data["options"]["abort"], (path.name, reason)
        assert "export" in data["options"]["progress"]
