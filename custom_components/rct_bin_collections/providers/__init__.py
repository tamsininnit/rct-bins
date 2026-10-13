"""Collection data providers.

Only providers whose data has been verified for Rhondda Cynon Taf are registered here.
See ``docs/data-source-research.md`` for why no secondary provider is currently offered.
"""

from __future__ import annotations

from collections.abc import Callable

import aiohttp

from .base import CollectionProvider, ProviderCapability
from .rct_council import RctCouncilProvider

PROVIDERS: dict[str, Callable[[aiohttp.ClientSession], CollectionProvider]] = {
    RctCouncilProvider.provider_id: RctCouncilProvider,
}


def create_provider(provider_id: str, session: aiohttp.ClientSession) -> CollectionProvider:
    """Instantiate a registered provider.

    Raises:
        KeyError: if ``provider_id`` is not registered.

    """
    return PROVIDERS[provider_id](session)


__all__ = ["PROVIDERS", "CollectionProvider", "ProviderCapability", "create_provider"]
