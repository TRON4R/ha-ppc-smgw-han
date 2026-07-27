"""Config-flow tests: meter selection, reauth, and the meter-swap guard.

The SMGW HTTP layer is patched at the ``async_validate_and_get_device_info``
boundary so no gateway is contacted; ``async_setup_entry`` is patched on the
create path so creating an entry does not kick off a real coordinator setup.
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.smgw_han import async_migrate_entry
from custom_components.smgw_han.config_flow import (
    ZoneDefinitionError,
    _parse_tariff_zones,
)
from custom_components.smgw_han.const import (
    CONF_INSTANCE_ID,
    CONF_METER_ID,
    CONF_PASSWORD,
    CONF_TARIFF_SWITCH_HOUR,
    CONF_TARIFF_SWITCH_MINUTE,
    CONF_TARIFF_ZONES,
    CONF_UPDATE_TIME,
    CONF_URL,
    CONF_USERNAME,
    DOMAIN,
    ZONE_NAME,
    ZONE_TIME,
)
from custom_components.smgw_han.smgw_client import SmgwAuthError, SmgwDeviceInfo

VALIDATE = (
    "custom_components.smgw_han.config_flow.SmgwClient"
    ".async_validate_and_get_device_info"
)
CLOSE = "custom_components.smgw_han.config_flow.SmgwClient.close"
SETUP_ENTRY = "custom_components.smgw_han.async_setup_entry"

URL = "https://192.168.100.100/cgi-bin/hanservice.cgi"
GO_ZONE_ENTRIES = ["00:00 Go", "05:00 Standard"]
GO_ZONES_STORED = [
    {ZONE_TIME: "00:00", ZONE_NAME: "Go"},
    {ZONE_TIME: "05:00", ZONE_NAME: "Standard"},
]
USER_INPUT = {
    CONF_URL: URL,
    CONF_USERNAME: "user",
    CONF_PASSWORD: "pw",
    CONF_TARIFF_ZONES: GO_ZONE_ENTRIES,
    CONF_UPDATE_TIME: "00:15:00",
}


@pytest.fixture(autouse=True)
def _enable(enable_custom_integrations):
    yield


def _info(meter_id: str, available: list[str]) -> SmgwDeviceInfo:
    return SmgwDeviceInfo(
        meter_id=meter_id,
        firmware_version="00861-34788",
        available_meter_ids=available,
    )


def _entry(meter_id: str, instance_id: int = 1, **extra) -> MockConfigEntry:
    data = {
        CONF_URL: URL,
        CONF_USERNAME: "user",
        CONF_PASSWORD: "pw",
        CONF_METER_ID: meter_id,
        CONF_INSTANCE_ID: instance_id,
        CONF_TARIFF_ZONES: [dict(z) for z in GO_ZONES_STORED],
        CONF_UPDATE_TIME: "00:15:00",
        **extra,
    }
    return MockConfigEntry(
        domain=DOMAIN, unique_id=f"{meter_id}:user", data=data, version=2
    )


async def _start_user_flow(
    hass: HomeAssistant, template: str = "tariff_custom"
):
    """Start setup and pick a tariff template, returning the credentials form.

    Since v3.1.0 the flow opens with a template menu; the chosen template only
    prefills the (editable) zone field of the following form.
    """
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] == FlowResultType.MENU
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": template}
    )
    assert result["step_id"] == "credentials"
    return result


async def test_user_flow_single_meter_creates_entry(hass: HomeAssistant):
    with (
        patch(VALIDATE, return_value=_info("1lgz0072999211", ["1lgz0072999211"])),
        patch(CLOSE, return_value=None),
        patch(SETUP_ENTRY, return_value=True),
    ):
        result = await _start_user_flow(hass)
        assert result["type"] == FlowResultType.FORM
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], dict(USER_INPUT)
        )
        await hass.async_block_till_done()

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_METER_ID] == "1lgz0072999211"
    assert result["data"][CONF_INSTANCE_ID] == 1
    # Chip entries are stored in their parsed JSON shape.
    assert result["data"][CONF_TARIFF_ZONES] == GO_ZONES_STORED


async def test_user_flow_multi_meter_goes_through_select_step(
    hass: HomeAssistant,
):
    with (
        patch(
            VALIDATE,
            return_value=_info(
                "1lgz0072999211", ["1lgz0072999211", "1lgz0088888888"]
            ),
        ),
        patch(CLOSE, return_value=None),
        patch(SETUP_ENTRY, return_value=True),
    ):
        result = await _start_user_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], dict(USER_INPUT)
        )
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "select_meter"
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_METER_ID: "1lgz0088888888"}
        )
        await hass.async_block_till_done()

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_METER_ID] == "1lgz0088888888"


async def test_user_flow_duplicate_aborts(hass: HomeAssistant):
    _entry("1lgz0072999211").add_to_hass(hass)
    with (
        patch(VALIDATE, return_value=_info("1lgz0072999211", ["1lgz0072999211"])),
        patch(CLOSE, return_value=None),
    ):
        result = await _start_user_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], dict(USER_INPUT)
        )

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_user_flow_invalid_auth(hass: HomeAssistant):
    with (
        patch(VALIDATE, side_effect=SmgwAuthError("bad creds")),
        patch(CLOSE, return_value=None),
    ):
        result = await _start_user_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], dict(USER_INPUT)
        )

    assert result["type"] == FlowResultType.FORM
    assert result["errors"]["base"] == "invalid_auth"


async def _open_settings(hass: HomeAssistant, entry: MockConfigEntry):
    """Open the options flow and navigate to the 'settings' step."""
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == FlowResultType.MENU
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "settings"}
    )
    assert result["step_id"] == "settings"
    return result


async def test_meter_swap_keeps_identity(hass: HomeAssistant):
    """⭐ A single-meter hardware swap adopts the new meter id AND re-syncs the
    config-entry unique_id (since v2.3.0), while instance_id / entry_id stay
    put — history/entities ride on instance_id, not on the unique_id, so the
    unique_id can safely follow the new meter id."""
    entry = _entry("OLDMETER", instance_id=1)
    entry.add_to_hass(hass)
    original_entry_id = entry.entry_id

    result = await _open_settings(hass, entry)
    with (
        patch(VALIDATE, return_value=_info("NEWMETER", ["NEWMETER"])),
        patch(CLOSE, return_value=None),
        patch.object(
            hass.config_entries, "async_reload", new_callable=AsyncMock
        ),
    ):
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], dict(USER_INPUT)
        )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert entry.data[CONF_METER_ID] == "NEWMETER"  # swap adopted
    assert entry.data[CONF_INSTANCE_ID] == 1  # unchanged -> history preserved
    assert entry.unique_id == "NEWMETER:user"  # now re-synced to the new meter
    assert entry.entry_id == original_entry_id  # same device/entities


async def test_meter_swap_refused_on_multimeter_setup(hass: HomeAssistant):
    """With a sibling entry on the same SMGW, the 'only one meter left' case
    must NOT auto-repoint the entry — it surfaces an error instead."""
    entry = _entry("OLDMETER", instance_id=1)
    entry.add_to_hass(hass)
    _entry("SIBLING", instance_id=2).add_to_hass(hass)

    result = await _open_settings(hass, entry)
    with (
        patch(VALIDATE, return_value=_info("NEWMETER", ["NEWMETER"])),
        patch(CLOSE, return_value=None),
    ):
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], dict(USER_INPUT)
        )

    assert result["type"] == FlowResultType.FORM
    assert result["errors"]["base"] == "configured_meter_missing"
    assert entry.data[CONF_METER_ID] == "OLDMETER"  # left untouched


def _other_entry(meter_id: str, username: str, instance_id: int) -> MockConfigEntry:
    """A second entry with an explicit username (so its unique_id can differ)."""
    return MockConfigEntry(
        domain=DOMAIN,
        unique_id=f"{meter_id}:{username}",
        data={
            CONF_URL: URL,
            CONF_USERNAME: username,
            CONF_PASSWORD: "pw",
            CONF_METER_ID: meter_id,
            CONF_INSTANCE_ID: instance_id,
        },
    )


async def test_settings_username_change_syncs_unique_id(hass: HomeAssistant):
    """Changing the username in settings re-syncs the unique_id (meter:user)
    while instance_id stays put."""
    entry = _entry("M", instance_id=1)  # unique_id "M:user"
    entry.add_to_hass(hass)

    result = await _open_settings(hass, entry)
    with (
        patch(VALIDATE, return_value=_info("M", ["M"])),  # meter unchanged
        patch(CLOSE, return_value=None),
        patch.object(
            hass.config_entries, "async_reload", new_callable=AsyncMock
        ),
    ):
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {**USER_INPUT, CONF_USERNAME: "user2"}
        )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert entry.data[CONF_USERNAME] == "user2"
    assert entry.unique_id == "M:user2"  # re-synced
    assert entry.data[CONF_INSTANCE_ID] == 1  # unchanged


async def test_settings_unique_id_collision_refused(hass: HomeAssistant):
    """If the new (meter, username) identity already belongs to another entry,
    the settings step refuses with duplicate_login and changes nothing."""
    entry_a = _entry("M", instance_id=1)  # "M:user"
    entry_a.add_to_hass(hass)
    _other_entry("M", "user2", instance_id=2).add_to_hass(hass)  # owns "M:user2"

    result = await _open_settings(hass, entry_a)
    with (
        patch(VALIDATE, return_value=_info("M", ["M"])),
        patch(CLOSE, return_value=None),
        patch.object(
            hass.config_entries, "async_reload", new_callable=AsyncMock
        ),
    ):
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {**USER_INPUT, CONF_USERNAME: "user2"}
        )

    assert result["type"] == FlowResultType.FORM
    assert result["errors"]["base"] == "duplicate_login"
    assert entry_a.unique_id == "M:user"  # unchanged
    assert entry_a.data[CONF_USERNAME] == "user"  # nothing saved


async def test_reauth_syncs_unique_id(hass: HomeAssistant):
    """Reauth with a new username re-syncs the unique_id."""
    entry = _entry("M", instance_id=1)  # "M:user"
    entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={
            "source": config_entries.SOURCE_REAUTH,
            "entry_id": entry.entry_id,
        },
        data=entry.data,
    )
    assert result["step_id"] == "reauth_confirm"
    with (
        patch(VALIDATE, return_value=_info("M", ["M"])),
        patch(CLOSE, return_value=None),
        patch.object(
            hass.config_entries, "async_reload", new_callable=AsyncMock
        ),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_USERNAME: "user2", CONF_PASSWORD: "newpw"},
        )
        await hass.async_block_till_done()

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.unique_id == "M:user2"
    assert entry.data[CONF_USERNAME] == "user2"


async def test_reauth_collision_refused(hass: HomeAssistant):
    """Reauth that would collide with another entry's identity is refused."""
    entry_a = _entry("M", instance_id=1)  # "M:user"
    entry_a.add_to_hass(hass)
    _other_entry("M", "user2", instance_id=2).add_to_hass(hass)  # owns "M:user2"

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={
            "source": config_entries.SOURCE_REAUTH,
            "entry_id": entry_a.entry_id,
        },
        data=entry_a.data,
    )
    with (
        patch(VALIDATE, return_value=_info("M", ["M"])),
        patch(CLOSE, return_value=None),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_USERNAME: "user2", CONF_PASSWORD: "newpw"},
        )

    assert result["type"] == FlowResultType.FORM
    assert result["errors"]["base"] == "duplicate_login"
    assert entry_a.unique_id == "M:user"  # unchanged


