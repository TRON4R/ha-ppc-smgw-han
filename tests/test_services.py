"""Tests for services._resolve_coordinator and the run_export core."""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.smgw_han.const import DOMAIN
from custom_components.smgw_han.services import _resolve_coordinator, run_export

FIXTURE = Path(__file__).parent / "fixtures" / "cms_sample.xml.cms"


@pytest.fixture(autouse=True)
def _enable(enable_custom_integrations):
    yield


class FakeCoordinator:
    """Stand-in for the coordinator: serves the signed CMS bytes for the range.

    run_export now sources its data from a single CMS download, so the export
    fixture (3 import + 2 export readings, one summarized day) drives the
    assertions below.
    """

    target_meter_id = "1lgz0072999211"
    tariff_switch = (5, 0)

    async def async_download_cms(self, from_dt, to_dt):
        return FIXTURE.read_bytes(), "export.sm_data.xml.cms"


async def test_resolve_auto_detects_single_loaded_entry(hass: HomeAssistant):
    entry = MockConfigEntry(domain=DOMAIN, state=ConfigEntryState.LOADED)
    entry.add_to_hass(hass)
    sentinel = object()
    entry.runtime_data = sentinel
    assert _resolve_coordinator(hass, None) is sentinel


async def test_resolve_no_entry_raises(hass: HomeAssistant):
    with pytest.raises(ServiceValidationError):
        _resolve_coordinator(hass, None)


async def test_resolve_multiple_requires_device_id(hass: HomeAssistant):
    for _ in range(2):
        entry = MockConfigEntry(domain=DOMAIN, state=ConfigEntryState.LOADED)
        entry.add_to_hass(hass)
        entry.runtime_data = object()
    with pytest.raises(ServiceValidationError):
        _resolve_coordinator(hass, None)


async def test_resolve_unknown_device_id_raises(hass: HomeAssistant):
    with pytest.raises(ServiceValidationError):
        _resolve_coordinator(hass, "no-such-device")


async def test_run_export_writes_all_files(hass: HomeAssistant):
    from_dt = datetime(2026, 5, 15, 0, 0, 0)
    to_dt = datetime(2026, 5, 16, 0, 15, 0)
    resp = await run_export(
        hass,
        FakeCoordinator(),
        from_dt,
        to_dt,
        download_cms=True,
        do_csv=True,
        do_xlsx=True,
    )
    assert resp["reading_count"] == 5  # 3 import + 2 export from the CMS fixture
    assert resp["meter_id"] == "1lgz0072999211"
    assert set(resp["files"]) == {"cms", "csv", "xlsx"}
    assert len(resp["daily_summary"]) >= 1
    # Files are physically written under <config>/www/smgw_han_exports/<token>/.
    export_root = os.path.join(
        hass.config.config_dir, "www", "smgw_han_exports"
    )
    assert os.path.isdir(export_root)
    tokens = os.listdir(export_root)
    assert tokens
    written = os.listdir(os.path.join(export_root, tokens[0]))
    assert any(f.endswith(".csv") for f in written)
    assert any(f.endswith(".xlsx") for f in written)
    assert any(f.endswith(".cms") for f in written)


async def test_run_export_without_files_omits_files_key(hass: HomeAssistant):
    from_dt = datetime(2026, 5, 15, 0, 0, 0)
    to_dt = datetime(2026, 5, 16, 0, 15, 0)
    resp = await run_export(
        hass,
        FakeCoordinator(),
        from_dt,
        to_dt,
        download_cms=False,
        do_csv=False,
        do_xlsx=False,
    )
    assert "files" not in resp
    assert resp["reading_count"] == 5
    assert resp["readings"]
