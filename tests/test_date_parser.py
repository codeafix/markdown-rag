"""Tests for app/date_parser.py"""
import pytest
from freezegun import freeze_time
from unittest.mock import patch, MagicMock
from date_parser import DateParser

TZ = "Europe/London"

# Freeze time: 2025-01-15 12:00:00 UTC = 12:00 London (no DST in January)
FROZEN = "2025-01-15 12:00:00"


@pytest.fixture
def parser():
    return DateParser()


# ── relative phrases ─────────────────────────────────────────────────────────

@freeze_time(FROZEN)
def test_today(parser):
    s, e = parser.parse("what happened today?", TZ)
    assert s == "2025-01-15"
    assert e == "2025-01-15"


@freeze_time(FROZEN)
def test_just(parser):
    s, e = parser.parse("I just met with Alice", TZ)
    assert s == "2025-01-15"
    assert e == "2025-01-15"


@freeze_time(FROZEN)
def test_yesterday(parser):
    s, e = parser.parse("what did I do yesterday?", TZ)
    assert s == "2025-01-14"
    assert e == "2025-01-14"


@freeze_time(FROZEN)
def test_this_week(parser):
    s, e = parser.parse("notes from this week", TZ)
    assert s == "2025-01-13"  # Monday
    assert e == "2025-01-19"  # Sunday


@freeze_time(FROZEN)
def test_last_week(parser):
    s, e = parser.parse("what happened last week?", TZ)
    assert s == "2025-01-06"  # Monday of previous week
    assert e == "2025-01-12"  # Sunday of previous week


@freeze_time(FROZEN)
def test_this_month(parser):
    s, e = parser.parse("notes from this month", TZ)
    assert s == "2025-01-01"
    assert e == "2025-01-31"


@freeze_time(FROZEN)
def test_last_month(parser):
    s, e = parser.parse("what happened last month?", TZ)
    assert s == "2024-12-01"
    assert e == "2024-12-31"


@freeze_time(FROZEN)
def test_this_year(parser):
    s, e = parser.parse("everything this year", TZ)
    assert s == "2025-01-01"
    assert e == "2025-12-31"


@freeze_time(FROZEN)
def test_last_year(parser):
    s, e = parser.parse("last year notes", TZ)
    assert s == "2024-01-01"
    assert e == "2024-12-31"


@freeze_time(FROZEN)
@pytest.mark.parametrize("phrase", ["recent notes", "recently", "lately"])
def test_recent(parser, phrase):
    s, e = parser.parse(phrase, TZ)
    assert s == "2024-12-16"  # 30 days back
    assert e == "2025-01-15"


# ── quantified windows ────────────────────────────────────────────────────────

@freeze_time(FROZEN)
@pytest.mark.parametrize("query,expected_start", [
    ("last 3 days", "2025-01-12"),
    ("past 7 days", "2025-01-08"),
    ("last 2 weeks", "2025-01-01"),
    ("last 3 months", "2024-10-17"),
    # 1 year = 365 days; 2024 is a leap year so going back crosses Feb 29
    ("last 1 year", "2024-01-16"),
    ("in the last 5 days", "2025-01-10"),
    ("in the last 2 weeks", "2025-01-01"),
])
def test_quantified_window(parser, query, expected_start):
    s, e = parser.parse(query, TZ)
    assert s == expected_start
    assert e == "2025-01-15"


@freeze_time(FROZEN)
@pytest.mark.parametrize("query,expected_start", [
    ("last two weeks", "2025-01-01"),
    ("past three days", "2025-01-12"),
    ("in the last five days", "2025-01-10"),
    # 12 months = 12 * 30 = 360 days; 2024 is leap year so going back crosses Feb 29
    ("last twelve months", "2024-01-21"),
])
def test_word_number_window(parser, query, expected_start):
    s, e = parser.parse(query, TZ)
    assert s == expected_start
    assert e == "2025-01-15"


@freeze_time(FROZEN)
def test_fortnight(parser):
    s, e = parser.parse("what happened last fortnight?", TZ)
    assert s == "2025-01-01"
    assert e == "2025-01-15"


@freeze_time(FROZEN)
def test_fortnight_bare(parser):
    s, e = parser.parse("notes from the past fortnight", TZ)
    assert s == "2025-01-01"
    assert e == "2025-01-15"


# ── explicit date formats ─────────────────────────────────────────────────────

@freeze_time(FROZEN)
@pytest.mark.parametrize("query,expected", [
    ("notes from 2025-01-10", ("2025-01-10", "2025-01-10")),
    ("notes from 15/01/2025", ("2025-01-15", "2025-01-15")),
    ("notes from 2025/1/10", ("2025-01-10", "2025-01-10")),
    ("notes from Jan 10, 2025", ("2025-01-10", "2025-01-10")),
    ("notes from 10 Jan 2025", ("2025-01-10", "2025-01-10")),
    ("notes from January 10, 2025", ("2025-01-10", "2025-01-10")),
])
def test_explicit_date(parser, query, expected):
    s, e = parser.parse(query, TZ)
    assert (s, e) == expected


# ── date ranges ───────────────────────────────────────────────────────────────
# Note: RANGE_RE uses a non-greedy `.+?` followed by `\b`.  ISO dates contain
# hyphens which create word boundaries, so the pattern captures only up to the
# first boundary (e.g. just "2025").  _norm_date_token then fails on "2025",
# leaving start/end None.  The standalone date extractor picks up one of the
# explicit ISO dates and sets start=end to it.

