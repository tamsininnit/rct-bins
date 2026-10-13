"""Tests for the RCT Council parser and provider."""

from __future__ import annotations

from datetime import date

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import pytest
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.rct_bin_collections.exceptions import (
    ParseError,
    PostcodeNotFoundError,
    PropertyMismatchError,
    PropertyNotFoundError,
)
from custom_components.rct_bin_collections.models import (
    FOOD_WASTE,
    GARDEN_WASTE,
    GENERAL_WASTE,
    HYGIENE,
    RECYCLING,
)
from custom_components.rct_bin_collections.providers import (
    PROVIDERS,
    ProviderCapability,
    create_provider,
)
from custom_components.rct_bin_collections.providers.base import CollectionProvider
from custom_components.rct_bin_collections.providers.rct_council import RctCouncilProvider
from custom_components.rct_bin_collections.providers.rct_council_parser import (
    parse_address_list,
    parse_schedule,
)

from .conftest import POSTCODE, UPRN, load_fixture, mock_addresses, mock_schedule

# --------------------------------------------------------------------------------------
# Address list parsing
# --------------------------------------------------------------------------------------


def test_parse_multiple_addresses() -> None:
    properties = parse_address_list(load_fixture("addresses-multiple.html"))
    assert [p.uprn for p in properties] == ["999000000001", "999000000002", "999000000003"]
    assert properties[2].address == "Flat 1, 3 Example Street, Exampletown, Pontyclun, CF99 9ZZ"
    assert all(p.postcode == POSTCODE for p in properties)


def test_parse_single_address() -> None:
    properties = parse_address_list(load_fixture("addresses-single.html"))
    assert len(properties) == 1
    assert properties[0].uprn == UPRN


def test_parse_no_addresses() -> None:
    with pytest.raises(PostcodeNotFoundError):
        parse_address_list(load_fixture("addresses-none.html"))


def test_parse_address_list_with_only_placeholder_option() -> None:
    html = load_fixture("addresses-single.html").replace('value="999000000017"', 'value=""')
    with pytest.raises(PostcodeNotFoundError):
        parse_address_list(html)


@pytest.mark.parametrize("fixture", ["not-lookup-page.html", "schedule-page.html"])
def test_parse_address_list_unrecognised(fixture: str) -> None:
    with pytest.raises(ParseError):
        parse_address_list(load_fixture(fixture))


def test_parse_address_list_ignores_bad_and_duplicate_values() -> None:
    html = load_fixture("addresses-multiple.html")
    html = html.replace('value="999000000002"', 'value="999000000001"')
    html = html.replace('value="999000000003"', 'value="not-a-uprn"')
    assert [p.uprn for p in parse_address_list(html)] == ["999000000001"]


# --------------------------------------------------------------------------------------
# Schedule parsing
# --------------------------------------------------------------------------------------


def test_parse_live_schedule() -> None:
    parsed = parse_schedule(load_fixture("schedule-page.html"))
    assert parsed.horizon_start == date(2026, 10, 1)
    assert parsed.horizon_end == date(2026, 12, 31)
    assert parsed.address_postcode == POSTCODE
    assert parsed.skipped_entries == 0
    assert len(parsed.events) == 44

    on_6th = {e.category for e in parsed.events if e.date == date(2026, 10, 6)}
    assert on_6th == {RECYCLING, GARDEN_WASTE, GENERAL_WASTE, FOOD_WASTE}
    black_bags = sorted(e.date for e in parsed.events if e.category == GENERAL_WASTE)
    assert black_bags == [
        date(2026, 10, 6),
        date(2026, 10, 27),
        date(2026, 11, 17),
        date(2026, 12, 8),
        date(2026, 12, 29),
    ]
    labels = {e.category: e.label for e in parsed.events}
    assert labels[GENERAL_WASTE] == "Black Bags"


def test_parse_single_collection_event() -> None:
    parsed = parse_schedule(load_fixture("schedule-monthly-only.html"))
    assert [(e.date, e.category) for e in parsed.events if e.date == date(2026, 10, 20)] == [
        (date(2026, 10, 20), RECYCLING),
        (date(2026, 10, 20), FOOD_WASTE),
    ]


def test_parse_monthly_fallback() -> None:
    parsed = parse_schedule(load_fixture("schedule-monthly-only.html"))
    assert parsed.horizon_start == date(2026, 10, 1)
    assert parsed.horizon_end == date(2026, 10, 31)
    assert {e.date for e in parsed.events} == {date(2026, 10, 20), date(2026, 10, 27)}


