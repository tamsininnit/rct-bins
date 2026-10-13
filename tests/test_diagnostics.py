"""Tests for diagnostics."""

from __future__ import annotations

import json

from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.diagnostics import (
    get_diagnostics_for_config_entry,
)
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

from .conftest import UPRN


async def test_diagnostics_are_redacted(
    hass: HomeAssistant, hass_client: ClientSessionGenerator, loaded_entry: MockConfigEntry
) -> None:
    assert await async_setup_component(hass, "diagnostics", {})
    diag = await get_diagnostics_for_config_entry(hass, hass_client, loaded_entry)

    assert diag["integration_version"] == "0.1.0"
    assert diag["provider"] == "rct_council"
    assert diag["coordinator"]["last_update_success"] is True
    assert diag["coordinator"]["last_http_status"] == 200
    assert diag["coordinator"]["update_interval_seconds"] == 12 * 3600
    schedule = diag["schedule"]
    assert schedule["category_count"] == 4
    assert schedule["total_events"] == 44
    assert schedule["future_events"] == 40
    assert schedule["stale"] is False
    assert schedule["fetched_at"] == "2026-10-13T07:00:00+00:00"
    assert schedule["source_labels"] == ["Black Bags", "Food Waste", "Garden Waste", "Recycling"]
    assert diag["entry"]["data"]["uprn"] == "**REDACTED**"
    assert diag["entry"]["data"]["postcode"] == "**REDACTED**"
    assert diag["entry"]["title"] == "**REDACTED**"

    dumped = json.dumps(diag)
    assert UPRN not in dumped
    assert "CF99" not in dumped
    assert "Home" not in dumped
