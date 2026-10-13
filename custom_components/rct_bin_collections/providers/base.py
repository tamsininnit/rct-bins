"""Provider interface for collection data sources."""

from __future__ import annotations

from abc import ABC, abstractmethod
from enum import StrEnum
from typing import ClassVar

from ..models import CollectionSchedule, Property


class ProviderCapability(StrEnum):
    """Optional features a provider may support."""

    #: The provider can list properties (with identifiers) for a postcode.
    POSTCODE_LOOKUP = "postcode_lookup"
    #: Schedule responses identify the property, so they can be checked against config.
    ADDRESS_VERIFICATION = "address_verification"


class CollectionProvider(ABC):
    """A source of collection schedules keyed by UPRN.

    Implementations must be async, perform no blocking I/O on the event loop and raise
    the exceptions defined in :mod:`..exceptions`.
    """

    provider_id: ClassVar[str]
    name: ClassVar[str]
    capabilities: ClassVar[frozenset[ProviderCapability]]

    def supports(self, capability: ProviderCapability) -> bool:
        """Return True if the provider declares ``capability``."""
        return capability in self.capabilities

    async def async_find_properties(self, postcode: str) -> list[Property]:
        """Return properties for a canonical postcode.

        Only available when :attr:`ProviderCapability.POSTCODE_LOOKUP` is declared.
        """
        raise NotImplementedError(f"{self.name} does not support postcode lookup")

    @abstractmethod
    async def async_get_schedule(
        self,
        uprn: str,
        postcode: str | None,
        *,
        require_address_match: bool = False,
    ) -> CollectionSchedule:
        """Fetch and validate the schedule for a property.

        Args:
            uprn: The property's UPRN.
            postcode: The configured canonical postcode, used to check the response.
            require_address_match: Fail if the response does not identify the property.

        """
