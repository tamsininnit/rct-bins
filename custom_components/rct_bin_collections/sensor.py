"""Sensor entities for RCT Bin Collections."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorEntityDescription
from homeassistant.const import EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import (
    MAX_FOLLOWING_COLLECTIONS_IN_ATTRIBUTES,
    MAX_STATE_LENGTH,
    MAX_UPCOMING_DATES_IN_ATTRIBUTES,
)
from .coordinator import RctBinConfigEntry, RctBinCoordinator
from .entity import RctBinEntity
from .models import (
    KNOWN_CATEGORIES,
    CollectionEvent,
    category_display_name,
    group_by_date,
    join_names,
)

PARALLEL_UPDATES = 0

CATEGORY_KEY_PREFIX = "category_"


@dataclass(frozen=True, kw_only=True)
class RctSummarySensorDescription(SensorEntityDescription):
    """Describes a property-level sensor."""

    value_fn: Callable[[RctBinCoordinator], date | datetime | int | str | None]
    attrs_fn: Callable[[RctBinCoordinator], dict[str, Any]] | None = None


def _next_group(coordinator: RctBinCoordinator) -> tuple[date, list[CollectionEvent]] | None:
    groups = group_by_date(coordinator.upcoming())
    return groups[0] if groups else None


def _days_until(coordinator: RctBinCoordinator, day: date) -> int:
    return (day - coordinator.today()).days


def _next_date(coordinator: RctBinCoordinator) -> date | None:
    group = _next_group(coordinator)
    return group[0] if group else None


def _next_attrs(coordinator: RctBinCoordinator) -> dict[str, Any]:
    group = _next_group(coordinator)
    if group is None:
        return {"collection_types": [], "days_until": None}
    day, events = group
    return {
        "collection_types": [e.display_name for e in events],
        "days_until": _days_until(coordinator, day),
    }


def _days_until_next(coordinator: RctBinCoordinator) -> int | None:
    day = _next_date(coordinator)
    return None if day is None else _days_until(coordinator, day)


def _days_until_attrs(coordinator: RctBinCoordinator) -> dict[str, Any]:
    day = _next_date(coordinator)
    return {"next_date": day.isoformat() if day else None}


def _summary(coordinator: RctBinCoordinator) -> str | None:
    group = _next_group(coordinator)
    if group is None:
        return None
    text = join_names(e.display_name for e in group[1])
    return text if len(text) <= MAX_STATE_LENGTH else f"{text[: MAX_STATE_LENGTH - 1]}…"


def _summary_attrs(coordinator: RctBinCoordinator) -> dict[str, Any]:
    groups = group_by_date(coordinator.upcoming())
    if not groups:
        return {"next_date": None, "days_until": None, "following_collections": []}
    day = groups[0][0]
    return {
        "next_date": day.isoformat(),
        "days_until": _days_until(coordinator, day),
        "following_collections": [
            {"date": d.isoformat(), "types": [e.display_name for e in events]}
            for d, events in groups[1 : 1 + MAX_FOLLOWING_COLLECTIONS_IN_ATTRIBUTES]
        ],
    }


def _last_success(coordinator: RctBinCoordinator) -> datetime:
    return coordinator.data.last_success


def _last_success_attrs(coordinator: RctBinCoordinator) -> dict[str, Any]:
    data = coordinator.data
    return {
        "data_stale": data.stale,
        "last_error": data.last_error,
        "published_from": data.schedule.horizon_start.isoformat(),
        "published_until": data.schedule.horizon_end.isoformat(),
        "address_verified": data.schedule.address_verified,
    }


SUMMARY_SENSORS: tuple[RctSummarySensorDescription, ...] = (
    RctSummarySensorDescription(
        key="next_collection",
        translation_key="next_collection",
        device_class=SensorDeviceClass.DATE,
        value_fn=_next_date,
        attrs_fn=_next_attrs,
    ),
    RctSummarySensorDescription(
        key="days_until_next_collection",
        translation_key="days_until_next_collection",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.DAYS,
        suggested_display_precision=0,
        value_fn=_days_until_next,
        attrs_fn=_days_until_attrs,
    ),
    RctSummarySensorDescription(
        key="collection_summary",
        translation_key="collection_summary",
        value_fn=_summary,
        attrs_fn=_summary_attrs,
    ),
    RctSummarySensorDescription(
        key="last_successful_update",
        translation_key="last_successful_update",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_last_success,
        attrs_fn=_last_success_attrs,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: RctBinConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up sensors for a configured property."""
    coordinator = entry.runtime_data
    async_add_entities(RctSummarySensor(coordinator, d) for d in SUMMARY_SENSORS)

    registry = er.async_get(hass)
    prefix = f"{coordinator.uprn}_{CATEGORY_KEY_PREFIX}"
    known: set[str] = set()

    # Categories seen before (e.g. seasonal garden waste) keep their entity even when the
    # current horizon has no dates for them. Excluded categories are removed outright.
    for reg_entry in er.async_entries_for_config_entry(registry, entry.entry_id):
        if reg_entry.domain != "sensor" or not reg_entry.unique_id.startswith(prefix):
            continue
        category = reg_entry.unique_id.removeprefix(prefix)
        if category in coordinator.excluded_categories:
            registry.async_remove(reg_entry.entity_id)
        else:
            known.add(category)

    @callback
    def _async_add_new_categories() -> None:
        current = set(coordinator.visible_categories())
        new = sorted((current | known) - created)
        if new:
            created.update(new)
            async_add_entities(RctCategorySensor(coordinator, c) for c in new)

    created: set[str] = set()
    _async_add_new_categories()
    entry.async_on_unload(coordinator.async_add_listener(_async_add_new_categories))


