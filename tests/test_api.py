"""Tests for the polite HTTP client."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from http import HTTPStatus
import traceback
from unittest.mock import AsyncMock

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import pytest
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker
from yarl import URL

from custom_components.rct_bin_collections import api
from custom_components.rct_bin_collections.api import HttpClient, parse_retry_after
from custom_components.rct_bin_collections.exceptions import (
    PropertyNotFoundError,
    RateLimitedError,
    ServiceUnavailableError,
    UnsupportedResponseError,
)

from .conftest import HTML_HEADERS

URL_ = URL("https://example.invalid/lookup.aspx")
PARAMS = {"uprn": "1", "month": "0"}


@pytest.fixture
async def client(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> HttpClient:
    return HttpClient(async_get_clientsession(hass))


async def test_success(client: HttpClient, aioclient_mock: AiohttpClientMocker) -> None:
    aioclient_mock.get(URL_, params=PARAMS, text="<html>ok</html>", headers=HTML_HEADERS)
    assert await client.get_text(URL_, PARAMS) == "<html>ok</html>"
    _, _, _, headers = aioclient_mock.mock_calls[0]
    assert headers["User-Agent"].startswith("HomeAssistant-RCTBinCollections/")
    assert client.last_status == 200


async def test_refuses_plain_http(client: HttpClient) -> None:
    with pytest.raises(UnsupportedResponseError):
        await client.get_text(URL("http://example.invalid/"), PARAMS)


async def test_decodes_declared_charset(
    client: HttpClient, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.get(
        URL_,
        params=PARAMS,
        content="Caerffili – café".encode("cp1252"),
        headers={"Content-Type": "text/html; charset=windows-1252"},
    )
    assert await client.get_text(URL_, PARAMS) == "Caerffili – café"


async def test_unknown_charset_falls_back_to_utf8(
    client: HttpClient, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.get(
        URL_,
        params=PARAMS,
        content="café".encode(),
        headers={"Content-Type": "text/html; charset=x-made-up"},
    )
    assert await client.get_text(URL_, PARAMS) == "café"


async def test_invalid_bytes_are_replaced(
    client: HttpClient, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.get(URL_, params=PARAMS, content=b"<p>\xff\xfe</p>", headers=HTML_HEADERS)
    assert "�" in await client.get_text(URL_, PARAMS)


@pytest.mark.parametrize(
    ("status", "exc"),
    [
        (HTTPStatus.NOT_FOUND, PropertyNotFoundError),
        (HTTPStatus.FORBIDDEN, UnsupportedResponseError),
        (HTTPStatus.FOUND, UnsupportedResponseError),
        (HTTPStatus.MOVED_PERMANENTLY, UnsupportedResponseError),
    ],
)
async def test_non_retryable_status(
    client: HttpClient,
    aioclient_mock: AiohttpClientMocker,
    status: HTTPStatus,
    exc: type[Exception],
) -> None:
    aioclient_mock.get(URL_, params=PARAMS, status=status, headers=HTML_HEADERS)
    with pytest.raises(exc):
        await client.get_text(URL_, PARAMS)
    assert aioclient_mock.call_count == 1


async def test_server_error_retries_then_fails(
    client: HttpClient, aioclient_mock: AiohttpClientMocker, no_sleep: AsyncMock
) -> None:
    aioclient_mock.get(URL_, params=PARAMS, status=HTTPStatus.INTERNAL_SERVER_ERROR)
    with pytest.raises(ServiceUnavailableError):
        await client.get_text(URL_, PARAMS)
    assert aioclient_mock.call_count == api.MAX_ATTEMPTS
    delays = [call.args[0] for call in no_sleep.await_args_list]
    assert len(delays) == api.MAX_ATTEMPTS - 1
    assert 2 <= delays[0] <= 3
    assert 4 <= delays[1] <= 6


async def test_server_error_recovers(
    hass: HomeAssistant, client: HttpClient, aioclient_mock: AiohttpClientMocker
) -> None:
    responses = iter([HTTPStatus.BAD_GATEWAY, HTTPStatus.OK])

    async def side_effect(method, url, data):  # type: ignore[no-untyped-def]
        from pytest_homeassistant_custom_component.test_util.aiohttp import (
            AiohttpClientMockResponse,
        )

        return AiohttpClientMockResponse(
            method, url, status=next(responses), text="<html/>", headers=HTML_HEADERS
        )

    aioclient_mock.get(URL_, params=PARAMS, side_effect=side_effect)
    assert await client.get_text(URL_, PARAMS) == "<html/>"
    assert aioclient_mock.call_count == 2


@pytest.mark.parametrize(
    "exc",
    [
        TimeoutError("Connection timeout to host https://x/?uprn=123456"),
        aiohttp.ClientConnectionError("boom"),
        aiohttp.ClientPayloadError(),
    ],
)
async def test_network_errors(
    client: HttpClient, aioclient_mock: AiohttpClientMocker, exc: Exception
) -> None:
    aioclient_mock.get(URL_, params=PARAMS, exc=exc)
    with pytest.raises(ServiceUnavailableError) as err:
        await client.get_text(URL_, PARAMS)
    assert aioclient_mock.call_count == api.MAX_ATTEMPTS
    assert "123456" not in "".join(traceback.format_exception(err.value))


async def test_rate_limited_short_retry_after_is_waited(
    client: HttpClient, aioclient_mock: AiohttpClientMocker, no_sleep: AsyncMock
) -> None:
    aioclient_mock.get(
        URL_, params=PARAMS, status=HTTPStatus.TOO_MANY_REQUESTS, headers={"Retry-After": "5"}
    )
    with pytest.raises(RateLimitedError) as err:
        await client.get_text(URL_, PARAMS)
    assert err.value.retry_after == 5
    assert [c.args[0] for c in no_sleep.await_args_list] == [5, 5]


async def test_rate_limited_long_retry_after_is_raised(
    client: HttpClient, aioclient_mock: AiohttpClientMocker, no_sleep: AsyncMock
) -> None:
    aioclient_mock.get(
        URL_, params=PARAMS, status=HTTPStatus.TOO_MANY_REQUESTS, headers={"Retry-After": "3600"}
    )
    with pytest.raises(RateLimitedError) as err:
        await client.get_text(URL_, PARAMS)
    assert err.value.retry_after == 3600
    assert aioclient_mock.call_count == 1
    no_sleep.assert_not_awaited()


async def test_rate_limited_without_header(
    client: HttpClient, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.get(URL_, params=PARAMS, status=HTTPStatus.TOO_MANY_REQUESTS)
    with pytest.raises(RateLimitedError) as err:
        await client.get_text(URL_, PARAMS)
    assert err.value.retry_after is None


@pytest.mark.parametrize(
    ("headers", "body"),
    [
        ({"Content-Type": "application/json"}, "{}"),
        (HTML_HEADERS, "   \n "),
        (HTML_HEADERS, ""),
        ({**HTML_HEADERS, "Content-Length": str(api.MAX_RESPONSE_BYTES + 1)}, "<html/>"),
    ],
)
async def test_unsupported_responses(
    client: HttpClient, aioclient_mock: AiohttpClientMocker, headers: dict[str, str], body: str
) -> None:
    aioclient_mock.get(URL_, params=PARAMS, text=body, headers=headers)
    with pytest.raises(UnsupportedResponseError):
        await client.get_text(URL_, PARAMS)


async def test_streamed_body_over_limit(
    client: HttpClient, aioclient_mock: AiohttpClientMocker, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(api, "MAX_RESPONSE_BYTES", 10)
    aioclient_mock.get(URL_, params=PARAMS, text="<html>" + "x" * 100, headers=HTML_HEADERS)
    with pytest.raises(UnsupportedResponseError, match="too large"):
        await client.get_text(URL_, PARAMS)


def test_parse_retry_after() -> None:
    assert parse_retry_after(None) is None
    assert parse_retry_after("") is None
    assert parse_retry_after("120") == 120
    assert parse_retry_after("soon") is None
    future = datetime.now(UTC) + timedelta(seconds=90)
    assert 60 < (parse_retry_after(format_datetime(future, usegmt=True)) or 0) <= 90
    past = datetime.now(UTC) - timedelta(hours=1)
    assert parse_retry_after(format_datetime(past, usegmt=True)) == 0
    assert parse_retry_after("Wed, 21 Oct 2026 07:28:00") is not None


async def test_network_error_message_does_not_leak_url(
    client: HttpClient, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.get(URL_, params=PARAMS, exc=aiohttp.InvalidURL("https://x/?uprn=123456"))
    with pytest.raises(ServiceUnavailableError) as err:
        await client.get_text(URL_, PARAMS)
    assert "123456" not in str(err.value)
    # The original exception is not chained, so tracebacks logged at debug level omit it.
    assert err.value.__cause__ is None
    assert err.value.__suppress_context__
    assert "123456" not in "".join(traceback.format_exception(err.value))
