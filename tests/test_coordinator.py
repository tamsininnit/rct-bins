"""Tests for the coordinator: refresh, failure handling, caching and recovery."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any

from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
    AiohttpClientMockResponse,
)

from custom_components.rct_bin_collections.const import DOMAIN
from custom_components.rct_bin_collections.coordinator import (
    RctBinCoordinator,
    property_fingerprint,
)

from .conftest import HTML_HEADERS, UPRN, load_fixture, make_entry, mock_schedule

NEXT = "sensor.rct_bins_home_next_collection"
LAST_UPDATE = "sensor.rct_bins_home_last_successful_update"


def _coordinator(entry: MockConfigEntry) -> RctBinCoordinator:
    coordinator: RctBinCoordinator = entry.runtime_data
    return coordinator


async def _advance(hass: HomeAssistant, freezer: FrozenDateTimeFactory, delta: timedelta) -> None:
    freezer.tick(delta)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def test_scheduled_refresh(
    hass: HomeAssistant,
    loaded_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
) -> None:
    assert aioclient_mock.call_count == 1
    await _advance(hass, freezer, timedelta(hours=12, seconds=1))
    assert aioclient_mock.call_count == 2
    assert not _coordinator(loaded_entry).data.stale
    assert hass.states.get(LAST_UPDATE).state == "2026-10-13T19:00:01+00:00"


@pytest.mark.parametrize(
    ("mock_kwargs", "error_key"),
    [
        ({"exc": TimeoutError()}, "service_unavailable"),
        ({"status": 404}, "property_not_found"),
        ({"status": 500}, "service_unavailable"),
        ({"text": "<html><p>Down for maintenance</p></html>"}, "parse_error"),
        ({"text": load_fixture("schedule-changed-structure.html")}, "parse_error"),
        ({"text": "", "headers": HTML_HEADERS}, "unsupported_response"),
    ],
)
async def test_failure_keeps_last_known_good(
    hass: HomeAssistant,
    loaded_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
    mock_kwargs: dict[str, Any],
    error_key: str,
) -> None:
    aioclient_mock.clear_requests()
    mock_schedule(aioclient_mock, None, **mock_kwargs)
    await _advance(hass, freezer, timedelta(hours=12, seconds=1))

    coordinator = _coordinator(loaded_entry)
    assert coordinator.last_update_success
    assert coordinator.data.stale
    assert coordinator.data.last_error == error_key
    assert coordinator.update_interval == timedelta(hours=1)
    state = hass.states.get(NEXT)
    assert state.state == "2026-10-13"
    assert state.attributes["data_stale"] is True
    assert hass.states.get(LAST_UPDATE).attributes["last_error"] == error_key


async def test_rate_limit_respects_retry_after(
    hass: HomeAssistant,
    loaded_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
) -> None:
    aioclient_mock.clear_requests()
    mock_schedule(aioclient_mock, None, status=429, headers={"Retry-After": "7200"})
    await _advance(hass, freezer, timedelta(hours=12, seconds=1))
    coordinator = _coordinator(loaded_entry)
    assert coordinator.data.last_error == "rate_limited"
    assert coordinator.update_interval == timedelta(hours=2)
    # No further request before the server's back-off has elapsed.
    await _advance(hass, freezer, timedelta(hours=1, minutes=30))
    assert aioclient_mock.call_count == 1


async def test_recovery_after_failure(
    hass: HomeAssistant,
    loaded_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
) -> None:
    aioclient_mock.clear_requests()
    mock_schedule(aioclient_mock, None, status=503)
    await _advance(hass, freezer, timedelta(hours=12, seconds=1))
    assert _coordinator(loaded_entry).data.stale

    aioclient_mock.clear_requests()
    mock_schedule(aioclient_mock)
    await _advance(hass, freezer, timedelta(hours=1, seconds=1))
    coordinator = _coordinator(loaded_entry)
    assert not coordinator.data.stale
    assert coordinator.data.last_error is None
    assert coordinator.update_interval == timedelta(hours=12)
    assert hass.states.get(NEXT).attributes["data_stale"] is False


async def test_stale_data_expires(
    hass: HomeAssistant,
    loaded_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
) -> None:
    aioclient_mock.clear_requests()
    mock_schedule(aioclient_mock, None, status=503)
    await _advance(hass, freezer, timedelta(days=6))
    assert hass.states.get(NEXT).state != STATE_UNAVAILABLE

    await _advance(hass, freezer, timedelta(days=1, hours=1))
    assert not _coordinator(loaded_entry).last_update_success
    assert hass.states.get(NEXT).state == STATE_UNAVAILABLE


async def test_property_mismatch_at_runtime_makes_entities_unavailable(
    hass: HomeAssistant,
    loaded_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
) -> None:
    aioclient_mock.clear_requests()
    mock_schedule(
        aioclient_mock, text=load_fixture("schedule-page.html").replace("CF99 9ZZ", "CF37 1AA")
    )
    await _advance(hass, freezer, timedelta(hours=12, seconds=1))
    assert not _coordinator(loaded_entry).last_update_success
    assert hass.states.get(NEXT).state == STATE_UNAVAILABLE


async def test_manual_refresh_is_rate_limited(
    hass: HomeAssistant,
    loaded_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
) -> None:
    await async_setup_component(hass, "homeassistant", {})
    for _ in range(3):
        await hass.services.async_call(
            "homeassistant", "update_entity", {"entity_id": NEXT}, blocking=True
        )
    await hass.async_block_till_done()
    # Within five minutes of a successful fetch nothing is requested.
    assert aioclient_mock.call_count == 1

    freezer.tick(timedelta(minutes=6))
    await hass.services.async_call(
        "homeassistant", "update_entity", {"entity_id": NEXT}, blocking=True
    )
    await hass.async_block_till_done()
    assert aioclient_mock.call_count == 2


async def test_concurrent_refreshes_share_one_request(
    hass: HomeAssistant,
    loaded_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
) -> None:
    release = asyncio.Event()

    async def slow_response(method, url, data):  # type: ignore[no-untyped-def]
        await release.wait()
        return AiohttpClientMockResponse(
            method, url, text=load_fixture("schedule-page.html"), headers=HTML_HEADERS
        )

    aioclient_mock.clear_requests()
    mock_schedule(aioclient_mock, None, side_effect=slow_response)
    freezer.tick(timedelta(minutes=10))
    coordinator = _coordinator(loaded_entry)

    first = hass.async_create_task(coordinator.async_refresh())
    second = hass.async_create_task(coordinator.async_refresh())
    await asyncio.sleep(0)
    release.set()
    await asyncio.gather(first, second)
    assert aioclient_mock.call_count == 1
    assert not coordinator.data.stale


async def test_unload_during_fetch_cancels_it(
    hass: HomeAssistant,
    loaded_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
) -> None:
    never = asyncio.Event()

    async def hang(method, url, data):  # type: ignore[no-untyped-def]
        await never.wait()

    aioclient_mock.clear_requests()
    mock_schedule(aioclient_mock, None, side_effect=hang)
    freezer.tick(timedelta(minutes=10))
    coordinator = _coordinator(loaded_entry)
    refresh = hass.async_create_task(coordinator.async_refresh())
    await asyncio.sleep(0)
    task = coordinator._fetch_task
    assert task is not None
    assert not task.done()

    assert await hass.config_entries.async_unload(loaded_entry.entry_id)
    await hass.async_block_till_done()
    assert task.cancelled()
    refresh.cancel()


# --------------------------------------------------------------------------------------
# Persistent cache
# --------------------------------------------------------------------------------------


def _cache(entry: MockConfigEntry, *, fetched_at: str, uprn: str = UPRN) -> dict[str, Any]:
    return {
        "version": 1,
        "minor_version": 1,
        "key": f"{DOMAIN}.{entry.entry_id}",
        "data": {
            "property": property_fingerprint(uprn),
            "schedule": {
                "events": [
                    {"date": "2026-10-20", "category": "recycling", "label": "Recycling"},
                    {"date": "2026-10-27", "category": "general_waste", "label": "Black Bags"},
                ],
                "horizon_start": "2026-10-01",
                "horizon_end": "2026-12-31",
                "fetched_at": fetched_at,
                "source": "rct_council",
                "address_verified": True,
            },
        },
    }


async def test_cache_used_when_council_down_at_startup(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, hass_storage: dict[str, Any]
) -> None:
    entry = make_entry()
    hass_storage[f"{DOMAIN}.{entry.entry_id}"] = _cache(
        entry, fetched_at="2026-10-12T19:00:00+00:00"
    )
    mock_schedule(aioclient_mock, None, status=503)
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    state = hass.states.get(NEXT)
    assert state.state == "2026-10-20"
    assert state.attributes["data_stale"] is True
    assert hass.states.get(LAST_UPDATE).state == "2026-10-12T19:00:00+00:00"


async def test_fresh_fetch_replaces_cache(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, hass_storage: dict[str, Any]
) -> None:
    entry = make_entry()
    key = f"{DOMAIN}.{entry.entry_id}"
    hass_storage[key] = _cache(entry, fetched_at="2026-10-12T19:00:00+00:00")
    mock_schedule(aioclient_mock)
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert hass.states.get(NEXT).state == "2026-10-13"
    assert hass.states.get(NEXT).attributes["data_stale"] is False
    assert len(hass_storage[key]["data"]["schedule"]["events"]) == 44


@pytest.mark.parametrize(
    "cache_factory",
    [
        # Older than the seven-day limit.
        lambda entry: _cache(entry, fetched_at="2026-10-01T00:00:00+00:00"),
        # Belongs to a different property (e.g. before a reconfigure).
        lambda entry: _cache(entry, fetched_at="2026-10-12T19:00:00+00:00", uprn="1"),
        # Structurally broken.
        lambda entry: {**_cache(entry, fetched_at="x"), "data": {"property": "?"}},
        lambda entry: {
            **_cache(entry, fetched_at="2026-10-12T19:00:00+00:00"),
            "data": {
                "property": property_fingerprint(UPRN),
                "schedule": {"events": "not a list"},
            },
        },
    ],
)
async def test_unusable_cache_is_ignored(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    hass_storage: dict[str, Any],
    cache_factory: Any,
) -> None:
    entry = make_entry()
    hass_storage[f"{DOMAIN}.{entry.entry_id}"] = cache_factory(entry)
    mock_schedule(aioclient_mock, None, status=503)
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_unreadable_cache_is_ignored(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    hass_storage: dict[str, Any],
    caplog: pytest.LogCaptureFixture,
) -> None:
    entry = make_entry()
    hass_storage[f"{DOMAIN}.{entry.entry_id}"] = {"no": "version"}
    mock_schedule(aioclient_mock)
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.LOADED
    assert "Ignoring unreadable collection cache" in caplog.text


async def test_cached_past_events_are_not_upcoming(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    hass_storage: dict[str, Any],
    freezer: FrozenDateTimeFactory,
) -> None:
    freezer.move_to("2026-10-28 08:00:00+00:00")
    entry = make_entry()
    hass_storage[f"{DOMAIN}.{entry.entry_id}"] = _cache(
        entry, fetched_at="2026-10-27T19:00:00+00:00"
    )
    mock_schedule(aioclient_mock, None, status=503)
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    # Both cached dates are in the past: nothing is shown as upcoming.
    assert hass.states.get(NEXT).state == "unknown"
    assert hass.states.get("binary_sensor.rct_bins_home_collection_today").state == "off"


async def test_repeated_failures_warn_once(
    hass: HomeAssistant,
    loaded_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    aioclient_mock.clear_requests()
    mock_schedule(aioclient_mock, None, status=503)
    await _advance(hass, freezer, timedelta(hours=12, seconds=1))
    await _advance(hass, freezer, timedelta(hours=1, seconds=1))
    await _advance(hass, freezer, timedelta(hours=1, seconds=1))
    warnings = [
        r for r in caplog.records if r.levelname == "WARNING" and "Could not refresh" in r.message
    ]
    assert len(warnings) == 1
    assert UPRN not in caplog.text