def test_parse_year_boundary_and_bank_holidays() -> None:
    parsed = parse_schedule(load_fixture("schedule-year-boundary.html"))
    assert parsed.horizon_start == date(2026, 12, 1)
    assert parsed.horizon_end == date(2027, 2, 28)
    dates = sorted({e.date for e in parsed.events})
    # Replacement Saturdays are taken exactly as published, not inferred from Fridays.
    assert date(2026, 12, 26) in dates
    assert date(2027, 1, 2) in dates
    assert date(2026, 12, 25) not in dates
    assert date(2027, 1, 1) not in dates
    assert dates[-1] == date(2027, 2, 26)


def test_parse_unusual_entries() -> None:
    parsed = parse_schedule(load_fixture("schedule-unusual.html"))
    by_day: dict[date, list[tuple[str, str]]] = {}
    for event in parsed.events:
        by_day.setdefault(event.date, []).append((event.category, event.label))
    # Duplicates are kept by the parser; CollectionSchedule.build removes them.
    assert by_day[date(2026, 10, 14)].count((RECYCLING, "Recycling")) == 2
    assert (FOOD_WASTE, "Food Waste") in by_day[date(2026, 10, 14)]
    assert (HYGIENE, "Nappy & Hygiene") in by_day[date(2026, 10, 15)]
    assert ("other_bulky_waste_collection", "Bulky Waste Collection") in by_day[date(2026, 10, 15)]


def test_parse_empty_schedule() -> None:
    parsed = parse_schedule(load_fixture("schedule-empty.html"))
    assert parsed.events == ()
    assert parsed.horizon_end == date(2026, 12, 31)


def test_parse_invalid_dates() -> None:
    parsed = parse_schedule(load_fixture("schedule-invalid-dates.html"))
    assert {e.date for e in parsed.events} == {date(2026, 11, 24)}
    # 31 November, a "??" day and the non-English month header are skipped and counted.
    assert parsed.skipped_entries == 3
    # The unreadable December must not count as published-but-empty.
    assert parsed.horizon_end == date(2026, 11, 30)


def test_parse_leap_day() -> None:
    parsed = parse_schedule(load_fixture("schedule-leap-year.html"))
    assert date(2028, 2, 29) in {e.date for e in parsed.events}
    assert parsed.horizon_end == date(2028, 2, 29)
    assert parsed.skipped_entries == 0


def test_unreadable_middle_month_shortens_horizon() -> None:
    html = load_fixture("schedule-page.html").replace("November 2026", "November 2026 (revised)")
    parsed = parse_schedule(html)
    assert parsed.horizon_start == date(2026, 10, 1)
    assert parsed.horizon_end == date(2026, 10, 31)
    assert max(e.date for e in parsed.events) == date(2026, 10, 27)


@pytest.mark.parametrize("fixture", ["schedule-changed-structure.html", "not-lookup-page.html"])
def test_parse_changed_structure(fixture: str) -> None:
    with pytest.raises(ParseError):
        parse_schedule(load_fixture(fixture))


def test_parse_unknown_property() -> None:
    with pytest.raises(PropertyNotFoundError):
        parse_schedule(load_fixture("schedule-unknown-property.html"))


def test_parse_search_form_without_calendar_is_unknown_property() -> None:
    html = load_fixture("schedule-unknown-property.html").replace("RAZOR VIEW ERROR", "")
    with pytest.raises(PropertyNotFoundError):
        parse_schedule(html)


def test_parse_unparseable_month_headers() -> None:
    html = load_fixture("schedule-monthly-only.html").replace("October 2026", "Hydref 2026")
    with pytest.raises(ParseError):
        parse_schedule(html)


def test_parse_survives_unexpected_characters() -> None:
    html = load_fixture("schedule-page.html").replace("Recycling", "Recycling \u200b", 1)
    parsed = parse_schedule(html)
    assert len(parsed.events) == 44


# --------------------------------------------------------------------------------------
# Provider (HTTP + parsing + validation)
# --------------------------------------------------------------------------------------


