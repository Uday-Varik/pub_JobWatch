"""Tests for 3-tier notification routing (target / faang / other).

Covers the company->tier map, topic env resolution, which jobs each tier
alerts on, and the crash-safe per-tier delivery in cmd_run: a failure in one
tier resets only that tier's job ids, the rest are marked notified."""

from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest.mock import patch

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import jobwatch


CONFIG = {
    "companies": [
        {"name": "Salesforce", "tier": "target"},
        {"name": "Amazon", "tier": "faang"},
        {"name": "Reddit", "tier": "other"},
        {"name": "Legacy"},  # no tier -> default 'other'
    ],
    "alerting": {"email_bands": ["Top", "Strong"]},
    "notification": {
        "tiers": {
            "target": {
                "subject_tag": "🎯 TARGET",
                "ntfy_priority": "5",
                "ntfy_topic_env": ["JOBWATCH_NTFY_TOPIC_TARGET", "JOBWATCH_NTFY_TOPIC"],
                "alert": True,
            },
            "faang": {
                "subject_tag": "🚀 FAANG",
                "ntfy_priority": "4",
                "ntfy_topic_env": ["JOBWATCH_NTFY_TOPIC_FAANG"],
                "alert": True,
            },
            "other": {"subject_tag": "OTHER", "alert": False},
        }
    },
}


def _job(company: str, band: str = "Top", jid: str | None = None) -> dict:
    return {
        "job_id": jid or f"{company}-1",
        "company": company,
        "title": "Software Engineer",
        "location": "Austin, TX",
        "url": "https://example.com/1",
        "rank_band": band,
    }


class TierMapTests(unittest.TestCase):
    def test_company_tier_map_with_default(self) -> None:
        m = jobwatch._company_tier_map(CONFIG)
        self.assertEqual(m["Salesforce"], "target")
        self.assertEqual(m["Amazon"], "faang")
        self.assertEqual(m["Reddit"], "other")
        self.assertEqual(m["Legacy"], "other")  # missing tier defaults

    def test_topic_env_resolution_prefers_first_set(self) -> None:
        cfg = CONFIG["notification"]["tiers"]["target"]
        with patch.dict(os.environ, {"JOBWATCH_NTFY_TOPIC": "old", "JOBWATCH_NTFY_TOPIC_TARGET": "new"}, clear=False):
            self.assertEqual(jobwatch._resolve_tier_topic(cfg), "new")

    def test_topic_env_falls_back_to_legacy(self) -> None:
        cfg = CONFIG["notification"]["tiers"]["target"]
        with patch.dict(os.environ, {"JOBWATCH_NTFY_TOPIC": "legacy"}, clear=False):
            os.environ.pop("JOBWATCH_NTFY_TOPIC_TARGET", None)
            self.assertEqual(jobwatch._resolve_tier_topic(cfg), "legacy")

    def test_topic_env_empty_when_none_set(self) -> None:
        cfg = CONFIG["notification"]["tiers"]["faang"]
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("JOBWATCH_NTFY_TOPIC_FAANG", None)
            self.assertEqual(jobwatch._resolve_tier_topic(cfg), "")


class RoutingTests(unittest.TestCase):
    def test_other_tier_is_excluded_from_alerts(self) -> None:
        jobs = [_job("Salesforce"), _job("Amazon"), _job("Reddit"), _job("Legacy")]
        grouped = jobwatch._route_email_jobs(jobs, CONFIG)
        self.assertEqual(set(grouped.keys()), {"target", "faang"})
        self.assertEqual([j["company"] for j in grouped["target"]], ["Salesforce"])
        self.assertEqual([j["company"] for j in grouped["faang"]], ["Amazon"])

    def test_watch_band_is_not_alerted(self) -> None:
        jobs = [_job("Salesforce", band="Watch"), _job("Amazon", band="Top")]
        grouped = jobwatch._route_email_jobs(jobs, CONFIG)
        self.assertNotIn("target", grouped)
        self.assertIn("faang", grouped)

    def test_other_fires_when_alert_enabled(self) -> None:
        # When 'other' has alert: true it routes like any other tier.
        cfg = {**CONFIG, "notification": {"tiers": {
            **CONFIG["notification"]["tiers"],
            "other": {"subject_tag": "📋 OTHER", "ntfy_priority": "3",
                      "ntfy_topic_env": ["JOBWATCH_NTFY_TOPIC_OTHER"], "alert": True},
        }}}
        jobs = [_job("Salesforce"), _job("Amazon"), _job("Reddit"), _job("Legacy")]
        grouped = jobwatch._route_email_jobs(jobs, cfg)
        self.assertEqual(set(grouped.keys()), {"target", "faang", "other"})
        # Reddit (explicit other) and Legacy (default other) both land in other.
        self.assertEqual({j["company"] for j in grouped["other"]}, {"Reddit", "Legacy"})


