import sqlite3
from collections.abc import Iterable
from datetime import datetime, timedelta, timezone
from pathlib import Path

from filters import _parse_timestamp

DB_PATH = Path(__file__).parent / "jobwatch.db"

VALID_STATUSES = ("new", "applied", "phone_screen", "interview", "offer", "rejected", "skipped")


def _existing_columns(conn: sqlite3.Connection) -> set[str]:
    return {row[1] for row in conn.execute("PRAGMA table_info(seen_jobs)")}


def _ensure_seen_jobs_schema(conn: sqlite3.Connection) -> None:
    columns = _existing_columns(conn)
    added: set[str] = set()

    for name, definition in {
        "status": "TEXT DEFAULT 'new'",
        "status_updated": "TEXT",
        "posted_at": "TEXT",
        "source": "TEXT",
        "salary": "TEXT",
        "last_seen": "TEXT",
        "notified_at": "TEXT",
    }.items():
        if name not in columns:
            conn.execute(f"ALTER TABLE seen_jobs ADD COLUMN {name} {definition}")
            added.add(name)

    conn.execute("CREATE INDEX IF NOT EXISTS idx_seen_jobs_notified_at ON seen_jobs(notified_at)")
    # Supports the cross-run URL dedup in sync_jobs (same posting, churned id).
    conn.execute("CREATE INDEX IF NOT EXISTS idx_seen_jobs_url ON seen_jobs(url)")

    # Backfill legacy rows once when migrating older databases to the new schema.
    if "last_seen" in added:
        conn.execute("UPDATE seen_jobs SET last_seen = first_seen WHERE last_seen IS NULL")
    if "notified_at" in added:
        conn.execute("UPDATE seen_jobs SET notified_at = first_seen WHERE notified_at IS NULL")


