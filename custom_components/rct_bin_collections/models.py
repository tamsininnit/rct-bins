"""Typed models shared by providers, the coordinator and entities.

Nothing in this module imports Home Assistant, so it can be unit-tested directly.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
import re
from typing import Any

# --------------------------------------------------------------------------------------
# Postcodes and UPRNs
# --------------------------------------------------------------------------------------

_POSTCODE_RE = re.compile(r"^([A-Z]{1,2}[0-9][A-Z0-9]?)([0-9][A-Z]{2})$")
_POSTCODE_SEARCH_RE = re.compile(r"\b([A-Z]{1,2}[0-9][A-Z0-9]?)\s*([0-9][A-Z]{2})\s*$")
_UPRN_RE = re.compile(r"^[0-9]{1,12}$")


def normalise_postcode(raw: str) -> str:
    """Return a UK postcode in canonical display form (``CF37 1AB``).

    Whitespace is removed and letters upper-cased before validation.

    Raises:
        ValueError: if the input is not a syntactically valid UK postcode.

    """
    compact = re.sub(r"\s+", "", raw or "").upper()
    match = _POSTCODE_RE.match(compact)
    if not match:
        raise ValueError("invalid postcode")
    return f"{match.group(1)} {match.group(2)}"


def extract_trailing_postcode(text: str) -> str | None:
    """Return the canonical postcode at the end of an address string, if present."""
    match = _POSTCODE_SEARCH_RE.search(" ".join(text.upper().split()))
    if not match:
        return None
    return f"{match.group(1)} {match.group(2)}"


def normalise_uprn(raw: str | int) -> str:
    """Return a UPRN as a string of 1-12 digits.

    Raises:
        ValueError: if the input is not a plausible UPRN.

    """
    value = str(raw).strip()
    if not _UPRN_RE.match(value):
        raise ValueError("invalid UPRN")
    return value


# --------------------------------------------------------------------------------------
# Waste categories
# --------------------------------------------------------------------------------------

GENERAL_WASTE = "general_waste"
RECYCLING = "recycling"
FOOD_WASTE = "food_waste"
GARDEN_WASTE = "garden_waste"
HYGIENE = "hygiene"

#: Normalised categories with their default English display names.
KNOWN_CATEGORIES: Mapping[str, str] = {
    GENERAL_WASTE: "General Waste",
    RECYCLING: "Recycling",
    FOOD_WASTE: "Food Waste",
    GARDEN_WASTE: "Garden Waste",
    HYGIENE: "Hygiene Waste",
}

#: Exact (case-insensitive) source labels observed on the council website.
_LABEL_TO_CATEGORY: Mapping[str, str] = {
    "black bags": GENERAL_WASTE,
    "black bag": GENERAL_WASTE,
    "general waste": GENERAL_WASTE,
    "refuse": GENERAL_WASTE,
    "recycling": RECYCLING,
    "dry recycling": RECYCLING,
    "food waste": FOOD_WASTE,
    "food": FOOD_WASTE,
    "garden waste": GARDEN_WASTE,
    "green waste": GARDEN_WASTE,
}

#: Substring rules for labels not seen live yet; checked in order.
_LABEL_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("nappy", HYGIENE),
    ("nappies", HYGIENE),
    ("hygiene", HYGIENE),
    ("absorbent", HYGIENE),
)

OTHER_PREFIX = "other_"
_MAX_LABEL_LENGTH = 60
_MAX_SLUG_LENGTH = 40


def clean_label(raw: str) -> str:
    """Collapse whitespace and bound the length of a source label."""
    return " ".join(raw.split())[:_MAX_LABEL_LENGTH]


def categorise(label: str, hint: str | None = None) -> str:
    """Map a source label to a normalised category key.

    Args:
        label: The collection description exactly as published by the source.
        hint: An optional provider-supplied category, used only if the label is unknown.

    Unknown labels are never discarded: they become ``other_<slug>`` categories so they
    still appear on the calendar and in their own sensor.

    """
    key = clean_label(label).casefold()
    if key in _LABEL_TO_CATEGORY:
        return _LABEL_TO_CATEGORY[key]
    for keyword, category in _LABEL_KEYWORDS:
        if keyword in key:
            return category
    if hint is not None and hint in KNOWN_CATEGORIES:
        return hint
    slug = re.sub(r"[^a-z0-9]+", "_", key).strip("_")[:_MAX_SLUG_LENGTH] or "unknown"
    return f"{OTHER_PREFIX}{slug}"


def category_display_name(category: str, label: str | None = None) -> str:
    """Return a human-readable name for a category."""
    if category in KNOWN_CATEGORIES:
        return KNOWN_CATEGORIES[category]
    if label:
        return clean_label(label)
    return category.removeprefix(OTHER_PREFIX).replace("_", " ").title()


# --------------------------------------------------------------------------------------
# Properties and schedules
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Property:
    """A property returned by a provider's postcode lookup."""

    uprn: str
    address: str
    postcode: str | None = None