class TierBandOverrideTests(unittest.TestCase):
    """Per-tier email_bands: date-less sources (Playwright, Netflix) can never
    score above Watch, so target/faang may opt into every band."""

    def _cfg_with(self, tier: str, bands) -> dict:
        tiers = {**CONFIG["notification"]["tiers"]}
        tiers[tier] = {**tiers[tier], "email_bands": bands}
        return {**CONFIG, "notification": {"tiers": tiers}}

    def test_tier_all_lets_watch_through(self) -> None:
        cfg = self._cfg_with("target", "all")
        jobs = [_job("Salesforce", band="Watch"), _job("Amazon", band="Watch")]
        grouped = jobwatch._route_email_jobs(jobs, cfg)
        self.assertEqual([j["company"] for j in grouped.get("target", [])], ["Salesforce"])
        self.assertNotIn("faang", grouped)  # faang still on the global gate

    def test_tier_list_override(self) -> None:
        cfg = self._cfg_with("faang", ["Top", "Strong", "Watch"])
        jobs = [_job("Amazon", band="Watch")]
        self.assertIn("faang", jobwatch._route_email_jobs(jobs, cfg))

    def test_empty_override_falls_back_to_global(self) -> None:
        cfg = self._cfg_with("target", [])
        jobs = [_job("Salesforce", band="Watch")]
        self.assertEqual(jobwatch._route_email_jobs(jobs, cfg), {})

    def test_newgrad_override_applies_to_newgrad_stream(self) -> None:
        tiers = {**CONFIG["notification"]["tiers"],
                 "newgrad": {"subject_tag": "🎓", "alert": True, "email_bands": "all"}}
        cfg = {**CONFIG, "notification": {"tiers": tiers}}
        job = {**_job("Reddit", band="Watch"), "title": "Software Engineer, New Grad"}
        grouped = jobwatch._route_email_jobs([job], cfg)
        self.assertEqual([j["title"] for j in grouped.get("newgrad", [])], [job["title"]])

    def test_dateless_playwright_job_is_alerted_for_target(self) -> None:
        # End-to-end regression: a real ranked job with no posted_at lands in
        # Watch and used to be archived silently even for target companies.
        from ranking import rank_job
        cfg = self._cfg_with("target", "all")
        cfg["keywords"] = ["Software Engineer"]
        ranked = rank_job({"job_id": "gs-1", "company": "Salesforce",
                           "title": "Software Engineer II", "location": "New York, NY",
                           "source": "playwright", "posted_at": None}, cfg)
        self.assertEqual(ranked["rank_band"], "Watch")
        self.assertIn("target", jobwatch._route_email_jobs([ranked], cfg))
        self.assertEqual(jobwatch._route_email_jobs([ranked], CONFIG), {})


