from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import notifier
import ranking


class RankingTests(unittest.TestCase):
    def test_rank_jobs_prefers_fresh_exact_match_remote_and_sponsor(self) -> None:
        now = datetime.now(timezone.utc)
        jobs = [
            {
                "job_id": "older",
                "company": "BoardCo",
                "title": "Engineer II",
                "location": "Austin, TX",
                "url": "https://example.com/jobs/older",
                "posted_at": (now - timedelta(hours=12)).isoformat(),
                "source": "hn_hiring",
            },
            {
                "job_id": "fresh",
                "company": "SponsorCo",
                "title": "Software Development Engineer",
                "location": "Remote - United States",
                "url": "https://example.com/jobs/fresh",
                "posted_at": (now - timedelta(minutes=45)).isoformat(),
                "source": "greenhouse",
            },
        ]
        config = {
            "keywords": ["Software Development Engineer", "Engineer II"],
            "ranking": {"top_picks": 3, "preferred_companies": []},
        }

        with patch.object(ranking, "is_h1b_sponsor", side_effect=lambda company: company == "SponsorCo"):
            ranked = ranking.rank_jobs(jobs, config=config)

        self.assertEqual([job["job_id"] for job in ranked], ["fresh", "older"])
        self.assertEqual(ranked[0]["rank_band"], "Top")
        self.assertIn("H-1B sponsor", ranked[0]["rank_reasons"])
        self.assertTrue(
            any("Software Development Engineer" in reason for reason in ranked[0]["rank_reasons"])
        )
        self.assertIn("remote-friendly", ranked[0]["rank_reasons"])

    def test_notifier_text_surfaces_top_picks_before_grouped_roles(self) -> None:
        now = datetime.now(timezone.utc)
        jobs = [
            {
                "job_id": "fresh",
                "company": "FastCo",
                "title": "Software Development Engineer",
                "location": "Remote - United States",
                "url": "https://example.com/jobs/fresh",
                "posted_at": (now - timedelta(minutes=20)).isoformat(),
                "source": "greenhouse",
            },
            {
                "job_id": "later",
                "company": "LaterCo",
                "title": "Engineer II",
                "location": "Austin, TX",
                "url": "https://example.com/jobs/later",
                "posted_at": (now - timedelta(hours=10)).isoformat(),
                "source": "hn_hiring",
            },
        ]
        config = {
            "keywords": ["Software Development Engineer", "Engineer II"],
            "ranking": {"top_picks": 2, "preferred_companies": []},
        }

        with patch.object(ranking, "is_h1b_sponsor", return_value=False), patch.object(
            notifier, "is_h1b_sponsor", return_value=False
        ):
            ranked = ranking.rank_jobs(jobs, config=config)
            text = notifier._build_text(ranked, config)

        self.assertIn("Top picks", text)
        self.assertLess(
            text.index("1. FastCo — Software Development Engineer"),
            text.index("2. LaterCo — Engineer II"),
        )
        self.assertIn("Priority:", text)
        self.assertIn("Why: Top |", text)

    def test_posted_today_label_does_not_claim_last_hour_precision(self) -> None:
        job = {
            "job_id": "today",
            "company": "TodayCo",
            "title": "Software Engineer",
            "location": "Austin, TX",
            "url": "https://example.com/jobs/today",
            "posted_at": "Posted Today",
            "source": "workday",
        }
        config = {
            "keywords": ["Software Engineer"],
            "ranking": {"top_picks": 3, "preferred_companies": []},
        }

        with patch.object(ranking, "is_h1b_sponsor", return_value=False), patch.object(
            notifier, "is_h1b_sponsor", return_value=False
        ):
            ranked = ranking.rank_job(job, config=config)
            text = notifier._build_text([ranked], config)

        self.assertIn("posted today", ranked["rank_reasons"])
        self.assertNotIn("posted in the last hour", ranked["rank_reasons"])
        self.assertIn("Posted: posted today", text)


if __name__ == "__main__":
    unittest.main()


class FirstPartySourceWeightTests(unittest.TestCase):
    def test_first_party_apis_are_not_penalised_against_ats_feeds(self) -> None:
        from ranking import rank_job
        base = {"title": "Software Engineer", "company": "X", "location": "Austin, TX", "posted_at": None}
        cfg = {"keywords": ["Software Engineer"]}
        for source in ("amazon", "netflix", "oracle", "phenom", "eightfold_pw", "playwright"):
            scored = rank_job({**base, "source": source}, cfg)
            self.assertGreater(scored["rank_score"], rank_job({**base, "source": "unknown"}, cfg)["rank_score"], source)
            self.assertIn("official ATS source", scored["rank_reasons"])