@dataclass(frozen=True, slots=True, order=True)
class CollectionEvent:
    """A single dated collection of one waste category."""

    date: date
    category: str
    label: str

    @property
    def display_name(self) -> str:
        """Return the human-readable category name."""
        return category_display_name(self.category, self.label)


@dataclass(frozen=True, slots=True)
class CollectionSchedule:
    """A validated collection schedule for one property.

    ``horizon_start`` and ``horizon_end`` (inclusive) describe the date range the source
    actually published. Absence of an event inside the horizon means no collection is
    published for that day; absence outside the horizon means "unknown".
    """

    events: tuple[CollectionEvent, ...]
    horizon_start: date
    horizon_end: date
    fetched_at: datetime
    source: str
    address_verified: bool = False
    categories_seen: frozenset[str] = field(default_factory=frozenset)

    @classmethod
    def build(
        cls,
        events: Iterable[CollectionEvent],
        *,
        horizon_start: date,
        horizon_end: date,
        fetched_at: datetime,
        source: str,
        address_verified: bool = False,
    ) -> CollectionSchedule:
        """Create a schedule with events de-duplicated and in display order."""
        unique = tuple(
            sorted(set(events), key=lambda e: (e.date, category_sort_key(e.category), e.label))
        )
        return cls(
            events=unique,
            horizon_start=horizon_start,
            horizon_end=horizon_end,
            fetched_at=fetched_at,
            source=source,
            address_verified=address_verified,
            categories_seen=frozenset(event.category for event in unique),
        )

    def upcoming(
        self, today: date, categories: Iterable[str] | None = None
    ) -> list[CollectionEvent]:
        """Return events on or after ``today``, optionally limited to some categories."""
        wanted = set(categories) if categories is not None else None
        return [
            event
            for event in self.events
            if event.date >= today and (wanted is None or event.category in wanted)
        ]

    def covers(self, day: date) -> bool:
        """Return True if ``day`` falls inside the published horizon."""
        return self.horizon_start <= day <= self.horizon_end

    def as_cache(self) -> dict[str, Any]:
        """Serialise for persistent storage."""
        return {
            "events": [
                {"date": e.date.isoformat(), "category": e.category, "label": e.label}
                for e in self.events
            ],
            "horizon_start": self.horizon_start.isoformat(),
            "horizon_end": self.horizon_end.isoformat(),
            "fetched_at": self.fetched_at.isoformat(),
            "source": self.source,
            "address_verified": self.address_verified,
        }

    @classmethod
    def from_cache(cls, raw: Mapping[str, Any]) -> CollectionSchedule:
        """Deserialise from persistent storage.

        Raises:
            ValueError, KeyError, TypeError: if the payload is malformed.

        """
        fetched_at = datetime.fromisoformat(raw["fetched_at"])
        if fetched_at.tzinfo is None:
            raise ValueError("cached timestamp must be timezone-aware")
        return cls.build(
            (
                CollectionEvent(
                    date=date.fromisoformat(item["date"]),
                    category=str(item["category"]),
                    label=str(item["label"]),
                )
                for item in raw["events"]
            ),
            horizon_start=date.fromisoformat(raw["horizon_start"]),
            horizon_end=date.fromisoformat(raw["horizon_end"]),
            fetched_at=fetched_at,
            source=str(raw["source"]),
            address_verified=bool(raw.get("address_verified", False)),
        )


def category_sort_key(category: str) -> tuple[int, str]:
    """Order known categories as residents think of them, then unknown ones by name."""
    order = list(KNOWN_CATEGORIES)
    return (order.index(category), "") if category in order else (len(order), category)


def group_by_date(
    events: Iterable[CollectionEvent],
) -> list[tuple[date, list[CollectionEvent]]]:
    """Group events by date in date order, with each day's categories in display order."""
    grouped: dict[date, list[CollectionEvent]] = {}
    for event in sorted(events, key=lambda e: (e.date, category_sort_key(e.category))):
        grouped.setdefault(event.date, []).append(event)
    return list(grouped.items())


def join_names(names: Iterable[str]) -> str:
    """Join names as natural English: ``A``, ``A and B``, ``A, B and C``."""
    items = list(dict.fromkeys(names))
    if len(items) <= 1:
        return "".join(items)
    return f"{', '.join(items[:-1])} and {items[-1]}"
