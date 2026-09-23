from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import filters
import jobwatch
import store
import sponsors
from adapters import amazon, hn_hiring, smartrecruiters, workday
from adapters import playwright_scraper


class FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def json(self) -> dict:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class JobWatchReliabilityTests(unittest.TestCase):
    def test_runner_selects_fast_and_browser_lanes(self) -> None:
        companies = [
            {"name": "Okta", "ats": "greenhouse"},
            {"name": "Google", "ats": "playwright"},
        ]

        self.assertEqual([c["name"] for c in jobwatch._selected_companies(companies, "fast")], ["Okta"])
        self.assertEqual([c["name"] for c in jobwatch._selected_companies(companies, "browser")], ["Google"])
        self.assertEqual([c["name"] for c in jobwatch._selected_companies(companies, "all")], ["Okta", "Google"])

    def test_runner_settings_use_positive_config_values_only(self) -> None:
        settings = jobwatch._runner_settings(
            {
                "runner": {
                    "fast_workers": "4",
                    "browser_workers": 0,
                    "fast_timeout_seconds": "bad",
                    "browser_timeout_seconds": 90,
                }
            }
        )

        self.assertEqual(settings["fast_workers"], 4)
        self.assertEqual(settings["browser_workers"], jobwatch.RUNNER_DEFAULTS["browser_workers"])
        self.assertEqual(settings["fast_timeout_seconds"], jobwatch.RUNNER_DEFAULTS["fast_timeout_seconds"])
        self.assertEqual(settings["browser_timeout_seconds"], 90)

    def test_fetch_company_result_records_counts_and_source(self) -> None:
        def fake_adapter(_company: dict) -> list[dict]:
            return [
                {
                    "job_id": "job-1",
                    "company": "Example",
                    "title": "Software Development Engineer",
                    "location": "Austin, TX, USA",
                    "url": "https://example.com/jobs/1",
                    "posted_at": "Posted Today",
                },
                {
                    "job_id": "job-2",
                    "company": "Example",
                    "title": "Product Manager",
                    "location": "Austin, TX, USA",
                    "url": "https://example.com/jobs/2",
                    "posted_at": "Posted Today",
                },
            ]

        with patch.dict(jobwatch.ADAPTERS, {"fake": fake_adapter}, clear=False):
            result = jobwatch._fetch_company_result(
                0,
                {"name": "Example", "ats": "fake"},
                ["Software Development Engineer"],
                [],
            )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["raw_count"], 2)
        self.assertEqual(result["matched_count"], 1)
        self.assertEqual(result["jobs"][0]["source"], "fake")

    def test_select_email_jobs_uses_configured_rank_bands(self) -> None:
        jobs = [
            {"job_id": "top", "rank_band": "Top"},
            {"job_id": "strong", "rank_band": "Strong"},
            {"job_id": "watch", "rank_band": "Watch"},
        ]
        selected = jobwatch._select_email_jobs(jobs, {"alerting": {"email_bands": ["Top"]}})

        self.assertEqual([job["job_id"] for job in selected], ["top"])

    def test_source_health_is_persisted_and_detects_count_drop(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "jobwatch.db"
            previous = [
                {
                    "company": "Example",
                    "ats": "greenhouse",
                    "lane": "fast",
                    "status": "ok",
                    "raw_count": 100,
                    "matched_count": 4,
                    "duration": 0.5,
                    "error": None,
                }
            ]
            current = [
                {
                    "company": "Example",
                    "ats": "greenhouse",
                    "lane": "fast",
                    "status": "ok",
                    "raw_count": 0,
                    "matched_count": 0,
                    "duration": 0.4,
                    "error": None,
                }
            ]

            with patch.object(store, "DB_PATH", db_path):
                self.assertEqual(store.record_source_results(previous, "fast"), 1)
                recent = store.get_recent_source_health(limit=5)
                anomalies = store.detect_source_anomalies(current)

        self.assertEqual(len(recent), 1)
        self.assertEqual(recent[0]["company"], "Example")
        self.assertEqual(len(anomalies), 1)
        self.assertEqual(anomalies[0]["anomaly"], "zero_results")

    def test_sync_jobs_retries_pending_alerts_until_marked_notified(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "jobwatch.db"
            job = {
                "job_id": "job-1",
                "company": "Example",
                "title": "Software Development Engineer",
                "location": "Austin, TX",
                "url": "https://example.com/jobs/1",
                "posted_at": "2026-05-18T10:00:00Z",
                "source": "greenhouse",
            }

            with patch.object(store, "DB_PATH", db_path):
                first_pending = store.sync_jobs([job])
                self.assertEqual([item["job_id"] for item in first_pending], ["job-1"])

                retry_pending = store.sync_jobs([job])
                self.assertEqual([item["job_id"] for item in retry_pending], ["job-1"])

                updated = store.mark_jobs_notified(["job-1"])
                self.assertEqual(updated, 1)

                no_pending = store.sync_jobs([job])
                self.assertEqual(no_pending, [])

    def test_sync_jobs_requeues_reposted_roles_after_newer_posted_at(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "jobwatch.db"
            # Use fresh timestamps: original is "now - 30h", repost is "now".
            # The 30h gap exceeds the 24h requeue tolerance, and "now" is within
            # the 48h freshness window, so the job should be requeued.
            from datetime import datetime, timezone, timedelta
            now = datetime.now(timezone.utc)
            original_ts = (now - timedelta(hours=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
            repost_ts = now.strftime("%Y-%m-%dT%H:%M:%SZ")
            original = {
                "job_id": "job-1",
                "company": "Example",
                "title": "Software Development Engineer",
                "location": "Austin, TX",
                "url": "https://example.com/jobs/1",
                "posted_at": original_ts,
                "source": "greenhouse",
            }
            reposted = {**original, "posted_at": repost_ts}

            with patch.object(store, "DB_PATH", db_path):
                self.assertEqual([item["job_id"] for item in store.sync_jobs([original])], ["job-1"])
                self.assertEqual(store.mark_jobs_notified(["job-1"]), 1)

                requeued = store.sync_jobs([reposted])
                self.assertEqual([item["job_id"] for item in requeued], ["job-1"])

                retry_pending = store.sync_jobs([reposted])
                self.assertEqual([item["job_id"] for item in retry_pending], ["job-1"])

    def test_parse_timestamp_accepts_amazon_style_absolute_dates(self) -> None:
        parsed = filters._parse_timestamp("May 19, 2026")
        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed.year, 2026)
        self.assertEqual(parsed.month, 5)
        self.assertEqual(parsed.day, 19)

    def test_is_non_us_does_not_reject_us_locations_with_ambiguous_city_names(self) -> None:
        self.assertFalse(filters.is_non_us("Amsterdam, New York, USA"))

    def test_fetch_amazon_filters_non_us_results_before_returning_jobs(self) -> None:
        offsets: list[int] = []

        def fake_get(_url: str, *, params: dict, **kwargs) -> FakeResponse:
            offsets.append(params["offset"])
            if params["offset"] == 0:
                payload = {
                    "hits": 150,
                    "jobs": [
                        {
                            "id_icims": "foreign-1",
                            "title": "Software Engineer",
                            "normalized_location": "Sydney, New South Wales, AUS",
                            "job_path": "/en/jobs/foreign-1/software-engineer",
                            "posted_date": "May 19, 2026",
                        },
                        {
                            "id_icims": "us-1",
                            "title": "Software Engineer",
                            "normalized_location": "Austin, Texas, USA",
                            "job_path": "/en/jobs/us-1/software-engineer",
                            "posted_date": "May 19, 2026",
                        },
                    ],
                }
            elif params["offset"] == 100:
                payload = {
                    "hits": 150,
                    "jobs": [
                        {
                            "id_icims": "us-2",
                            "title": "Software Development Engineer",
                            "normalized_location": "Seattle, Washington, USA",
                            "job_path": "/en/jobs/us-2/software-development-engineer",
                            "posted_date": "May 18, 2026",
                        }
                    ],
                }
            else:
                self.fail(f"Unexpected Amazon offset: {params['offset']}")
            return FakeResponse(payload)

        with patch.object(amazon.requests, "get", side_effect=fake_get):
            jobs = amazon.fetch_amazon()

        self.assertEqual(offsets, [0, 100])
        self.assertEqual([job["job_id"] for job in jobs], ["amz-us-1", "amz-us-2"])
        self.assertTrue(all("USA" in job["location"] for job in jobs))

    def test_find_latest_hiring_thread_uses_date_ordering(self) -> None:
        captured_urls: list[str] = []

        def fake_get(url: str, **kwargs) -> FakeResponse:
            captured_urls.append(url)
            return FakeResponse({"hits": [{"objectID": "47975571"}]})

        with patch.object(hn_hiring.requests, "get", side_effect=fake_get):
            story_id = hn_hiring._find_latest_hiring_thread()

        self.assertEqual(story_id, 47975571)
        self.assertEqual(captured_urls, [hn_hiring.ALGOLIA_SEARCH_BY_DATE_URL])

    def test_hn_comment_parser_preserves_created_time_and_unescapes_text(self) -> None:
        comment = {
            "objectID": "12345",
            "comment_text": "Acme | Remote | Fullstack&#x2F;Backend Engineer <a href=\"https://example.com/apply\">apply</a>",
            "created_at": "2026-05-18T12:34:56Z",
        }

        parsed = hn_hiring._parse_comment(comment)

        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed["title"], "Fullstack/Backend Engineer")
        self.assertEqual(parsed["posted_at"], "2026-05-18T12:34:56Z")
        self.assertEqual(parsed["url"], "https://example.com/apply")

    def test_sponsor_matching_uses_normalized_word_boundaries(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "h1b_sponsors.db"
            conn = sqlite3.connect(db_path)
            conn.execute("CREATE TABLE sponsors (employer_name TEXT PRIMARY KEY, lca_count INTEGER)")
            conn.executemany(
                "INSERT INTO sponsors (employer_name, lca_count) VALUES (?, ?)",
                [
                    ("ARM, INC.", 216),
                    ("REGENERON PHARMACEUTICALS, INC.", 263),
                    ("HARMONY PUBLIC SCHOOLS", 27),
                ],
            )
            conn.commit()
            conn.close()

            with patch.object(sponsors, "DB_PATH", db_path):
                sponsors._is_sponsor_cache.clear()
                sponsors._count_cache.clear()
                self.assertTrue(sponsors.is_h1b_sponsor("Arm"))
                self.assertEqual(sponsors.get_sponsor_count("Arm"), 216)

    def test_smartrecruiters_builds_public_urls_when_list_api_only_has_ref(self) -> None:
        company = {"name": "Visa", "slug": "Visa"}

        payload = {
            "content": [
                {
                    "id": "744000122509268",
                    "name": "Sr. SW Engineer",
                    "ref": "https://api.smartrecruiters.com/v1/companies/visa/postings/744000122509268",
                    "releasedDate": "2026-04-23T16:54:54.835Z",
                    "location": {"fullLocation": "Austin, TX, United States"},
                }
            ]
        }

        with patch.object(smartrecruiters.requests, "get", return_value=FakeResponse(payload)):
            jobs = smartrecruiters.fetch_smartrecruiters(company)

        self.assertEqual(
            jobs[0]["url"],
            "https://jobs.smartrecruiters.com/Visa/744000122509268-sr-sw-engineer",
        )

    def test_workday_pagination_handles_total_reset_without_truncating_results(self) -> None:
        company = {
            "name": "Salesforce",
            "tenant": "salesforce.wd12",
            "site": "External_Career_Site",
        }

        def fake_post(_url: str, json: dict, **kwargs) -> FakeResponse:
            offset = json["offset"]
            limit = json["limit"]
            if offset == 0:
                total = 45
                start = 0
                count = 20
            elif offset == 20:
                total = 0
                start = 20
                count = 20
            elif offset == 40:
                total = 0
                start = 40
                count = 5
            else:
                self.fail(f"Unexpected offset requested: {offset}")

            postings = []
            for idx in range(start, start + count):
                postings.append(
                    {
                        "title": f"Software Engineer {idx}",
                        "locationsText": "Remote - USA",
                        "externalPath": f"/job/Remote/Software-Engineer-{idx}_JR{idx}",
                        "postedOn": "Posted Today",
                        "bulletFields": [f"JR{idx}"],
                    }
                )
            return FakeResponse({"total": total, "jobPostings": postings})

        with patch.object(workday.requests, "post", side_effect=fake_post):
            jobs = workday.fetch_workday(company)

        self.assertEqual(len(jobs), 45)
        self.assertEqual(jobs[0]["title"], "Software Engineer 0")
        self.assertEqual(jobs[-1]["title"], "Software Engineer 44")

    def test_workday_respects_max_pages_budget(self) -> None:
        company = {
            "name": "LargeCo",
            "tenant": "largeco.wd1",
            "site": "External",
            "max_pages": 2,
        }
        offsets: list[int] = []

        def fake_post(_url: str, json: dict, **kwargs) -> FakeResponse:
            offsets.append(json["offset"])
            start = json["offset"]
            postings = []
            for idx in range(start, start + json["limit"]):
                postings.append(
                    {
                        "title": f"Software Engineer {idx}",
                        "locationsText": "Remote - USA",
                        "externalPath": f"/job/Remote/Software-Engineer-{idx}_JR{idx}",
                        "postedOn": "Posted Today",
                    }
                )
            return FakeResponse({"total": 500, "jobPostings": postings})

        with patch.object(workday.requests, "post", side_effect=fake_post):
            jobs = workday.fetch_workday(company)

        self.assertEqual(offsets, [0, 20])
        self.assertEqual(len(jobs), 40)

    def test_playwright_job_ids_remain_stable_without_urls(self) -> None:
        first = playwright_scraper._build_job_id(
            "meta",
            "",
            "Software Engineer",
            "Menlo Park, CA",
            None,
        )
        second = playwright_scraper._build_job_id(
            "meta",
            "",
            "Software Engineer",
            "Menlo Park, CA",
            None,
        )
        different = playwright_scraper._build_job_id(
            "meta",
            "",
            "Software Engineer",
            "Seattle, WA",
            None,
        )

        self.assertEqual(first, second)
        self.assertNotEqual(first, different)

    def test_playwright_next_page_url_fallback_increments_page_query(self) -> None:
        self.assertEqual(
            playwright_scraper._increment_page_url(
                "https://www.google.com/about/careers/applications/jobs/results/?q=&location=United+States&page=1"
            ),
            "https://www.google.com/about/careers/applications/jobs/results/?q=&location=United+States&page=2",
        )


if __name__ == "__main__":
    unittest.main()