@pytest.fixture
async def provider(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> RctCouncilProvider:
    return RctCouncilProvider(async_get_clientsession(hass))


async def test_registry_and_capabilities(hass: HomeAssistant) -> None:
    assert set(PROVIDERS) == {"rct_council"}
    provider = create_provider("rct_council", async_get_clientsession(hass))
    assert provider.supports(ProviderCapability.POSTCODE_LOOKUP)
    assert provider.supports(ProviderCapability.ADDRESS_VERIFICATION)
    with pytest.raises(KeyError):
        create_provider("uk_bin_day", async_get_clientsession(hass))


async def test_base_provider_without_postcode_lookup() -> None:
    class UprnOnly(CollectionProvider):
        provider_id = "uprn_only"
        name = "UPRN only"
        capabilities = frozenset()

        async def async_get_schedule(self, uprn, postcode, *, require_address_match=False):  # type: ignore[no-untyped-def]
            raise NotImplementedError

    source = UprnOnly()
    assert not source.supports(ProviderCapability.POSTCODE_LOOKUP)
    with pytest.raises(NotImplementedError):
        await source.async_find_properties(POSTCODE)


async def test_provider_find_properties(
    provider: RctCouncilProvider, aioclient_mock: AiohttpClientMocker
) -> None:
    mock_addresses(aioclient_mock)
    properties = await provider.async_find_properties(POSTCODE)
    assert len(properties) == 3
    _, url, _, headers = aioclient_mock.mock_calls[0]
    assert url.scheme == "https"
    assert url.query["Postcode"] == POSTCODE
    assert "RCTBinCollections" in headers["User-Agent"]


async def test_provider_get_schedule(
    provider: RctCouncilProvider, aioclient_mock: AiohttpClientMocker
) -> None:
    mock_schedule(aioclient_mock)
    schedule = await provider.async_get_schedule(UPRN, POSTCODE)
    assert len(schedule.events) == 44
    assert schedule.address_verified
    assert schedule.source == "rct_council"
    assert schedule.categories_seen == {RECYCLING, GARDEN_WASTE, GENERAL_WASTE, FOOD_WASTE}
    assert provider.last_status == 200


async def test_provider_deduplicates(
    provider: RctCouncilProvider, aioclient_mock: AiohttpClientMocker
) -> None:
    mock_schedule(aioclient_mock, "schedule-unusual.html")
    schedule = await provider.async_get_schedule(UPRN, POSTCODE)
    on_14th = [e for e in schedule.events if e.date == date(2026, 10, 14)]
    assert sorted(e.category for e in on_14th) == [FOOD_WASTE, RECYCLING]
    on_21st = [e for e in schedule.events if e.date == date(2026, 10, 21)]
    assert len(on_21st) == 1


async def test_provider_postcode_mismatch(
    provider: RctCouncilProvider, aioclient_mock: AiohttpClientMocker
) -> None:
    mock_schedule(
        aioclient_mock, text=load_fixture("schedule-page.html").replace(POSTCODE, "CF37 1AA")
    )
    with pytest.raises(PropertyMismatchError):
        await provider.async_get_schedule(UPRN, POSTCODE)


async def test_provider_missing_address_paragraph(
    provider: RctCouncilProvider, aioclient_mock: AiohttpClientMocker
) -> None:
    html = load_fixture("schedule-page.html").replace(
        "17, Example Street, Exampletown, Pontyclun, CF99 9ZZ", ""
    )
    mock_schedule(aioclient_mock, text=html)
    # Tolerated during normal polling ...
    schedule = await provider.async_get_schedule(UPRN, POSTCODE)
    assert not schedule.address_verified
    # ... but refused when the caller needs proof of the property's identity.
    with pytest.raises(PropertyMismatchError):
        await provider.async_get_schedule(UPRN, POSTCODE, require_address_match=True)


async def test_provider_all_entries_invalid(
    provider: RctCouncilProvider, aioclient_mock: AiohttpClientMocker
) -> None:
    mock_schedule(aioclient_mock, "schedule-all-invalid.html")
    with pytest.raises(ParseError):
        await provider.async_get_schedule(UPRN, POSTCODE)


async def test_provider_some_entries_invalid(
    provider: RctCouncilProvider,
    aioclient_mock: AiohttpClientMocker,
    caplog: pytest.LogCaptureFixture,
) -> None:
    mock_schedule(aioclient_mock, "schedule-invalid-dates.html")
    schedule = await provider.async_get_schedule(UPRN, POSTCODE)
    assert date(2026, 11, 24) in {e.date for e in schedule.events}
    assert "Skipped 3 calendar entries" in caplog.text
    assert UPRN not in caplog.text
