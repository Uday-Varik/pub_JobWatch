"""Tests for seniority-aware title exclusion.

Banks (Morgan Stanley, Goldman, JPMorgan) use Vice President / Executive
Director / Director as individual-contributor engineering ranks. Companies
tagged allow_seniority_titles keep those; everyone else drops them, and the
hard exclusions (manager, intern, C-level, clearance) always apply."""

from __future__ import annotations

import unittest
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from filters import is_excluded, filter_jobs


class TitleExclusionTests(unittest.TestCase):
    def test_seniority_titles_dropped_by_default(self) -> None:
        for title in [
            "Vice President, Software Engineer",
            "VP, Backend Engineer",
            "Director, Software Engineer",
            "Principal Software Engineer",
        ]:
            self.assertTrue(is_excluded(title), title)

    def test_seniority_titles_kept_when_allowed(self) -> None:
        for title in [
            "Vice President, Software Engineer",
            "VP, Backend Engineer",
            "Executive Director, Software Engineer",
            "Database Engineer - Vice President",
            "Principal Software Engineer",
        ]:
            self.assertFalse(is_excluded(title, allow_seniority_titles=True), title)

    def test_hard_exclusions_apply_even_with_seniority_allowed(self) -> None:
        for title in [
            "Software Engineering Manager",
            "Engineering Management Lead",
            "Software Engineer Intern",
            "Chief Technology Officer",
            "Head of Engineering",
        ]:
            self.assertTrue(is_excluded(title, allow_seniority_titles=True), title)

    def test_plain_ic_titles_always_kept(self) -> None:
        for title in ["Software Engineer", "Senior Backend Engineer", "Staff Software Engineer"]:
            self.assertFalse(is_excluded(title), title)
            self.assertFalse(is_excluded(title, allow_seniority_titles=True), title)


class FilterJobsSeniorityTests(unittest.TestCase):
    JOBS = [
        {"title": "Vice President, Software Engineer", "location": "New York, NY",
         "posted_at": "Posted Today"},
        {"title": "Software Engineering Manager", "location": "New York, NY",
         "posted_at": "Posted Today"},
    ]
    KW = ["Software Engineer", "Backend Engineer"]

    def test_default_drops_vp(self) -> None:
        kept = filter_jobs(self.JOBS, self.KW, [])
        self.assertEqual(kept, [])

    def test_allow_seniority_keeps_vp_not_manager(self) -> None:
        kept = filter_jobs(self.JOBS, self.KW, [], allow_seniority_titles=True)
        titles = [j["title"] for j in kept]
        self.assertIn("Vice President, Software Engineer", titles)
        self.assertNotIn("Software Engineering Manager", titles)


if __name__ == "__main__":
    unittest.main()


class NonEngineeringTitleTests(unittest.TestCase):
    def test_customer_facing_titles_are_excluded(self) -> None:
        for title in [
            "Senior Solution Architect, AI Infrastructure",
            "Solutions Architect - Machine Learning",
            "AI Consultant",
            "Sales Engineer, Generative AI",
            "Customer Engineer, Machine Learning",
            "Technical Product Support Engineer",
            "Technical Account Manager",
            "Professional Services Engineer",
        ]:
            self.assertTrue(is_excluded(title), title)

    def test_engineering_titles_with_similar_words_are_kept(self) -> None:
        for title in [
            "Software Engineer, AI Infrastructure",
            "Senior Software Architect",
            "Machine Learning Engineer, Support Tooling",
        ]:
            self.assertFalse(is_excluded(title), title)
