from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest.mock import patch

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import notifier


class FakeSMTP:
    def __init__(self, host: str, port: int, timeout: float | None = None) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self.logged_in_as: tuple[str, str] | None = None
        self.sent_messages = []

    def __enter__(self) -> "FakeSMTP":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def starttls(self) -> None:
        return None

    def login(self, username: str, password: str) -> None:
        self.logged_in_as = (username, password)

    def send_message(self, message) -> None:
        self.sent_messages.append(message)


class FakeIMAP:
    def __init__(self, _host: str, timeout: float | None = None) -> None:
        self.timeout = timeout
        self.selected_folder = ""
        self.inbox_has_message = False
        self.search_history: list[tuple[str, tuple[str, ...]]] = []
        self.store_history: list[tuple[bytes, str, str]] = []
        self.copy_history: list[tuple[str, str]] = []
        self.append_history: list[tuple[str, bytes]] = []

    def login(self, _username: str, _password: str) -> tuple[str, list[bytes]]:
        return "OK", [b"Logged in"]

    def logout(self) -> tuple[str, list[bytes]]:
        return "BYE", [b"Logged out"]

    def select(self, folder: str) -> tuple[str, list[bytes]]:
        self.selected_folder = folder.strip('"')
        return "OK", [b"1"]

    def search(self, _charset, *criteria: str) -> tuple[str, list[bytes]]:
        self.search_history.append((self.selected_folder, criteria))
        if self.selected_folder == "INBOX" and self.inbox_has_message:
            return "OK", [b"42"]
        if self.selected_folder in ("[Gmail]/All Mail", "[Google Mail]/All Mail", "All Mail"):
            return "OK", [b"41"]
        return "OK", [b""]

    def store(self, message_id: bytes, command: str, flags: str) -> tuple[str, list[bytes]]:
        self.store_history.append((message_id, command, flags))
        return "OK", [b"stored"]

    def copy(self, message_set: str, folder: str) -> tuple[str, list[bytes]]:
        self.copy_history.append((message_set, folder))
        self.inbox_has_message = True
        return "OK", [b"copied"]

    def append(self, folder: str, _flags, _date_time, message: bytes) -> tuple[str, list[bytes]]:
        self.append_history.append((folder, message))
        self.inbox_has_message = True
        return "OK", [b"appended"]


