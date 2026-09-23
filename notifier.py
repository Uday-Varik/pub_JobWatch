import html
import os
import imaplib
import smtplib
import time
from datetime import datetime, timezone
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.utils import formatdate, make_msgid

from ranking import rank_summary, top_picks_limit
from sponsors import is_h1b_sponsor, get_sponsor_count

GMAIL_IMAP_HOST = "imap.gmail.com"
GMAIL_ALL_MAIL_FOLDERS = ("[Gmail]/All Mail", "[Google Mail]/All Mail", "All Mail")


def _sponsor_tag(company: str) -> str:
    if is_h1b_sponsor(company):
        count = get_sponsor_count(company)
        return f" [H-1B Sponsor - {count} LCAs]"
    return ""


def _format_posted(posted_at) -> str:
    if not posted_at:
        return ""
    normalized = str(posted_at).strip().lower()
    if normalized in ("posted today", "today"):
        return "posted today"
    if normalized in ("posted yesterday", "yesterday"):
        return "posted yesterday"
    from filters import _parse_timestamp
    from datetime import datetime, timezone
    dt = _parse_timestamp(posted_at)
    if not dt:
        return str(posted_at) if posted_at else ""
    now = datetime.now(timezone.utc)
    diff = now - dt
    hours = int(diff.total_seconds() // 3600)
    if hours < 1:
        return f"{max(1, int(diff.total_seconds() // 60))}m ago"
    elif hours < 24:
        return f"{hours}h ago"
    else:
        return dt.strftime("%b %d, %Y")


def _posted_at_sort_value(job: dict) -> datetime:
    from filters import _parse_timestamp

    posted_at = _parse_timestamp(job.get("posted_at"))
    if posted_at:
        return posted_at
    return datetime.min.replace(tzinfo=timezone.utc)


def _job_sort_key(job: dict) -> tuple[int, datetime, str]:
    return (
        int(job.get("rank_score", 0)),
        _posted_at_sort_value(job),
        job["title"].lower(),
    )


def _group_jobs(new_jobs: list[dict]) -> list[tuple[str, list[dict]]]:
    grouped: dict[str, list[dict]] = {}
    for job in new_jobs:
        grouped.setdefault(job["company"], []).append(job)

    ordered_groups: list[tuple[tuple[int, datetime, str], str, list[dict]]] = []
    for company, jobs in grouped.items():
        ordered_jobs = sorted(jobs, key=_job_sort_key, reverse=True)
        lead_job = ordered_jobs[0]
        ordered_groups.append((_job_sort_key(lead_job), company, ordered_jobs))

    ordered_groups.sort(key=lambda item: (-item[0][0], -item[0][1].timestamp(), item[1].lower()))
    return [(company, jobs) for _, company, jobs in ordered_groups]


def _build_top_picks_text(new_jobs: list[dict], config: dict) -> list[str]:
    lines = ["Top picks", ""]
    for idx, job in enumerate(new_jobs[: top_picks_limit(config)], start=1):
        lines.append(f"{idx}. {job['company']} — {job['title']} [{job.get('rank_band', 'Watch')}]")
        meta_parts = []
        if job.get("location"):
            meta_parts.append(job["location"])
        if job.get("salary"):
            meta_parts.append(job["salary"])
        posted = _format_posted(job.get("posted_at"))
        if posted:
            meta_parts.append(posted)
        if meta_parts:
            lines.append(f"   {' | '.join(meta_parts)}")
        lines.append(f"   Why: {rank_summary(job)}")
        if job.get("url"):
            lines.append(f"   → {job['url']}")
        lines.append("")
    return lines


def _build_text(new_jobs: list[dict], config: dict) -> str:
    lines = []
    lines.extend(_build_top_picks_text(new_jobs, config))
    for company, jobs in _group_jobs(new_jobs):
        tag = _sponsor_tag(company)
        lines.append(f"\n{'━' * 40}")
        lines.append(f"  {company}{tag}")
        lines.append(f"{'━' * 40}")
        for job in jobs:
            lines.append(f"  • {job['title']} [{job.get('rank_band', 'Watch')}]")
            if job.get("location"):
                lines.append(f"    Location: {job['location']}")
            if job.get("salary"):
                lines.append(f"    Salary: {job['salary']}")
            posted = _format_posted(job.get("posted_at"))
            if posted:
                lines.append(f"    Posted: {posted}")
            lines.append(f"    Priority: {rank_summary(job)}")
            if job.get("url"):
                lines.append(f"    → {job['url']}")
            lines.append("")

    return "\n".join(lines)


def _sponsor_badge_html(company: str) -> str:
    if is_h1b_sponsor(company):
        count = get_sponsor_count(company)
        return (
            f' <span style="background: #22c55e; color: white; padding: 2px 8px; '
            f'border-radius: 12px; font-size: 0.75em; font-weight: 600;">'
            f'H-1B Sponsor ({count} LCAs)</span>'
        )
    return ' <span style="background: #ef4444; color: white; padding: 2px 8px; border-radius: 12px; font-size: 0.75em;">H-1B Unknown</span>'


def _rank_badge_html(job: dict) -> str:
    band = job.get("rank_band", "Watch")
    color = {
        "Top": "#0f766e",
        "Strong": "#2563eb",
        "Watch": "#475569",
    }.get(band, "#475569")
    return (
        f'<span style="background: {color}; color: white; padding: 2px 8px; '
        f'border-radius: 12px; font-size: 0.75em; font-weight: 600;">{band}</span>'
    )


def _esc(value) -> str:
    """Escape scraped text before it goes into the HTML body."""
    return html.escape(str(value or ""), quote=True)


def _build_html(new_jobs: list[dict], config: dict) -> str:
    parts = ['<div style="font-family: Arial, sans-serif; max-width: 700px;">']
    parts.append(f'<h2 style="color: #1a1a1a;">JobWatch: {len(new_jobs)} new role(s) found</h2>')
    parts.append('<h3 style="color: #1a1a1a; margin-top: 24px;">Top picks</h3>')
    parts.append("<ol style='padding-left: 20px;'>")
    for job in new_jobs[: top_picks_limit(config)]:
        url = job.get("url", "")
        title = job["title"]
        meta_parts = []
        if job.get("location"):
            meta_parts.append(job["location"])
        if job.get("salary"):
            meta_parts.append(job["salary"])
        posted = _format_posted(job.get("posted_at"))
        if posted:
            meta_parts.append(posted)
        parts.append("<li style='margin-bottom: 14px;'>")
        if url:
            parts.append(
                f'<a href="{_esc(url)}" style="color: #1a1a1a; text-decoration: none; font-weight: 700;">'
                f"{_esc(job['company'])} — {_esc(title)}</a> {_rank_badge_html(job)}"
            )
        else:
            parts.append(f"<strong>{_esc(job['company'])} — {_esc(title)}</strong> {_rank_badge_html(job)}")
        if meta_parts:
            parts.append(f'<br><span style="color: #555; font-size: 0.9em;">{" &nbsp;|&nbsp; ".join(_esc(m) for m in meta_parts)}</span>')
        parts.append(f'<br><span style="color: #334155; font-size: 0.85em;">Why: {_esc(rank_summary(job))}</span>')
        parts.append("</li>")
    parts.append("</ol>")

    for company, jobs in _group_jobs(new_jobs):
        badge = _sponsor_badge_html(company)
        parts.append(
            f'<h3 style="color: #2563eb; border-bottom: 2px solid #2563eb; padding-bottom: 4px;">'
            f'{_esc(company)}{badge}</h3>'
        )
        parts.append("<ul style='list-style: none; padding-left: 0;'>")
        for job in jobs:
            url = job.get("url", "")
            title = job["title"]
            loc = job.get("location", "")
            salary = job.get("salary", "")
            posted = _format_posted(job.get("posted_at"))
            parts.append('<li style="margin-bottom: 12px; padding: 10px; border-left: 3px solid #2563eb; background: #f8fafc;">')
            if url:
                parts.append(
                    f'<a href="{_esc(url)}" style="color: #1a1a1a; text-decoration: none; font-weight: 600; font-size: 1.05em;">'
                    f"{_esc(title)}</a> {_rank_badge_html(job)}"
                )
            else:
                parts.append(f"<strong>{_esc(title)}</strong> {_rank_badge_html(job)}")
            meta_parts = []
            if loc:
                meta_parts.append(f'📍 {loc}')
            if salary:
                meta_parts.append(f'💰 {salary}')
            if posted:
                meta_parts.append(f'🕐 {posted}')
            if meta_parts:
                parts.append(f'<br><span style="color: #555; font-size: 0.85em;">{" &nbsp;|&nbsp; ".join(_esc(m) for m in meta_parts)}</span>')
            parts.append(f'<br><span style="color: #334155; font-size: 0.82em;">Why: {_esc(rank_summary(job))}</span>')
            parts.append("</li>")
        parts.append("</ul>")

    parts.append("</div>")
    return "\n".join(parts)


def build_subject(new_jobs: list[dict], config: dict, subject_tag: str = "") -> str:
    notif = config.get("notification", {})
    subject_prefix = notif.get("subject_prefix", "").strip()
    subject = f"JobWatch: {len(new_jobs)} new role(s) found"
    if subject_tag:
        subject = f"{subject_tag} {subject}"
    if subject_prefix:
        subject = f"{subject_prefix} {subject}"
    return subject


def _build_message(new_jobs: list[dict], config: dict, from_user: str, to_email: str,
                   subject_tag: str = "") -> MIMEMultipart:
    msg = MIMEMultipart("alternative")
    msg["Subject"] = build_subject(new_jobs, config, subject_tag)
    msg["From"] = from_user
    msg["To"] = to_email
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid("jobwatch")
    msg["X-JobWatch-Inbox"] = "workflow"

    msg.attach(MIMEText(_build_text(new_jobs, config), "plain"))
    msg.attach(MIMEText(_build_html(new_jobs, config), "html"))
    return msg


def _quoted_mailbox(folder: str) -> str:
    if " " in folder:
        return f'"{folder}"'
    return folder


def _search_message_ids(mailbox: imaplib.IMAP4_SSL, folder: str, message_id: str) -> list[bytes]:
    status, _ = mailbox.select(_quoted_mailbox(folder))
    if status != "OK":
        return []

    status, data = mailbox.search(None, "HEADER", "Message-ID", message_id)
    if status != "OK" or not data:
        return []

    return [token for token in data[0].split() if token]


def _mark_unread(mailbox: imaplib.IMAP4_SSL, message_ids: list[bytes]) -> None:
    for message_id in message_ids:
        status, _ = mailbox.store(message_id, "-FLAGS", r"\Seen")
        if status != "OK":
            raise RuntimeError("Unable to mark Gmail JobWatch alert as unread.")


def _copy_ids_to_inbox(mailbox: imaplib.IMAP4_SSL, message_ids: list[bytes]) -> bool:
    if not message_ids:
        return False

    status, _ = mailbox.copy(b",".join(message_ids).decode(), "INBOX")
    return status == "OK"


def _append_unread_inbox_copy(mailbox: imaplib.IMAP4_SSL, msg: MIMEMultipart) -> None:
    status, _ = mailbox.append(
        "INBOX",
        None,
        imaplib.Time2Internaldate(time.time()),
        msg.as_bytes(),
    )
    if status != "OK":
        raise RuntimeError("Unable to create a Gmail inbox copy for JobWatch.")


def _promote_self_gmail_alert(msg: MIMEMultipart, from_user: str, from_pass: str) -> str:
    message_id = msg["Message-ID"]
    if not message_id:
        raise RuntimeError("JobWatch message is missing a Message-ID header.")

    mailbox = imaplib.IMAP4_SSL(GMAIL_IMAP_HOST, timeout=30)
    try:
        mailbox.login(from_user, from_pass)

        for _ in range(3):
            inbox_ids = _search_message_ids(mailbox, "INBOX", message_id)
            if inbox_ids:
                _mark_unread(mailbox, inbox_ids)
                return "Email sent and restored in Gmail Inbox as unread for mobile alerts."

            for folder in GMAIL_ALL_MAIL_FOLDERS:
                folder_ids = _search_message_ids(mailbox, folder, message_id)
                if not folder_ids:
                    continue
                if _copy_ids_to_inbox(mailbox, folder_ids):
                    inbox_ids = _search_message_ids(mailbox, "INBOX", message_id)
                    if inbox_ids:
                        _mark_unread(mailbox, inbox_ids)
                        return "Email sent and copied back to Gmail Inbox as unread for mobile alerts."

            time.sleep(2)

        _append_unread_inbox_copy(mailbox, msg)
        inbox_ids = _search_message_ids(mailbox, "INBOX", message_id)
        if inbox_ids:
            _mark_unread(mailbox, inbox_ids)
        return "Email sent and an unread Gmail inbox copy was created for mobile alerts."
    finally:
        try:
            mailbox.logout()
        except Exception:
            pass


def _classify_source_health(rows: list[dict]) -> tuple[list[dict], list[dict], list[dict]]:
    """Split latest-per-source rows into (broken, degraded, healthy).

    broken   = error/timeout, or 0 raw jobs on a source that should have some
    degraded = ok but 0 matches AND low raw count (worth a glance)
    healthy  = everything else
    """
    broken, degraded, healthy = [], [], []
    for r in rows:
        status = (r.get("status") or "").lower()
        raw = int(r.get("raw_count") or 0)
        if status in ("error", "timeout") or (status == "ok" and raw == 0):
            broken.append(r)
        elif status == "ok" and raw < 5:
            degraded.append(r)
        else:
            healthy.append(r)
    return broken, degraded, healthy


def send_health_digest(rows: list[dict], config: dict) -> tuple[bool, str]:
    """Email a weekly source-health digest (best-effort).

    ``rows`` is the latest run per source (store.get_latest_source_health_per_source).
    Sent even when nothing is broken, so silence never hides a dead pipeline.
    """
    notif = config.get("notification", {})
    from_user = os.environ.get("JOBWATCH_EMAIL_USER", "")
    from_pass = os.environ.get("JOBWATCH_EMAIL_PASSWORD", "")
    to_email = notif.get("email") or os.environ.get("JOBWATCH_NOTIFY_EMAIL", "") or from_user
    smtp_host = notif.get("smtp_host", "smtp.gmail.com")
    smtp_port = notif.get("smtp_port", 587)
    if not to_email or not from_user or not from_pass:
        return False, "Email not configured."

    broken, degraded, healthy = _classify_source_health(rows)

    def _line(r):
        st = r.get("status", "")
        if r.get("error"):
            st = f"{st}: {r['error']}"
        return f"{r.get('company','?')} ({r.get('ats','')}) — {int(r.get('raw_count') or 0)} jobs, {int(r.get('matched_count') or 0)} matches [{st}]"

    lines = [f"JobWatch health: {len(healthy)} healthy, {len(degraded)} low, {len(broken)} broken "
             f"(of {len(rows)} sources)", ""]
    if broken:
        lines += ["🔴 BROKEN (fetching nothing / erroring):"] + [f"  - {_line(r)}" for r in broken] + [""]
    if degraded:
        lines += ["🟡 LOW (returning very few jobs — check the slug/source):"] + [f"  - {_line(r)}" for r in degraded] + [""]
    if not broken and not degraded:
        lines += ["✅ All sources healthy this week.", ""]
    body = "\n".join(lines)

    subject_prefix = notif.get("subject_prefix", "").strip()
    subject = f"🩺 JobWatch Weekly Health — {len(broken)} broken, {len(degraded)} low"
    if subject_prefix:
        subject = f"{subject_prefix} {subject}"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = from_user
    msg["To"] = to_email
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid("jobwatch-health")
    msg.attach(MIMEText(body, "plain"))

    with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as server:
        server.starttls()
        server.login(from_user, from_pass)
        server.send_message(msg)

    # Self-sent Gmail can land read/only-in-Sent; promote to unread inbox like
    # the job alerts do so the weekly digest is actually seen.
    if from_user.strip().lower() == to_email.strip().lower():
        try:
            _promote_self_gmail_alert(msg, from_user, from_pass)
        except Exception:
            pass

    return True, f"Health digest sent to {to_email}: {len(broken)} broken, {len(degraded)} low."


def send_email(new_jobs: list[dict], config: dict, subject_tag: str = "") -> tuple[bool, str]:
    notif = config.get("notification", {})
    from_user = os.environ.get("JOBWATCH_EMAIL_USER", "")
    from_pass = os.environ.get("JOBWATCH_EMAIL_PASSWORD", "")
    to_email = notif.get("email") or os.environ.get("JOBWATCH_NOTIFY_EMAIL", "") or from_user
    smtp_host = notif.get("smtp_host", "smtp.gmail.com")
    smtp_port = notif.get("smtp_port", 587)

    if not notif.get("email") and to_email:
        print(f"  Notification recipient defaulting to {to_email}")

    if not to_email or not from_user or not from_pass:
        return False, "Email not configured — set JOBWATCH_EMAIL_USER and JOBWATCH_EMAIL_PASSWORD env vars."

    msg = _build_message(new_jobs, config, from_user, to_email, subject_tag)

    with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as server:
        server.starttls()
        server.login(from_user, from_pass)
        server.send_message(msg)

    if from_user.strip().lower() != to_email.strip().lower():
        return True, f"Email sent to {to_email}"

    try:
        delivery_note = _promote_self_gmail_alert(msg, from_user, from_pass)
    except Exception as exc:
        delivery_note = (
            "Email sent to the same Gmail account, but Gmail inbox promotion failed. "
            f"JobWatch may appear only in Sent/labels until that is fixed: {exc}"
        )

    return True, delivery_note


def send_ntfy(email_jobs: list[dict], config: dict, topic: str = "",
              priority: str = "", title: str = "") -> None:
    """Send a push notification via ntfy.sh (optional, best-effort).

    ``topic``/``priority``/``title`` let a caller route a specific tier to its
    own ntfy topic. When ``topic`` is empty it falls back to the legacy
    single-topic config so existing single-stream setups keep working.
    """
    try:
        import urllib.request
        import urllib.error

        notif = config.get("notification", {})
        if not topic:
            topic = notif.get("ntfy_topic") or os.environ.get("JOBWATCH_NTFY_TOPIC", "")
        if not topic:
            return

        server = notif.get("ntfy_server", "https://ntfy.sh")
        count = len(email_jobs)

        # Build numbered list, max 5 jobs
        lines = []
        for idx, job in enumerate(email_jobs[:5], start=1):
            loc = job.get("location", "")
            loc_part = f" ({loc})" if loc else ""
            lines.append(f"{idx}. {job['company']} — {job['title']}{loc_part}")
        if count > 5:
            lines.append(f"... and {count - 5} more")
        body = "\n".join(lines)

        if not priority:
            # Determine priority based on rank bands
            has_top = any(job.get("rank_band") == "Top" for job in email_jobs)
            priority = "4" if has_top else "3"

        if not title:
            title = f"JobWatch: {count} new role(s)"

        # First job URL for the click action
        click_url = email_jobs[0].get("url", "") if email_jobs else ""

        # HTTP headers are latin-1 only — strip emoji/non-latin-1 (e.g. the
        # 🎯/🚀 tier tags) from the Title or urllib raises and the push is
        # lost. The message body is sent as UTF-8 bytes, so it keeps emoji.
        def _header_safe(value: str) -> str:
            return value.encode("latin-1", "ignore").decode("latin-1").strip()

        url = f"{server.rstrip('/')}/{topic}"
        req = urllib.request.Request(url, data=body.encode("utf-8"), method="POST")
        req.add_header("Title", _header_safe(title) or "JobWatch")
        req.add_header("Priority", priority)
        req.add_header("Tags", "briefcase")
        if click_url:
            req.add_header("Click", _header_safe(click_url))

        urllib.request.urlopen(req, timeout=10)
        # Don't log the topic: on ntfy.sh the topic name is the credential
        # (anyone who knows it can subscribe or push). The title already
        # identifies the tier.
        print(f"  ntfy push sent [{_header_safe(title) or 'JobWatch'}] ({count} job(s))")
    except Exception as exc:
        print(f"  WARNING: ntfy push failed: {exc}")


def print_report(new_jobs: list[dict], config: dict):
    if not new_jobs:
        print("\nNo new matching roles found this run.")
        return
    print(f"\n{'=' * 50}")
    print(f"  JOBWATCH: {len(new_jobs)} NEW ROLE(S) FOUND")
    print(f"{'=' * 50}")
    print(_build_text(new_jobs, config))
