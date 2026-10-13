"""Shared fixtures for RCT Bin Collections tests.

No test talks to the network: pytest-homeassistant-custom-component blocks sockets, and
HTTP is served from sanitised fixtures through ``aioclient_mock``.
"""

from __future__ import annotations

from collections.abc import Callable, Generator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.rct_bin_collections.const import (
    CONF_EXCLUDED_CATEGORIES,
    CONF_POSTCODE,
    CONF_PROVIDER,
    CONF_REMINDER_DAYS,
    CONF_UPDATE_INTERVAL,
    CONF_UPRN,
    DOMAIN,
)
from custom_components.rct_bin_collections.providers.rct_council import LOOKUP_URL

FIXTURES = Path(__file__).parent / "fixtures"

#: Fictitious identifiers used throughout the fixtures.
UPRN = "999000000017"
POSTCODE = "CF99 9ZZ"
OTHER_UPRN = "999000000002"

#: A Tuesday on which the sanitised live fixture has Recycling, Garden Waste and Food Waste.
FROZEN_NOW = "2026-10-13 08:00:00+01:00"

HTML_HEADERS = {"Content-Type": "text/html; charset=utf-8"}


def load_fixture(name: str) -> str:
    """Return the text of a fixture file."""
    return (FIXTURES / name).read_text(encoding="utf-8")


def mock_schedule(
    aioclient_mock: AiohttpClientMocker,
    fixture: str | None = "schedule-page.html",
    *,
    uprn: str = UPRN,
    text: str | None = None,
    **kwargs: Any,
) -> None:
    """Register a schedule response for ``uprn``."""
    body = text if text is not None else (load_fixture(fixture) if fixture else None)
    kwargs.setdefault("headers", HTML_HEADERS)
    aioclient_mock.get(LOOKUP_URL, params={"uprn": uprn, "month": "0"}, text=body, **kwargs)


def mock_addresses(
    aioclient_mock: AiohttpClientMocker,
    fixture: str = "addresses-multiple.html",
    *,
    postcode: str = POSTCODE,
    **kwargs: Any,
) -> None:
    """Register an address-list response for ``postcode``."""
    kwargs.setdefault("headers", HTML_HEADERS)
    aioclient_mock.get(
        LOOKUP_URL,
        params={"Postcode": postcode},
        text=load_fixture(fixture),
        **kwargs,
    )


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Allow Home Assistant to load the integration under test."""


@pytest.fixture(autouse=True)
async def london_time(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    """Run every test in Europe/London at a fixed instant."""
    await hass.config.async_set_time_zone("Europe/London")
    freezer.move_to(FROZEN_NOW)


@pytest.fixture(autouse=True)
def no_sleep() -> Generator[AsyncMock]:
    """Skip retry back-off delays in the HTTP client."""
    with patch(
        "custom_components.rct_bin_collections.api.asyncio.sleep", new_callable=AsyncMock
    ) as sleep:
        yield sleep


def make_entry(
    uprn: str = UPRN,
    *,
    name: str = "Home",
    postcode: str = POSTCODE,
    options: dict[str, Any] | None = None,
) -> MockConfigEntry:
    """Return a config entry as the config flow would create it."""
    return MockConfigEntry(
        domain=DOMAIN,
        title=name,
        unique_id=uprn,
        data={CONF_UPRN: uprn, CONF_POSTCODE: postcode, CONF_PROVIDER: "rct_council"},
        options={
            CONF_UPDATE_INTERVAL: 12,
            CONF_REMINDER_DAYS: 1,
            CONF_EXCLUDED_CATEGORIES: [],
            **(options or {}),
        },
    )


@pytest.fixture
def config_entry() -> MockConfigEntry:
    """Return the default config entry."""
    return make_entry()


@pytest.fixture
def setup_integration(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> Callable[..., Any]:
    """Return a helper that adds an entry and sets it up against a fixture."""

    async def _setup(
        entry: MockConfigEntry | None = None, fixture: str = "schedule-page.html"
    ) -> MockConfigEntry:
        entry = entry or make_entry()
        mock_schedule(aioclient_mock, fixture, uprn=entry.data[CONF_UPRN])
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        return entry

    return _setup


@pytest.fixture
async def loaded_entry(
    setup_integration: Callable[..., Any],
) -> MockConfigEntry:
    """Return a loaded entry backed by the sanitised live fixture."""
    entry: MockConfigEntry = await setup_integration()
    return entry