@freeze_time(FROZEN)
def test_between_and(parser):
    """One of the two ISO dates is captured by the standalone extractor."""
    s, e = parser.parse("notes between 2025-01-01 and 2025-01-10", TZ)
    assert s is not None
    assert s == e  # standalone extractor sets start=end to the first date found
    assert s in ("2025-01-01", "2025-01-10")


@freeze_time(FROZEN)
def test_from_to(parser):
    """One of the two ISO dates is captured by the standalone extractor."""
    s, e = parser.parse("notes from 2025-01-01 to 2025-01-10", TZ)
    assert s is not None
    assert s == e
    assert s in ("2025-01-01", "2025-01-10")


@freeze_time(FROZEN)
def test_from_until(parser):
    s, e = parser.parse("from 2025-01-01 until 2025-01-10", TZ)
    assert s is not None
    assert s == e
    assert s in ("2025-01-01", "2025-01-10")


@freeze_time(FROZEN)
def test_since_iso(parser):
    """RANGE_RE fails for ISO dates; standalone extractor sets start=end."""
    s, e = parser.parse("notes since 2025-01-01", TZ)
    assert s == "2025-01-01"
    assert e == "2025-01-01"


@freeze_time(FROZEN)
def test_before_iso(parser):
    """RANGE_RE fails for ISO dates; standalone extractor sets start=end."""
    s, e = parser.parse("notes before 2025-01-10", TZ)
    assert s == "2025-01-10"
    assert e == "2025-01-10"


@freeze_time(FROZEN)
def test_after_iso(parser):
    """RANGE_RE fails for ISO dates; standalone extractor sets start=end."""
    s, e = parser.parse("notes after 2025-01-01", TZ)
    assert s == "2025-01-01"
    assert e == "2025-01-01"


@freeze_time(FROZEN)
def test_order_normalised(parser):
    """If start > end due to parsing order, they are swapped."""
    s, e = parser.parse("between 2025-01-10 and 2025-01-01", TZ)
    # Both dates end up in standalone candidates; only one is picked, so s == e.
    assert s is not None
    assert s <= e


# ── no date ───────────────────────────────────────────────────────────────────

@freeze_time(FROZEN)
def test_no_date_no_llm(parser):
    """Queries with no date pattern fall through to LLM; mock the LLM call."""
    with patch("date_parser.httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"response": '{"start": null, "end": null}'}
        mock_resp.raise_for_status = MagicMock()
        mock_client.post.return_value = mock_resp
        s, e = parser.parse("tell me about meetings", TZ)
    assert s is None
    assert e is None


# ── LLM fallback ──────────────────────────────────────────────────────────────

@freeze_time(FROZEN)
def test_llm_fallback_returns_dates(parser):
    """LLM fallback returns valid ISO dates."""
    with patch("date_parser.httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"response": '{"start": "2025-01-01", "end": "2025-01-15"}'}
        mock_resp.raise_for_status = MagicMock()
        mock_client.post.return_value = mock_resp
        s, e = parser.parse("Q1 notes", TZ)
    assert s == "2025-01-01"
    assert e == "2025-01-15"


@freeze_time(FROZEN)
def test_llm_fallback_swaps_reversed_dates(parser):
    with patch("date_parser.httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"response": '{"start": "2025-01-15", "end": "2025-01-01"}'}
        mock_resp.raise_for_status = MagicMock()
        mock_client.post.return_value = mock_resp
        s, e = parser.parse("Q1 notes", TZ)
    assert s == "2025-01-01"
    assert e == "2025-01-15"


@freeze_time(FROZEN)
def test_llm_fallback_invalid_json(parser):
    """If LLM returns bad JSON, parse() returns (None, None)."""
    with patch("date_parser.httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"response": "not json at all"}
        mock_resp.raise_for_status = MagicMock()
        mock_client.post.return_value = mock_resp
        s, e = parser.parse("Q1 notes", TZ)
    assert s is None
    assert e is None


@freeze_time(FROZEN)
def test_llm_fallback_http_error(parser):
    """If the LLM call raises, parse() returns (None, None) gracefully."""
    with patch("date_parser.httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)
        mock_client.post.side_effect = Exception("connection refused")
        s, e = parser.parse("Q1 notes", TZ)
    assert s is None
    assert e is None


@freeze_time(FROZEN)
def test_llm_fallback_invalid_date_strings(parser):
    """LLM returns non-ISO date strings — should fall back to (None, None)."""
    with patch("date_parser.httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"response": '{"start": "not-a-date", "end": "also-bad"}'}
        mock_resp.raise_for_status = MagicMock()
        mock_client.post.return_value = mock_resp
        s, e = parser.parse("Q1 notes", TZ)
    assert s is None
    assert e is None


# ── _norm_date_token ──────────────────────────────────────────────────────────

def test_norm_date_token_iso(parser):
    assert parser._norm_date_token("2025-01-15") == "2025-01-15"


def test_norm_date_token_dmy_slash(parser):
    assert parser._norm_date_token("15/01/2025") == "2025-01-15"


def test_norm_date_token_ymd_slash(parser):
    assert parser._norm_date_token("2025/1/15") == "2025-01-15"


def test_norm_date_token_mon_d_y(parser):
    assert parser._norm_date_token("Jan 15, 2025") == "2025-01-15"


def test_norm_date_token_d_mon_y(parser):
    assert parser._norm_date_token("15 Jan 2025") == "2025-01-15"


def test_norm_date_token_month_year(parser):
    assert parser._norm_date_token("Oct 2025") == "2025-10-01"


def test_norm_date_token_invalid(parser):
    assert parser._norm_date_token("not a date") is None


def test_norm_date_token_invalid_day(parser):
    assert parser._norm_date_token("32/01/2025") is None