def _ensure_source_runs_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS source_runs (
            run_id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_at TEXT NOT NULL,
            selected_lane TEXT NOT NULL,
            company TEXT NOT NULL,
            ats TEXT,
            source_lane TEXT,
            status TEXT NOT NULL,
            raw_count INTEGER NOT NULL,
            matched_count INTEGER NOT NULL,
            duration_seconds REAL NOT NULL,
            error TEXT
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_source_runs_company ON source_runs(company, ats, run_at DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_source_runs_run_at ON source_runs(run_at DESC)")


def _connect():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS seen_jobs (
            job_id     TEXT PRIMARY KEY,
            company    TEXT NOT NULL,
            title      TEXT NOT NULL,
            location   TEXT,
            url        TEXT,
            first_seen TEXT NOT NULL,
            status     TEXT DEFAULT 'new',
            status_updated TEXT
        )
        """
    )
    _ensure_seen_jobs_schema(conn)
    _ensure_source_runs_schema(conn)
    conn.commit()
    return conn


def _in_clause(ids: list[str]) -> str:
    """Build a parameterised IN-clause placeholder string."""
    return ",".join("?" for _ in ids)


def _should_requeue_job(
    incoming_posted_at: str,
    existing_posted_at: str | None,
    notified_at: str | None,
    status: str | None,
) -> bool:
    if (status or "new") != "new":
        return False
    if not notified_at or not incoming_posted_at or incoming_posted_at == (existing_posted_at or ""):
        return False

    incoming_posted = _parse_timestamp(incoming_posted_at)
    if not incoming_posted:
        return False
    # Only requeue genuinely fresh postings (48h).  ATS platforms like Workday
    # and Phenom recalculate relative timestamps each scrape ("Posted 2 days
    # ago" -> "Posted 3 days ago"), so a 3-day window caused false requeues.
    if incoming_posted < datetime.now(timezone.utc) - timedelta(hours=48):
        return False

    existing_posted = _parse_timestamp(existing_posted_at)
    # Tolerate up to 24h of drift -- relative-date ATS timestamps can shift by
    # a full calendar day between scrapes without representing a real repost.
    if existing_posted and incoming_posted <= existing_posted + timedelta(hours=24):
        return False
    return True


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)
    ).fetchone()
    return row is not None


def cleanup_old_jobs(
    retention_days: int = 30,
    health_days: int | None = None,
    batch_days: int | None = None,
) -> int:
    """Prune old rows so the state file stays small.

    * ``seen_jobs`` with status ``'new'`` not seen for *retention_days* are
      removed -- applied / interview / offer rows are kept regardless of age.
    * ``source_runs`` (per-source health history) older than *health_days*.
    * ``notification_batches`` / ``notification_batch_jobs`` (an archive copy
      of every alerted role, only used to render the workflow inbox) older
      than *batch_days*.

    *health_days* and *batch_days* default to *retention_days*. Returns the
    total number of deleted rows.
    """
    health_days = retention_days if health_days is None else health_days
    batch_days = retention_days if batch_days is None else batch_days
    now = datetime.now(timezone.utc)

    with _connect() as conn:
        cutoff = (now - timedelta(days=retention_days)).isoformat()
        deleted = conn.execute(
            "DELETE FROM seen_jobs WHERE last_seen < ? AND status = 'new'",
            (cutoff,),
        ).rowcount
        deleted += conn.execute(
            "DELETE FROM source_runs WHERE run_at < ?",
            ((now - timedelta(days=health_days)).isoformat(),),
        ).rowcount
        if _table_exists(conn, "notification_batches"):
            batch_cutoff = (now - timedelta(days=batch_days)).isoformat()
            if _table_exists(conn, "notification_batch_jobs"):
                deleted += conn.execute(
                    "DELETE FROM notification_batch_jobs WHERE batch_id IN "
                    "(SELECT batch_id FROM notification_batches WHERE created_at < ?)",
                    (batch_cutoff,),
                ).rowcount
            deleted += conn.execute(
                "DELETE FROM notification_batches WHERE created_at < ?",
                (batch_cutoff,),
            ).rowcount
        conn.commit()
    return deleted


def compact_database() -> int:
    """VACUUM when a meaningful share of the file is free pages.

    SQLite never shrinks a file on DELETE; without this the state snapshot
    stayed ~5 MB while holding ~2 MB of data. Returns bytes reclaimed.
    """
    if not DB_PATH.exists():
        return 0
    before = DB_PATH.stat().st_size
    conn = sqlite3.connect(DB_PATH, isolation_level=None)
    try:
        pages = conn.execute("PRAGMA page_count").fetchone()[0] or 0
        free = conn.execute("PRAGMA freelist_count").fetchone()[0] or 0
        if pages == 0 or free / pages < 0.10:
            return 0
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.execute("VACUUM")
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        conn.close()
    return max(0, before - DB_PATH.stat().st_size)


def sync_jobs(jobs: list[dict]) -> list[dict]:
    with _connect() as conn:
        cur = conn.cursor()
        pending = []
        seen_job_ids: set[str] = set()
        seen_urls: set[str] = set()
        now = datetime.now(timezone.utc).isoformat()

        for job in jobs:
            job_id = job["job_id"]
            if job_id in seen_job_ids:
                continue
            seen_job_ids.add(job_id)

            location = job.get("location", "")
            url = job.get("url", "")
            posted_at = job.get("posted_at", "")
            source = job.get("source", "")
            salary = job.get("salary", "")

            # URL is a per-posting key. If the same URL shows up twice in one
            # scrape under different job_ids (pagination overlap, or the same
            # role re-listed), keep only the first — it's one posting, one alert.
            if url:
                if url in seen_urls:
                    continue
                seen_urls.add(url)

            cur.execute(
                "SELECT first_seen, notified_at, posted_at, status FROM seen_jobs WHERE job_id = ?",
                (job_id,),
            )
            existing = cur.fetchone()
            if existing is None:
                # Cross-run guard: if this posting's URL is already tracked
                # under a different (churned) job_id, don't insert a second row
                # or re-alert — just refresh the existing row so retention keeps
                # it alive.
                if url:
                    cur.execute(
                        "SELECT job_id FROM seen_jobs WHERE url = ? AND job_id != ? LIMIT 1",
                        (url, job_id),
                    )
                    url_match = cur.fetchone()
                    if url_match is not None:
                        cur.execute(
                            "UPDATE seen_jobs SET last_seen = ? WHERE job_id = ?",
                            (now, url_match[0]),
                        )
                        continue
                pending.append({**job, "first_seen": now, "last_seen": now})
                cur.execute(
                    """
                    INSERT INTO seen_jobs (
                        job_id,
                        company,
                        title,
                        location,
                        url,
                        first_seen,
                        status,
                        posted_at,
                        source,
                        salary,
                        last_seen,
                        notified_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, 'new', ?, ?, ?, ?, NULL)
                    """,
                    (
                        job_id,
                        job["company"],
                        job["title"],
                        location,
                        url,
                        now,
                        posted_at,
                        source,
                        salary,
                        now,
                    ),
                )
                continue

            should_requeue = _should_requeue_job(posted_at, existing[2], existing[1], existing[3])

            cur.execute(
                """
                UPDATE seen_jobs
                SET company = ?,
                    title = ?,
                    location = COALESCE(NULLIF(?, ''), location),
                    url = COALESCE(NULLIF(?, ''), url),
                    posted_at = COALESCE(NULLIF(?, ''), posted_at),
                    source = COALESCE(NULLIF(?, ''), source),
                    salary = COALESCE(NULLIF(?, ''), salary),
                    last_seen = ?,
                    notified_at = CASE WHEN ? THEN NULL ELSE notified_at END
                WHERE job_id = ?
                """,
                (
                    job["company"],
                    job["title"],
                    location,
                    url,
                    posted_at,
                    source,
                    salary,
                    now,
                    1 if should_requeue else 0,
                    job_id,
                ),
            )

            if existing[1] is None or should_requeue:
                pending.append({**job, "first_seen": existing[0], "last_seen": now})

        conn.commit()
        return pending


def filter_new_jobs(jobs: list[dict]) -> list[dict]:
    return sync_jobs(jobs)


def mark_jobs_pending_notification(job_ids: Iterable[str]) -> int:
    """Set ``notified_at`` to the sentinel ``'pending'`` BEFORE sending email.

    This prevents duplicate alerts when the process crashes between sending and
    marking.  After a successful send, call :func:`mark_jobs_notified` to stamp
    the real timestamp (it overwrites ``'pending'``).  If the send fails, call
    :func:`reset_pending_notifications` to set ``'pending'`` back to NULL so the
    jobs are retried on the next run.
    """
    ids = list(dict.fromkeys(job_ids))
    if not ids:
        return 0

    with _connect() as conn:
        cur = conn.execute(
            f"UPDATE seen_jobs SET notified_at = 'pending' WHERE job_id IN ({_in_clause(ids)})",
            ids,
        )
        conn.commit()
        return cur.rowcount


def reset_pending_notifications(job_ids: Iterable[str] | None = None) -> int:
    """Reset ``notified_at = 'pending'`` rows back to NULL.

    Call this when email delivery fails so the jobs are picked up again on the
    next pipeline run.  Pass the job_ids this run marked to scope the reset
    (so an overlapping run's in-flight pending markers are left alone); call
    with no arguments at startup to recover rows stranded at 'pending' by a
    previous hard-killed run.

    Returns the number of rows reset.
    """
    with _connect() as conn:
        if job_ids is None:
            cur = conn.execute(
                "UPDATE seen_jobs SET notified_at = NULL WHERE notified_at = 'pending'",
            )
        else:
            ids = list(dict.fromkeys(job_ids))
            if not ids:
                return 0
            cur = conn.execute(
                "UPDATE seen_jobs SET notified_at = NULL "
                f"WHERE notified_at = 'pending' AND job_id IN ({_in_clause(ids)})",
                ids,
            )
        conn.commit()
        return cur.rowcount


def mark_jobs_notified(job_ids: Iterable[str]) -> int:
    ids = list(dict.fromkeys(job_ids))
    if not ids:
        return 0

    with _connect() as conn:
        now = datetime.now(timezone.utc).isoformat()
        cur = conn.execute(
            f"UPDATE seen_jobs SET notified_at = ? WHERE job_id IN ({_in_clause(ids)})",
            [now, *ids],
        )
        conn.commit()
        return cur.rowcount


def _latest_successful_source_run(conn: sqlite3.Connection, company: str, ats: str) -> sqlite3.Row | None:
    conn.row_factory = sqlite3.Row
    cur = conn.execute(
        """
        SELECT run_at, raw_count, matched_count, duration_seconds
        FROM source_runs
        WHERE company = ? AND ats = ? AND status = 'ok'
        ORDER BY run_at DESC
        LIMIT 1
        """,
        (company, ats),
    )
    return cur.fetchone()


def detect_source_anomalies(results: list[dict]) -> list[dict]:
    with _connect() as conn:
        anomalies = []

        for result in results:
            company = result.get("company", "Unknown")
            ats = result.get("ats", "")
            status = result.get("status", "")
            raw_count = int(result.get("raw_count") or 0)
            previous = _latest_successful_source_run(conn, company, ats)

            if status in {"error", "timeout"}:
                anomalies.append({
                    **result,
                    "anomaly": "source_failed",
                    "detail": result.get("error") or status,
                })
                continue

            if status != "ok" or previous is None:
                continue

            previous_raw = int(previous["raw_count"] or 0)
            if previous_raw >= 20 and raw_count == 0:
                anomalies.append({
                    **result,
                    "anomaly": "zero_results",
                    "detail": f"returned 0 jobs after previously returning {previous_raw}",
                })
            elif previous_raw >= 50 and raw_count < previous_raw * 0.2:
                anomalies.append({
                    **result,
                    "anomaly": "count_drop",
                    "detail": f"raw job count dropped from {previous_raw} to {raw_count}",
                })

        return anomalies


def record_source_results(results: list[dict], selected_lane: str) -> int:
    if not results:
        return 0

    with _connect() as conn:
        now = datetime.now(timezone.utc).isoformat()
        rows = [
            (
                now,
                selected_lane,
                result.get("company", "Unknown"),
                result.get("ats", ""),
                result.get("lane", ""),
                result.get("status", "unknown"),
                int(result.get("raw_count") or 0),
                int(result.get("matched_count") or 0),
                float(result.get("duration") or 0.0),
                result.get("error"),
            )
            for result in results
        ]
        conn.executemany(
            """
            INSERT INTO source_runs (
                run_at,
                selected_lane,
                company,
                ats,
                source_lane,
                status,
                raw_count,
                matched_count,
                duration_seconds,
                error
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        conn.commit()
        return len(rows)


def mark_status(job_id: str, status: str) -> bool:
    if status not in VALID_STATUSES:
        return False
    with _connect() as conn:
        now = datetime.now(timezone.utc).isoformat()
        cur = conn.execute(
            "UPDATE seen_jobs SET status = ?, status_updated = ? WHERE job_id = ?",
            (status, now, job_id),
        )
        conn.commit()
        return cur.rowcount > 0


def search_jobs(query: str, limit: int = 20) -> list[dict]:
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            """
            SELECT
                job_id,
                company,
                title,
                location,
                url,
                status,
                first_seen,
                last_seen,
                posted_at,
                source,
                salary,
                notified_at
            FROM seen_jobs
            WHERE title LIKE ? OR company LIKE ?
            ORDER BY first_seen DESC
            LIMIT ?
            """,
            (f"%{query}%", f"%{query}%", limit),
        )
        return [dict(r) for r in cur.fetchall()]


def get_status_summary() -> dict:
    with _connect() as conn:
        cur = conn.cursor()
        cur.execute("SELECT status, COUNT(*) FROM seen_jobs GROUP BY status ORDER BY COUNT(*) DESC")
        by_status = cur.fetchall()
        cur.execute(
            """
            SELECT company, title, url, status, first_seen, posted_at, source
            FROM seen_jobs
            WHERE status IN ('applied', 'phone_screen', 'interview', 'offer')
            ORDER BY status_updated DESC
            """
        )
        active = [
            {
                "company": r[0],
                "title": r[1],
                "url": r[2],
                "status": r[3],
                "first_seen": r[4],
                "posted_at": r[5],
                "source": r[6],
            }
            for r in cur.fetchall()
        ]
        return {"by_status": by_status, "active_applications": active}


def get_recent_source_health(limit: int = 50) -> list[dict]:
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT
                run_at,
                selected_lane,
                company,
                ats,
                source_lane,
                status,
                raw_count,
                matched_count,
                duration_seconds,
                error
            FROM source_runs
            ORDER BY run_at DESC, duration_seconds DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]


def get_latest_source_health_per_source() -> list[dict]:
    """Most recent run for each (company, ats), newest first.

    Backs the weekly health digest: one row per source so we can see which
    sources are currently broken (0 raw jobs, error/timeout) rather than a
    flat recent-runs list that repeats the same source many times.
    """
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT company, ats, source_lane, status, raw_count,
                   matched_count, duration_seconds, error, run_at
            FROM source_runs sr
            WHERE run_at = (
                SELECT MAX(run_at) FROM source_runs
                WHERE company = sr.company AND ats IS sr.ats
            )
            ORDER BY company COLLATE NOCASE
            """,
        ).fetchall()
        # A source can have two rows at the same MAX run_at only in edge cases;
        # de-dup by (company, ats) keeping the first.
        seen = set()
        out = []
        for row in rows:
            key = (row["company"], row["ats"])
            if key in seen:
                continue
            seen.add(key)
            out.append(dict(row))
        return out


def get_stats() -> dict:
    with _connect() as conn:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM seen_jobs")
        total = cur.fetchone()[0]
        cur.execute("SELECT company, COUNT(*) FROM seen_jobs GROUP BY company ORDER BY COUNT(*) DESC")
        by_company = cur.fetchall()
        return {"total": total, "by_company": by_company}
