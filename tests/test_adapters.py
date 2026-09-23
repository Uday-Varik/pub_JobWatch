"""Fixture-payload tests for the previously untested adapters.

job_id format stability is the load-bearing assertion in each test: job_id
is the dedup key, and a silent format change re-alerts every existing job
(the duplicate-alert bug the store layer was hardened against)."""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from adapters import ashby, greenhouse, lever, netflix, oleeo, phenom, talentbrew, uber
from adapters import workday


class FakeResponse:
    def __init__(self, payload, status_code: int = 200, text: str = "") -> None:
        self._payload = payload
        self.status_code = status_code
        self.text = text

    def json(self):
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class GreenhouseTests(unittest.TestCase):
    def test_field_mapping_and_job_id(self) -> None:
        payload = {
            "jobs": [
                {
                    "id": 12345,
                    "title": "Backend Engineer",
                    "location": {"name": "Austin, TX"},
                    "absolute_url": "https://boards.greenhouse.io/acme/jobs/12345",
                    "updated_at": "2026-06-09T12:00:00Z",
                    "content": "Pay range: $150,000 - $200,000 annually",
                }
            ]
        }
        with patch.object(greenhouse.requests, "get", return_value=FakeResponse(payload)):
            jobs = greenhouse.fetch_greenhouse({"name": "Acme", "slug": "acme"})

        self.assertEqual(len(jobs), 1)
        job = jobs[0]
        self.assertEqual(job["job_id"], "gh-acme-12345")
        self.assertEqual(job["title"], "Backend Engineer")
        self.assertEqual(job["location"], "Austin, TX")
        self.assertEqual(job["posted_at"], "2026-06-09T12:00:00Z")
        self.assertIn("$150,000", job["salary"])


