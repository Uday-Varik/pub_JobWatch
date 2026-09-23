from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sqlite3

from ranking import rank_jobs, rank_summary
import store
from filters import _parse_timestamp


WORKFLOW_INBOX_DIR = Path(__file__).parent / "workflow-inbox"
WORKFLOW_BATCH_DIR = WORKFLOW_INBOX_DIR / "batches"


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS notification_batches (
            batch_id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            status TEXT NOT NULL,
            recipient TEXT,
            subject TEXT,
            error TEXT,
            job_count INTEGER NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS notification_batch_jobs (
            batch_id INTEGER NOT NULL,
            position INTEGER NOT NULL,
            job_id TEXT NOT NULL,
            company TEXT NOT NULL,
            title TEXT NOT NULL,
            location TEXT,
            url TEXT,
            posted_at TEXT,
            source TEXT,
            salary TEXT,
            first_seen TEXT,
            PRIMARY KEY (batch_id, job_id),
            FOREIGN KEY(batch_id) REFERENCES notification_batches(batch_id)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_notification_batches_created_at ON notification_batches(created_at DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_notification_batch_jobs_batch_id ON notification_batch_jobs(batch_id)"
    )
    conn.commit()


def _connect() -> sqlite3.Connection:
    conn = store._connect()
    _ensure_schema(conn)
    return conn


def _format_posted(raw: str | None) -> str:
    if not raw:
        return "unknown"
    normalized = str(raw).strip().lower()
    if normalized in ("posted today", "today"):
        return "posted today"
    if normalized in ("posted yesterday", "yesterday"):
        return "posted yesterday"
    parsed = _parse_timestamp(raw)
    if parsed:
        return parsed.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return str(raw)


def _format_seen(raw: str | None) -> str:
    if not raw:
        return "unknown"
    try:
        parsed = datetime.fromisoformat(str(raw))
    except ValueError:
        return str(raw)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _write_batch_file(
    batch_id: int,
    created_at: str,
    status: str,
    recipient: str | None,
    subject: str,
    error: str | None,
    jobs: list[dict],
) -> Path:
    WORKFLOW_BATCH_DIR.mkdir(parents=True, exist_ok=True)
    stamp = created_at.replace(":", "").replace("+00:00", "Z")
    path = WORKFLOW_BATCH_DIR / f"{stamp}-batch-{batch_id:05d}.md"

    lines = [
        f"# JobWatch Batch {batch_id}",
        "",
        f"- Created: {_format_seen(created_at)}",
        f"- Status: {status}",
        f"- Recipient: {recipient or 'not configured'}",
        f"- Subject: {subject}",
        f"- Jobs: {len(jobs)}",
    ]
    if error:
        lines.append(f"- Error: {error}")
    lines.append("")

    if not jobs:
        lines.append("No jobs were archived in this batch.")
    else:
        for idx, job in enumerate(jobs, start=1):
            lines.extend(
                [
                    f"## {idx}. {job['company']} — {job['title']}",
                    "",
                    f"- First seen: {_format_seen(job.get('first_seen'))}",
                    f"- Posted: {_format_posted(job.get('posted_at'))}",
                    f"- Source: {job.get('source') or 'unknown'}",
                    f"- Location: {job.get('location') or 'unknown'}",
                ]
            )
            if job.get("rank_band") or job.get("rank_reason_text"):
                lines.append(f"- Priority: {rank_summary(job)}")
            if job.get("salary"):
                lines.append(f"- Salary: {job['salary']}")
            if job.get("url"):
                lines.append(f"- Link: {job['url']}")
            lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def record_batch(
    *,
    status: str,
    jobs: list[dict],
    recipient: str | None,
    subject: str,
    error: str | None = None,
) -> Path | None:
    if not jobs:
        return None

    conn = _connect()
    created_at = datetime.now(timezone.utc).isoformat()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO notification_batches (created_at, status, recipient, subject, error, job_count)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (created_at, status, recipient, subject, error, len(jobs)),
    )
    batch_id = int(cur.lastrowid)

    for position, job in enumerate(jobs, start=1):
        cur.execute(
            """
            INSERT INTO notification_batch_jobs (
                batch_id,
                position,
                job_id,
                company,
                title,
                location,
                url,
                posted_at,
                source,
                salary,
                first_seen
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                batch_id,
                position,
                job["job_id"],
                job["company"],
                job["title"],
                job.get("location", ""),
                job.get("url", ""),
                job.get("posted_at", ""),
                job.get("source", ""),
                job.get("salary", ""),
                job.get("first_seen", ""),
            ),
        )

    conn.commit()
    conn.close()
    return _write_batch_file(batch_id, created_at, status, recipient, subject, error, jobs)


def _fetch_recent_batches(limit: int) -> list[dict]:
    conn = _connect()
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT batch_id, created_at, status, recipient, subject, error, job_count
        FROM notification_batches
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def _fetch_pending_jobs(limit: int) -> list[dict]:
    conn = _connect()
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT company, title, location, url, posted_at, source, salary, first_seen
        FROM seen_jobs
        WHERE notified_at IS NULL
        ORDER BY first_seen DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def render_inbox(limit: int = 20, config: dict | None = None) -> Path:
    WORKFLOW_INBOX_DIR.mkdir(parents=True, exist_ok=True)
    batches = _fetch_recent_batches(limit)
    pending = rank_jobs(_fetch_pending_jobs(limit), config=config)
    index_path = WORKFLOW_INBOX_DIR / "index.md"

    lines = [
        "# JobWatch Workflow Inbox",
        "",
        f"- Refreshed: {_format_seen(datetime.now(timezone.utc).isoformat())}",
        f"- Pending delivery jobs: {len(pending)}",
        f"- Archived batches shown: {len(batches)}",
        "",
    ]

    lines.append("## Pending Delivery")
    lines.append("")
    if not pending:
        lines.append("No pending delivery jobs.")
        lines.append("")
    else:
        for job in pending:
            lines.extend(
                [
                    f"- {job['company']} — {job['title']} [{job.get('rank_band', 'Watch')}]",
                    f"  First seen: {_format_seen(job.get('first_seen'))} | Posted: {_format_posted(job.get('posted_at'))} | Source: {job.get('source') or 'unknown'}",
                    f"  Priority: {rank_summary(job)}",
                ]
            )
            if job.get("url"):
                lines.append(f"  Link: {job['url']}")
        lines.append("")

    lines.append("## Recent Batches")
    lines.append("")
    if not batches:
        lines.append("No archived batches yet.")
        lines.append("")
    else:
        for batch in batches:
            stamp = batch["created_at"].replace(":", "").replace("+00:00", "Z")
            batch_path = WORKFLOW_BATCH_DIR / f"{stamp}-batch-{batch['batch_id']:05d}.md"
            lines.extend(
                [
                    f"- Batch {batch['batch_id']} [{batch['status']}]",
                    f"  Created: {_format_seen(batch['created_at'])} | Jobs: {batch['job_count']} | Recipient: {batch['recipient'] or 'not configured'}",
                    f"  Subject: {batch['subject']}",
                    f"  File: {batch_path}",
                ]
            )
            if batch.get("error"):
                lines.append(f"  Error: {batch['error']}")
        lines.append("")

    index_path.write_text("\n".join(lines), encoding="utf-8")
    return index_path


def print_summary(limit: int = 10, config: dict | None = None) -> str:
    batches = _fetch_recent_batches(limit)
    pending = rank_jobs(_fetch_pending_jobs(limit), config=config)

    lines = [
        f"Pending delivery jobs: {len(pending)}",
    ]
    if pending:
        for job in pending[:limit]:
            lines.append(
                f"  - {job['company']} — {job['title']} [{job.get('rank_band', 'Watch')}] | first seen {_format_seen(job.get('first_seen'))} | posted {_format_posted(job.get('posted_at'))} | {rank_summary(job)}"
            )

    lines.append("Recent batches:")
    if batches:
        for batch in batches[:limit]:
            lines.append(
                f"  - Batch {batch['batch_id']} [{batch['status']}] {_format_seen(batch['created_at'])} | {batch['job_count']} jobs"
            )
    else:
        lines.append("  - none yet")

    return "\n".join(lines)
