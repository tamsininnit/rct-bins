"""Tests for reminder binary sensors."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import async_fire_time_changed
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.rct_bin_collections.const import CONF_REMINDER_DAYS

from .conftest import make_entry

TODAY = "binary_sensor.rct_bins_home_collection_today"
TOMORROW = "binary_sensor.rct_bins_home_collection_tomorrow"
SOON = "binary_sensor.rct_bins_home_collection_due_soon"


@pytest.mark.parametrize(
    ("instant", "today", "tomorrow", "soon"),
    [
        ("2026-10-13 08:00:00+01:00", "on", "off", "on"),
        ("2026-10-12 18:00:00+01:00", "off", "on", "on"),
        ("2026-10-15 18:00:00+01:00", "off", "off", "off"),
    ],
)
async def test_reminder_states(
    hass: HomeAssistant,
    setup_integration: Callable[..., Any],
    freezer: FrozenDateTimeFactory,
    instant: str,
    today: str,
    tomorrow: str,
    soon: str,
) -> None:
    freezer.move_to(instant)
    await setup_integration()
    assert hass.states.get(TODAY).state == today
    assert hass.states.get(TOMORROW).state == tomorrow
    assert hass.states.get(SOON).state == soon


async def test_tomorrow_attributes(
    hass: HomeAssistant, setup_integration: Callable[..., Any], freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to("2026-10-26 18:00:00+00:00")
    await setup_integration()
    state = hass.states.get(TOMORROW)
    assert state.state == "on"
    assert state.attributes["date"] == "2026-10-27"
    assert state.attributes["days_until"] == 1
    assert state.attributes["collection_types"] == [
        "General Waste",
        "Recycling",
        "Food Waste",
        "Garden Waste",
    ]
    assert hass.states.get(TODAY).attributes["collection_types"] == []


async def test_transition_at_local_midnight(
    hass: HomeAssistant,
    setup_integration: Callable[..., Any],
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
) -> None:
    freezer.move_to("2026-10-12 23:59:58+01:00")
    await setup_integration()
    assert hass.states.get(TODAY).state == "off"
    assert hass.states.get(TOMORROW).state == "on"

    freezer.move_to("2026-10-13 00:00:00+01:00")
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get(TODAY).state == "on"
    assert hass.states.get(TOMORROW).state == "off"
    assert aioclient_mock.call_count == 1


async def test_unknown_beyond_published_horizon(
    hass: HomeAssistant, setup_integration: Callable[..., Any], freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to("2026-12-31 18:00:00+00:00")
    await setup_integration()
    assert hass.states.get(TODAY).state == "off"
    # 1 January is not published yet: that is not the same as "no collection".
    assert hass.states.get(TOMORROW).state == "unknown"
    assert hass.states.get(SOON).state == "unknown"


@pytest.mark.parametrize(("days", "expected"), [(0, "off"), (1, "off"), (2, "on")])
async def test_reminder_window_option(
    hass: HomeAssistant,
    setup_integration: Callable[..., Any],
    freezer: FrozenDateTimeFactory,
    days: int,
    expected: str,
) -> None:
    freezer.move_to("2026-10-18 12:00:00+01:00")
    await setup_integration(make_entry(options={CONF_REMINDER_DAYS: days}))
    state = hass.states.get(SOON)
    assert state.state == expected
    if expected == "on":
        assert state.attributes["date"] == "2026-10-20"
