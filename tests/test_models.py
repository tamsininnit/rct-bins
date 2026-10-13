"""Tests for models and normalisation helpers."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from custom_components.rct_bin_collections.models import (
    GENERAL_WASTE,
    HYGIENE,
    RECYCLING,
    CollectionEvent,
    CollectionSchedule,
    categorise,
    category_display_name,
    extract_trailing_postcode,
    group_by_date,
    join_names,
    normalise_postcode,
    normalise_uprn,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("CF37 1AB", "CF37 1AB"),
        ("cf37 1ab", "CF37 1AB"),
        ("  CF371AB  ", "CF37 1AB"),
        ("cf37\t 1Ab", "CF37 1AB"),
        ("CF721AB", "CF72 1AB"),
        ("SW1A 1AA", "SW1A 1AA"),
        ("M1 1AE", "M1 1AE"),
        ("B33 8TH", "B33 8TH"),
    ],
)
def test_normalise_postcode_valid(raw: str, expected: str) -> None:
    assert normalise_postcode(raw) == expected


@pytest.mark.parametrize("raw", ["", "   ", "CF37", "NOTAPOSTCODE", "CF37 1A", "12345", "CF37-1AB"])
def test_normalise_postcode_invalid(raw: str) -> None:
    with pytest.raises(ValueError, match="invalid postcode"):
        normalise_postcode(raw)


def test_extract_trailing_postcode() -> None:
    assert extract_trailing_postcode("1 Example St, Town,  cf99 9zz ") == "CF99 9ZZ"
    assert extract_trailing_postcode("No Address Found") is None


@pytest.mark.parametrize(("raw", "expected"), [(" 100100123456 ", "100100123456"), (42, "42")])
def test_normalise_uprn_valid(raw: str | int, expected: str) -> None:
    assert normalise_uprn(raw) == expected


@pytest.mark.parametrize("raw", ["", "abc", "1234567890123", "12 34", "-1"])
def test_normalise_uprn_invalid(raw: str) -> None:
    with pytest.raises(ValueError, match="invalid UPRN"):
        normalise_uprn(raw)


@pytest.mark.parametrize(
    ("label", "hint", "expected"),
    [
        ("Black Bags", None, GENERAL_WASTE),
        ("  RECYCLING ", None, RECYCLING),
        ("Nappy & Hygiene", None, HYGIENE),
        ("Absorbent hygiene products", None, HYGIENE),
        ("Something New", GENERAL_WASTE, GENERAL_WASTE),
        ("Bulky Waste Collection", None, "other_bulky_waste_collection"),
        ("Bulky Waste Collection", "not-a-category", "other_bulky_waste_collection"),
        ("!!!", None, "other_unknown"),
    ],
)
def test_categorise(label: str, hint: str | None, expected: str) -> None:
    assert categorise(label, hint) == expected


def test_category_display_name() -> None:
    assert category_display_name(GENERAL_WASTE, "Black Bags") == "General Waste"
    assert category_display_name("other_bulky", "Bulky   Items") == "Bulky Items"
    assert category_display_name("other_bulky_items") == "Bulky Items"


def test_join_names() -> None:
    assert join_names([]) == ""
    assert join_names(["A"]) == "A"
    assert join_names(["A", "B"]) == "A and B"
    assert join_names(["A", "B", "C", "A"]) == "A, B and C"


def _schedule(*events: CollectionEvent) -> CollectionSchedule:
    return CollectionSchedule.build(
        events,
        horizon_start=date(2026, 12, 1),
        horizon_end=date(2027, 1, 31),
        fetched_at=datetime(2026, 12, 1, tzinfo=UTC),
        source="rct_council",
    )


def test_schedule_deduplicates_and_sorts() -> None:
    later = CollectionEvent(date(2027, 1, 2), RECYCLING, "Recycling")
    earlier = CollectionEvent(date(2026, 12, 31), GENERAL_WASTE, "Black Bags")
    schedule = _schedule(later, earlier, later)
    assert schedule.events == (earlier, later)
    same_day = _schedule(
        CollectionEvent(date(2027, 1, 2), "food_waste", "Food Waste"),
        CollectionEvent(date(2027, 1, 2), RECYCLING, "Recycling"),
    )
    assert [e.category for e in same_day.events] == [RECYCLING, "food_waste"]
    assert schedule.categories_seen == {RECYCLING, GENERAL_WASTE}


def test_schedule_upcoming_and_horizon() -> None:
    schedule = _schedule(
        CollectionEvent(date(2026, 12, 30), RECYCLING, "Recycling"),
        CollectionEvent(date(2027, 1, 2), GENERAL_WASTE, "Black Bags"),
    )
    assert [e.date for e in schedule.upcoming(date(2026, 12, 31))] == [date(2027, 1, 2)]
    assert schedule.upcoming(date(2026, 12, 1), {RECYCLING})[0].category == RECYCLING
    assert schedule.covers(date(2027, 1, 31))
    assert not schedule.covers(date(2027, 2, 1))


def test_cache_round_trip() -> None:
    schedule = _schedule(CollectionEvent(date(2027, 1, 2), RECYCLING, "Recycling"))
    assert CollectionSchedule.from_cache(schedule.as_cache()) == schedule


@pytest.mark.parametrize(
    "mutate",
    [
        lambda raw: raw.pop("events"),
        lambda raw: raw.update(fetched_at="2026-12-01T00:00:00"),
        lambda raw: raw["events"][0].update(date="2027-02-30"),
        lambda raw: raw.update(events=None),
    ],
)
def test_cache_rejects_malformed(mutate) -> None:  # type: ignore[no-untyped-def]
    raw = _schedule(CollectionEvent(date(2027, 1, 2), RECYCLING, "Recycling")).as_cache()
    mutate(raw)
    with pytest.raises((KeyError, TypeError, ValueError)):
        CollectionSchedule.from_cache(raw)


def test_group_by_date() -> None:
    a = CollectionEvent(date(2027, 1, 2), RECYCLING, "Recycling")
    b = CollectionEvent(date(2027, 1, 2), GENERAL_WASTE, "Black Bags")
    c = CollectionEvent(date(2027, 1, 9), RECYCLING, "Recycling")
    groups = group_by_date([c, a, b])
    assert [g[0] for g in groups] == [date(2027, 1, 2), date(2027, 1, 9)]
    assert {e.category for e in groups[0][1]} == {RECYCLING, GENERAL_WASTE}
