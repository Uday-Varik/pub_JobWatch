"""Tests for the crash-safe notification machinery, retention cleanup,
and source anomaly detection — the state transitions behind the
duplicate-alert and lost-alert fixes."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import store


def _job(job_id: str = "job-1", **overrides) -> dict:
    base = {
        "job_id": job_id,
        "company": "Example",
        "title": "Software Development Engineer",
        "location": "Austin, TX",
        "url": f"https://example.com/jobs/{job_id}",
        "posted_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "greenhouse",
    }
    base.update(overrides)
    return base


class PendingNotificationTests(unittest.TestCase):
    def test_pending_sentinel_excludes_job_from_requeue(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "jobwatch.db"
            with patch.object(store, "DB_PATH", db_path):
                store.sync_jobs([_job()])
                store.mark_jobs_pending_notification(["job-1"])
                # While 'pending', sync_jobs must treat the job as in-flight,
                # not re-queue it (that would duplicate the alert).
                self.assertEqual(store.sync_jobs([_job()]), [])

    def test_reset_returns_pending_jobs_to_queue(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "jobwatch.db"
            with patch.object(store, "DB_PATH", db_path):
                store.sync_jobs([_job()])
                store.mark_jobs_pending_notification(["job-1"])
                self.assertEqual(store.reset_pending_notifications(), 1)
                requeued = store.sync_jobs([_job()])
                self.assertEqual([j["job_id"] for j in requeued], ["job-1"])

    def test_mark_notified_overwrites_pending_sentinel(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "jobwatch.db"
            with patch.object(store, "DB_PATH", db_path):
                store.sync_jobs([_job()])
                store.mark_jobs_pending_notification(["job-1"])
                self.assertEqual(store.mark_jobs_notified(["job-1"]), 1)
                # Fully notified: neither requeued nor reset-able.
                self.assertEqual(store.sync_jobs([_job()]), [])
                self.assertEqual(store.reset_pending_notifications(), 0)

    def test_scoped_reset_leaves_other_pending_rows_alone(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "jobwatch.db"
            with patch.object(store, "DB_PATH", db_path):
                store.sync_jobs([_job("job-1"), _job("job-2")])
                store.mark_jobs_pending_notification(["job-1", "job-2"])
                # Scoped reset: only job-1 goes back to the queue; job-2's
                # in-flight marker (e.g. an overlapping run) is untouched.
                self.assertEqual(store.reset_pending_notifications(["job-1"]), 1)
                requeued = store.sync_jobs([_job("job-1"), _job("job-2")])
                self.assertEqual([j["job_id"] for j in requeued], ["job-1"])

    def test_scoped_reset_with_empty_list_is_noop(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "jobwatch.db"
            with patch.object(store, "DB_PATH", db_path):
                store.sync_jobs([_job()])
                store.mark_jobs_pending_notification(["job-1"])
                self.assertEqual(store.reset_pending_notifications([]), 0)


class DedupTests(unittest.TestCase):
    def test_same_job_id_collapsed_within_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "jobwatch.db"
            with patch.object(store, "DB_PATH", db_path):
                pending = store.sync_jobs([_job("dup"), _job("dup")])
            self.assertEqual(len(pending), 1)

    def test_same_url_different_id_collapsed_within_run(self) -> None:
        # Same posting re-listed under two job_ids in one scrape -> one alert.
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "jobwatch.db"
            with patch.object(store, "DB_PATH", db_path):
                a = _job("id-a", url="https://co.com/jobs/req123")
                b = _job("id-b", url="https://co.com/jobs/req123")
                pending = store.sync_jobs([a, b])
            self.assertEqual([j["job_id"] for j in pending], ["id-a"])

    def test_churned_id_same_url_not_realerted_across_runs(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "jobwatch.db"
            with patch.object(store, "DB_PATH", db_path):
                run1 = store.sync_jobs([_job("old-id", url="https://co.com/jobs/req9")])
                self.assertEqual([j["job_id"] for j in run1], ["old-id"])
                store.mark_jobs_notified(["old-id"])
                # Next run: same posting, new id -> must NOT alert again.
                run2 = store.sync_jobs([_job("new-id", url="https://co.com/jobs/req9")])
                self.assertEqual(run2, [])

    def test_empty_urls_are_not_collapsed(self) -> None:
        # Jobs without a URL must each survive on their own job_id.
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "jobwatch.db"
            with patch.object(store, "DB_PATH", db_path):
                pending = store.sync_jobs([_job("a", url=""), _job("b", url="")])
            self.assertEqual({j["job_id"] for j in pending}, {"a", "b"})


class RetentionCleanupTests(unittest.TestCase):
    def _backdate(self, db_path: Path, job_id: str, days: int) -> None:
        old = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "UPDATE seen_jobs SET last_seen = ? WHERE job_id = ?",
                (old, job_id),
            )
            conn.commit()

    def _set_status(self, db_path: Path, job_id: str, status: str) -> None:
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "UPDATE seen_jobs SET status = ? WHERE job_id = ?",
                (status, job_id),
            )
            conn.commit()

    def _job_ids(self, db_path: Path) -> set[str]:
        with sqlite3.connect(db_path) as conn:
            rows = conn.execute("SELECT job_id FROM seen_jobs").fetchall()
        return {row[0] for row in rows}

    def test_deletes_only_stale_new_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "jobwatch.db"
            with patch.object(store, "DB_PATH", db_path):
                store.sync_jobs([_job("stale-new"), _job("fresh-new"), _job("stale-applied")])
                self._backdate(db_path, "stale-new", days=40)
                self._backdate(db_path, "stale-applied", days=40)
                self._set_status(db_path, "stale-applied", "applied")

                store.cleanup_old_jobs()

            remaining = self._job_ids(db_path)
        # Stale 'new' row is gone; fresh row and applied row survive
        # regardless of age (they carry application tracking).
        self.assertEqual(remaining, {"fresh-new", "stale-applied"})

    def test_fresh_last_seen_protects_old_first_seen(self) -> None:
        # The live-posting invariant: sync_jobs refreshes last_seen on every
        # scrape, so a job first seen months ago but still listed must NOT
        # be deleted (deleting it would re-alert it as brand-new).
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "jobwatch.db"
            with patch.object(store, "DB_PATH", db_path):
                store.sync_jobs([_job("long-lived")])
                old = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
                with sqlite3.connect(db_path) as conn:
                    conn.execute(
                        "UPDATE seen_jobs SET first_seen = ? WHERE job_id = 'long-lived'",
                        (old,),
                    )
                    conn.commit()
                # Re-scrape refreshes last_seen, then cleanup runs.
                store.sync_jobs([_job("long-lived")])
                store.cleanup_old_jobs()

            self.assertIn("long-lived", self._job_ids(db_path))


class SourceAnomalyTests(unittest.TestCase):
    PREVIOUS = [
        {
            "company": "Example",
            "ats": "greenhouse",
            "lane": "fast",
            "status": "ok",
            "raw_count": 100,
            "matched_count": 5,
            "duration": 0.4,
            "error": None,
        }
    ]

    def _current(self, **overrides) -> list[dict]:
        base = {
            "company": "Example",
            "ats": "greenhouse",
            "lane": "fast",
            "status": "ok",
            "raw_count": 100,
            "matched_count": 5,
            "duration": 0.4,
            "error": None,
        }
        base.update(overrides)
        return [base]

    def test_count_drop_detected(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "jobwatch.db"
            with patch.object(store, "DB_PATH", db_path):
                store.record_source_results(self.PREVIOUS, "fast")
                anomalies = store.detect_source_anomalies(self._current(raw_count=15))
        self.assertEqual(len(anomalies), 1)
        self.assertEqual(anomalies[0]["anomaly"], "count_drop")

    def test_source_failed_detected(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "jobwatch.db"
            with patch.object(store, "DB_PATH", db_path):
                store.record_source_results(self.PREVIOUS, "fast")
                anomalies = store.detect_source_anomalies(
                    self._current(status="error", raw_count=0, error="boom")
                )
        self.assertEqual(len(anomalies), 1)
        self.assertEqual(anomalies[0]["anomaly"], "source_failed")

    def test_healthy_run_has_no_anomalies(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "jobwatch.db"
            with patch.object(store, "DB_PATH", db_path):
                store.record_source_results(self.PREVIOUS, "fast")
                anomalies = store.detect_source_anomalies(self._current(raw_count=95))
        self.assertEqual(anomalies, [])


if __name__ == "__main__":
    unittest.main()


class StateSizeTests(unittest.TestCase):
    def test_cleanup_prunes_health_and_batch_archive_and_compacts(self) -> None:
        import sqlite3
        import tempfile
        from datetime import datetime, timedelta, timezone
        import workflow_inbox

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "jobwatch.db"
            with patch.object(store, "DB_PATH", db_path):
                old = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat()
                recent = datetime.now(timezone.utc).isoformat()
                conn = workflow_inbox._connect()
                for i in range(400):
                    conn.execute(
                        "INSERT INTO source_runs (run_at, selected_lane, company, ats, source_lane, status, raw_count, matched_count, duration_seconds) "
                        "VALUES (?, 'all', ?, 'greenhouse', 'fast', 'ok', 1, 0, 1.0)", (old, f"C{i}"))
                conn.execute("INSERT INTO source_runs (run_at, selected_lane, company, ats, source_lane, status, raw_count, matched_count, duration_seconds) "
                             "VALUES (?, 'all', 'Keep', 'greenhouse', 'fast', 'ok', 1, 0, 1.0)", (recent,))
                conn.execute("INSERT INTO notification_batches (batch_id, created_at, status, job_count) VALUES (1, ?, 'sent', 400)", (old,))
                conn.execute("INSERT INTO notification_batches (batch_id, created_at, status, job_count) VALUES (2, ?, 'sent', 1)", (recent,))
                for i in range(400):
                    conn.execute("INSERT INTO notification_batch_jobs (batch_id, position, job_id, company, title, url) VALUES (1, ?, ?, 'C', ?, ?)",
                                 (i, f"j{i}", "Software Engineer " * 5, "https://example.com/" + "x" * 120))
                conn.execute("INSERT INTO notification_batch_jobs (batch_id, position, job_id, company, title) VALUES (2, 1, 'keep', 'C', 'T')")
                conn.commit(); conn.close()

                deleted = store.cleanup_old_jobs(retention_days=60, health_days=30, batch_days=14)
                self.assertEqual(deleted, 400 + 400 + 1)
                reclaimed = store.compact_database()
                self.assertGreater(reclaimed, 0)

                check = sqlite3.connect(db_path)
                self.assertEqual(check.execute("SELECT company FROM source_runs").fetchall(), [("Keep",)])
                self.assertEqual(check.execute("SELECT batch_id FROM notification_batches").fetchall(), [(2,)])
                self.assertEqual(check.execute("SELECT job_id FROM notification_batch_jobs").fetchall(), [("keep",)])
                self.assertEqual(check.execute("PRAGMA freelist_count").fetchone()[0], 0)
                check.close()

    def test_cleanup_without_batch_tables_is_safe(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(store, "DB_PATH", Path(tmp) / "jobwatch.db"):
                self.assertEqual(store.cleanup_old_jobs(), 0)
                self.assertEqual(store.compact_database(), 0)
