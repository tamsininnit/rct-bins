"""Diagnostics support for RCT Bin Collections."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant
from homeassistant.loader import async_get_integration

from .const import CONF_POSTCODE, CONF_PROPERTY_NAME, CONF_UPRN, DOMAIN
from .coordinator import RctBinConfigEntry

TO_REDACT = {CONF_UPRN, CONF_POSTCODE, CONF_PROPERTY_NAME, "title", "unique_id"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: RctBinConfigEntry
) -> dict[str, Any]:
    """Return redacted diagnostics for a config entry."""
    coordinator = entry.runtime_data
    integration = await async_get_integration(hass, DOMAIN)
    data = coordinator.data
    schedule = data.schedule
    future = schedule.upcoming(coordinator.today())

    return {
        "integration_version": str(integration.version),
        "provider": coordinator.provider.provider_id,
        "entry": async_redact_data(
            {
                "title": entry.title,
                "unique_id": entry.unique_id,
                "data": dict(entry.data),
                "options": dict(entry.options),
            },
            TO_REDACT,
        ),
        "coordinator": {
            "last_update_success": coordinator.last_update_success,
            "update_interval_seconds": (
                coordinator.update_interval.total_seconds() if coordinator.update_interval else None
            ),
            "last_exception": (
                type(coordinator.last_exception).__name__ if coordinator.last_exception else None
            ),
            "last_http_status": getattr(coordinator.provider, "last_status", None),
        },
        "schedule": {
            "source": schedule.source,
            "fetched_at": schedule.fetched_at.isoformat(),
            "stale": data.stale,
            "last_error": data.last_error,
            "address_verified": schedule.address_verified,
            "horizon_start": schedule.horizon_start.isoformat(),
            "horizon_end": schedule.horizon_end.isoformat(),
            "total_events": len(schedule.events),
            "future_events": len(future),
            "category_count": len(schedule.categories_seen),
            "categories": sorted(schedule.categories_seen),
            "source_labels": sorted({e.label for e in schedule.events}),
            "next_dates": sorted({e.date.isoformat() for e in future})[:5],
        },
    }
