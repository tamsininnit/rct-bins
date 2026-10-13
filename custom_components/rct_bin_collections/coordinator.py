"""Data update coordinator for one configured property."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date, datetime, timedelta
import hashlib
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryError
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import (
    CONF_EXCLUDED_CATEGORIES,
    CONF_POSTCODE,
    CONF_REMINDER_DAYS,
    CONF_UPDATE_INTERVAL,
    CONF_UPRN,
    DEFAULT_REMINDER_DAYS,
    DEFAULT_UPDATE_INTERVAL_HOURS,
    DOMAIN,
    MANUAL_REFRESH_COOLDOWN_SECONDS,
    MAX_STALE_AGE,
    MIN_FETCH_INTERVAL,
    RETRY_AFTER_FAILURE,
    STORAGE_VERSION,
)
from .exceptions import PropertyMismatchError, RateLimitedError, RctBinError
from .models import CollectionEvent, CollectionSchedule
from .providers import CollectionProvider

_LOGGER = logging.getLogger(__name__)

type RctBinConfigEntry = ConfigEntry[RctBinCoordinator]


@dataclass(frozen=True, slots=True)
class CoordinatorData:
    """Snapshot shared with entities.

    ``stale`` is True when ``schedule`` comes from an earlier successful fetch because the
    latest attempt failed. ``last_error`` is a translation key describing that failure.
    """

    schedule: CollectionSchedule
    stale: bool = False
    last_error: str | None = None

    @property
    def last_success(self) -> datetime:
        """Return when the schedule was last fetched successfully."""
        return self.schedule.fetched_at


def property_fingerprint(uprn: str) -> str:
    """Return a non-reversible tag used to tie cached data to a property."""
    return hashlib.sha256(f"{DOMAIN}:{uprn}".encode()).hexdigest()[:16]


class RctBinCoordinator(DataUpdateCoordinator[CoordinatorData]):
    """Fetch, cache and share one property's collection schedule."""

    config_entry: RctBinConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: RctBinConfigEntry,
        provider: CollectionProvider,
    ) -> None:
        """Initialise the coordinator."""
        self._normal_interval = timedelta(
            hours=entry.options.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL_HOURS)
        )
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {entry.title}",
            update_interval=self._normal_interval,
            request_refresh_debouncer=Debouncer(
                hass,
                _LOGGER,
                cooldown=MANUAL_REFRESH_COOLDOWN_SECONDS,
                immediate=True,
            ),
        )
        self.provider = provider
        self.uprn: str = entry.data[CONF_UPRN]
        self.postcode: str | None = entry.data.get(CONF_POSTCODE)
        self.store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}", private=True, atomic_writes=True
        )
        self._fetch_task: asyncio.Task[CollectionSchedule] | None = None
        self._unsub_midnight: CALLBACK_TYPE | None = None

    # ----------------------------------------------------------------------------------
    # Options-derived settings
    # ----------------------------------------------------------------------------------

    @property
    def excluded_categories(self) -> frozenset[str]:
        """Return categories the user has chosen to hide."""
        return frozenset(self.config_entry.options.get(CONF_EXCLUDED_CATEGORIES, ()))

    @property
    def reminder_days(self) -> int:
        """Return the reminder window in days."""
        return int(self.config_entry.options.get(CONF_REMINDER_DAYS, DEFAULT_REMINDER_DAYS))

    # ----------------------------------------------------------------------------------
    # Derived views used by entities
    # ----------------------------------------------------------------------------------

    @staticmethod
    def today() -> date:
        """Return today's date in Home Assistant's configured time zone."""
        return dt_util.now().date()

    def visible_categories(self) -> list[str]:
        """Return categories present in the schedule that are not excluded, sorted."""
        return sorted(self.data.schedule.categories_seen - self.excluded_categories)

    def upcoming(self, categories: set[str] | None = None) -> list[CollectionEvent]:
        """Return future-or-today events for visible categories (or a given subset)."""
        wanted = set(self.visible_categories()) if categories is None else categories
        return self.data.schedule.upcoming(self.today(), wanted)

    # ----------------------------------------------------------------------------------
    # Lifecycle
    # ----------------------------------------------------------------------------------

    async def _async_setup(self) -> None:
        """Load the cached schedule and start the local-midnight tick.

        A schedule fetched within the last few minutes (by the config flow, or before a reload
        or restart) counts as fresh, so the first refresh does not hit the council again.
        """
        seeded: CollectionSchedule | None = self.hass.data.get(DOMAIN, {}).pop(self.uprn, None)
        if seeded is not None and self._is_recent(seeded):
            self.data = CoordinatorData(schedule=seeded)
            await self._async_save_cache(seeded)
        elif (cached := await self._async_load_cache()) is not None:
            self.data = CoordinatorData(schedule=cached, stale=not self._is_recent(cached))
        self._unsub_midnight = async_track_time_change(
            self.hass, self._async_handle_midnight, hour=0, minute=0, second=0
        )

    async def async_shutdown(self) -> None:
        """Cancel timers and any in-flight fetch."""
        if self._unsub_midnight is not None:
            self._unsub_midnight()
            self._unsub_midnight = None
        if self._fetch_task is not None and not self._fetch_task.done():
            self._fetch_task.cancel()
        await super().async_shutdown()

    @staticmethod
    def _is_recent(schedule: CollectionSchedule) -> bool:
        return dt_util.utcnow() - schedule.fetched_at < MIN_FETCH_INTERVAL

    @callback
    def _async_handle_midnight(self, _now: datetime) -> None:
        """Recalculate date-relative entity states when the local date changes."""
        if self.data is not None:
            self.async_update_listeners()

    # ----------------------------------------------------------------------------------
    # Fetching
    # ----------------------------------------------------------------------------------

    async def _async_update_data(self) -> CoordinatorData:
        """Fetch a fresh schedule, falling back to the last good one on transient errors."""
        previous = self.data
        if previous is not None and not previous.stale and self._is_recent(previous.schedule):
            _LOGGER.debug("Skipping fetch; last successful fetch was under 5 minutes ago")
            return previous

        try:
            schedule = await self._async_fetch_once()
        except PropertyMismatchError as err:
            # The council answered for a different place: never show those dates here.
            raise ConfigEntryError(
                translation_domain=DOMAIN, translation_key=err.translation_key
            ) from err
        except RctBinError as err:
            return self._handle_fetch_failure(previous, err)

        self.update_interval = self._normal_interval
        await self._async_save_cache(schedule)
        return CoordinatorData(schedule=schedule)

    async def _async_fetch_once(self) -> CollectionSchedule:
        """Fetch the schedule, sharing one in-flight request between concurrent callers."""
        if self._fetch_task is None or self._fetch_task.done():
            self._fetch_task = self.hass.async_create_task(
                self.provider.async_get_schedule(self.uprn, self.postcode),
                f"{DOMAIN} fetch {self.config_entry.entry_id}",
                eager_start=True,
            )
        return await asyncio.shield(self._fetch_task)

    def _handle_fetch_failure(
        self, previous: CoordinatorData | None, err: RctBinError
    ) -> CoordinatorData:
        """Keep serving recent data after a transient failure, or raise UpdateFailed."""
        retry_after = err.retry_after if isinstance(err, RateLimitedError) else None
        retry_seconds = max(retry_after or 0, RETRY_AFTER_FAILURE.total_seconds())
        self.update_interval = timedelta(
            seconds=min(retry_seconds, self._normal_interval.total_seconds())
        )

        if previous is not None and (dt_util.utcnow() - previous.last_success <= MAX_STALE_AGE):
            # Warn once when data first goes stale; repeat failures are logged at debug level.
            _LOGGER.log(
                logging.DEBUG if previous.stale else logging.WARNING,
                "Could not refresh collection schedule (%s); keeping data from %s",
                err,
                previous.last_success.isoformat(timespec="minutes"),
            )
            return CoordinatorData(
                schedule=previous.schedule, stale=True, last_error=err.translation_key
            )

        raise UpdateFailed(
            translation_domain=DOMAIN,
            translation_key=err.translation_key,
            retry_after=retry_after,
        ) from err

    # ----------------------------------------------------------------------------------
    # Cache
    # ----------------------------------------------------------------------------------

    async def _async_save_cache(self, schedule: CollectionSchedule) -> None:
        """Persist a validated schedule (atomic write, no address or UPRN stored)."""
        await self.store.async_save(
            {"property": property_fingerprint(self.uprn), "schedule": schedule.as_cache()}
        )

    async def _async_load_cache(self) -> CollectionSchedule | None:
        """Return the cached schedule if it is valid, recent and for this property."""
        try:
            raw = await self.store.async_load()
        except Exception:  # noqa: BLE001 - a corrupt cache must never block setup
            _LOGGER.warning("Ignoring unreadable collection cache")
            return None
        if not raw:
            return None
        if raw.get("property") != property_fingerprint(self.uprn):
            _LOGGER.debug("Ignoring cache belonging to a different property")
            return None
        try:
            schedule = CollectionSchedule.from_cache(raw["schedule"])
        except KeyError, TypeError, ValueError:
            _LOGGER.warning("Ignoring malformed collection cache")
            return None
        if dt_util.utcnow() - schedule.fetched_at > MAX_STALE_AGE:
            _LOGGER.debug("Ignoring cache older than %s", MAX_STALE_AGE)
            return None
        return schedule
