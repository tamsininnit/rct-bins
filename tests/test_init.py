"""Tests for setup, unload, reload and removal."""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from typing import Any

from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.rct_bin_collections.const import CONF_PROVIDER, CONF_UPRN, DOMAIN

from .conftest import OTHER_UPRN, UPRN, load_fixture, make_entry, mock_schedule


async def test_setup_creates_entities(hass: HomeAssistant, loaded_entry: MockConfigEntry) -> None:
    assert loaded_entry.state is ConfigEntryState.LOADED
    registry = er.async_get(hass)
    entities = er.async_entries_for_config_entry(registry, loaded_entry.entry_id)
    unique_ids = {e.unique_id for e in entities}
    assert unique_ids == {
        f"{UPRN}_next_collection",
        f"{UPRN}_days_until_next_collection",
        f"{UPRN}_collection_summary",
        f"{UPRN}_last_successful_update",
        f"{UPRN}_category_general_waste",
        f"{UPRN}_category_recycling",
        f"{UPRN}_category_food_waste",
        f"{UPRN}_category_garden_waste",
        f"{UPRN}_collection_today",
        f"{UPRN}_collection_tomorrow",
        f"{UPRN}_collection_due_soon",
        f"{UPRN}_calendar",
    }
    device = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, UPRN), loaded_entry.entry_id
    )
    assert device is not None
    assert device.name == "RCT Bins (Home)"
    assert hass.states.get("sensor.rct_bins_home_next_collection") is not None
    assert hass.states.get("calendar.rct_bins_home") is not None


async def test_unload_and_reload(
    hass: HomeAssistant,
    loaded_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
) -> None:
    assert await hass.config_entries.async_unload(loaded_entry.entry_id)
    await hass.async_block_till_done()
    assert loaded_entry.state is ConfigEntryState.NOT_LOADED
    assert hass.states.get("sensor.rct_bins_home_next_collection").state == "unavailable"

    calls = aioclient_mock.call_count
    assert await hass.config_entries.async_setup(loaded_entry.entry_id)
    await hass.async_block_till_done()
    assert loaded_entry.state is ConfigEntryState.LOADED
    assert hass.states.get("sensor.rct_bins_home_next_collection").state == "2026-10-13"
    assert hass.states.get("sensor.rct_bins_home_next_collection").attributes["data_stale"] is False
    # Reloading straight away reuses the minutes-old cache instead of fetching again ...
    assert aioclient_mock.call_count == calls

    # ... but a reload later on fetches fresh data.
    assert await hass.config_entries.async_unload(loaded_entry.entry_id)
    freezer.tick(timedelta(minutes=10))
    assert await hass.config_entries.async_setup(loaded_entry.entry_id)
    await hass.async_block_till_done()
    assert aioclient_mock.call_count == calls + 1


async def test_setup_retries_when_council_unavailable(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    entry = make_entry()
    mock_schedule(aioclient_mock, None, status=503)
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_setup_fails_for_wrong_property(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    entry = make_entry()
    mock_schedule(
        aioclient_mock, text=load_fixture("schedule-page.html").replace("CF99 9ZZ", "CF37 1AA")
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.SETUP_ERROR


async def test_unknown_provider(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Home",
        unique_id=UPRN,
        data={CONF_UPRN: UPRN, CONF_PROVIDER: "not_a_provider"},
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.SETUP_ERROR


async def test_multiple_properties_are_isolated(
    hass: HomeAssistant, setup_integration: Callable[..., Any]
) -> None:
    await setup_integration(make_entry(UPRN, name="Home"))
    await setup_integration(
        make_entry(OTHER_UPRN, name="Mum's House"), fixture="schedule-year-boundary.html"
    )
    home = hass.states.get("sensor.rct_bins_home_next_collection")
    mums = hass.states.get("sensor.rct_bins_mum_s_house_next_collection")
    assert home.state == "2026-10-13"
    assert mums.state == "2026-12-04"
    assert (
        hass.states.get("sensor.rct_bins_home_general_waste_next_collection").state == "2026-10-27"
    )
    assert (
        hass.states.get("sensor.rct_bins_mum_s_house_general_waste_next_collection").state
        == "2026-12-04"
    )
    assert len(dr.async_get(hass).devices) == 2


async def test_remove_entry_deletes_cache(
    hass: HomeAssistant, loaded_entry: MockConfigEntry, hass_storage: dict[str, Any]
) -> None:
    key = f"{DOMAIN}.{loaded_entry.entry_id}"
    assert UPRN not in str(hass_storage[key])
    await hass.config_entries.async_remove(loaded_entry.entry_id)
    await hass.async_block_till_done()
    assert key not in hass_storage