async def _open_export_dates(hass: HomeAssistant, entry: MockConfigEntry):
    """Drive the options export flow to the date-range step."""
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "export"}
    )
    assert result["step_id"] == "export"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "period": "last_month",
            "download_cms": True,
            "write_csv": False,
            "write_xlsx": False,
        },
    )
    assert result["step_id"] == "export_dates"
    return result


async def test_options_export_rejects_future_day(hass: HomeAssistant):
    # The options flow now runs the same _validate_range as the service path.
    entry = _entry("M", instance_id=1)
    entry.add_to_hass(hass)
    result = await _open_export_dates(hass, entry)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "from_datetime": datetime(2026, 1, 1, 0, 0, 0),
            "to_datetime": datetime(2099, 1, 1, 0, 0, 0),
        },
    )
    assert result["type"] == FlowResultType.FORM
    assert result["errors"]["base"] == "to_in_future"


async def test_options_export_rejects_too_old(hass: HomeAssistant):
    entry = _entry("M", instance_id=1)
    entry.add_to_hass(hass)
    result = await _open_export_dates(hass, entry)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "from_datetime": datetime(2000, 1, 1, 0, 0, 0),
            "to_datetime": datetime(2026, 5, 1, 0, 0, 0),
        },
    )
    assert result["type"] == FlowResultType.FORM
    assert result["errors"]["base"] == "from_too_old"


