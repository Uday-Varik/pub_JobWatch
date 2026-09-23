"""Tests for the weekly source-health digest classification and store helper."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import store
from notifier import _classify_source_health


class ClassifyTests(unittest.TestCase):
    def test_split_broken_degraded_healthy(self) -> None:
        rows = [
            {"company": "A", "ats": "greenhouse", "status": "ok", "raw_count": 300, "matched_count": 5},
            {"company": "B", "ats": "playwright", "status": "ok", "raw_count": 0, "matched_count": 0},
            {"company": "C", "ats": "playwright", "status": "error", "raw_count": 0, "matched_count": 0, "error": "timeout"},
            {"company": "D", "ats": "smartrecruiters", "status": "ok", "raw_count": 2, "matched_count": 0},
        ]
        broken, degraded, healthy = _classify_source_health(rows)
        self.assertEqual({r["company"] for r in broken}, {"B", "C"})    # 0 jobs / error
        self.assertEqual({r["company"] for r in degraded}, {"D"})        # low raw count
        self.assertEqual({r["company"] for r in healthy}, {"A"})

    def test_all_healthy(self) -> None:
        rows = [{"company": "A", "ats": "gh", "status": "ok", "raw_count": 100, "matched_count": 3}]
        broken, degraded, healthy = _classify_source_health(rows)
        self.assertEqual((broken, degraded), ([], []))
        self.assertEqual(len(healthy), 1)


class LatestPerSourceTests(unittest.TestCase):
    def test_returns_one_row_per_source_newest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "jobwatch.db"
            with patch.object(store, "DB_PATH", db):
                # two runs for the same source; latest should win
                store.record_source_results(
                    [{"company": "Acme", "ats": "greenhouse", "status": "ok",
                      "raw_count": 100, "matched_count": 2, "duration": 0.3, "error": None}], "fast")
                store.record_source_results(
                    [{"company": "Acme", "ats": "greenhouse", "status": "ok",
                      "raw_count": 0, "matched_count": 0, "duration": 0.3, "error": None},
                     {"company": "Beta", "ats": "lever", "status": "error",
                      "raw_count": 0, "matched_count": 0, "duration": 0.3, "error": "boom"}], "fast")
                rows = store.get_latest_source_health_per_source()
            by = {r["company"]: r for r in rows}
            self.assertEqual(len(rows), 2)
            self.assertEqual(int(by["Acme"]["raw_count"]), 0)  # newest run, not the earlier 100
            self.assertEqual(by["Beta"]["status"], "error")


if __name__ == "__main__":
    unittest.main()


class DigestScopedToConfigTests(unittest.TestCase):
    def test_removed_companies_are_dropped_from_digest(self) -> None:
        import jobwatch
        cfg = {"companies": [{"name": "Stripe", "ats": "greenhouse"}]}
        rows = [
            {"company": "Stripe", "ats": "greenhouse", "status": "ok", "raw_count": 10, "matched_count": 0},
            {"company": "Comcast", "ats": "workday", "status": "error", "raw_count": 0, "matched_count": 0},
        ]
        sent = {}
        with patch("jobwatch.load_config", return_value=cfg), \
             patch("jobwatch.get_latest_source_health_per_source", return_value=rows), \
             patch("jobwatch.send_health_digest", side_effect=lambda r, c: sent.update(rows=r) or (True, "ok")):
            jobwatch.cmd_health_digest(None)
        self.assertEqual([r["company"] for r in sent["rows"]], ["Stripe"])