class TierDeliveryTests(unittest.TestCase):
    def test_deliver_tier_tags_subject_and_routes_topic(self) -> None:
        captured = {}

        def fake_send_email(jobs, config, subject_tag=""):
            captured["subject_tag"] = subject_tag
            return True, "Email sent"

        def fake_send_ntfy(jobs, config, topic="", priority="", title=""):
            captured["topic"] = topic
            captured["priority"] = priority

        with patch.object(jobwatch, "send_email", side_effect=fake_send_email), patch.object(
            jobwatch, "send_ntfy", side_effect=fake_send_ntfy
        ), patch.dict(os.environ, {"JOBWATCH_NTFY_TOPIC_FAANG": "faang-topic"}, clear=False):
            sent, _ = jobwatch._deliver_tier("faang", [_job("Amazon")], CONFIG)

        self.assertTrue(sent)
        self.assertEqual(captured["subject_tag"], "🚀 FAANG")
        self.assertEqual(captured["topic"], "faang-topic")
        self.assertEqual(captured["priority"], "4")

    def test_failed_email_does_not_push(self) -> None:
        pushed = {"called": False}

        def fake_send_ntfy(*a, **k):
            pushed["called"] = True

        with patch.object(jobwatch, "send_email", return_value=(False, "SMTP 421")), patch.object(
            jobwatch, "send_ntfy", side_effect=fake_send_ntfy
        ):
            sent, msg = jobwatch._deliver_tier("target", [_job("Salesforce")], CONFIG)

        self.assertFalse(sent)
        self.assertFalse(pushed["called"])


class CrashSafePerTierTests(unittest.TestCase):
    """A failure in one tier must reset only that tier's job ids."""

    def _run_with(self, deliver_results: dict):
        marked_notified = {}
        reset_called = {}

        def fake_deliver(tier, jobs, config):
            ok = deliver_results[tier]
            return (ok, "ok" if ok else "SMTP 421")

        def fake_mark_notified(ids):
            marked_notified["ids"] = list(ids)
            return len(ids)

        def fake_reset(ids=None):
            reset_called["ids"] = list(ids) if ids else None
            return len(ids) if ids else 0

        pending = [_job("Salesforce", jid="t1"), _job("Amazon", jid="f1"), _job("Reddit", jid="o1")]

        with patch.object(jobwatch, "_deliver_tier", side_effect=fake_deliver), patch.object(
            jobwatch, "mark_jobs_pending_notification", return_value=3
        ), patch.object(jobwatch, "mark_jobs_notified", side_effect=fake_mark_notified), patch.object(
            jobwatch, "reset_pending_notifications", side_effect=fake_reset
        ), patch.object(jobwatch, "record_batch", return_value=None), patch.object(
            jobwatch, "rank_jobs", return_value=pending
        ), patch.object(jobwatch, "sync_jobs", return_value=pending), patch.object(
            jobwatch, "_run_fetch_plan", return_value=[]
        ), patch.object(jobwatch, "detect_source_anomalies", return_value=[]), patch.object(
            jobwatch, "record_source_results", return_value=0
        ), patch.object(jobwatch, "render_inbox", return_value="/tmp/inbox.md"), patch.object(
            jobwatch, "cleanup_old_jobs", return_value=0
        ), patch.object(jobwatch, "get_stats", return_value={"total": 3}), patch.object(
            jobwatch, "print_report", return_value=None
        ), patch.object(jobwatch, "load_config", return_value=CONFIG):
            class Args:
                lane = "all"
                dry_run = False
            try:
                jobwatch.cmd_run(Args())
            except SystemExit:
                pass
        return marked_notified, reset_called

    def test_faang_failure_resets_only_faang_ids(self) -> None:
        marked, reset = self._run_with({"target": True, "faang": False})
        # target (t1) + other (o1) marked notified; faang (f1) reset for retry
        self.assertEqual(set(marked["ids"]), {"t1", "o1"})
        self.assertEqual(reset["ids"], ["f1"])

    def test_all_success_marks_everything_notified(self) -> None:
        marked, reset = self._run_with({"target": True, "faang": True})
        self.assertEqual(set(marked["ids"]), {"t1", "f1", "o1"})
        self.assertIsNone(reset.get("ids", None))


if __name__ == "__main__":
    unittest.main()


