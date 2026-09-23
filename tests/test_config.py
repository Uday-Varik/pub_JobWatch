"""Validate config.yaml itself so a typo never silently disables a source.

Runs in CI before every pipeline run.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from adapters import ADAPTERS

CONFIG_PATH = Path(__file__).resolve().parents[1] / "config.yaml"

REQUIRED_KEYS = {
    "greenhouse": ("slug",),
    "lever": ("slug",),
    "ashby": ("slug",),
    "smartrecruiters": ("slug",),
    "workday": ("tenant", "site"),
    "talentbrew": ("domain",),
    "phenom": ("domain",),
    "eightfold": ("domain",),
    "eightfold_pw": ("tenant",),
    "playwright": ("slug",),
    "oracle": ("host", "site"),
}


class ConfigValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        with open(CONFIG_PATH) as f:
            cls.config = yaml.safe_load(f)

    def test_keywords_present(self) -> None:
        self.assertTrue(self.config.get("keywords"))

    def test_companies_have_unique_names(self) -> None:
        names = [c.get("name") for c in self.config["companies"]]
        self.assertEqual(len(names), len(set(names)), f"duplicate companies: {names}")
        self.assertTrue(all(names))

    def test_every_company_has_a_known_adapter(self) -> None:
        for company in self.config["companies"]:
            self.assertIn(company.get("ats"), ADAPTERS, company.get("name"))

    def test_every_company_has_a_configured_tier(self) -> None:
        tiers = set(self.config["notification"]["tiers"])
        for company in self.config["companies"]:
            self.assertIn(company.get("tier"), tiers, company.get("name"))

    def test_adapter_required_keys_present(self) -> None:
        for company in self.config["companies"]:
            for key in REQUIRED_KEYS.get(company["ats"], ()):
                self.assertTrue(company.get(key), f"{company['name']}: missing '{key}' for ats={company['ats']}")

    def test_retention_settings(self) -> None:
        import jobwatch
        self.assertEqual(jobwatch._retention_settings(self.config)["job_days"], 60)
        self.assertEqual(jobwatch._retention_settings({})["job_days"], 30)
        self.assertEqual(jobwatch._retention_settings(self.config)["health_days"], 30)
        self.assertEqual(jobwatch._retention_settings(self.config)["batch_days"], 14)
        self.assertEqual(jobwatch._retention_settings({"retention": {"job_days": -5}})["job_days"], 30)

    def test_artifact_retention_outlives_the_weekend_gap(self) -> None:
        import re
        workflow = (CONFIG_PATH.parent / ".github" / "workflows" / "jobwatch.yml").read_text()
        days = int(re.search(r"retention-days:\s*(\d+)", workflow).group(1))
        self.assertGreaterEqual(days, 4, "state artifact must survive the ~63h weekend gap plus slack")

    def test_freshness_window_covers_the_weekend_gap(self) -> None:
        # Weekday-only schedule: Friday evening UTC -> Monday midday UTC is ~63h.
        self.assertGreaterEqual(int(self.config["filters"]["max_age_hours"]), 63)


if __name__ == "__main__":
    unittest.main()
