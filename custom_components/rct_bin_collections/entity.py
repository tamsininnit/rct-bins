"""Base entity shared by all platforms."""

from __future__ import annotations

from typing import Any

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DEFAULT_PROPERTY_NAME, DOMAIN
from .coordinator import RctBinCoordinator
from .providers.rct_council import LOOKUP_URL


def property_name(coordinator: RctBinCoordinator) -> str:
    """Return the user's friendly name for the property (the config entry title)."""
    return coordinator.config_entry.title or DEFAULT_PROPERTY_NAME


class RctBinEntity(CoordinatorEntity[RctBinCoordinator]):
    """An entity attached to one property's device.

    Unique IDs are ``<uprn>_<key>``: stable across restarts and option changes, and unique
    per property. The UPRN never appears in entity attributes.
    """

    _attr_has_entity_name = True

    def __init__(self, coordinator: RctBinCoordinator, key: str) -> None:
        """Initialise the entity."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.uprn}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.uprn)},
            name=f"RCT Bins ({property_name(coordinator)})",
            manufacturer="Rhondda Cynon Taf County Borough Council",
            model="Waste collection calendar (unofficial)",
            entry_type=DeviceEntryType.SERVICE,
            configuration_url=str(LOOKUP_URL),
        )

    def _common_attributes(self) -> dict[str, Any]:
        """Return attributes shared by summary-style entities."""
        data = self.coordinator.data
        return {
            "property_name": property_name(self.coordinator),
            "source": data.schedule.source,
            "data_stale": data.stale,
            "published_until": data.schedule.horizon_end.isoformat(),
        }
