"""The RCT Bin Collections integration.

Unofficial integration for Rhondda Cynon Taf County Borough Council waste and recycling
collection dates. Each config entry represents one property.
"""

from __future__ import annotations

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store

from .const import CONF_PROVIDER, DOMAIN, PROVIDER_RCT_COUNCIL, STORAGE_VERSION
from .coordinator import RctBinConfigEntry, RctBinCoordinator
from .providers import create_provider

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.CALENDAR, Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: RctBinConfigEntry) -> bool:
    """Set up one property from a config entry."""
    provider_id = entry.data.get(CONF_PROVIDER, PROVIDER_RCT_COUNCIL)
    try:
        provider = create_provider(provider_id, async_get_clientsession(hass))
    except KeyError as err:
        raise ConfigEntryError(
            translation_domain=DOMAIN,
            translation_key="unknown_provider",
            translation_placeholders={"provider": provider_id},
        ) from err

    coordinator = RctBinCoordinator(hass, entry, provider)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: RctBinConfigEntry) -> bool:
    """Unload a config entry. The coordinator shuts itself down on unload."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: RctBinConfigEntry) -> None:
    """Delete the cached schedule when a property is removed."""
    await Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}").async_remove()