class NotifierDeliveryTests(unittest.TestCase):
    def test_send_email_promotes_same_gmail_alert_back_to_unread_inbox(self) -> None:
        jobs = [
            {
                "job_id": "job-1",
                "company": "Example",
                "title": "Software Development Engineer",
                "location": "Austin, TX",
                "url": "https://example.com/jobs/1",
                "posted_at": "2026-05-19T12:00:00Z",
                "source": "greenhouse",
                "rank_band": "Top",
                "rank_score": 140,
                "rank_reasons": ["posted in the last hour", 'matched "Software Development Engineer"'],
                "rank_reason_text": 'posted in the last hour; matched "Software Development Engineer"',
            }
        ]
        config = {
            "notification": {
                "email": "me@gmail.com",
                "smtp_host": "smtp.gmail.com",
                "smtp_port": 587,
                "subject_prefix": "[JobWatch Inbox]",
            },
            "ranking": {"top_picks": 3, "preferred_companies": []},
        }

        smtp_instances: list[FakeSMTP] = []
        imap_instances: list[FakeIMAP] = []

        def fake_smtp(host: str, port: int, timeout: float | None = None) -> FakeSMTP:
            instance = FakeSMTP(host, port, timeout=timeout)
            smtp_instances.append(instance)
            return instance

        def fake_imap(host: str, timeout: float | None = None) -> FakeIMAP:
            instance = FakeIMAP(host, timeout=timeout)
            imap_instances.append(instance)
            return instance

        with patch.dict(
            os.environ,
            {"JOBWATCH_EMAIL_USER": "me@gmail.com", "JOBWATCH_EMAIL_PASSWORD": "app-password"},
            clear=False,
        ), patch.object(notifier.smtplib, "SMTP", side_effect=fake_smtp), patch.object(
            notifier.imaplib, "IMAP4_SSL", side_effect=fake_imap
        ), patch.object(
            notifier, "is_h1b_sponsor", return_value=False
        ):
            sent, detail = notifier.send_email(jobs, config)

        self.assertTrue(sent)
        self.assertIn("Gmail Inbox", detail)
        self.assertEqual(len(smtp_instances), 1)
        self.assertEqual(len(smtp_instances[0].sent_messages), 1)
        self.assertEqual(len(imap_instances), 1)
        self.assertTrue(imap_instances[0].copy_history)
        self.assertTrue(imap_instances[0].store_history)
        self.assertFalse(imap_instances[0].append_history)

    def test_send_email_skips_gmail_promotion_for_different_recipient(self) -> None:
        jobs = [
            {
                "job_id": "job-1",
                "company": "Example",
                "title": "Software Development Engineer",
                "location": "Austin, TX",
                "url": "https://example.com/jobs/1",
                "posted_at": "2026-05-19T12:00:00Z",
                "source": "greenhouse",
                "rank_band": "Top",
                "rank_score": 140,
                "rank_reasons": ["posted in the last hour"],
                "rank_reason_text": "posted in the last hour",
            }
        ]
        config = {
            "notification": {
                "email": "alerts@example.com",
                "smtp_host": "smtp.gmail.com",
                "smtp_port": 587,
            },
            "ranking": {"top_picks": 3, "preferred_companies": []},
        }

        smtp_instances: list[FakeSMTP] = []

        def fake_smtp(host: str, port: int, timeout: float | None = None) -> FakeSMTP:
            instance = FakeSMTP(host, port, timeout=timeout)
            smtp_instances.append(instance)
            return instance

        with patch.dict(
            os.environ,
            {"JOBWATCH_EMAIL_USER": "me@gmail.com", "JOBWATCH_EMAIL_PASSWORD": "app-password"},
            clear=False,
        ), patch.object(notifier.smtplib, "SMTP", side_effect=fake_smtp), patch.object(
            notifier, "is_h1b_sponsor", return_value=False
        ):
            sent, detail = notifier.send_email(jobs, config)

        self.assertTrue(sent)
        self.assertEqual(detail, "Email sent to alerts@example.com")
        self.assertEqual(len(smtp_instances), 1)
        self.assertEqual(len(smtp_instances[0].sent_messages), 1)

    def _same_account_config(self) -> dict:
        return {
            "notification": {
                "email": "me@gmail.com",
                "smtp_host": "smtp.gmail.com",
                "smtp_port": 587,
            },
            "ranking": {"top_picks": 3, "preferred_companies": []},
        }

    def _job(self) -> dict:
        return {
            "job_id": "job-1",
            "company": "Example",
            "title": "Software Development Engineer",
            "location": "Austin, TX",
            "url": "https://example.com/jobs/1",
            "posted_at": "2026-05-19T12:00:00Z",
            "source": "greenhouse",
            "rank_band": "Top",
            "rank_score": 140,
            "rank_reasons": ["posted in the last hour"],
            "rank_reason_text": "posted in the last hour",
        }

    def test_gmail_promotion_falls_back_to_append_when_message_not_found(self) -> None:
        class EmptySearchIMAP(FakeIMAP):
            def search(self, _charset, *criteria):
                self.search_history.append((self.selected_folder, criteria))
                if self.selected_folder == "INBOX" and self.inbox_has_message:
                    return "OK", [b"42"]
                return "OK", [b""]

        imap_instances: list[EmptySearchIMAP] = []

        def fake_imap(host: str, timeout: float | None = None) -> EmptySearchIMAP:
            instance = EmptySearchIMAP(host, timeout=timeout)
            imap_instances.append(instance)
            return instance

        with patch.dict(
            os.environ,
            {"JOBWATCH_EMAIL_USER": "me@gmail.com", "JOBWATCH_EMAIL_PASSWORD": "app-password"},
            clear=False,
        ), patch.object(notifier.smtplib, "SMTP", side_effect=FakeSMTP), patch.object(
            notifier.imaplib, "IMAP4_SSL", side_effect=fake_imap
        ), patch.object(
            notifier.time, "sleep", return_value=None
        ), patch.object(
            notifier, "is_h1b_sponsor", return_value=False
        ):
            sent, detail = notifier.send_email([self._job()], self._same_account_config())

        self.assertTrue(sent)
        self.assertTrue(imap_instances[0].append_history)
        self.assertIn("inbox copy", detail)

    def test_send_email_succeeds_even_when_gmail_promotion_raises(self) -> None:
        # This invariant is load-bearing for the duplicate-alert fix: a flaky
        # IMAP must not report sent=False after SMTP accepted the message, or
        # already-delivered jobs get re-queued and re-sent.
        with patch.dict(
            os.environ,
            {"JOBWATCH_EMAIL_USER": "me@gmail.com", "JOBWATCH_EMAIL_PASSWORD": "app-password"},
            clear=False,
        ), patch.object(notifier.smtplib, "SMTP", side_effect=FakeSMTP), patch.object(
            notifier.imaplib, "IMAP4_SSL", side_effect=RuntimeError("imap down")
        ), patch.object(
            notifier, "is_h1b_sponsor", return_value=False
        ):
            sent, detail = notifier.send_email([self._job()], self._same_account_config())

        self.assertTrue(sent)
        self.assertIn("promotion failed", detail)


