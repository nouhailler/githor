"""Tests des utilitaires de dates : lecture, formatage et durées écoulées."""

from datetime import UTC, datetime, timedelta, timezone

import pytest

from githor.utils.dates import format_age, from_epoch, isoformat, parse_datetime, utc_now

# ── utc_now et from_epoch ────────────────────────────────────────────────────


def test_utc_now_is_aware_and_in_utc() -> None:
    moment = utc_now()

    assert moment.tzinfo is not None
    assert moment.utcoffset() == timedelta(0)


def test_from_epoch_returns_utc() -> None:
    assert from_epoch(0) == datetime(1970, 1, 1, tzinfo=UTC)


# ── isoformat ────────────────────────────────────────────────────────────────


def test_isoformat_marks_utc_with_a_z() -> None:
    assert isoformat(datetime(2026, 9, 2, 12, 30, tzinfo=UTC)) == "2026-09-02T12:30:00Z"


def test_isoformat_converts_another_offset_to_utc() -> None:
    paris = datetime(2026, 9, 2, 14, 30, tzinfo=timezone(timedelta(hours=2)))

    assert isoformat(paris) == "2026-09-02T12:30:00Z"


def test_isoformat_of_an_absent_date_is_an_empty_cell() -> None:
    assert isoformat(None) == ""


def test_parse_datetime_and_isoformat_round_trip() -> None:
    assert isoformat(parse_datetime("2026-09-02T12:30:00Z")) == "2026-09-02T12:30:00Z"


# ── format_age ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("delta", "expected"),
    [
        (timedelta(seconds=0), "moins d'une minute"),
        (timedelta(seconds=59), "moins d'une minute"),
        (timedelta(minutes=1), "1 min"),
        (timedelta(minutes=59, seconds=59), "59 min"),
        (timedelta(hours=1), "1 h"),
        (timedelta(hours=23, minutes=59), "23 h"),
        (timedelta(days=1), "1 j"),
        (timedelta(days=9, hours=23), "9 j"),
    ],
)
def test_format_age_keeps_a_single_unit(delta: timedelta, expected: str) -> None:
    assert format_age(delta) == expected


def test_format_age_rounds_down() -> None:
    """Mieux vaut annoncer « 2 h » pour 2 h 59 que prétendre à une précision absente."""
    assert format_age(timedelta(hours=2, minutes=59)) == "2 h"


def test_a_date_ahead_of_the_clock_reads_as_just_now() -> None:
    """Une horloge qui dérive ne doit pas produire « -1 j »."""
    assert format_age(timedelta(hours=-5)) == "moins d'une minute"
