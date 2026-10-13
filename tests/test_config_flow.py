"""Tests for the config, reconfigure and options flows."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr, entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.rct_bin_collections.const import (
    CONF_EXCLUDED_CATEGORIES,
    CONF_INCLUDED_CATEGORIES,
    CONF_MANUAL_UPRN,
    CONF_POSTCODE,
    CONF_PROPERTY_NAME,
    CONF_PROVIDER,
    CONF_REMINDER_DAYS,
    CONF_SELECTED_ADDRESS,
    CONF_UPDATE_INTERVAL,
    CONF_UPRN,
    DOMAIN,
)

from .conftest import (
    OTHER_UPRN,
    POSTCODE,
    UPRN,
    load_fixture,
    make_entry,
    mock_addresses,
    mock_schedule,
)


async def _start(hass: HomeAssistant, **user_input: Any) -> dict[str, Any]:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    return await hass.config_entries.flow.async_configure(result["flow_id"], user_input)


@pytest.mark.parametrize("raw_postcode", ["CF99 9ZZ", "cf99 9zz", "  cf999zz ", "CF99\t9ZZ"])
async def test_full_flow_with_address_list(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, raw_postcode: str
) -> None:
    mock_addresses(aioclient_mock)
    mock_schedule(aioclient_mock, uprn=OTHER_UPRN)

    result = await _start(hass, postcode=raw_postcode, property_name="  Home  ")
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "select_address"
    assert result["description_placeholders"] == {"postcode": POSTCODE, "count": "3"}
    options = result["data_schema"].schema[CONF_SELECTED_ADDRESS].config["options"]
    assert [o["value"] for o in options] == ["999000000001", OTHER_UPRN, "999000000003"]
    assert options[2]["label"].startswith("Flat 1, 3 Example Street")

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_SELECTED_ADDRESS: OTHER_UPRN}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Home"
    assert result["data"] == {
        CONF_UPRN: OTHER_UPRN,
        CONF_POSTCODE: POSTCODE,
        CONF_PROVIDER: "rct_council",
    }
    assert result["options"] == {
        CONF_UPDATE_INTERVAL: 12,
        CONF_REMINDER_DAYS: 1,
        CONF_EXCLUDED_CATEGORIES: [],
    }
    assert result["result"].unique_id == OTHER_UPRN
    await hass.async_block_till_done()
    # The schedule fetched by the flow seeds the coordinator: one address + one schedule call.
    assert aioclient_mock.call_count == 2
    assert hass.states.get("sensor.rct_bins_home_next_collection").state == "2026-10-13"


async def test_single_address_still_asks_for_confirmation(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    mock_addresses(aioclient_mock, "addresses-single.html")
    mock_schedule(aioclient_mock)
    result = await _start(hass, postcode=POSTCODE)
    assert result["step_id"] == "select_address"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_SELECTED_ADDRESS: UPRN}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Home"


@pytest.mark.parametrize("postcode", ["", "not a postcode", "CF99", "12345"])
async def test_invalid_postcode(hass: HomeAssistant, postcode: str) -> None:
    result = await _start(hass, postcode=postcode)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_POSTCODE: "invalid_postcode"}


@pytest.mark.parametrize(
    ("mock_kwargs", "error"),
    [
        ({"fixture": "addresses-none.html"}, "postcode_not_found"),
        ({"fixture": "not-lookup-page.html"}, "parse_error"),
        ({"status": 503}, "cannot_connect"),
        ({"status": 429, "headers": {"Retry-After": "600"}}, "rate_limited"),
        ({"status": 302}, "unsupported_response"),
        ({"exc": TimeoutError()}, "cannot_connect"),
    ],
)
async def test_address_lookup_errors(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_kwargs: dict[str, Any],
    error: str,
) -> None:
    mock_addresses(aioclient_mock, **mock_kwargs)
    result = await _start(hass, postcode=POSTCODE)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": error}


async def test_unexpected_error_is_logged(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    with patch(
        "custom_components.rct_bin_collections.providers.rct_council.RctCouncilProvider"
        ".async_find_properties",
        side_effect=RuntimeError("bug"),
    ):
        result = await _start(hass, postcode=POSTCODE)
    assert result["errors"] == {"base": "unknown"}
    assert "Unexpected error" in caplog.text


@pytest.mark.parametrize(
    ("schedule_kwargs", "error"),
    [
        ({"fixture": "schedule-empty.html"}, "no_collections"),
        ({"fixture": "schedule-unknown-property.html"}, "property_not_found"),
        ({"fixture": "schedule-changed-structure.html"}, "parse_error"),
        ({"fixture": None, "status": 500}, "cannot_connect"),
        ({"fixture": "schedule-monthly-only.html"}, "no_future_collections"),
    ],
)
async def test_schedule_validation_errors(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    schedule_kwargs: dict[str, Any],
    error: str,
    freezer: Any,
) -> None:
    if error == "no_future_collections":
        freezer.move_to("2026-10-28 09:00:00+00:00")
    mock_addresses(aioclient_mock)
    mock_schedule(aioclient_mock, uprn=OTHER_UPRN, **schedule_kwargs)
    result = await _start(hass, postcode=POSTCODE)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_SELECTED_ADDRESS: OTHER_UPRN}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "select_address"
    assert result["errors"] == {"base": error}


async def test_selected_uprn_not_in_list(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    mock_addresses(aioclient_mock)
    result = await _start(hass, postcode=POSTCODE)
    with patch.object(hass.config_entries.flow._progress[result["flow_id"]], "_properties", []):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_SELECTED_ADDRESS: OTHER_UPRN}
        )
    assert result["errors"] == {"base": "property_not_found"}


async def test_manual_uprn(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    mock_schedule(aioclient_mock)
    result = await _start(
        hass, postcode=POSTCODE, property_name="Rental", **{CONF_MANUAL_UPRN: True}
    )
    assert result["step_id"] == "manual_uprn"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_UPRN: "not-a-uprn"}
    )
    assert result["errors"] == {CONF_UPRN: "invalid_uprn"}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_UPRN: f" {UPRN} "}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Rental"
    assert result["data"][CONF_UPRN] == UPRN


async def test_manual_uprn_requires_matching_postcode(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    mock_schedule(aioclient_mock)
    result = await _start(hass, postcode="CF37 1AA", **{CONF_MANUAL_UPRN: True})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_UPRN: UPRN})
    assert result["errors"] == {"base": "property_mismatch"}


async def test_manual_uprn_requires_address_in_response(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    html = load_fixture("schedule-page.html").replace(
        "17, Example Street, Exampletown, Pontyclun, CF99 9ZZ", ""
    )
    mock_schedule(aioclient_mock, text=html)
    result = await _start(hass, postcode=POSTCODE, **{CONF_MANUAL_UPRN: True})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_UPRN: UPRN})
    assert result["errors"] == {"base": "property_mismatch"}


async def test_duplicate_property_aborts(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    make_entry().add_to_hass(hass)
    mock_addresses(aioclient_mock, "addresses-single.html")
    result = await _start(hass, postcode=POSTCODE)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_SELECTED_ADDRESS: UPRN}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_empty_property_name_defaults_to_home(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    mock_schedule(aioclient_mock)
    result = await _start(hass, postcode=POSTCODE, property_name="   ", **{CONF_MANUAL_UPRN: True})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_UPRN: UPRN})
    assert result["title"] == "Home"


# --------------------------------------------------------------------------------------
# Reconfigure
# --------------------------------------------------------------------------------------


async def test_reconfigure_to_new_property_keeps_entities(
    hass: HomeAssistant, loaded_entry: MockConfigEntry, aioclient_mock: AiohttpClientMocker
) -> None:
    entity_registry = er.async_get(hass)
    entity_id = "sensor.rct_bins_home_next_collection"
    assert entity_registry.async_get(entity_id).unique_id == f"{UPRN}_next_collection"

    mock_addresses(aioclient_mock)
    mock_schedule(aioclient_mock, "schedule-year-boundary.html", uprn=OTHER_UPRN)

    result = await loaded_entry.start_reconfigure_flow(hass)
    assert result["step_id"] == "reconfigure"
    assert CONF_PROPERTY_NAME not in result["data_schema"].schema
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_POSTCODE: POSTCODE}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_SELECTED_ADDRESS: OTHER_UPRN}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    await hass.async_block_till_done()

    assert loaded_entry.unique_id == OTHER_UPRN
    assert loaded_entry.data[CONF_UPRN] == OTHER_UPRN
    assert entity_registry.async_get(entity_id).unique_id == f"{OTHER_UPRN}_next_collection"
    assert hass.states.get(entity_id).state == "2026-12-04"
    device_registry = dr.async_get(hass)
    assert device_registry.async_get_device_by_identifier(
        (DOMAIN, OTHER_UPRN), loaded_entry.entry_id
    )
    assert not device_registry.async_get_device_by_identifier((DOMAIN, UPRN), loaded_entry.entry_id)


async def test_reconfigure_same_property(
    hass: HomeAssistant, loaded_entry: MockConfigEntry, aioclient_mock: AiohttpClientMocker
) -> None:
    mock_addresses(aioclient_mock, "addresses-single.html")
    result = await loaded_entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_POSTCODE: POSTCODE}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_SELECTED_ADDRESS: UPRN}
    )
    assert result["reason"] == "reconfigure_successful"
    assert loaded_entry.unique_id == UPRN


async def test_reconfigure_to_property_owned_by_another_entry(
    hass: HomeAssistant, loaded_entry: MockConfigEntry, aioclient_mock: AiohttpClientMocker
) -> None:
    make_entry(OTHER_UPRN, name="Other").add_to_hass(hass)
    mock_addresses(aioclient_mock)
    result = await loaded_entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_POSTCODE: POSTCODE}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_SELECTED_ADDRESS: OTHER_UPRN}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert loaded_entry.unique_id == UPRN


# --------------------------------------------------------------------------------------
# Options
# --------------------------------------------------------------------------------------


async def test_options_flow(hass: HomeAssistant, loaded_entry: MockConfigEntry) -> None:
    result = await hass.config_entries.options.async_init(loaded_entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    schema = result["data_schema"].schema
    categories = [o["value"] for o in schema[CONF_INCLUDED_CATEGORIES].config["options"]]
    assert categories == ["food_waste", "garden_waste", "general_waste", "recycling"]

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_PROPERTY_NAME: "Cottage",
            CONF_UPDATE_INTERVAL: "24",
            CONF_REMINDER_DAYS: 2,
            CONF_INCLUDED_CATEGORIES: ["food_waste", "general_waste", "recycling"],
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert loaded_entry.options == {
        CONF_UPDATE_INTERVAL: 24,
        CONF_REMINDER_DAYS: 2,
        CONF_EXCLUDED_CATEGORIES: ["garden_waste"],
    }
    assert loaded_entry.title == "Cottage"
    await hass.async_block_till_done()

    coordinator = loaded_entry.runtime_data
    assert coordinator.update_interval.total_seconds() == 24 * 3600
    assert coordinator.reminder_days == 2
    assert hass.states.get("sensor.rct_bins_home_garden_waste_next_collection") is None
    assert hass.states.get("sensor.rct_bins_home_collection_summary").state == (
        "Recycling and Food Waste"
    )


async def test_options_flow_when_not_loaded(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    mock_schedule(aioclient_mock)
    entry = make_entry(options={CONF_EXCLUDED_CATEGORIES: ["garden_waste"]})
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    categories = result["data_schema"].schema[CONF_INCLUDED_CATEGORIES].config["options"]
    assert categories == [{"value": "garden_waste", "label": "Garden Waste"}]
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_PROPERTY_NAME: "", CONF_UPDATE_INTERVAL: "6", CONF_REMINDER_DAYS: 0},
    )
    assert entry.title == "Home"
    assert CONF_PROPERTY_NAME not in entry.options
    # Leaving the category list untouched keeps the existing exclusions.
    assert entry.options[CONF_EXCLUDED_CATEGORIES] == ["garden_waste"]
    await hass.async_block_till_done()


async def test_options_flow_without_categories(hass: HomeAssistant) -> None:
    entry = make_entry()
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert CONF_INCLUDED_CATEGORIES not in result["data_schema"].schema


async def test_options_offer_registered_categories(
    hass: HomeAssistant, setup_integration: Any
) -> None:
    """An out-of-season category with an existing sensor can still be hidden."""
    entry = make_entry()
    entry.add_to_hass(hass)
    er.async_get(hass).async_get_or_create(
        "sensor", DOMAIN, f"{UPRN}_category_other_bank_holiday", config_entry=entry
    )
    await setup_integration(entry)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    values = [
        o["value"] for o in result["data_schema"].schema[CONF_INCLUDED_CATEGORIES].config["options"]
    ]
    assert "other_bank_holiday" in values
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_PROPERTY_NAME: "Home",
            CONF_UPDATE_INTERVAL: "12",
            CONF_REMINDER_DAYS: 1,
            CONF_INCLUDED_CATEGORIES: [v for v in values if v != "other_bank_holiday"],
        },
    )
    await hass.async_block_till_done()
    assert entry.options[CONF_EXCLUDED_CATEGORIES] == ["other_bank_holiday"]
    assert (
        er.async_get(hass).async_get_entity_id(
            "sensor", DOMAIN, f"{UPRN}_category_other_bank_holiday"
        )
        is None
    )


async def test_title_is_the_property_name(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """Renaming the entry in the UI is reflected and not undone by the options flow."""
    hass.config_entries.async_update_entry(loaded_entry, title="Flat")
    result = await hass.config_entries.options.async_init(loaded_entry.entry_id)
    defaults = {
        str(k): k.default()
        for k in result["data_schema"].schema
        if callable(getattr(k, "default", None))
    }
    assert defaults[CONF_PROPERTY_NAME] == "Flat"
    await hass.config_entries.async_reload(loaded_entry.entry_id)
    await hass.async_block_till_done()
    state = hass.states.get("sensor.rct_bins_home_next_collection")
    assert state.attributes["property_name"] == "Flat"