# ---------------------------------------------------------------------------
# Tariff-zone parsing / validation
# ---------------------------------------------------------------------------


def test_parse_tariff_zones_go():
    assert _parse_tariff_zones(["00:00 Go", "05:00 Standard"]) == [
        {ZONE_TIME: "00:00", ZONE_NAME: "Go"},
        {ZONE_TIME: "05:00", ZONE_NAME: "Standard"},
    ]


def test_parse_tariff_zones_heat_and_normalization():
    # Single-digit hours and surrounding whitespace are normalized; zone
    # names may contain blanks and may repeat (grouped zones).
    parsed = _parse_tariff_zones(
        [
            "0:00 Standard",
            "  02:00  Niedrig ",
            "6:00 Standard",
            "12:00 Niedrig",
            "16:00 Standard",
            "18:00 Hoch Tarif",
            "21:00 Standard",
        ]
    )
    assert parsed[0] == {ZONE_TIME: "00:00", ZONE_NAME: "Standard"}
    assert parsed[1] == {ZONE_TIME: "02:00", ZONE_NAME: "Niedrig"}
    assert parsed[5] == {ZONE_TIME: "18:00", ZONE_NAME: "Hoch Tarif"}


@pytest.mark.parametrize(
    ("entries", "error_key"),
    [
        ([], "zone_too_few"),
        (["00:00 Nur eine Zone"], "zone_too_few"),
        (["00:00 A", "5 Uhr B"], "invalid_zone_entry"),
        (["00:00 A", "25:00 B"], "invalid_zone_entry"),
        (["00:00 A", "05:00"], "invalid_zone_entry"),
        (["01:00 A", "05:00 B"], "zone_first_not_midnight"),
        (["00:00 A", "06:00 B", "05:00 C"], "zone_times_not_ascending"),
        (["00:00 A", "05:00 B", "05:00 C"], "zone_times_not_ascending"),
        (["00:00 A", "05:10 B"], "zone_minute_grid"),
    ],
)
def test_parse_tariff_zones_rejects(entries, error_key):
    with pytest.raises(ZoneDefinitionError) as ei:
        _parse_tariff_zones(entries)
    assert ei.value.error_key == error_key


