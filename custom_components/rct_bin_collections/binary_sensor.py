"""Binary sensor reminders for RCT Bin Collections."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from homeassistant.components.binary_sensor import BinarySensorEntity, BinarySensorEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import RctBinConfigEntry, RctBinCoordinator
from .entity import RctBinEntity
from .models import CollectionEvent

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class RctReminderDescription(BinarySensorEntityDescription):
    """Describes a reminder covering a window of days from today."""

    #: Returns (first_day_offset, last_day_offset) relative to today, inclusive.
    window_fn: Callable[[RctBinCoordinator], tuple[int, int]]


REMINDERS: tuple[RctReminderDescription, ...] = (
    RctReminderDescription(
        key="collection_today",
        translation_key="collection_today",
        window_fn=lambda _: (0, 0),
    ),
    RctReminderDescription(
        key="collection_tomorrow",
        translation_key="collection_tomorrow",
        window_fn=lambda _: (1, 1),
    ),
    RctReminderDescription(
        key="collection_due_soon",
        translation_key="collection_due_soon",
        window_fn=lambda coordinator: (0, coordinator.reminder_days),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: RctBinConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up reminder binary sensors."""
    coordinator = entry.runtime_data
    async_add_entities(RctReminderSensor(coordinator, d) for d in REMINDERS)


class RctReminderSensor(RctBinEntity, BinarySensorEntity):
    """On when at least one collection falls inside the window.

    The state is ``unknown`` rather than ``off`` when the window extends past the
    published horizon and no collection was found inside it, because the absence of a
    published date is not confirmation that no collection will happen.
    """

    entity_description: RctReminderDescription

    def __init__(self, coordinator: RctBinCoordinator, description: RctReminderDescription) -> None:
        """Initialise the reminder."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    def _window(self) -> tuple[date, date]:
        start, end = self.entity_description.window_fn(self.coordinator)
        today = self.coordinator.today()
        return today + timedelta(days=start), today + timedelta(days=end)

    def _matches(self) -> list[CollectionEvent]:
        first, last = self._window()
        return [e for e in self.coordinator.upcoming() if first <= e.date <= last]

    @property
    def is_on(self) -> bool | None:
        """Return whether a collection is due within the window."""
        if self._matches():
            return True
        data = self.coordinator.data
        first, last = self._window()
        if not (data.schedule.covers(first) and data.schedule.covers(last)):
            return None
        return False

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the date and types of the first matching collection."""
        matches = self._matches()
        attrs = self._common_attributes()
        if not matches:
            attrs.update({"date": None, "collection_types": [], "days_until": None})
            return attrs
        day = matches[0].date
        attrs.update(
            {
                "date": day.isoformat(),
                "collection_types": [e.display_name for e in matches if e.date == day],
                "days_until": (day - self.coordinator.today()).days,
            }
        )
        return attrs
