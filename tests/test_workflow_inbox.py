from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ranking
import store
import workflow_inbox


class WorkflowInboxTests(unittest.TestCase):
    def test_record_batch_and_render_inbox_create_archive_without_duplicates(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            db_path = base / "jobwatch.db"
            inbox_dir = base / "workflow-inbox"
            config = {
                "keywords": ["Software Development Engineer"],
                "ranking": {"top_picks": 3, "preferred_companies": []},
            }
            batch_jobs = ranking.rank_jobs([
                {
                    "job_id": "job-1",
                    "company": "Example",
                    "title": "Software Development Engineer",
                    "location": "Austin, TX",
                    "url": "https://example.com/jobs/1",
                    "posted_at": "2026-05-18T10:00:00Z",
                    "source": "greenhouse",
                    "first_seen": "2026-05-18T10:01:00+00:00",
                }
            ], config=config)

            with patch.object(store, "DB_PATH", db_path), patch.object(
                workflow_inbox, "WORKFLOW_INBOX_DIR", inbox_dir
            ), patch.object(workflow_inbox, "WORKFLOW_BATCH_DIR", inbox_dir / "batches"):
                archive_path = workflow_inbox.record_batch(
                    status="sent",
                    jobs=batch_jobs,
                    recipient="me@example.com",
                    subject="[JobWatch Inbox] JobWatch: 1 new role(s) found",
                )
                index_path = workflow_inbox.render_inbox(limit=10, config=config)
                summary = workflow_inbox.print_summary(limit=10, config=config)

            self.assertIsNotNone(archive_path)
            assert archive_path is not None
            self.assertTrue(archive_path.exists())
            self.assertTrue(index_path.exists())
            self.assertIn("Recent batches:", summary)
            self.assertIn("Batch 1", index_path.read_text())
            self.assertIn("Software Development Engineer", archive_path.read_text())
            self.assertIn("Priority:", archive_path.read_text())

    def test_pending_delivery_is_ranked_in_inbox_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            db_path = base / "jobwatch.db"
            inbox_dir = base / "workflow-inbox"
            config = {
                "keywords": ["Software Development Engineer", "Engineer II"],
                "ranking": {"top_picks": 3, "preferred_companies": []},
            }
            jobs = [
                {
                    "job_id": "older",
                    "company": "BoardCo",
                    "title": "Engineer II",
                    "location": "Austin, TX",
                    "url": "https://example.com/jobs/older",
                    "posted_at": "2026-05-18T04:00:00Z",
                    "source": "hn_hiring",
                },
                {
                    "job_id": "fresh",
                    "company": "FastCo",
                    "title": "Software Development Engineer",
                    "location": "Remote - United States",
                    "url": "https://example.com/jobs/fresh",
                    "posted_at": "2026-05-18T16:00:00Z",
                    "source": "greenhouse",
                },
            ]

            with patch.object(store, "DB_PATH", db_path), patch.object(
                workflow_inbox, "WORKFLOW_INBOX_DIR", inbox_dir
            ), patch.object(workflow_inbox, "WORKFLOW_BATCH_DIR", inbox_dir / "batches"), patch.object(
                ranking, "is_h1b_sponsor", side_effect=lambda company: company == "FastCo"
            ):
                store.sync_jobs(jobs)
                index_path = workflow_inbox.render_inbox(limit=10, config=config)
                summary = workflow_inbox.print_summary(limit=10, config=config)

            index_text = index_path.read_text()
            self.assertLess(index_text.index("FastCo"), index_text.index("BoardCo"))
            self.assertLess(summary.index("FastCo"), summary.index("BoardCo"))
            self.assertIn("Priority:", index_text)


if __name__ == "__main__":
    unittest.main()
