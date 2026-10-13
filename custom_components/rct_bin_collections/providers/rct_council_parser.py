"""Pure parsing functions for the RCT Council "Find your collection day" page.

The page structure is documented in ``docs/data-source-research.md``. These functions take
HTML text and return typed results, raising integration exceptions for recognised error
pages and :class:`ParseError` for anything unrecognised. They perform no I/O.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date
import logging
import re

from bs4 import BeautifulSoup, Tag

from ..exceptions import ParseError, PostcodeNotFoundError, PropertyNotFoundError
from ..models import (
    FOOD_WASTE,
    GARDEN_WASTE,
    GENERAL_WASTE,
    RECYCLING,
    CollectionEvent,
    Property,
    categorise,
    clean_label,
    extract_trailing_postcode,
)

_LOGGER = logging.getLogger(__name__)

#: Locale-independent month names (the site is served in English at /EN/).
_MONTHS = {
    name: index
    for index, name in enumerate(
        (
            "january",
            "february",
            "march",
            "april",
            "may",
            "june",
            "july",
            "august",
            "september",
            "october",
            "november",
            "december",
        ),
        start=1,
    )
}
_MONTH_HEADER_RE = re.compile(r"^\s*([A-Za-z]+)\s+(\d{4})\s*$")

#: CSS classes on each collection badge, used only when a label is unrecognised.
_CLASS_HINTS = {
    "alert-primary-black": GENERAL_WASTE,
    "alert-primary-recycling": RECYCLING,
    "alert-primary-food": FOOD_WASTE,
    "alert-primary-green": GARDEN_WASTE,
}

_NO_ADDRESS_MARKER = "no address found"
_SERVER_ERROR_MARKER = "razor view error"

#: Upper bounds that keep a hostile or broken document from producing silly output.
MAX_ADDRESSES = 1000
MAX_MONTH_BLOCKS = 24
MAX_EVENTS = 2000


@dataclass(frozen=True, slots=True)
class ParsedSchedule:
    """Raw result of parsing a schedule page."""

    events: tuple[CollectionEvent, ...]
    horizon_start: date
    horizon_end: date
    address_postcode: str | None
    skipped_entries: int


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


def _search_block(soup: BeautifulSoup) -> Tag:
    """Return the waste lookup block, or raise if the page layout is unrecognised."""
    block = soup.find("div", class_="waste-collection-search")
    if not isinstance(block, Tag):
        raise ParseError("waste collection block not found")
    return block


def parse_address_list(html: str) -> list[Property]:
    """Parse the postcode search result page into properties.

    Raises:
        PostcodeNotFoundError: the council reported "No Address Found".
        ParseError: the page did not contain an address list or a recognised message.

    """
    block = _search_block(_soup(html))
    select = block.find("select", attrs={"name": "SelectedAddress"})
    if isinstance(select, Tag):
        properties: list[Property] = []
        seen: set[str] = set()
        for option in select.find_all("option"):
            value = str(option.get("value") or "").strip()
            if not value.isdigit() or len(value) > 12 or value in seen:
                continue
            seen.add(value)
            label = clean_label(option.get_text(" "))
            properties.append(
                Property(
                    uprn=value,
                    address=label,
                    postcode=extract_trailing_postcode(label),
                )
            )
            if len(properties) >= MAX_ADDRESSES:
                break
        if properties:
            return properties
        raise PostcodeNotFoundError("address list was empty")

    if _NO_ADDRESS_MARKER in block.get_text(" ").casefold():
        raise PostcodeNotFoundError("no address found")
    raise ParseError("address list not found")


def parse_schedule(html: str) -> ParsedSchedule:
    """Parse a property schedule page.

    The printable calendar (current month plus two) is preferred. If it is missing, the
    single-month interactive calendar is used instead.

    Raises:
        PropertyNotFoundError: the council could not resolve the UPRN.
        ParseError: no calendar could be found or parsed.

    """
    block = _search_block(_soup(html))
    text = block.get_text(" ").casefold()

    month_blocks = _printable_month_blocks(block) or _monthly_month_blocks(block)
    if not month_blocks:
        if _SERVER_ERROR_MARKER in text:
            raise PropertyNotFoundError("council could not resolve the property")
        if block.find("input", attrs={"name": "AddressLookupPostcode"}) is not None:
            # The lookup form with no calendar is what the site shows for an unknown UPRN.
            raise PropertyNotFoundError("council returned the search form")
        raise ParseError("no collection calendar found")

    events: list[CollectionEvent] = []
    months: list[tuple[int, int]] = []
    skipped = 0
    for month_block in month_blocks[:MAX_MONTH_BLOCKS]:
        year_month = _parse_month_header(month_block)
        if year_month is None:
            skipped += 1
            continue
        months.append(year_month)
        month_events, month_skipped = _parse_month_days(month_block, *year_month)
        events.extend(month_events)
        skipped += month_skipped
        if len(events) >= MAX_EVENTS:
            break

    if not months:
        raise ParseError("no calendar month headers could be parsed")

    # The horizon must not span a month whose header could not be read: its missing events
    # would otherwise look like confirmed "no collection" days. Keep the contiguous run of
    # months from the earliest one parsed, and drop events beyond it.
    first_year, first_month = min(months)
    last_year, last_month = first_year, first_month
    parsed = set(months)
    while _next_month(last_year, last_month) in parsed:
        last_year, last_month = _next_month(last_year, last_month)
    horizon_end = _last_day_of_month(last_year, last_month)
    if len(parsed) > (last_year - first_year) * 12 + last_month - first_month + 1:
        _LOGGER.warning("Calendar months are not contiguous; ignoring months after the gap")
    events = [e for e in events if e.date <= horizon_end]

    return ParsedSchedule(
        events=tuple(events[:MAX_EVENTS]),
        horizon_start=date(first_year, first_month, 1),
        horizon_end=horizon_end,
        address_postcode=_address_postcode(block),
        skipped_entries=skipped,
    )


def _printable_month_blocks(block: Tag) -> list[Tag]:
    printable = block.find("div", class_="printableCalendar")
    if not isinstance(printable, Tag):
        return []
    return [
        wrap
        for wrap in printable.find_all("div", class_="calendar-wrap")
        if isinstance(wrap, Tag) and wrap.find("div", class_="calendar-month")
    ]


def _monthly_month_blocks(block: Tag) -> list[Tag]:
    monthly = block.find("div", class_="monthlyCalendar")
    if not isinstance(monthly, Tag) or not monthly.find("div", class_="calendar-month"):
        return []
    return [monthly]


def _parse_month_header(month_block: Tag) -> tuple[int, int] | None:
    header = month_block.find("div", class_="calendar-month")
    if not isinstance(header, Tag):
        return None
    match = _MONTH_HEADER_RE.match(header.get_text(" "))
    if not match:
        _LOGGER.debug("Unrecognised calendar month header")
        return None
    month = _MONTHS.get(match.group(1).casefold())
    year = int(match.group(2))
    if month is None or not 2000 <= year <= 2100:
        _LOGGER.debug("Unrecognised calendar month header")
        return None
    return year, month


def _parse_month_days(month_block: Tag, year: int, month: int) -> tuple[list[CollectionEvent], int]:
    events: list[CollectionEvent] = []
    skipped = 0
    for day_cell in month_block.find_all("div", class_="card-body"):
        badges = day_cell.find_all("div", class_="collection-day")
        if not badges:
            continue
        title = day_cell.find("div", class_="card-title")
        day_text = title.get_text(strip=True) if isinstance(title, Tag) else ""
        try:
            day = date(year, month, int(day_text))
        except ValueError:
            _LOGGER.debug("Skipping calendar cell with invalid day")
            skipped += len(badges)
            continue
        for badge in badges:
            label = clean_label(badge.get_text(" "))
            if not label:
                skipped += 1
                continue
            hint = next(
                (_CLASS_HINTS[c] for c in badge.get("class") or [] if c in _CLASS_HINTS),
                None,
            )
            events.append(CollectionEvent(date=day, category=categorise(label, hint), label=label))
    return events, skipped


def _address_postcode(block: Tag) -> str | None:
    """Return the postcode of the address paragraph shown above the calendar, if any."""
    for paragraph in block.find_all("p"):
        postcode = extract_trailing_postcode(paragraph.get_text(" "))
        if postcode:
            return postcode
    return None


def _next_month(year: int, month: int) -> tuple[int, int]:
    return (year + 1, 1) if month == 12 else (year, month + 1)


def _last_day_of_month(year: int, month: int) -> date:
    return date(year, month, calendar.monthrange(year, month)[1])
