"""Shared pytest fixtures for the SMGW HAN test suite."""

from __future__ import annotations

from pathlib import Path

import pytest

FIXTURE_DIR = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> str:
    """Return the text content of a file under tests/fixtures/."""
    return (FIXTURE_DIR / name).read_text(encoding="utf-8")


@pytest.fixture
def fixture_text():
    """Provide the load_fixture helper as a fixture."""
    return load_fixture


# Note: `enable_custom_integrations` (which requires the async `hass` fixture)
# is opted into per-module via a local autouse fixture in the HA-harness test
# files (test_config_flow.py, test_services.py). It is intentionally NOT global
# so the pure-logic tests stay synchronous and hass-free.
