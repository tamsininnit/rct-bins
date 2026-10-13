"""Tests for the collection calendar."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, timedelta
from typing import Any

from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.rct_bin_collections.const import CONF_EXCLUDED_CATEGORIES

from .conftest import UPRN, make_entry

CALENDAR = "calendar.rct_bins_home"


async def _events(hass: HomeAssistant, start: str, end: str) -> list[dict[str, Any]]:
    response = await hass.services.async_call(
        "calendar",
        "get_events",
        {"entity_id": CALENDAR, "start_date_time": start, "end_date_time": end},
        blocking=True,
        return_response=True,
    )
    events: list[dict[str, Any]] = response[CALENDAR]["events"]
    return events


async def test_current_event_state(hass: HomeAssistant, loaded_entry: MockConfigEntry) -> None:
    state = hass.states.get(CALENDAR)
    assert state.state == "on"
    assert state.attributes["message"] == "Recycling Collection"
    assert state.attributes["all_day"] is True
    assert state.attributes["start_time"] == "2026-10-13 00:00:00"
    assert state.attributes["end_time"] == "2026-10-14 00:00:00"


async def test_events_in_range(hass: HomeAssistant, loaded_entry: MockConfigEntry) -> None:
    events = await _events(hass, "2026-10-14T00:00:00+01:00", "2026-10-28T00:00:00+00:00")
    assert [(e["start"], e["summary"]) for e in events] == [
        ("2026-10-20", "Recycling Collection"),
        ("2026-10-20", "Food Waste Collection"),
        ("2026-10-20", "Garden Waste Collection"),
        ("2026-10-27", "General Waste Collection"),
        ("2026-10-27", "Recycling Collection"),
        ("2026-10-27", "Food Waste Collection"),
        ("2026-10-27", "Garden Waste Collection"),
    ]
    assert all(e["end"] == _next_day(e["start"]) for e in events)
    assert events[3]["description"] == "Black Bags (published by Rhondda Cynon Taf Council)"


async def test_range_is_end_exclusive(hass: HomeAssistant, loaded_entry: MockConfigEntry) -> None:
    events = await _events(hass, "2026-10-14T00:00:00+01:00", "2026-10-20T00:00:00+01:00")
    assert events == []
    events = await _events(hass, "2026-10-20T23:59:00+01:00", "2026-10-21T00:00:00+01:00")
    assert {e["start"] for e in events} == {"2026-10-20"}


async def test_past_events_in_horizon_are_shown(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    events = await _events(hass, "2026-10-01T00:00:00+01:00", "2026-10-07T00:00:00+01:00")
    assert len(events) == 4
    assert {e["start"] for e in events} == {"2026-10-06"}


async def test_nothing_invented_beyond_horizon(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    assert await _events(hass, "2027-01-01T00:00:00+00:00", "2027-03-01T00:00:00+00:00") == []


async def test_year_boundary_and_bank_holidays(
    hass: HomeAssistant, setup_integration: Callable[..., Any], freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to("2026-12-20 12:00:00+00:00")
    await setup_integration(fixture="schedule-year-boundary.html")
    events = await _events(hass, "2026-12-21T00:00:00+00:00", "2027-01-05T00:00:00+00:00")
    assert sorted({e["start"] for e in events}) == ["2026-12-26", "2027-01-02"]


async def test_no_duplicates_after_refresh(
    hass: HomeAssistant, loaded_entry: MockConfigEntry, freezer: FrozenDateTimeFactory
) -> None:
    before = await _events(hass, "2026-10-01T00:00:00+01:00", "2027-01-01T00:00:00+00:00")
    freezer.tick(timedelta(hours=12, seconds=1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    after = await _events(hass, "2026-10-01T00:00:00+01:00", "2027-01-01T00:00:00+00:00")
    assert len(before) == len(after) == 44
    assert len({(e["start"], e["summary"]) for e in after}) == 44

    entity = hass.data["entity_components"]["calendar"].get_entity(CALENDAR)
    raw = await entity.async_get_events(
        hass,
        datetime.fromisoformat("2026-10-01T00:00:00+01:00"),
        datetime.fromisoformat("2027-01-01T00:00:00+00:00"),
    )
    uids = {e.uid for e in raw}
    assert len(uids) == 44
    assert all(uid and UPRN not in uid for uid in uids)


async def test_excluded_categories_hidden(
    hass: HomeAssistant, setup_integration: Callable[..., Any]
) -> None:
    await setup_integration(make_entry(options={CONF_EXCLUDED_CATEGORIES: ["garden_waste"]}))
    events = await _events(hass, "2026-10-01T00:00:00+01:00", "2027-01-01T00:00:00+00:00")
    assert len(events) == 44 - 13
    assert all("Garden" not in e["summary"] for e in events)


async def test_no_event_when_nothing_upcoming(
    hass: HomeAssistant, setup_integration: Callable[..., Any], freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to("2026-12-31 12:00:00+00:00")
    await setup_integration()
    assert hass.states.get(CALENDAR).state == "off"


def _next_day(iso: str) -> str:
    return (date.fromisoformat(iso) + timedelta(days=1)).isoformat()
