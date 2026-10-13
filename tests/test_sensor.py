"""Tests for sensor entities."""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from typing import Any

from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.rct_bin_collections.const import (
    CONF_EXCLUDED_CATEGORIES,
    DOMAIN,
    MAX_STATE_LENGTH,
)

from .conftest import UPRN, load_fixture, make_entry, mock_schedule

P = "sensor.rct_bins_home_"


def _state(hass: HomeAssistant, suffix: str) -> Any:
    state = hass.states.get(P + suffix)
    assert state is not None, suffix
    return state


async def test_sensor_values(hass: HomeAssistant, loaded_entry: MockConfigEntry) -> None:
    nxt = _state(hass, "next_collection")
    assert nxt.state == "2026-10-13"
    assert nxt.attributes["device_class"] == "date"
    assert nxt.attributes["collection_types"] == ["Recycling", "Food Waste", "Garden Waste"]
    assert nxt.attributes["days_until"] == 0
    assert nxt.attributes["property_name"] == "Home"
    assert nxt.attributes["source"] == "rct_council"
    assert nxt.attributes["published_until"] == "2026-12-31"

    days = _state(hass, "days_until_next_collection")
    assert days.state == "0"
    assert days.attributes["unit_of_measurement"] == "d"
    assert days.attributes["next_date"] == "2026-10-13"

    summary = _state(hass, "collection_summary")
    assert summary.state == "Recycling, Food Waste and Garden Waste"
    assert summary.attributes["following_collections"][0] == {
        "date": "2026-10-20",
        "types": ["Recycling", "Food Waste", "Garden Waste"],
    }
    assert summary.attributes["following_collections"][1] == {
        "date": "2026-10-27",
        "types": ["General Waste", "Recycling", "Food Waste", "Garden Waste"],
    }
    assert len(summary.attributes["following_collections"]) == 4

    general = _state(hass, "general_waste_next_collection")
    assert general.state == "2026-10-27"
    assert general.attributes["days_until"] == 14
    assert general.attributes["category"] == "general_waste"
    assert general.attributes["description"] == "Black Bags"
    assert general.attributes["upcoming_dates"] == [
        "2026-10-27",
        "2026-11-17",
        "2026-12-08",
        "2026-12-29",
    ]
    assert general.attributes["last_successful_update"] == "2026-10-13T07:00:00+00:00"

    assert _state(hass, "recycling_next_collection").state == "2026-10-13"
    assert _state(hass, "food_waste_next_collection").state == "2026-10-13"
    assert _state(hass, "garden_waste_next_collection").state == "2026-10-13"
    assert hass.states.get(P + "hygiene_waste_next_collection") is None

    updated = _state(hass, "last_successful_update")
    assert updated.state == "2026-10-13T07:00:00+00:00"
    assert updated.attributes["address_verified"] is True