class RctSummarySensor(RctBinEntity, SensorEntity):
    """A property-level sensor derived from the shared schedule."""

    entity_description: RctSummarySensorDescription

    def __init__(
        self, coordinator: RctBinCoordinator, description: RctSummarySensorDescription
    ) -> None:
        """Initialise the sensor."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> date | datetime | int | str | None:
        """Return the current value."""
        return self.entity_description.value_fn(self.coordinator)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra attributes."""
        attrs = self._common_attributes()
        if self.entity_description.attrs_fn is not None:
            attrs.update(self.entity_description.attrs_fn(self.coordinator))
        return attrs


class RctCategorySensor(RctBinEntity, SensorEntity):
    """Next collection date for one waste category."""

    _attr_device_class = SensorDeviceClass.DATE

    def __init__(self, coordinator: RctBinCoordinator, category: str) -> None:
        """Initialise the sensor."""
        super().__init__(coordinator, f"{CATEGORY_KEY_PREFIX}{category}")
        self.category = category
        if category in KNOWN_CATEGORIES:
            self._attr_translation_key = f"{category}_next_collection"
        else:
            self._attr_translation_key = "other_next_collection"
            self._attr_translation_placeholders = {
                "category": category_display_name(category, self._source_label())
            }

    def _source_label(self) -> str | None:
        events = self.coordinator.data.schedule.events
        return next((e.label for e in events if e.category == self.category), None)

    def _upcoming(self) -> list[CollectionEvent]:
        return self.coordinator.upcoming({self.category})

    @property
    def native_value(self) -> date | None:
        """Return the next collection date, or None if none is published."""
        upcoming = self._upcoming()
        return upcoming[0].date if upcoming else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra attributes."""
        upcoming = self._upcoming()
        attrs = self._common_attributes()
        attrs.update(
            {
                "category": self.category,
                "description": self._source_label(),
                "days_until": (
                    (upcoming[0].date - self.coordinator.today()).days if upcoming else None
                ),
                "upcoming_dates": [
                    e.date.isoformat() for e in upcoming[:MAX_UPCOMING_DATES_IN_ATTRIBUTES]
                ],
                "last_successful_update": self.coordinator.data.last_success.isoformat(),
            }
        )
        return attrs
