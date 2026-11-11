"""Upgrade from v3.8.0-beta.1: a weekend-only sensor changes its number, not its identity.

beta.1 numbered a name that only occurs on Saturday or Sunday right after the
Mon-Fri names, later versions count such names from 101. The sensor is therefore
removed and created anew, and only Home Assistant's entity registry decides that
the new one gets the old entity ID, which in turn is the key of its statistics.
These tests run the real registry and the real recorder instead of assuming either.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import patch

import pytest
from homeassistant.components.recorder.statistics import statistics_during_period
from homeassistant.const import EVENT_HOMEASSISTANT_START, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
    do_adhoc_statistics,
)

from custom_components.smgw_han.const import (
    CONF_INSTANCE_ID,
    CONF_METER_ID,
    CONF_PASSWORD,
    CONF_TARIFF_ZONES,
    CONF_TARIFF_ZONES_SUNDAY,
    CONF_URL,
    CONF_USERNAME,
    DOMAIN,
)
from custom_components.smgw_han.smgw_client import DailyData

SLOT_UID = "smgw_meter1_daily_consumption_slot_{}"
# Exactly what the code of the v3.8.0-beta.1 tag registers for this layout, checked
# by running it. The entity ID is built from the name in the system's language.
BETA1_ENTITY_IDS = {
    "en": {
        1: "sensor.ppc_smgw_daily_consumption_nt",
        2: "sensor.ppc_smgw_daily_consumption_ht",
        3: "sensor.ppc_smgw_daily_consumption_weekend",
    },
    "de": {
        1: "sensor.ppc_smgw_tagesverbrauch_nt",
        2: "sensor.ppc_smgw_tagesverbrauch_ht",
        3: "sensor.ppc_smgw_tagesverbrauch_weekend",
    },
}
WEEKEND = BETA1_ENTITY_IDS["en"][3]


class _GatewayStub:
    async def close(self):
        pass

    async def async_fetch_daily_data(self, target_date, zones=None, target_meter_id=None):
        return DailyData(
            date=target_date,
            import_boundaries=[1000.0, 1007.0],
            export_midnight=0.0,
            export_next_midnight=0.0,
            zone_totals={"NT": 2.0, "Weekend": 5.0},
            daily_import_total=7.0,
            daily_export_total=0.0,
        )


@pytest.fixture(autouse=True)
def _enable(enable_custom_integrations):
    yield


@pytest.fixture
def mock_recorder_before_hass(async_test_recorder):
    """Have the recorder's test setup in place before hass starts."""


async def _system_as_left_by_beta1(
    hass: HomeAssistant, language: str = "en"
) -> MockConfigEntry:
    """A config entry with a Sunday-only name, and the registry beta.1 left for it."""
    hass.config.language = language
    await hass.config.async_set_time_zone("Europe/Berlin")
    assert await async_setup_component(hass, "http", {})
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_URL: "https://gw/cgi-bin/hanservice.cgi",
            CONF_USERNAME: "u",
            CONF_PASSWORD: "p",
            CONF_METER_ID: "M",
            CONF_INSTANCE_ID: 1,
            CONF_TARIFF_ZONES: [
                {"time": "00:00", "name": "NT"},
                {"time": "06:00", "name": "HT"},
            ],
            CONF_TARIFF_ZONES_SUNDAY: [
                {"time": "00:00", "name": "NT"},
                {"time": "08:00", "name": "Weekend"},
            ],
        },
    )
    entry.add_to_hass(hass)
    for n, entity_id in BETA1_ENTITY_IDS[language].items():
        er.async_get(hass).async_get_or_create(
            "sensor",
            DOMAIN,
            SLOT_UID.format(n),
            config_entry=entry,
            suggested_object_id=entity_id.removeprefix("sensor."),
        )
    return entry