class LeverTests(unittest.TestCase):
    def test_field_mapping_epoch_conversion_and_job_id(self) -> None:
        payload = [
            {
                "id": "ab-12",
                "text": "Fullstack Developer",
                "categories": {"location": "Seattle, WA", "commitment": "Full-time"},
                "hostedUrl": "https://jobs.lever.co/acme/ab-12",
                "createdAt": 1765300000000,
                "descriptionPlain": "no salary listed",
            }
        ]
        with patch.object(lever.requests, "get", return_value=FakeResponse(payload)):
            jobs = lever.fetch_lever({"name": "Acme", "slug": "acme"})

        self.assertEqual(len(jobs), 1)
        job = jobs[0]
        self.assertEqual(job["job_id"], "lv-acme-ab-12")
        self.assertEqual(job["location"], "Seattle, WA")
        # Epoch ms converted to Z-suffixed ISO (a safe format for is_stale).
        self.assertRegex(job["posted_at"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


class AshbyTests(unittest.TestCase):
    def test_field_mapping_and_job_id(self) -> None:
        payload = {
            "jobs": [
                {
                    "id": "uuid-1",
                    "title": "Platform Engineer",
                    "location": "Remote",
                    "jobUrl": "https://jobs.ashbyhq.com/acme/uuid-1",
                    "publishedAt": "2026-06-09T08:00:00Z",
                    "compensationTierSummary": "$170K - $210K",
                }
            ]
        }
        with patch.object(ashby.requests, "get", return_value=FakeResponse(payload)):
            jobs = ashby.fetch_ashby({"name": "Acme", "slug": "acme"})

        self.assertEqual(len(jobs), 1)
        job = jobs[0]
        self.assertEqual(job["job_id"], "ab-acme-uuid-1")
        self.assertEqual(job["salary"], "$170K - $210K")
        self.assertEqual(job["posted_at"], "2026-06-09T08:00:00Z")


class NetflixTests(unittest.TestCase):
    def test_field_mapping_and_job_id(self) -> None:
        payload = {
            "positions": [
                {
                    "id": "790300000000",
                    "name": "Software Engineer L4",
                    "location": "Los Gatos, CA",
                }
            ],
            "page_count": 1,
        }
        with patch.object(netflix.requests, "get", return_value=FakeResponse(payload)):
            jobs = netflix.fetch_netflix({"name": "Netflix"})

        self.assertEqual(len(jobs), 1)
        job = jobs[0]
        self.assertEqual(job["job_id"], "netflix-790300000000")
        self.assertEqual(job["url"], "https://explore.jobs.netflix.net/careers/790300000000")
        self.assertEqual(job["source"], "netflix")


class UberTests(unittest.TestCase):
    def test_field_mapping_nested_results_and_job_id(self) -> None:
        payload = {
            "data": {
                "results": [
                    {
                        "id": 140000,
                        "title": "Senior Software Engineer",
                        "location": {"name": "San Francisco, CA"},
                        "postedAt": "2026-06-09T10:00:00Z",
                    }
                ]
            }
        }
        with patch.object(uber.requests, "post", return_value=FakeResponse(payload)):
            jobs = uber.fetch_uber({"name": "Uber"})

        self.assertEqual(len(jobs), 1)
        job = jobs[0]
        self.assertEqual(job["job_id"], "uber-140000")
        self.assertEqual(job["location"], "San Francisco, CA")
        self.assertEqual(job["url"], "https://www.uber.com/global/en/careers/list/140000/")


class PhenomTests(unittest.TestCase):
    def test_field_mapping_and_job_id(self) -> None:
        page_one = {
            "jobs": [
                {
                    "data": {
                        "title": "Software Engineer II",
                        "city": "Santa Clara",
                        "state": "California",
                        "country": "United States",
                        "req_id": "JR100",
                        "slug": "jr100-software-engineer-ii",
                        "postedDate": "2026-06-09T00:00:00Z",
                        "description": "",
                    }
                }
            ]
        }
        responses = iter([FakeResponse(page_one), FakeResponse({"jobs": []})])
        with patch.object(phenom.requests, "get", side_effect=lambda *a, **k: next(responses)):
            jobs = phenom.fetch_phenom({"name": "AMD", "domain": "careers.amd.com"})

        self.assertEqual(len(jobs), 1)
        job = jobs[0]
        self.assertEqual(job["job_id"], "ph-careers.amd.com-JR100")
        self.assertEqual(job["location"], "Santa Clara, California, United States")
        self.assertIn("jr100-software-engineer-ii", job["url"])

    def test_job_id_does_not_collide_across_careers_domains(self) -> None:
        # AMD (careers.amd.com) and Rivian (careers.rivian.com) share a req_id;
        # the full-domain prefix must keep their job_ids distinct.
        job_page = {"jobs": [{"data": {"title": "Software Engineer", "city": "Austin",
                                       "state": "TX", "country": "United States",
                                       "req_id": "12345", "slug": "12345",
                                       "postedDate": "2026-06-22T00:00:00Z",
                                       "description": ""}}]}

        def one_page(*a, **k):
            # page 1 returns the job, page 2 is empty so pagination stops
            return FakeResponse(job_page if k.get("params", {}).get("page") == 1 else {"jobs": []})

        with patch.object(phenom.requests, "get", side_effect=one_page):
            amd = phenom.fetch_phenom({"name": "AMD", "domain": "careers.amd.com"})[0]
            rivian = phenom.fetch_phenom({"name": "Rivian", "domain": "careers.rivian.com"})[0]
        self.assertNotEqual(amd["job_id"], rivian["job_id"])
        self.assertEqual(amd["job_id"], "ph-careers.amd.com-12345")
        self.assertEqual(rivian["job_id"], "ph-careers.rivian.com-12345")


class OleeoTests(unittest.TestCase):
    def test_html_row_parsing_and_job_id(self) -> None:
        html = (
            "<table><tr>"
            '<td><a href="https://ms.tal.net/vx/candidate/jobboard/vacancy/55501/detail">'
            "Software Engineer</a></td>"
            "<td><span>New York, NY</span></td>"
            "</tr></table>"
        )
        fake = FakeResponse({}, text=html)
        with patch.object(oleeo.requests, "get", return_value=fake):
            jobs = oleeo.fetch_oleeo({"name": "Morgan Stanley", "subdomain": "ms"})

        self.assertEqual(len(jobs), 1)
        job = jobs[0]
        self.assertEqual(job["job_id"], "ol-ms-55501")
        self.assertEqual(job["title"], "Software Engineer")
        self.assertEqual(job["location"], "New York, NY")


class TalentBrewTests(unittest.TestCase):
    def test_html_results_parsing_and_job_id(self) -> None:
        payload = {
            "results": (
                '<a href="/job/austin/software-engineer/123/456">Software Engineer</a>'
            )
        }
        with patch.object(talentbrew.requests, "get", return_value=FakeResponse(payload)):
            jobs = talentbrew.fetch_talentbrew({"name": "Arm", "domain": "careers.arm.com"})

        self.assertEqual(len(jobs), 1)
        job = jobs[0]
        self.assertEqual(job["job_id"], "tb-careers.arm.com-/job/austin/software-engineer/123/456")
        self.assertEqual(job["location"], "Austin")
        self.assertEqual(job["url"], "https://careers.arm.com/job/austin/software-engineer/123/456")

    def test_page_cap_terminates_runaway_pagination(self) -> None:
        # 100 anchors per page makes the loop ask for the next page forever
        # if the cap regresses.
        full_page = {
            "results": "".join(
                f'<a href="/job/loc/title-{i}/1/{i}">Job {i}</a>' for i in range(100)
            )
        }
        calls = {"n": 0}

        def fake_get(*_args, **_kwargs):
            calls["n"] += 1
            return FakeResponse(full_page)

        with patch.object(talentbrew.requests, "get", side_effect=fake_get):
            talentbrew.fetch_talentbrew({"name": "Arm", "domain": "careers.arm.com"})

        self.assertEqual(calls["n"], talentbrew.MAX_PAGES)


class OracleTests(unittest.TestCase):
    def test_field_mapping_and_job_id(self) -> None:
        from adapters import oracle
        payload = {
            "items": [{
                "TotalJobsCount": 2,
                "requisitionList": [
                    {"Id": 342263, "Title": "Software Developer 3",
                     "PrimaryLocation": "Austin, TX, United States", "PostedDate": "2026-08-10"},
                    {"Id": 342260, "Title": "Software Developer 4",
                     "PrimaryLocation": "Seattle, WA, United States", "PostedDate": "2026-08-10"},
                ],
            }]
        }
        with patch.object(oracle.requests, "get", return_value=FakeResponse(payload)):
            jobs = oracle.fetch_oracle({"name": "Oracle", "host": "eeho.fa.us2.oraclecloud.com", "site": "CX_1"})
        self.assertEqual(len(jobs), 2)
        j = jobs[0]
        self.assertEqual(j["job_id"], "orc-eeho-342263")
        self.assertEqual(j["title"], "Software Developer 3")
        self.assertEqual(j["location"], "Austin, TX, United States")
        self.assertIn("/job/342263", j["url"])
        self.assertEqual(j["posted_at"], "2026-08-10")

    def test_registered_fast_lane(self) -> None:
        from adapters import ADAPTERS
        import jobwatch
        self.assertIn("oracle", ADAPTERS)
        self.assertNotIn("oracle", jobwatch.BROWSER_ATS)  # API adapter → fast lane


class EightfoldPwTests(unittest.TestCase):
    def test_registered_and_browser_lane(self) -> None:
        from adapters import ADAPTERS
        import jobwatch
        self.assertIn("eightfold_pw", ADAPTERS)
        self.assertIn("eightfold_pw", jobwatch.BROWSER_ATS)

    def test_careers_url_encodes_query_and_us_location(self) -> None:
        from adapters.eightfold_pw import _careers_url
        url = _careers_url({"name": "Morgan Stanley", "tenant": "morganstanley", "query": "software"})
        self.assertTrue(url.startswith("https://morganstanley.eightfold.ai/careers"))
        self.assertIn("query=software", url)
        self.assertIn("location=United%20States", url)
        self.assertIn("sort_by=timestamp", url)

    def test_careers_url_defaults(self) -> None:
        from adapters.eightfold_pw import _careers_url
        url = _careers_url({"name": "X", "tenant": "acme"})
        self.assertIn("query=software%20engineer", url)


class WorkdayTimeBudgetTests(unittest.TestCase):
    def _page(self, start: int, n: int = 20, total: int = 1000):
        return {
            "total": total,
            "jobPostings": [
                {
                    "title": f"Software Engineer {start + i}",
                    "locationsText": "Austin, TX",
                    "externalPath": f"/job/{start + i}",
                    "postedOn": "Posted Today",
                    "bulletFields": [],
                }
                for i in range(n)
            ],
        }

    def test_time_budget_returns_partial_instead_of_timing_out(self) -> None:
        # Simulate a huge board: every page is full, total far exceeds what
        # the budget allows. A fake clock trips the budget after 3 pages.
        company = {"name": "Salesforce", "tenant": "salesforce.wd12",
                   "site": "External_Career_Site", "max_pages": 80}

        offsets = {"n": 0}

        def fake_post(url, json=None, headers=None, timeout=None):
            page = self._page(json["offset"])
            offsets["n"] += 1
            return FakeResponse(page)

        clock = {"t": 0.0}

        def fake_monotonic():
            # advance 50s of virtual time per call so we cross 110s after a
            # few pages
            clock["t"] += 50.0
            return clock["t"]

        with patch.object(workday.requests, "post", side_effect=fake_post), patch.object(
            workday.time, "monotonic", side_effect=fake_monotonic
        ):
            jobs = workday.fetch_workday(company)

        # Returned the jobs it managed to fetch (non-zero), did not raise.
        self.assertGreater(len(jobs), 0)
        # Stopped well before max_pages * 20 jobs.
        self.assertLess(len(jobs), 80 * 20)
        self.assertTrue(all(j["job_id"].startswith("wd-salesforce-") for j in jobs))


if __name__ == "__main__":
    unittest.main()


class EightfoldBaseUrlTests(unittest.TestCase):
    def test_default_host_is_tenant_eightfold_ai(self) -> None:
        from adapters import eightfold_pw
        url = eightfold_pw._careers_url({"tenant": "morganstanley"})
        self.assertTrue(url.startswith("https://morganstanley.eightfold.ai/careers?"))
        self.assertEqual(eightfold_pw._base_url({"tenant": "morganstanley"}), "https://morganstanley.eightfold.ai")

    def test_custom_domain_overrides_host(self) -> None:
        from adapters import eightfold_pw
        company = {"tenant": "appliedmaterials", "base_url": "https://careers.appliedmaterials.com/"}
        self.assertEqual(eightfold_pw._base_url(company), "https://careers.appliedmaterials.com")
        self.assertTrue(eightfold_pw._careers_url(company).startswith("https://careers.appliedmaterials.com/careers?"))


class GreenhousePostedAtTests(unittest.TestCase):
    def _fetch(self, job: dict) -> dict:
        payload = {"jobs": [{"id": 1, "title": "Software Engineer", "location": {"name": "Remote"},
                             "absolute_url": "https://boards.greenhouse.io/acme/jobs/1", **job}]}
        with patch.object(greenhouse.requests, "get", return_value=FakeResponse(payload)):
            return greenhouse.fetch_greenhouse({"name": "Acme", "slug": "acme"})[0]

    def test_first_published_wins_over_a_newer_updated_at(self) -> None:
        # A bulk edit bumps updated_at on every role; that must not make a
        # months-old posting look fresh.
        job = self._fetch({"first_published": "2026-03-22T10:00:00Z", "updated_at": "2026-09-18T04:55:00Z"})
        self.assertEqual(job["posted_at"], "2026-03-22T10:00:00Z")

    def test_bulk_edited_old_role_is_stale_to_the_filter(self) -> None:
        from datetime import datetime, timedelta, timezone
        from filters import is_stale
        now = datetime.now(timezone.utc)
        job = self._fetch({
            "first_published": (now - timedelta(days=120)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "updated_at": (now - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        })
        self.assertTrue(is_stale(job, max_age_hours=72))

    def test_falls_back_to_updated_at_when_first_published_missing(self) -> None:
        job = self._fetch({"updated_at": "2026-06-09T12:00:00Z"})
        self.assertEqual(job["posted_at"], "2026-06-09T12:00:00Z")
