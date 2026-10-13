"""Provider for the Rhondda Cynon Taf Council collection day lookup page."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
import logging
from typing import ClassVar

import aiohttp
from yarl import URL

from ..api import HttpClient
from ..const import PROVIDER_RCT_COUNCIL
from ..exceptions import ParseError, PropertyMismatchError
from ..models import CollectionSchedule, Property
from .base import CollectionProvider, ProviderCapability
from .rct_council_parser import parse_address_list, parse_schedule

_LOGGER = logging.getLogger(__name__)

LOOKUP_URL = URL(
    "https://www.rctcbc.gov.uk/EN/Resident/RecyclingandWasteServices/"
    "Findyourrecyclingandwastecollectionday.aspx"
)


class RctCouncilProvider(CollectionProvider):
    """Scrape the council's own "Find your recycling and waste collection day" page."""

    provider_id: ClassVar[str] = PROVIDER_RCT_COUNCIL
    name: ClassVar[str] = "Rhondda Cynon Taf Council website"
    capabilities: ClassVar[frozenset[ProviderCapability]] = frozenset(
        {ProviderCapability.POSTCODE_LOOKUP, ProviderCapability.ADDRESS_VERIFICATION}
    )

    def __init__(
        self,
        session: aiohttp.ClientSession,
        *,
        client: HttpClient | None = None,
        utcnow: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        """Initialise with a shared aiohttp session."""
        self._client = client or HttpClient(session)
        self._utcnow = utcnow

    @property
    def last_status(self) -> int | None:
        """Return the HTTP status of the most recent response."""
        return self._client.last_status

    async def async_find_properties(self, postcode: str) -> list[Property]:
        """Return the council's address list for a canonical postcode."""
        html = await self._client.get_text(LOOKUP_URL, {"PropertyNumber": "", "Postcode": postcode})
        properties = await _run_parser(parse_address_list, html)
        _LOGGER.debug("Postcode lookup returned %d properties", len(properties))
        return properties

    async def async_get_schedule(
        self,
        uprn: str,
        postcode: str | None,
        *,
        require_address_match: bool = False,
    ) -> CollectionSchedule:
        """Fetch the printable calendar for a UPRN and validate it."""
        html = await self._client.get_text(LOOKUP_URL, {"uprn": uprn, "month": "0"})
        parsed = await _run_parser(parse_schedule, html)

        if parsed.address_postcode is not None and postcode is not None:
            if parsed.address_postcode != postcode:
                raise PropertyMismatchError(
                    "the council returned a schedule for a different postcode"
                )
        elif require_address_match:
            raise PropertyMismatchError("the council response did not identify the property")

        if parsed.skipped_entries and not parsed.events:
            raise ParseError("calendar entries were present but none could be parsed")
        if parsed.skipped_entries:
            _LOGGER.warning(
                "Skipped %d calendar entries that could not be parsed",
                parsed.skipped_entries,
            )

        schedule = CollectionSchedule.build(
            parsed.events,
            horizon_start=parsed.horizon_start,
            horizon_end=parsed.horizon_end,
            fetched_at=self._utcnow(),
            source=self.provider_id,
            address_verified=parsed.address_postcode is not None
            and parsed.address_postcode == postcode,
        )
        _LOGGER.debug(
            "Parsed %d events (%d categories) from %s to %s",
            len(schedule.events),
            len(schedule.categories_seen),
            schedule.horizon_start,
            schedule.horizon_end,
        )
        return schedule


async def _run_parser[T](parser: Callable[[str], T], html: str) -> T:
    """Run a CPU-bound HTML parser in the default executor, off the event loop."""
    return await asyncio.get_running_loop().run_in_executor(None, parser, html)
