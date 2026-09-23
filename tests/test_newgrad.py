"""Tests for new-grad classification and routing into the dedicated stream."""

from __future__ import annotations

import unittest
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import jobwatch
from filters import is_new_grad


class NewGradClassifierTests(unittest.TestCase):
    def test_positive_titles(self) -> None:
        for title in [
            "Software Engineer, New Grad 2026",
            "New Graduate Software Engineer",
            "University Graduate - Software Engineer",
            "Software Engineer, Early Career",
            "Entry-Level Backend Engineer",
            "Associate Software Engineer",
            "Graduate Software Engineer",
            "Software Engineer - Class of 2027",
            "New College Graduate - Software Engineer",
        ]:
            self.assertTrue(is_new_grad(title), title)

    def test_negative_titles(self) -> None:
        for title in [
            "Senior Software Engineer",
            "Staff Software Engineer",
            "Software Engineer",
            "Backend Engineer, Payments",
            "Principal Engineer",
        ]:
            self.assertFalse(is_new_grad(title), title)


NEWGRAD_CONFIG = {
    "companies": [
        {"name": "Salesforce", "tier": "target"},
        {"name": "Amazon", "tier": "faang"},
        {"name": "Reddit", "tier": "other"},
    ],
    "alerting": {"email_bands": ["Top", "Strong"]},
    "notification": {"tiers": {
        "target": {"subject_tag": "T", "ntfy_topic_env": ["X"], "alert": True},
        "faang": {"subject_tag": "F", "ntfy_topic_env": ["Y"], "alert": True},
        "other": {"subject_tag": "O", "ntfy_topic_env": ["Z"], "alert": True},
        "newgrad": {"subject_tag": "NG", "ntfy_topic_env": ["NG"], "alert": True},
    }},
}


def _job(company, title, band="Top"):
    return {"job_id": f"{company}-{title}", "company": company, "title": title,
            "location": "Austin, TX", "url": "https://x/1", "rank_band": band}


class NewGradRoutingTests(unittest.TestCase):
    def test_newgrad_role_routes_to_newgrad_not_company_tier(self) -> None:
        jobs = [
            _job("Salesforce", "New Grad Software Engineer"),   # target co, but new-grad
            _job("Amazon", "Senior Software Engineer"),          # faang, normal
            _job("Reddit", "Associate Software Engineer"),       # other co, new-grad
        ]
        grouped = jobwatch._route_email_jobs(jobs, NEWGRAD_CONFIG)
        self.assertEqual(sorted(grouped.keys()), ["faang", "newgrad"])
        newgrad_titles = {j["title"] for j in grouped["newgrad"]}
        self.assertEqual(newgrad_titles, {"New Grad Software Engineer", "Associate Software Engineer"})
        self.assertNotIn("target", grouped)  # the Salesforce new-grad went to newgrad

    def test_newgrad_disabled_falls_back_to_company_tier(self) -> None:
        cfg = {**NEWGRAD_CONFIG, "notification": {"tiers": {
            **NEWGRAD_CONFIG["notification"]["tiers"],
            "newgrad": {"subject_tag": "NG", "alert": False},
        }}}
        jobs = [_job("Salesforce", "New Grad Software Engineer")]
        grouped = jobwatch._route_email_jobs(jobs, cfg)
        self.assertIn("target", grouped)
        self.assertNotIn("newgrad", grouped)


if __name__ == "__main__":
    unittest.main()


class ExcludeNewGradFilterTests(unittest.TestCase):
    def test_filter_flag_drops_new_grad_titles(self) -> None:
        from filters import filter_jobs
        jobs = [
            {"title": "Software Engineer, New Grad", "location": "Austin, TX"},
            {"title": "Software Engineer", "location": "Austin, TX"},
        ]
        kept = filter_jobs(jobs, ["Software Engineer"], [], exclude_new_grad=True)
        self.assertEqual([j["title"] for j in kept], ["Software Engineer"])
        # Default stays permissive.
        self.assertEqual(len(filter_jobs(jobs, ["Software Engineer"], [])), 2)

    def test_filter_settings_read_from_config(self) -> None:
        import jobwatch
        self.assertTrue(jobwatch._filter_settings({"filters": {"exclude_new_grad": True}})["exclude_new_grad"])
        self.assertFalse(jobwatch._filter_settings({})["exclude_new_grad"])
        self.assertFalse(jobwatch._filter_settings({"filters": None})["exclude_new_grad"])


class MaxAgeHoursTests(unittest.TestCase):
    def test_max_age_hours_read_from_config_with_default(self) -> None:
        import jobwatch
        self.assertEqual(jobwatch._filter_settings({"filters": {"max_age_hours": 72}})["max_age_hours"], 72)
        self.assertEqual(jobwatch._filter_settings({})["max_age_hours"], 24)
        self.assertEqual(jobwatch._filter_settings({"filters": {"max_age_hours": "bogus"}})["max_age_hours"], 24)

    def test_filter_jobs_honours_max_age_hours(self) -> None:
        from filters import filter_jobs
        jobs = [{"title": "Software Engineer", "location": "Austin, TX", "posted_at": "Posted 2 days ago"}]
        self.assertEqual(len(filter_jobs(jobs, ["Software Engineer"], [])), 0)
        self.assertEqual(len(filter_jobs(jobs, ["Software Engineer"], [], max_age_hours=72)), 1)
