"""Tests for the config entry diagnostics."""

from __future__ import annotations

from types import SimpleNamespace

from custom_components.smgw_han.const import (
    CONF_METER_ID,
    CONF_PASSWORD,
    CONF_USERNAME,
)
from custom_components.smgw_han.diagnostics import (
    _mask_meter_id,
    async_get_config_entry_diagnostics,
)

METER_ID = "1xyz0123456789"


def _entry(**data) -> SimpleNamespace:
    return SimpleNamespace(
        data=data, runtime_data=SimpleNamespace(data={"date": "2026-10-29"})
    )


def test_mask_keeps_the_first_four_characters_and_the_length():
    assert _mask_meter_id(METER_ID) == "1xyz**********"


def test_mask_does_not_lengthen_a_short_value():
    assert _mask_meter_id("1xy") == "1xy"
    assert _mask_meter_id("") == ""


async def test_meter_id_is_masked_everywhere_in_the_output():
    entry = _entry(**{CONF_METER_ID: METER_ID})
    result = await async_get_config_entry_diagnostics(None, entry)
    assert result["config_entry_data"][CONF_METER_ID] == "1xyz**********"
    assert "0123456789" not in str(result)


async def test_credentials_stay_fully_redacted():
    entry = _entry(**{CONF_USERNAME: "someone", CONF_PASSWORD: "secret"})
    result = await async_get_config_entry_diagnostics(None, entry)
    assert "someone" not in str(result)
    assert "secret" not in str(result)


async def test_the_config_entry_itself_is_not_changed():
    # The gateway lookup reads the full id from the entry; only the copy for
    # the diagnostics file may be masked.
    entry = _entry(**{CONF_METER_ID: METER_ID})
    await async_get_config_entry_diagnostics(None, entry)
    assert entry.data[CONF_METER_ID] == METER_ID


async def test_entry_without_meter_id_still_works():
    result = await async_get_config_entry_diagnostics(None, _entry(url="https://x"))
    assert CONF_METER_ID not in result["config_entry_data"]
    assert result["coordinator_data"] == {"date": "2026-10-29"}
