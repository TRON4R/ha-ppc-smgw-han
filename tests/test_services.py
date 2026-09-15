"""Tests for services._resolve_coordinator and the run_export core."""

from __future__ import annotations

import os
from datetime import datetime, time
from pathlib import Path
from unittest.mock import patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.smgw_han.const import DOMAIN
from custom_components.smgw_han.services import (
    DEVICE_NAME_MAX_CHARS,
    _cms_name_with_device,
    _device_suffix,
    _resolve_coordinator,
    _sanitize,
    async_setup_services,
    build_links_markdown,
    run_export,
)

NOTIFY = "custom_components.smgw_han.services.persistent_notification.async_create"

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
    device_name = None  # unnamed: filenames stay as they were pre-3.5.2
    tariff_zones = [(time(0, 0), "Go"), (time(5, 0), "Standard")]

    async def async_download_cms(self, from_dt, to_dt):
        return FIXTURE.read_bytes(), "export.sm_data.xml.cms"

    def zone_resolver(self):
        """Day -> zones. One layout for the whole range (no scheduled change)."""
        return lambda _day: self.tariff_zones

    def zone_periods(self, first_day, last_day):
        return [
            {
                "valid_from": first_day.isoformat(),
                "zones": [
                    {"time": t.strftime("%H:%M"), "name": name}
                    for t, name in self.tariff_zones
                ],
            }
        ]


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


async def test_build_links_markdown():
    # Explicit target="_blank" anchors (not markdown links) so a same-origin
    # export URL is not swallowed by HA's SPA router on left-click.
    md = build_links_markdown(
        {
            "cms": "http://h/a.cms",
            "csv": "http://h/a.csv",
            "xlsx": "http://h/a.xlsx",
        }
    )
    assert md == (
        '<a href="http://h/a.cms" target="_blank">CMS</a> · '
        '<a href="http://h/a.csv" target="_blank">CSV</a> · '
        '<a href="http://h/a.xlsx" target="_blank">Excel</a>'
    )
    assert build_links_markdown({}) == ""


def _loaded_entry(hass) -> None:
    entry = MockConfigEntry(domain=DOMAIN, state=ConfigEntryState.LOADED)
    entry.add_to_hass(hass)
    entry.runtime_data = FakeCoordinator()


async def test_service_posts_notification_with_links(hass: HomeAssistant):
    _loaded_entry(hass)
    async_setup_services(hass)
    with patch(NOTIFY) as mock_notify:
        await hass.services.async_call(
            DOMAIN,
            "export_period",
            {
                "period": "last_month",
                "download_cms": True,
                "write_csv": False,
                "write_xlsx": False,
            },
            blocking=True,
            return_response=True,
        )
    assert mock_notify.called
    message = mock_notify.call_args.args[1]
    assert 'target="_blank">CMS</a>' in message


async def test_service_no_notification_without_files(hass: HomeAssistant):
    _loaded_entry(hass)
    async_setup_services(hass)
    with patch(NOTIFY) as mock_notify:
        await hass.services.async_call(
            DOMAIN,
            "export_period",
            {
                "period": "last_month",
                "download_cms": False,
                "write_csv": False,
                "write_xlsx": False,
            },
            blocking=True,
            return_response=True,
        )
    assert not mock_notify.called


async def test_run_export_no_data_raises(hass: HomeAssistant):
    class EmptyCoordinator(FakeCoordinator):
        async def async_download_cms(self, from_dt, to_dt):
            return b"", None  # range entirely before the meter start

    with pytest.raises(ServiceValidationError):
        await run_export(
            hass,
            EmptyCoordinator(),
            datetime(2026, 5, 15, 0, 0, 0),
            datetime(2026, 5, 16, 0, 15, 0),
            download_cms=True,
            do_csv=False,
            do_xlsx=False,
        )


# ----------------------------------------------------------------------
# Device name in the export filenames
# ----------------------------------------------------------------------


def test_sanitize_transliterates_umlauts():
    """"Zähler Süd" must not become the unreadable "Z_hler_S_d"."""
    assert _sanitize("Zähler Süd") == "Zaehler_Sued"
    assert _sanitize("Großer Öltank") == "Grosser_Oeltank"
    assert _sanitize("Modul 3 SMGW") == "Modul_3_SMGW"


@pytest.mark.parametrize(
    ("device_name", "expected"),
    [
        (None, ""),
        ("", ""),
        ("   ", ""),
        ("Modul 3 SMGW", "_Modul_3_SMGW"),
        ("Zähler Süd", "_Zaehler_Sued"),
        # Truncated to the cap, and never left ending on a separator.
        ("A" * 40, "_" + "A" * DEVICE_NAME_MAX_CHARS),
        ("Sehr langer Geraetename ohne Ende", "_Sehr_langer_Geraetename"),
    ],
)
def test_device_suffix(device_name, expected):
    assert _device_suffix(device_name) == expected


def test_cms_name_keeps_the_gateway_naming():
    """The suffix goes before the extension, not after it."""
    assert _cms_name_with_device(
        "1lgz0072999211.sm_data.xml.cms", "_Modul_3_SMGW"
    ) == "1lgz0072999211_Modul_3_SMGW.sm_data.xml.cms"
    # No device name, or no gateway name: nothing changes.
    assert _cms_name_with_device("1lgz00.sm_data.xml.cms", "") == (
        "1lgz00.sm_data.xml.cms"
    )
    assert _cms_name_with_device(None, "_X") is None


async def test_export_filenames_carry_the_device_name(hass: HomeAssistant):
    """Two evaluations of one meter share the meter id, so only the device
    name tells their exports apart — in all three files, the gateway-named
    .cms included.

    Asserted on the returned download URLs rather than on the export
    directory: several tests in this module write into the same config dir,
    so picking a token folder off the filesystem picks an arbitrary one.
    """

    class NamedCoordinator(FakeCoordinator):
        device_name = "Modul 3 SMGW"

    resp = await run_export(
        hass,
        NamedCoordinator(),
        datetime(2026, 5, 15, 0, 0, 0),
        datetime(2026, 5, 16, 0, 15, 0),
        download_cms=True,
        do_csv=True,
        do_xlsx=True,
    )

    names = {
        kind: url.rsplit("/", 1)[-1] for kind, url in resp["files"].items()
    }
    assert set(names) == {"cms", "csv", "xlsx"}
    assert all("Modul_3_SMGW" in name for name in names.values()), names
    for kind in ("csv", "xlsx"):
        # The period still leads, so exports sort chronologically.
        assert names[kind].startswith("Zaehlerstaende_2026-05-15_000000")
        assert names[kind].endswith(f"_1lgz0072999211_Modul_3_SMGW.{kind}")
    # The gateway names the .cms itself; the suffix goes before the extension
    # so that naming stays recognisable.
    assert names["cms"] == "export_Modul_3_SMGW.sm_data.xml.cms"


async def test_export_filenames_unchanged_without_a_device_name(
    hass: HomeAssistant,
):
    """A single-device installation keeps exactly the filenames it had."""
    resp = await run_export(
        hass,
        FakeCoordinator(),
        datetime(2026, 5, 15, 0, 0, 0),
        datetime(2026, 5, 16, 0, 15, 0),
        download_cms=True,
        do_csv=True,
        do_xlsx=True,
    )

    names = {
        kind: url.rsplit("/", 1)[-1] for kind, url in resp["files"].items()
    }
    assert names["csv"] == (
        "Zaehlerstaende_2026-05-15_000000_bis_2026-05-16_001500"
        "_1lgz0072999211.csv"
    )
    assert names["cms"] == "export.sm_data.xml.cms"