async def test_user_flow_invalid_zones_shows_field_error(hass: HomeAssistant):
    # A malformed zone list fails locally — the SMGW is never contacted.
    with patch(VALIDATE) as mock_validate:
        result = await _start_user_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {**USER_INPUT, CONF_TARIFF_ZONES: ["05:00 Standard"]},
        )

    assert result["type"] == FlowResultType.FORM
    assert result["errors"][CONF_TARIFF_ZONES] == "zone_too_few"
    assert not mock_validate.called


async def test_settings_invalid_zones_shows_field_error(hass: HomeAssistant):
    entry = _entry("M", instance_id=1)
    entry.add_to_hass(hass)

    result = await _open_settings(hass, entry)
    with patch(VALIDATE) as mock_validate:
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {**USER_INPUT, CONF_TARIFF_ZONES: ["00:00 A", "05:10 B"]},
        )

    assert result["type"] == FlowResultType.FORM
    assert result["errors"][CONF_TARIFF_ZONES] == "zone_minute_grid"
    assert not mock_validate.called
    assert entry.data[CONF_TARIFF_ZONES] == GO_ZONES_STORED  # nothing saved


async def test_settings_stores_parsed_zones(hass: HomeAssistant):
    entry = _entry("M", instance_id=1)
    entry.add_to_hass(hass)

    heat_entries = [
        "00:00 Standard",
        "02:00 Niedrig",
        "06:00 Standard",
        "12:00 Niedrig",
        "16:00 Standard",
        "18:00 Hoch",
        "21:00 Standard",
    ]
    result = await _open_settings(hass, entry)
    with (
        patch(VALIDATE, return_value=_info("M", ["M"])),
        patch(CLOSE, return_value=None),
        patch.object(
            hass.config_entries, "async_reload", new_callable=AsyncMock
        ),
    ):
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {**USER_INPUT, CONF_TARIFF_ZONES: heat_entries},
        )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert entry.data[CONF_TARIFF_ZONES] == [
        {ZONE_TIME: "00:00", ZONE_NAME: "Standard"},
        {ZONE_TIME: "02:00", ZONE_NAME: "Niedrig"},
        {ZONE_TIME: "06:00", ZONE_NAME: "Standard"},
        {ZONE_TIME: "12:00", ZONE_NAME: "Niedrig"},
        {ZONE_TIME: "16:00", ZONE_NAME: "Standard"},
        {ZONE_TIME: "18:00", ZONE_NAME: "Hoch"},
        {ZONE_TIME: "21:00", ZONE_NAME: "Standard"},
    ]