async def test_no_identifying_data_in_attributes(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    for state in hass.states.async_all():
        text = str(state.attributes)
        assert UPRN not in text
        assert "CF99" not in text
        assert "Example Street" not in text


async def test_values_change_at_local_midnight_without_fetching(
    hass: HomeAssistant,
    setup_integration: Callable[..., Any],
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
) -> None:
    freezer.move_to("2026-10-13 23:59:58+01:00")
    await setup_integration()
    assert _state(hass, "next_collection").state == "2026-10-13"

    freezer.move_to("2026-10-14 00:00:00+01:00")
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    assert _state(hass, "next_collection").state == "2026-10-20"
    assert _state(hass, "days_until_next_collection").state == "6"
    assert _state(hass, "general_waste_next_collection").attributes["days_until"] == 13
    assert aioclient_mock.call_count == 1


@pytest.mark.parametrize(
    ("instant", "expected_next", "expected_days"),
    [
        # 23:30 UTC on the 13th is already 00:30 BST on the 14th in Wales.
        ("2026-10-13 23:30:00+00:00", "2026-10-20", "6"),
        # Clocks go back at 02:00 BST on 25 October 2026.
        ("2026-10-24 23:30:00+00:00", "2026-10-27", "2"),
        ("2026-10-25 23:30:00+00:00", "2026-10-27", "2"),
        ("2026-10-26 00:30:00+00:00", "2026-10-27", "1"),
    ],
)
async def test_local_timezone_dates(
    hass: HomeAssistant,
    setup_integration: Callable[..., Any],
    freezer: FrozenDateTimeFactory,
    instant: str,
    expected_next: str,
    expected_days: str,
) -> None:
    freezer.move_to(instant)
    await setup_integration()
    assert _state(hass, "next_collection").state == expected_next
    assert _state(hass, "days_until_next_collection").state == expected_days


async def test_category_without_upcoming_dates_is_unknown(
    hass: HomeAssistant, setup_integration: Callable[..., Any], freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to("2026-12-30 09:00:00+00:00")
    await setup_integration()
    general = _state(hass, "general_waste_next_collection")
    # The last published black-bag date was the 29th; nothing later is known yet.
    assert general.state == "unknown"
    assert general.attributes["days_until"] is None
    assert general.attributes["upcoming_dates"] == []
    assert general.attributes["published_until"] == "2026-12-31"


async def test_no_upcoming_collections(
    hass: HomeAssistant, setup_integration: Callable[..., Any], freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to("2026-12-31 12:00:00+00:00")
    await setup_integration()
    assert _state(hass, "next_collection").state == "unknown"
    assert _state(hass, "next_collection").attributes["collection_types"] == []
    assert _state(hass, "days_until_next_collection").state == "unknown"
    assert _state(hass, "collection_summary").state == "unknown"
    assert _state(hass, "collection_summary").attributes["following_collections"] == []


async def test_unknown_categories_get_their_own_sensor(
    hass: HomeAssistant, setup_integration: Callable[..., Any]
) -> None:
    await setup_integration(fixture="schedule-unusual.html")
    bulky = _state(hass, "bulky_waste_collection_next_collection")
    assert bulky.state == "2026-10-15"
    assert bulky.attributes["description"] == "Bulky Waste Collection"
    assert bulky.attributes["friendly_name"] == (
        "RCT Bins (Home) Bulky Waste Collection next collection"
    )
    hygiene = _state(hass, "hygiene_waste_next_collection")
    assert hygiene.attributes["description"] == "Nappy & Hygiene"


async def test_new_category_appears_after_refresh(
    hass: HomeAssistant,
    setup_integration: Callable[..., Any],
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
) -> None:
    entry = await setup_integration(fixture="schedule-monthly-only.html")
    assert hass.states.get(P + "hygiene_waste_next_collection") is None

    aioclient_mock.clear_requests()
    mock_schedule(aioclient_mock, "schedule-unusual.html")
    freezer.tick(timedelta(hours=12, seconds=1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert _state(hass, "hygiene_waste_next_collection").state == "2026-10-15"
    assert entry.runtime_data.data.schedule.categories_seen >= {"hygiene"}


async def test_previously_seen_category_is_kept(
    hass: HomeAssistant, setup_integration: Callable[..., Any]
) -> None:
    entry = make_entry()
    entry.add_to_hass(hass)
    er.async_get(hass).async_get_or_create(
        "sensor",
        DOMAIN,
        f"{UPRN}_category_hygiene",
        config_entry=entry,
        suggested_object_id="rct_bins_home_hygiene_waste_next_collection",
    )
    await setup_integration(entry)
    # Seasonal or paused services keep their entity, showing "unknown" rather than vanishing.
    assert _state(hass, "hygiene_waste_next_collection").state == "unknown"


async def test_excluded_category_is_removed(
    hass: HomeAssistant, setup_integration: Callable[..., Any]
) -> None:
    entry = make_entry(options={CONF_EXCLUDED_CATEGORIES: ["garden_waste"]})
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    registry.async_get_or_create(
        "sensor", DOMAIN, f"{UPRN}_category_garden_waste", config_entry=entry
    )
    await setup_integration(entry)
    assert registry.async_get_entity_id("sensor", DOMAIN, f"{UPRN}_category_garden_waste") is None
    assert _state(hass, "next_collection").attributes["collection_types"] == [
        "Recycling",
        "Food Waste",
    ]


async def test_long_summary_is_truncated(
    hass: HomeAssistant, setup_integration: Callable[..., Any], aioclient_mock: AiohttpClientMocker
) -> None:
    labels = "".join(
        f'<div class="alert collection-day"><a href="#">Special collection number {i} with a '
        f"long description</a></div>"
        for i in range(8)
    )
    html = load_fixture("schedule-monthly-only.html").replace(
        '<div class="card-title">20</div><div class="card-text">',
        f'<div class="card-title">20</div><div class="card-text">{labels}',
    )
    entry = make_entry()
    mock_schedule(aioclient_mock, text=html)
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    state = _state(hass, "collection_summary")
    assert len(state.state) == MAX_STATE_LENGTH
    assert state.state.endswith("…")
