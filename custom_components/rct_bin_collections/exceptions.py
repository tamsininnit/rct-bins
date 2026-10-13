"""Exceptions raised by the RCT Bin Collections data layer.

These are deliberately free of Home Assistant imports so that providers and parsers can be
tested in isolation. The coordinator and config flow translate them into Home Assistant
exceptions and user-facing error keys.
"""

from __future__ import annotations


class RctBinError(Exception):
    """Base class for all integration data-layer errors."""

    #: Translation key used for config flow errors and Home Assistant exceptions.
    translation_key = "unknown_error"


class ServiceUnavailableError(RctBinError):
    """The remote service could not be reached or returned a server error."""

    translation_key = "service_unavailable"


class RateLimitedError(ServiceUnavailableError):
    """The remote service asked us to slow down (HTTP 429)."""

    translation_key = "rate_limited"

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        """Store the server-provided back-off, in seconds, if any."""
        super().__init__(message)
        self.retry_after = retry_after


class UnsupportedResponseError(RctBinError):
    """The remote service returned something other than the expected document."""

    translation_key = "unsupported_response"


class ParseError(UnsupportedResponseError):
    """The document was received but its structure was not recognised."""

    translation_key = "parse_error"


class PostcodeNotFoundError(RctBinError):
    """The provider has no properties for this postcode."""

    translation_key = "postcode_not_found"


class PropertyNotFoundError(RctBinError):
    """The provider does not recognise the property identifier."""

    translation_key = "property_not_found"


class PropertyMismatchError(RctBinError):
    """The returned schedule does not belong to the requested property."""

    translation_key = "property_mismatch"