async def _start_this_version(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    with patch("custom_components.smgw_han.SmgwClient", return_value=_GatewayStub()):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()


async def _unload(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


@pytest.mark.parametrize("language", ["en", "de"])
@pytest.mark.parametrize(
    "ha_already_started",
    [
        pytest.param(False, id="normal_restart"),
        # The gateway was out of reach during the restart, so the setup is retried
        # later. By then the old sensor holds a placeholder state, and an entity ID
        # that is still in use would push the new sensor to "..._2".
        pytest.param(True, id="setup_retried_after_start"),
    ],
)
async def test_weekend_sensor_keeps_its_entity_id(
    recorder_mock, hass: HomeAssistant, freezer, language, ha_already_started
):
    freezer.move_to("2027-01-04 08:00:00+00:00")
    entry = await _system_as_left_by_beta1(hass, language)
    registry = er.async_get(hass)
    weekend = BETA1_ENTITY_IDS[language][3]
    kept_ids = {
        n: registry.async_get(BETA1_ENTITY_IDS[language][n]).id for n in (1, 2)
    }
    if ha_already_started:
        hass.bus.async_fire(EVENT_HOMEASSISTANT_START)
        await hass.async_block_till_done()
        assert hass.states.get(weekend).state == STATE_UNAVAILABLE

    await _start_this_version(hass, entry)

    assert registry.async_get(weekend).unique_id == SLOT_UID.format(101)
    assert registry.async_get_entity_id("sensor", DOMAIN, SLOT_UID.format(3)) is None
    assert float(hass.states.get(weekend).state) == 5.0
    # The sensors whose number stays are the same registry entries as before.
    for n, registry_id in kept_ids.items():
        kept = registry.async_get(BETA1_ENTITY_IDS[language][n])
        assert (kept.id, kept.unique_id) == (registry_id, SLOT_UID.format(n))
    # No "..._2" next to them.
    assert {
        reg_entry.entity_id
        for reg_entry in er.async_entries_for_config_entry(registry, entry.entry_id)
        if "daily_consumption_slot_" in reg_entry.unique_id
    } == set(BETA1_ENTITY_IDS[language].values())
    await _unload(hass, entry)


async def test_weekend_statistics_carry_on_across_the_upgrade(
    recorder_mock, hass: HomeAssistant, freezer
):
    entry = await _system_as_left_by_beta1(hass)
    assert await async_setup_component(hass, "sensor", {})

    # Two Sundays on beta.1, compiled by the recorder from what the old sensor reported.
    for now, last_reset, value in (
        ("2026-12-21 08:00:00+00:00", "2026-12-19T23:00:00+00:00", 3.0),
        ("2026-12-28 08:00:00+00:00", "2026-12-26T23:00:00+00:00", 4.0),
    ):
        freezer.move_to(now)
        hass.states.async_set(
            WEEKEND,
            value,
            {
                "device_class": "energy",
                "state_class": "total",
                "unit_of_measurement": "kWh",
                "last_reset": last_reset,
            },
        )
        await async_wait_recording_done(hass)
        do_adhoc_statistics(hass, start=dt_util.utcnow())
        await async_wait_recording_done(hass)

    # The restart into this version, and the next Sunday on the new sensor.
    hass.states.async_remove(WEEKEND)
    freezer.move_to("2027-01-04 08:00:00+00:00")
    await _start_this_version(hass, entry)
    await async_wait_recording_done(hass)
    do_adhoc_statistics(hass, start=dt_util.utcnow())
    await async_wait_recording_done(hass)

    rows = await hass.async_add_executor_job(
        statistics_during_period,
        hass,
        datetime(2026, 12, 1, tzinfo=UTC),
        None,
        {WEEKEND},
        "5minute",
        None,
        {"state", "sum"},
    )
    # The sum runs on from the old sensor's: a new series would restart at 0.
    assert [(row["state"], row["sum"]) for row in rows[WEEKEND]] == [
        (3.0, 0.0),
        (4.0, 4.0),
        (5.0, 9.0),
    ]
    await _unload(hass, entry)


async def test_manually_changed_entity_id_has_to_be_redone(
    recorder_mock, hass: HomeAssistant, freezer
):
    # The limit the release notes of v3.8.0-beta.2 name: the new sensor starts from
    # its generated ID, because the customization belonged to the removed entry.
    freezer.move_to("2027-01-04 08:00:00+00:00")
    entry = await _system_as_left_by_beta1(hass)
    registry = er.async_get(hass)
    registry.async_update_entity(WEEKEND, new_entity_id="sensor.wochenende")

    await _start_this_version(hass, entry)

    assert registry.async_get("sensor.wochenende") is None
    assert registry.async_get(WEEKEND).unique_id == SLOT_UID.format(101)
    await _unload(hass, entry)
