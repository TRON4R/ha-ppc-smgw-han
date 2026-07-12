"""Tests for the dynamic (zone-dependent) sensor generation and cleanup."""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.smgw_han.const import (
    CONF_INSTANCE_ID,
    DOMAIN,
    ZONE_NAME,
    ZONE_TIME,
)
from custom_components.smgw_han.sensor import (
    _dynamic_descriptions,
    _remove_stale_dynamic_entities,
)

HEAT_CONFIG = [
    {ZONE_TIME: "00:00", ZONE_NAME: "Standard"},
    {ZONE_TIME: "02:00", ZONE_NAME: "Niedrig"},
    {ZONE_TIME: "06:00", ZONE_NAME: "Standard"},
    {ZONE_TIME: "12:00", ZONE_NAME: "Niedrig"},
    {ZONE_TIME: "16:00", ZONE_NAME: "Standard"},
    {ZONE_TIME: "18:00", ZONE_NAME: "Hoch"},
    {ZONE_TIME: "21:00", ZONE_NAME: "Standard"},
]

GO_CONFIG = [
    {ZONE_TIME: "00:00", ZONE_NAME: "Zeitfenster 1"},
    {ZONE_TIME: "05:00", ZONE_NAME: "Zeitfenster 2"},
]


@pytest.fixture(autouse=True)
def _enable(enable_custom_integrations):
    yield


def test_dynamic_descriptions_go_matches_legacy_layout():
    descs = _dynamic_descriptions(GO_CONFIG)
    keys = [d.key for d in descs]
    # Exactly the pre-3.0 dynamic entities: two slots, one switch reading.
    assert keys == [
        "daily_consumption_slot_1",
        "daily_consumption_slot_2",
        "meter_consumption_switch_1",
    ]
    slot_1 = descs[0]
    assert slot_1.translation_key == "daily_consumption_zone"
    assert slot_1.translation_placeholders == {"zone_name": "Zeitfenster 1"}
    assert slot_1.is_daily_value
    switch_1 = descs[2]
    assert switch_1.translation_key == "meter_consumption_switch"
    assert switch_1.translation_placeholders == {"index": "1"}
    assert not switch_1.is_daily_value


def test_dynamic_descriptions_heat_grouped_zones():
    descs = _dynamic_descriptions(HEAT_CONFIG)
    slots = [d for d in descs if d.key.startswith("daily_consumption_slot_")]
    switches = [
        d for d in descs if d.key.startswith("meter_consumption_switch_")
    ]
    # 3 distinct zones (order of first appearance), 6 inner boundaries.
    assert [d.translation_placeholders["zone_name"] for d in slots] == [
        "Standard",
        "Niedrig",
        "Hoch",
    ]
    assert [d.key for d in slots] == [
        "daily_consumption_slot_1",
        "daily_consumption_slot_2",
        "daily_consumption_slot_3",
    ]
    assert len(switches) == 6
    assert switches[-1].key == "meter_consumption_switch_6"


async def test_remove_stale_dynamic_entities(hass: HomeAssistant):
    # An entry shrunk from a Heat-style config (3 zones / 6 switches) back to
    # 2 zones / 1 switch: the out-of-range registry entries must be removed,
    # everything else (including static sensors) kept.
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_INSTANCE_ID: 1})
    entry.add_to_hass(hass)
    registry = er.async_get(hass)

    uids = [
        "smgw_meter1_daily_consumption_total",
        "smgw_meter1_daily_consumption_slot_1",
        "smgw_meter1_daily_consumption_slot_2",
        "smgw_meter1_daily_consumption_slot_3",
        "smgw_meter1_meter_consumption_switch_1",
        "smgw_meter1_meter_consumption_switch_2",
        "smgw_meter1_meter_consumption_switch_6",
    ]
    for uid in uids:
        registry.async_get_or_create(
            "sensor", DOMAIN, uid, config_entry=entry
        )

    _remove_stale_dynamic_entities(hass, entry, zone_count=2, switch_count=1)

    remaining = {
        reg_entry.unique_id
        for reg_entry in er.async_entries_for_config_entry(
            registry, entry.entry_id
        )
    }
    assert remaining == {
        "smgw_meter1_daily_consumption_total",
        "smgw_meter1_daily_consumption_slot_1",
        "smgw_meter1_daily_consumption_slot_2",
        "smgw_meter1_meter_consumption_switch_1",
    }