# ---------------------------------------------------------------------------
# Config-entry migration v1 -> v2
# ---------------------------------------------------------------------------


async def test_migrate_entry_v1_builds_zones_from_switch_time(
    hass: HomeAssistant,
):
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="M:user",
        version=1,
        data={
            CONF_URL: URL,
            CONF_USERNAME: "user",
            CONF_PASSWORD: "pw",
            CONF_METER_ID: "M",
            CONF_INSTANCE_ID: 1,
            CONF_TARIFF_SWITCH_HOUR: 7,
            CONF_TARIFF_SWITCH_MINUTE: 30,
            CONF_UPDATE_TIME: "00:15:00",
        },
    )
    entry.add_to_hass(hass)

    assert await async_migrate_entry(hass, entry) is True

    assert entry.version == 2
    # Fallback names reproduce the pre-3.0 entity display names.
    assert entry.data[CONF_TARIFF_ZONES] == [
        {ZONE_TIME: "00:00", ZONE_NAME: "Zeitfenster 1"},
        {ZONE_TIME: "07:30", ZONE_NAME: "Zeitfenster 2"},
    ]
    assert CONF_TARIFF_SWITCH_HOUR not in entry.data
    assert CONF_TARIFF_SWITCH_MINUTE not in entry.data
    # Everything else is untouched.
    assert entry.data[CONF_METER_ID] == "M"
    assert entry.data[CONF_INSTANCE_ID] == 1


async def test_migrate_entry_v1_without_switch_time_uses_default(
    hass: HomeAssistant,
):
    # Very old entries may lack the switch-time keys entirely -> default 05:00.
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="M:user",
        version=1,
        data={
            CONF_URL: URL,
            CONF_USERNAME: "user",
            CONF_PASSWORD: "pw",
            CONF_METER_ID: "M",
        },
    )
    entry.add_to_hass(hass)

    assert await async_migrate_entry(hass, entry) is True
    assert entry.version == 2
    assert entry.data[CONF_TARIFF_ZONES] == [
        {ZONE_TIME: "00:00", ZONE_NAME: "Zeitfenster 1"},
        {ZONE_TIME: "05:00", ZONE_NAME: "Zeitfenster 2"},
    ]


async def test_migrate_entry_v1_midnight_switch_documented_degenerate(
    hass: HomeAssistant,
):
    # Legacy switch time 00:00 (always meant "slot 1 = 0") migrates to two
    # zones with the same 00:00 boundary. That is deliberately degenerate:
    # the calculation reproduces the v2 behaviour (covered by
    # test_degenerate_midnight_switch_computes_like_v2); only a later manual
    # edit in the settings form forces the user to enter a sane definition.
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="M:user",
        version=1,
        data={
            CONF_URL: URL,
            CONF_USERNAME: "user",
            CONF_PASSWORD: "pw",
            CONF_METER_ID: "M",
            CONF_TARIFF_SWITCH_HOUR: 0,
            CONF_TARIFF_SWITCH_MINUTE: 0,
        },
    )
    entry.add_to_hass(hass)

    assert await async_migrate_entry(hass, entry) is True
    assert entry.version == 2
    assert entry.data[CONF_TARIFF_ZONES] == [
        {ZONE_TIME: "00:00", ZONE_NAME: "Zeitfenster 1"},
        {ZONE_TIME: "00:00", ZONE_NAME: "Zeitfenster 2"},
    ]


async def test_migrate_entry_v2_is_noop(hass: HomeAssistant):
    entry = _entry("M", instance_id=1)
    entry.add_to_hass(hass)
    before = dict(entry.data)

    assert await async_migrate_entry(hass, entry) is True
    assert entry.version == 2
    assert dict(entry.data) == before


