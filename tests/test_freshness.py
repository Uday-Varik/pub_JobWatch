"""Tests for timestamp parsing and the 24h staleness gate — pins the fixes
for 'Posted Yesterday' jobs being deterministically dropped, naive ISO
strings raising TypeError, and date-only postings going stale at midnight."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from filters import _parse_timestamp, is_stale


class PostedYesterdayTests(unittest.TestCase):
    def test_posted_yesterday_is_not_stale(self) -> None:
        # Workday emits "Posted Yesterday" for jobs that can be only hours
        # old. Parsing it as exactly now-24h made every such job fail the
        # 24h cutoff by microseconds — a guaranteed false negative.
        self.assertFalse(is_stale({"posted_at": "Posted Yesterday"}))
        self.assertFalse(is_stale({"posted_at": "yesterday"}))

    def test_posted_today_is_not_stale(self) -> None:
        self.assertFalse(is_stale({"posted_at": "Posted Today"}))

    def test_posted_yesterday_parses_to_end_of_previous_day(self) -> None:
        parsed = _parse_timestamp("Posted Yesterday")
        start_of_today = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        self.assertEqual(parsed, start_of_today - timedelta(seconds=1))

    def test_relative_days_ago_still_stale(self) -> None:
        self.assertTrue(is_stale({"posted_at": "Posted 3 days ago"}))


class NaiveTimestampTests(unittest.TestCase):
    def test_naive_iso_datetime_does_not_crash_is_stale(self) -> None:
        # Offset-less ISO strings used to parse timezone-naive and raise
        # TypeError against the aware cutoff, silently dropping the whole
        # company's batch for the run.
        recent = (datetime.now(timezone.utc) - timedelta(hours=2)).strftime(
            "%Y-%m-%dT%H:%M:%S"
        )
        self.assertFalse(is_stale({"posted_at": recent}))

    def test_naive_iso_is_treated_as_utc(self) -> None:
        parsed = _parse_timestamp("2026-06-09T12:00:00")
        self.assertIsNotNone(parsed.tzinfo)

    def test_aware_iso_unchanged(self) -> None:
        parsed = _parse_timestamp("2026-06-09T12:00:00Z")
        self.assertEqual(parsed.hour, 12)
        self.assertIsNotNone(parsed.tzinfo)


class DateOnlyTests(unittest.TestCase):
    def test_date_only_iso_anchors_to_end_of_day(self) -> None:
        parsed = _parse_timestamp("2026-06-09")
        self.assertEqual((parsed.hour, parsed.minute, parsed.second), (23, 59, 59))

    def test_date_only_us_format_anchors_to_end_of_day(self) -> None:
        parsed = _parse_timestamp("June 9, 2026")
        self.assertEqual((parsed.hour, parsed.minute, parsed.second), (23, 59, 59))

    def test_todays_date_only_posting_is_not_stale(self) -> None:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self.assertFalse(is_stale({"posted_at": today}))

    def test_unparseable_timestamp_is_kept(self) -> None:
        self.assertFalse(is_stale({"posted_at": "sometime recently"}))
        self.assertFalse(is_stale({"posted_at": None}))


if __name__ == "__main__":
    unittest.main()