class NtfyTests(unittest.TestCase):
    def _job(self, band: str = "Strong", title: str = "Backend Engineer") -> dict:
        return {
            "job_id": f"job-{title}",
            "company": "Example",
            "title": title,
            "location": "Austin, TX",
            "url": "https://example.com/jobs/1",
            "rank_band": band,
        }

    def _config(self, topic: str = "test-topic") -> dict:
        return {"notification": {"ntfy_topic": topic, "ntfy_server": "https://ntfy.sh"}}

    def test_no_request_when_topic_missing(self) -> None:
        with patch.dict(os.environ, {}, clear=False), patch(
            "urllib.request.urlopen"
        ) as urlopen:
            os.environ.pop("JOBWATCH_NTFY_TOPIC", None)
            notifier.send_ntfy([self._job()], {"notification": {}})
        urlopen.assert_not_called()

    def test_push_sent_with_truncation_and_priority(self) -> None:
        jobs = [self._job(title=f"Engineer {i}") for i in range(7)]
        jobs[0]["rank_band"] = "Top"

        captured = {}

        def fake_urlopen(request, timeout=None):
            captured["request"] = request
            captured["timeout"] = timeout

            class _Resp:
                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    return None

            return _Resp()

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            notifier.send_ntfy(jobs, self._config())

        request = captured["request"]
        self.assertIn("test-topic", request.full_url)
        body = request.data.decode("utf-8")
        self.assertIn("and 2 more", body)
        self.assertEqual(request.get_header("Priority"), "4")

    def test_push_failure_is_swallowed(self) -> None:
        with patch("urllib.request.urlopen", side_effect=OSError("network down")):
            notifier.send_ntfy([self._job()], self._config())  # must not raise

    def test_emoji_title_does_not_break_push(self) -> None:
        # HTTP headers are latin-1; an emoji tier tag in the Title must be
        # stripped, not raise (regression: 🚀 broke the FAANG push live).
        captured = {}

        def fake_urlopen(request, timeout=None):
            captured["request"] = request

            class _Resp:
                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    return None

            return _Resp()

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            notifier.send_ntfy(
                [self._job()], self._config(),
                topic="faang-topic", priority="4", title="🚀 FAANG 1 new role(s)",
            )

        request = captured["request"]
        self.assertIn("faang-topic", request.full_url)
        title = request.get_header("Title")
        self.assertIn("FAANG", title)
        title.encode("latin-1")  # must not raise


if __name__ == "__main__":
    unittest.main()


class HtmlEscapingTests(unittest.TestCase):
    def test_scraped_fields_are_escaped_in_html(self) -> None:
        import notifier
        job = {
            "job_id": "x-1",
            "company": "Acme <script>alert(1)</script>",
            "title": "Engineer & \"Friend\"",
            "location": "NY <b>",
            "url": "https://example.com/?a=1&b=2",
            "rank_band": "Top",
            "rank_reason_text": "<i>fresh</i>",
        }
        html_out = notifier._build_html([job], {})
        self.assertNotIn("<script>", html_out)
        self.assertIn("&lt;script&gt;", html_out)
        self.assertIn("Engineer &amp; &quot;Friend&quot;", html_out)
        self.assertIn('href="https://example.com/?a=1&amp;b=2"', html_out)
        self.assertNotIn("<i>fresh</i>", html_out)