class CompanyKeywordOverrideTests(unittest.TestCase):
    def test_company_keywords_replace_global(self) -> None:
        self.assertEqual(jobwatch._company_keywords({"keywords": ["LLM", " Gemini "]}, ["Software Engineer"]), ["LLM", "Gemini"])

    def test_missing_or_empty_override_uses_global(self) -> None:
        self.assertEqual(jobwatch._company_keywords({}, ["Software Engineer"]), ["Software Engineer"])
        self.assertEqual(jobwatch._company_keywords({"keywords": []}, ["Software Engineer"]), ["Software Engineer"])
        self.assertEqual(jobwatch._company_keywords({"keywords": "LLM"}, ["Software Engineer"]), ["Software Engineer"])

    def test_override_applied_in_fetch(self) -> None:
        raw = [
            {"job_id": "1", "title": "Software Engineer III", "location": "Austin, TX", "posted_at": "Posted Today"},
            {"job_id": "2", "title": "Software Engineer, Machine Learning", "location": "Austin, TX", "posted_at": "Posted Today"},
        ]
        company = {"name": "G", "ats": "greenhouse", "keywords": ["Machine Learning"]}
        with patch.dict(jobwatch.ADAPTERS, {"greenhouse": lambda c: raw}):
            result = jobwatch._fetch_company_result(0, company, ["Software Engineer"], [])
        self.assertEqual([j["job_id"] for j in result["jobs"]], ["2"])


class PauseSwitchTests(unittest.TestCase):
    def test_paused_flag(self) -> None:
        self.assertTrue(jobwatch._is_paused({"paused": True}))
        self.assertFalse(jobwatch._is_paused({"paused": False}))
        self.assertFalse(jobwatch._is_paused({}))

    def test_paused_run_fetches_nothing(self) -> None:
        cfg = {"paused": True, "companies": [{"name": "X", "ats": "greenhouse", "slug": "x", "tier": "target"}],
               "keywords": ["Software Engineer"], "notification": {"tiers": {}}}
        with patch("jobwatch.load_config", return_value=cfg), \
             patch("jobwatch._run_fetch_plan") as fetch, \
             patch("jobwatch.sync_jobs") as sync:
            jobwatch.cmd_run(type("A", (), {"lane": "all", "dry_run": False})())
        fetch.assert_not_called()
        sync.assert_not_called()


class SuppressAlertsTests(unittest.TestCase):
    def test_env_flag_parsing(self) -> None:
        for value, expected in (("1", True), ("true", True), ("", False), ("0", False)):
            with patch.dict(os.environ, {"JOBWATCH_SUPPRESS_ALERTS": value}):
                self.assertEqual(jobwatch._alerts_suppressed(), expected, value)

    def test_suppressed_run_marks_notified_without_sending(self) -> None:
        jobs = [_job("Salesforce", jid="s-1"), _job("Amazon", jid="a-1")]
        args = type("A", (), {"lane": "fast", "dry_run": False})()
        with patch.dict(os.environ, {"JOBWATCH_SUPPRESS_ALERTS": "1"}), \
             patch("jobwatch.load_config", return_value=CONFIG), \
             patch("jobwatch._run_fetch_plan", return_value=[{"index": 0, "company": "Salesforce", "ats": "workday", "lane": "fast", "raw_count": 2, "matched_count": 2, "jobs": jobs, "duration": 1.0, "status": "ok", "error": None}]), \
             patch("jobwatch.detect_source_anomalies", return_value=[]), \
             patch("jobwatch.reset_pending_notifications", return_value=0), \
             patch("jobwatch.sync_jobs", return_value=jobs), \
             patch("jobwatch.rank_jobs", return_value=jobs), \
             patch("jobwatch.print_report"), \
             patch("jobwatch.record_source_results", return_value=1), \
             patch("jobwatch.mark_jobs_pending_notification"), \
             patch("jobwatch.mark_jobs_notified") as notified, \
             patch("jobwatch.record_batch"), \
             patch("jobwatch.render_inbox", return_value="x"), \
             patch("jobwatch.cleanup_old_jobs", return_value=0), \
             patch("jobwatch.get_stats", return_value={"total": 2}), \
             patch("jobwatch._deliver_tier") as deliver:
            jobwatch.cmd_run(args)
        deliver.assert_not_called()
        notified.assert_called_once_with(["s-1", "a-1"])