# --------------------------------------------------------------------------
# Tariff templates (v3.1.0)
#
# A template only PREFILLS the editable zone field — it is never stored
# without the user submitting the form. These tests pin both halves: the
# prefill reaches the form, and an edited value still wins.
# --------------------------------------------------------------------------

HEAT_ZONES_STORED = [
    {ZONE_TIME: "00:00", ZONE_NAME: "Standard"},
    {ZONE_TIME: "02:00", ZONE_NAME: "Niedrig"},
    {ZONE_TIME: "06:00", ZONE_NAME: "Standard"},
    {ZONE_TIME: "12:00", ZONE_NAME: "Niedrig"},
    {ZONE_TIME: "16:00", ZONE_NAME: "Standard"},
    {ZONE_TIME: "18:00", ZONE_NAME: "Hoch"},
    {ZONE_TIME: "21:00", ZONE_NAME: "Standard"},
]


def _zone_default(result) -> list[str]:
    """Read the tariff-zone default out of a shown form's schema."""
    for key in result["data_schema"].schema:
        if key == CONF_TARIFF_ZONES:
            return key.default()
    raise AssertionError("tariff zones not in schema")


@pytest.mark.parametrize(
    ("template", "expected"),
    [
        ("tariff_go", ["00:00 Go", "05:00 Standard"]),
        (
            "tariff_heat",
            [
                "00:00 Standard",
                "02:00 Niedrig",
                "06:00 Standard",
                "12:00 Niedrig",
                "16:00 Standard",
                "18:00 Hoch",
                "21:00 Standard",
            ],
        ),
        ("tariff_custom", ["00:00 Zeitfenster 1", "05:00 Zeitfenster 2"]),
    ],
    ids=["go", "heat", "custom"],
)
async def test_setup_template_prefills_zone_field(
    hass: HomeAssistant, template, expected
):
    result = await _start_user_flow(hass, template)
    assert _zone_default(result) == expected


async def test_setup_heat_template_creates_three_zone_entry(
    hass: HomeAssistant,
):
    """Submitting the Heat prefill unchanged stores all seven switch points."""
    with (
        patch(VALIDATE, return_value=_info("1lgz0072999211", ["1lgz0072999211"])),
        patch(CLOSE, return_value=None),
        patch(SETUP_ENTRY, return_value=True),
    ):
        result = await _start_user_flow(hass, "tariff_heat")
        submitted = {**USER_INPUT, CONF_TARIFF_ZONES: _zone_default(result)}
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], submitted
        )
        await hass.async_block_till_done()

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_TARIFF_ZONES] == HEAT_ZONES_STORED
    # Seven windows collapse into three distinct zone names.
    names = [z[ZONE_NAME] for z in result["data"][CONF_TARIFF_ZONES]]
    assert dict.fromkeys(names) == dict.fromkeys(
        ["Standard", "Niedrig", "Hoch"]
    )


async def test_setup_template_is_only_a_suggestion(hass: HomeAssistant):
    """Picking Heat but submitting own zones stores the user's zones."""
    with (
        patch(VALIDATE, return_value=_info("1lgz0072999211", ["1lgz0072999211"])),
        patch(CLOSE, return_value=None),
        patch(SETUP_ENTRY, return_value=True),
    ):
        result = await _start_user_flow(hass, "tariff_heat")
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], dict(USER_INPUT)  # carries the Go zones
        )
        await hass.async_block_till_done()

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_TARIFF_ZONES] == GO_ZONES_STORED


async def test_options_template_prefills_but_does_not_save(
    hass: HomeAssistant,
):
    """The options template menu prefills the settings form only.

    Until the form is submitted the stored zones must stay untouched.
    """
    entry = _entry("1lgz0072999211")
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == FlowResultType.MENU
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "tariff_template"}
    )
    assert result["type"] == FlowResultType.MENU
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "tariff_heat"}
    )

    assert result["step_id"] == "settings"
    assert _zone_default(result)[1] == "02:00 Niedrig"
    # Nothing saved yet — the entry still holds its original zones.
    assert entry.data[CONF_TARIFF_ZONES] == [dict(z) for z in GO_ZONES_STORED]
