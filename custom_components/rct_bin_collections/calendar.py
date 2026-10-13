"""Calendar entity for RCT Bin Collections."""

from __future__ import annotations

from datetime import datetime, timedelta

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .coordinator import RctBinConfigEntry, RctBinCoordinator, property_fingerprint
from .entity import RctBinEntity
from .models import CollectionEvent

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: RctBinConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the collection calendar."""
    async_add_entities([RctBinCalendar(entry.runtime_data)])


class RctBinCalendar(RctBinEntity, CalendarEntity):
    """One all-day event per published collection type and date."""

    _attr_name = None
    _attr_translation_key = "collections"

    def __init__(self, coordinator: RctBinCoordinator) -> None:
        """Initialise the calendar."""
        super().__init__(coordinator, "calendar")
        self._uid_prefix = property_fingerprint(coordinator.uprn)

    def _to_calendar_event(self, event: CollectionEvent) -> CalendarEvent:
        return CalendarEvent(
            start=event.date,
            end=event.date + timedelta(days=1),
            summary=f"{event.display_name} Collection",
            description=f"{event.label} (published by Rhondda Cynon Taf Council)",
            uid=f"{self._uid_prefix}-{event.date:%Y%m%d}-{event.category}",
        )

    @property
    def event(self) -> CalendarEvent | None:
        """Return today's or the next upcoming collection."""
        upcoming = self.coordinator.upcoming()
        return self._to_calendar_event(upcoming[0]) if upcoming else None

    async def async_get_events(
        self, hass: HomeAssistant, start_date: datetime, end_date: datetime
    ) -> list[CalendarEvent]:
        """Return events overlapping ``[start_date, end_date)``.

        Events earlier in the published horizon are included, so the calendar view shows
        the whole current month. Nothing beyond the published horizon is invented.
        """
        data = self.coordinator.data
        visible = set(self.coordinator.visible_categories())
        events: list[CalendarEvent] = []
        for event in data.schedule.events:
            if event.category not in visible:
                continue
            day_start = dt_util.start_of_local_day(event.date)
            day_end = dt_util.start_of_local_day(event.date + timedelta(days=1))
            if day_start < end_date and day_end > start_date:
                events.append(self._to_calendar_event(event))
        return events
