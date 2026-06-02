"""Tests for services._period_range and services._validate_range.

These import homeassistant (services.py depends on it) but need no running
hass; ``dt_util.now()`` is pinned with freezegun to exercise the month / year
boundaries that hand-built ranges get wrong.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from freezegun import freeze_time
from homeassistant.exceptions import ServiceValidationError

from custom_components.smgw_han.const import SMGW_HISTORY_DAYS
from custom_components.smgw_han.services import _period_range, _validate_range


def test_yesterday_with_closing_margin():
    with freeze_time("2026-05-15 08:30:00"):
        from_dt, to_dt = _period_range("yesterday")
    assert from_dt == datetime(2026, 5, 14, 0, 0, 0)
    # +00:15 margin so the closing 00:00:01 reading of the last day is caught.
    assert to_dt == datetime(2026, 5, 15, 0, 15, 0)


def test_last_7_and_30_days():
    with freeze_time("2026-05-15 23:59:00"):
        f7, _t7 = _period_range("last_7_days")
        f30, _t30 = _period_range("last_30_days")
    assert f7 == datetime(2026, 5, 8, 0, 0, 0)
    assert f30 == datetime(2026, 4, 15, 0, 0, 0)


def test_current_month():
    with freeze_time("2026-02-15 12:00:00"):
        from_dt, to_dt = _period_range("current_month")
    assert from_dt == datetime(2026, 2, 1, 0, 0, 0)
    assert to_dt == datetime(2026, 2, 15, 0, 15, 0)


def test_last_month_across_year_boundary():
    with freeze_time("2026-01-10 12:00:00"):
        from_dt, to_dt = _period_range("last_month")
    assert from_dt == datetime(2025, 12, 1, 0, 0, 0)
    assert to_dt == datetime(2026, 1, 1, 0, 15, 0)


def test_last_month_from_march_handles_february():
    with freeze_time("2026-03-05 06:00:00"):
        from_dt, to_dt = _period_range("last_month")
    assert from_dt == datetime(2026, 2, 1, 0, 0, 0)
    assert to_dt == datetime(2026, 3, 1, 0, 15, 0)


def test_unknown_preset_raises():
    with pytest.raises(ServiceValidationError):
        _period_range("does_not_exist")


def test_validate_range_accepts_valid():
    with freeze_time("2026-05-15 12:00:00"):
        _validate_range(datetime(2026, 5, 1), datetime(2026, 5, 10))


def test_validate_range_rejects_from_after_to():
    with freeze_time("2026-05-15 12:00:00"), pytest.raises(ServiceValidationError):
        _validate_range(datetime(2026, 5, 10), datetime(2026, 5, 1))


def test_validate_range_rejects_future():
    with freeze_time("2026-05-15 12:00:00"), pytest.raises(ServiceValidationError):
        _validate_range(datetime(2026, 5, 1), datetime(2026, 5, 16))


def test_validate_range_rejects_too_old():
    with freeze_time("2026-05-15 12:00:00"):
        too_old = datetime(2026, 5, 15) - timedelta(days=SMGW_HISTORY_DAYS + 1)
        with pytest.raises(ServiceValidationError):
            _validate_range(too_old, datetime(2026, 5, 14))
