#!/usr/bin/env python3
"""JobWatch — monitor company career pages for new roles."""

import os
import sys
import argparse
import multiprocessing as mp
import queue
import time
import yaml
from pathlib import Path

from adapters import ADAPTERS
from filters import MAX_AGE_HOURS, filter_jobs, is_new_grad
from ranking import rank_jobs
from store import (
    VALID_STATUSES,
    cleanup_old_jobs,
    compact_database,
    detect_source_anomalies,
    get_stats,
    get_status_summary,
    get_recent_source_health,
    get_latest_source_health_per_source,
    mark_jobs_notified,
    mark_jobs_pending_notification,
    mark_status,
    record_source_results,
    reset_pending_notifications,
    search_jobs,
    sync_jobs,
)
from notifier import build_subject, send_email, send_ntfy, send_health_digest, print_report
from workflow_inbox import print_summary, record_batch, render_inbox

BROWSER_ATS = {"playwright", "eightfold_pw"}
RUNNER_DEFAULTS = {
    "fast_workers": 8,
    "browser_workers": 1,
    "fast_timeout_seconds": 150,
    "browser_timeout_seconds": 210,
}
ALERTING_DEFAULTS = {
    "email_bands": ("Top", "Strong"),
}
RETENTION_DEFAULTS = {
    "job_days": 30,
    "health_days": 30,
    "batch_days": 14,
}


def load_config() -> dict:
    config_path = Path(__file__).parent / "config.yaml"
    with open(config_path) as f:
        return yaml.safe_load(f)


def _as_positive_int(value, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _runner_settings(config: dict) -> dict:
    configured = config.get("runner", {}) if config else {}
    return {
        key: _as_positive_int(configured.get(key), default)
        for key, default in RUNNER_DEFAULTS.items()
    }


def _alerting_settings(config: dict) -> dict:
    configured = config.get("alerting", {}) if config else {}
    email_bands = configured.get("email_bands", ALERTING_DEFAULTS["email_bands"])
    if not isinstance(email_bands, (list, tuple)):
        email_bands = ALERTING_DEFAULTS["email_bands"]
    return {"email_bands": {str(band).strip() for band in email_bands if str(band).strip()}}


def _retention_settings(config: dict) -> dict:
    configured = (config.get("retention", {}) if config else {}) or {}
    return {
        key: _as_positive_int(configured.get(key), default)
        for key, default in RETENTION_DEFAULTS.items()
    }


def _company_keywords(company: dict, global_keywords: list[str]) -> list[str]:
    override = company.get("keywords")
    if isinstance(override, (list, tuple)):
        cleaned = [str(k).strip() for k in override if str(k).strip()]
        if cleaned:
            return cleaned
    return list(global_keywords)


def _alerts_suppressed() -> bool:
    return os.environ.get("JOBWATCH_SUPPRESS_ALERTS", "").strip().lower() in ("1", "true", "yes")


def _is_paused(config: dict) -> bool:
    return bool((config or {}).get("paused", False))


def _company_lane(company: dict) -> str:
    return "browser" if company.get("ats") in BROWSER_ATS else "fast"


def _selected_companies(companies: list[dict], lane: str) -> list[dict]:
    if lane == "all":
        return companies
    return [company for company in companies if _company_lane(company) == lane]


def _filter_settings(config: dict) -> dict:
    configured = (config.get("filters", {}) if config else {}) or {}
    return {
        "exclude_new_grad": bool(configured.get("exclude_new_grad", False)),
        "max_age_hours": _as_positive_int(configured.get("max_age_hours"), MAX_AGE_HOURS),
    }


def _fetch_company_result(
    index: int,
    company: dict,
    keywords: list[str],
    locations: list[str],
    exclude_new_grad: bool = False,
    max_age_hours: int = MAX_AGE_HOURS,
) -> dict:
    name = company.get("name", "Unknown")
    ats = company.get("ats", "")
    lane = _company_lane(company)
    started = time.perf_counter()
    result = {
        "index": index,
        "company": name,
        "ats": ats,
        "lane": lane,
        "raw_count": 0,
        "matched_count": 0,
        "jobs": [],
        "duration": 0.0,
        "status": "ok",
        "error": None,
    }

    adapter = ADAPTERS.get(ats)
    if not adapter:
        result.update({"status": "skipped", "error": f"no adapter for '{ats}'"})
        return result

    # A company may carry its own `keywords:` list, replacing the global one.
    # Use it for boards where the global list matches nearly everything
    # (Google) to keep only the roles you actually want from that company.
    company_keywords = _company_keywords(company, keywords)

    try:
        raw_jobs = adapter(company)
        matched = filter_jobs(
            raw_jobs, company_keywords, locations,
            allow_seniority_titles=bool(company.get("allow_seniority_titles", False)),
            exclude_new_grad=exclude_new_grad,
            max_age_hours=max_age_hours,
        )
        for job in matched:
            job.setdefault("source", ats)
        result.update(
            {
                "raw_count": len(raw_jobs),
                "matched_count": len(matched),
                "jobs": matched,
            }
        )
    except Exception as exc:
        result.update({"status": "error", "error": str(exc)})
    finally:
        result["duration"] = time.perf_counter() - started

    return result


def _fetch_company_worker(
    index: int, company: dict, keywords: list[str], locations: list[str], outbox,
    exclude_new_grad: bool = False, max_age_hours: int = MAX_AGE_HOURS,
) -> None:
    outbox.put(_fetch_company_result(index, company, keywords, locations, exclude_new_grad, max_age_hours))


def _timeout_result(index: int, company: dict, timeout_seconds: int) -> dict:
    return {
        "index": index,
        "company": company.get("name", "Unknown"),
        "ats": company.get("ats", ""),
        "lane": _company_lane(company),
        "raw_count": 0,
        "matched_count": 0,
        "jobs": [],
        "duration": float(timeout_seconds),
        "status": "timeout",
        "error": f"exceeded {timeout_seconds}s source budget",
    }


def _run_source_pool(
    companies: list[dict],
    keywords: list[str],
    locations: list[str],
    *,
    max_workers: int,
    timeout_seconds: int,
    exclude_new_grad: bool = False,
    max_age_hours: int = MAX_AGE_HOURS,
) -> list[dict]:
    pending = list(enumerate(companies))
    active = []
    results = []

    def start_next() -> None:
        if not pending:
            return
        index, company = pending.pop(0)
        outbox = mp.Queue(maxsize=1)
        process = mp.Process(
            target=_fetch_company_worker,
            args=(index, company, keywords, locations, outbox, exclude_new_grad, max_age_hours),
        )
        process.start()
        active.append(
            {
                "index": index,
                "company": company,
                "process": process,
                "outbox": outbox,
                "started": time.perf_counter(),
            }
        )

    for _ in range(min(max_workers, len(pending))):
        start_next()

    while active:
        for item in list(active):
            process = item["process"]
            outbox = item["outbox"]
            elapsed = time.perf_counter() - item["started"]

            try:
                result = outbox.get_nowait()
            except queue.Empty:
                result = None

            if result is not None:
                process.join(timeout=1)
                outbox.close()
                active.remove(item)
                _print_source_result(result)
                results.append(result)
                start_next()
                continue

            if not process.is_alive():
                process.join(timeout=1)
                # The child can put its result and exit between get_nowait()
                # and is_alive() above — drain once more before declaring it
                # dead, or a finished worker's jobs get silently dropped.
                try:
                    result = outbox.get(timeout=1)
                except queue.Empty:
                    result = None
                outbox.close()
                active.remove(item)
                if result is None:
                    result = {
                        **_timeout_result(item["index"], item["company"], timeout_seconds),
                        "duration": elapsed,
                        "status": "error",
                        "error": "worker exited without returning a result",
                    }
                _print_source_result(result)
                results.append(result)
                start_next()
                continue

            if elapsed > timeout_seconds:
                process.terminate()
                process.join(timeout=5)
                outbox.close()
                active.remove(item)
                result = _timeout_result(item["index"], item["company"], timeout_seconds)
                _print_source_result(result)
                results.append(result)
                start_next()

        if active:
            time.sleep(0.1)

    return sorted(results, key=lambda item: item["index"])


def _print_source_result(result: dict) -> None:
    name = result["company"]
    ats = result["ats"] or "unknown"
    duration = result["duration"]
    if result["status"] == "ok":
        print(
            f"  [{name}] {ats}/{result['lane']}: "
            f"{result['raw_count']} jobs, {result['matched_count']} matches in {duration:.1f}s"
        )
        return
    print(f"  [{name}] {ats}/{result['lane']}: {result['status'].upper()} in {duration:.1f}s - {result['error']}")


def _run_fetch_plan(
    companies: list[dict],
    keywords: list[str],
    locations: list[str],
    config: dict,
    lane: str,
) -> list[dict]:
    selected = _selected_companies(companies, lane)
    settings = _runner_settings(config)
    filter_settings = _filter_settings(config)
    all_results = []

    if not selected:
        print(f"No companies selected for lane '{lane}'.")
        return []

    for lane_name, workers_key, timeout_key in (
        ("fast", "fast_workers", "fast_timeout_seconds"),
        ("browser", "browser_workers", "browser_timeout_seconds"),
    ):
        lane_companies = [company for company in selected if _company_lane(company) == lane_name]
        if not lane_companies:
            continue

        max_workers = min(settings[workers_key], len(lane_companies))
        timeout_seconds = settings[timeout_key]
        print(
            f"\n--- Fetching {len(lane_companies)} {lane_name} source(s) "
            f"with {max_workers} worker(s), {timeout_seconds}s/source ---"
        )
        all_results.extend(
            _run_source_pool(
                lane_companies,
                keywords,
                locations,
                max_workers=max_workers,
                timeout_seconds=timeout_seconds,
                exclude_new_grad=filter_settings["exclude_new_grad"],
                max_age_hours=filter_settings["max_age_hours"],
            )
        )

    return sorted(all_results, key=lambda item: item["index"])


def _print_health_summary(results: list[dict]) -> None:
    if not results:
        return

    print("\n--- Source health ---")
    print(f"{'Company':<24} {'Lane':<8} {'ATS':<16} {'Sec':>6} {'Raw':>5} {'Match':>5} Status")
    print("-" * 80)
    for result in sorted(results, key=lambda item: (-item["duration"], item["company"].lower())):
        status = result["status"]
        if result["error"]:
            status = f"{status}: {result['error']}"
        print(
            f"{result['company'][:24]:<24} {result['lane']:<8} {result['ats'][:16]:<16} "
            f"{result['duration']:>6.1f} {result['raw_count']:>5} {result['matched_count']:>5} {status}"
        )


def _print_health_anomalies(anomalies: list[dict]) -> None:
    if not anomalies:
        return

    print("\n--- Source health alerts ---")
    for anomaly in anomalies:
        print(f"  [{anomaly['company']}] {anomaly['anomaly']}: {anomaly['detail']}")


def _select_email_jobs(jobs: list[dict], config: dict) -> list[dict]:
    email_bands = _alerting_settings(config)["email_bands"]
    return [job for job in jobs if str(job.get("rank_band", "")).strip() in email_bands]


DEFAULT_TIER = "other"


def _company_tier_map(config: dict) -> dict:
    """Map company display name -> tier (target | faang | other)."""
    mapping = {}
    for company in config.get("companies", []):
        name = company.get("name")
        if name:
            mapping[name] = str(company.get("tier", DEFAULT_TIER)).strip() or DEFAULT_TIER
    return mapping


def _tier_settings(config: dict) -> dict:
    """Per-tier routing config from notification.tiers, with safe defaults."""
    tiers = (config.get("notification", {}) or {}).get("tiers", {}) or {}
    return tiers if isinstance(tiers, dict) else {}


def _resolve_tier_topic(tier_cfg: dict) -> str:
    """Resolve the first set env var in a tier's ntfy_topic_env list."""
    envs = tier_cfg.get("ntfy_topic_env", [])
    if isinstance(envs, str):
        envs = [envs]
    for env_name in envs:
        value = os.environ.get(str(env_name), "").strip()
        if value:
            return value
    return ""


def _tier_email_bands(tier_cfg: dict, config: dict) -> set[str]:
    """Rank bands a tier alerts on.

    A tier may set ``email_bands`` (a list, or the string ``all``) to override
    the global ``alerting.email_bands``. Sources that expose no posting date
    (Playwright scrapes, Netflix) can never reach the Strong band, so the
    tiers you care about most should alert on every band rather than
    silently archiving those roles.
    """
    override = tier_cfg.get("email_bands")
    if isinstance(override, str) and override.strip().lower() == "all":
        return {"Top", "Strong", "Watch"}
    if isinstance(override, (list, tuple)):
        bands = {str(band).strip() for band in override if str(band).strip()}
        if bands:
            return bands
    return _alerting_settings(config)["email_bands"]


def _route_email_jobs(pending_jobs: list[dict], config: dict) -> dict:
    """Group alert-worthy jobs by tier, keeping only tiers with alert: true.

    New-grad / entry-level roles route to the dedicated 'newgrad' stream
    (across every company) instead of their company tier, so they don't get
    mixed into the target/faang/other streams. Each tier then applies its own
    rank-band gate (``email_bands`` on the tier, else the global default).
    Roles that fail the gate, or belong to a tier with alert: false, still
    reach the workflow inbox via record_batch.
    """
    tier_map = _company_tier_map(config)
    tier_cfg = _tier_settings(config)
    newgrad_alerts = tier_cfg.get("newgrad", {}).get("alert", False)
    grouped: dict[str, list[dict]] = {}
    for job in pending_jobs:
        if newgrad_alerts and is_new_grad(job.get("title", "")):
            tier = "newgrad"
        else:
            tier = tier_map.get(job.get("company", ""), DEFAULT_TIER)
            if not tier_cfg.get(tier, {}).get("alert", False):
                continue
        band = str(job.get("rank_band", "")).strip()
        if band not in _tier_email_bands(tier_cfg.get(tier, {}), config):
            continue
        grouped.setdefault(tier, []).append(job)
    return grouped


def _deliver_tier(tier: str, jobs: list[dict], config: dict) -> tuple[bool, str]:
    """Send one tier's tagged email and its own ntfy push.

    Returns (sent, message). 'sent' reflects email delivery — push is
    best-effort and never blocks marking the batch notified.
    """
    tier_cfg = _tier_settings(config).get(tier, {})
    subject_tag = str(tier_cfg.get("subject_tag", tier.upper()))

    sent, message = send_email(jobs, config, subject_tag=subject_tag)

    if sent:
        topic = _resolve_tier_topic(tier_cfg)
        if topic:
            send_ntfy(
                jobs,
                config,
                topic=topic,
                priority=str(tier_cfg.get("ntfy_priority", "")),
                title=f"{subject_tag} {len(jobs)} new role(s)",
            )
    return sent, message


def cmd_run(args):
    config = load_config()
    keywords = config.get("keywords", [])
    locations = config.get("locations", [])
    companies = config.get("companies", [])
    notification = config.get("notification", {})
    recipient = (notification.get("email")
                 or os.environ.get("JOBWATCH_NOTIFY_EMAIL", "")
                 or os.environ.get("JOBWATCH_EMAIL_USER", ""))
    lane = getattr(args, "lane", "all")
    dry_run = getattr(args, "dry_run", False)

    if not companies:
        print("No companies configured in config.yaml")
        sys.exit(1)

    if _is_paused(config):
        # Nothing is fetched or alerted; the restored state passes through
        # untouched so un-pausing resumes exactly where it left off.
        print("JobWatch is paused (config.yaml: paused: true). Nothing fetched, nothing sent.")
        return

    delivery_error = None

    results = _run_fetch_plan(companies, keywords, locations, config, lane)
    all_matched = [job for result in results for job in result["jobs"]]
    errors = [result for result in results if result["status"] in {"error", "timeout"}]
    successful_sources = [result for result in results if result["status"] == "ok"]
    health_anomalies = [] if dry_run else detect_source_anomalies(results)

    if not dry_run:
        # Recover rows stranded at notified_at='pending' by a previous run
        # that died between marking and sending (Ctrl+C, SIGKILL, runner
        # cancellation).  Without this they would never be alerted and would
        # eventually be deleted by retention. A rare duplicate alert beats a
        # silently lost one.
        stale_pending = reset_pending_notifications()
        if stale_pending:
            print(
                f"Recovered {stale_pending} job(s) stuck in pending-notification "
                "state from a previous interrupted run."
            )

    jobs_to_report = all_matched if dry_run else sync_jobs(all_matched)
    pending_jobs = rank_jobs(jobs_to_report, config=config)
    print_report(pending_jobs, config)

    if dry_run:
        print("\nDry run: database, email delivery, and workflow inbox were not updated.")
        _print_health_summary(results)
        if errors and not successful_sources:
            print("\nWARNING: All selected adapters failed - no jobs fetched this run.")
            sys.exit(1)
        return

    recorded_sources = record_source_results(results, lane)
    if recorded_sources:
        print(f"\nRecorded source health for {recorded_sources} source(s).")

    if pending_jobs:
        pending_ids = [job["job_id"] for job in pending_jobs]
        mark_jobs_pending_notification(pending_ids)

        # Route alert-worthy roles into per-tier streams. Each tier delivers
        # independently: a failure in one tier resets only that tier's job ids
        # for retry, the rest are marked notified. Roles that fail a tier's
        # band gate, or belong to a tier with alert: false, only reach the
        # workflow inbox below.
        # JOBWATCH_SUPPRESS_ALERTS=1 (set by CI when the restored state is
        # missing or stale, or by hand) records the roles and marks them
        # notified without sending anything, so a lost DB cannot turn into a
        # wall of re-alerts.
        suppress = _alerts_suppressed()
        tier_groups = {} if suppress else _route_email_jobs(pending_jobs, config)
        if suppress:
            print(f"\nAlerts suppressed: recorded {len(pending_jobs)} role(s) without email or push.")
        failed_ids: set[str] = set()
        delivered_summaries: list[str] = []

        for tier, jobs in tier_groups.items():
            # Each send is isolated. Once send_email reports success the
            # delivery is irrevocable, so a later tier's failure (or any
            # bookkeeping error) must never reset an already-delivered tier.
            try:
                sent, message = _deliver_tier(tier, jobs, config)
            except Exception as e:
                sent, message = False, f"Email failed: {e}"

            if sent:
                delivered_summaries.append(f"{tier}: {message}")
            else:
                failed_ids.update(job["job_id"] for job in jobs)
                delivery_error = message
                print(f"\n[{tier}] {message}")

        notified_ids = [jid for jid in pending_ids if jid not in failed_ids]
        if notified_ids:
            mark_jobs_notified(notified_ids)
        if failed_ids:
            reset_pending_notifications(list(failed_ids))

        try:
            record_batch(
                status="pending_delivery" if failed_ids else "sent",
                jobs=pending_jobs,
                recipient=recipient,
                subject=build_subject(pending_jobs, config),
                error=delivery_error,
            )
        except Exception as e:
            print(f"\nWARNING: workflow inbox bookkeeping failed: {e}")

        if delivered_summaries:
            print("\n" + "\n".join(delivered_summaries))
        elif not tier_groups and not suppress:
            print("\nNo instant-alert tier roles this run; pending roles were archived in the workflow inbox.")

    inbox_path = render_inbox(config=config)
    print(f"\nWorkflow inbox updated: {inbox_path}")

    # Run retention cleanup only on the browser lane (every 4h) to avoid
    # unnecessary DELETE queries on every 30-minute fast-lane run.
    if lane in ("browser", "all"):
        retention = _retention_settings(config)
        cleaned = cleanup_old_jobs(
            retention_days=retention["job_days"],
            health_days=retention["health_days"],
            batch_days=retention["batch_days"],
        )
        if cleaned:
            print(f"Cleaned up {cleaned} expired row(s) (jobs unseen {retention['job_days']}d, "
                  f"health {retention['health_days']}d, alert archive {retention['batch_days']}d).")
        reclaimed = compact_database()
        if reclaimed:
            print(f"Compacted state database: reclaimed {reclaimed / 1024:.0f} KB.")

    _print_health_summary(results)
    _print_health_anomalies(health_anomalies)

    if errors:
        print(f"\n--- Errors ({len(errors)}) ---")
        for err in errors:
            print(f"  {err['company']}: {err['error']}")

    if errors and not successful_sources:
        print("\nWARNING: All selected adapters failed — no jobs fetched this run.")
        sys.exit(1)

    if delivery_error:
        print("\nWARNING: New roles were saved but not marked notified so the next run can retry delivery.")
        sys.exit(1)

    stats = get_stats()
    print(f"\nTotal jobs tracked: {stats['total']}")


def cmd_mark(args):
    job_id = args.job_id
    status = args.status

    if mark_status(job_id, status):
        print(f"Marked {job_id} as '{status}'")
    else:
        print(f"Job not found: {job_id}")
        print("Use 'jobwatch search <query>' to find job IDs")


def cmd_search(args):
    results = search_jobs(args.query, limit=args.limit)
    if not results:
        print(f"No jobs matching '{args.query}'")
        return

    print(f"\n{'ID':<45} {'Status':<12} {'Company':<20} Title")
    print("─" * 110)
    for r in results:
        jid = r["job_id"][:44]
        print(f"{jid:<45} {r['status']:<12} {r['company']:<20} {r['title']}")
        if r.get("url"):
            print(f"{'':>45} → {r['url']}")


def cmd_status(args):
    summary = get_status_summary()

    print("\n--- Application Pipeline ---")
    for status, count in summary["by_status"]:
        print(f"  {status:<15} {count:>5}")

    active = summary["active_applications"]
    if active:
        print(f"\n--- Active Applications ({len(active)}) ---")
        for app in active:
            print(f"  [{app['status']}] {app['company']} — {app['title']}")
            if app.get("url"):
                print(f"    → {app['url']}")
    else:
        print("\nNo active applications yet. Use 'jobwatch mark <job_id> applied' to track.")


def cmd_health(args):
    rows = get_recent_source_health(limit=args.limit)
    if not rows:
        print("No source health has been recorded yet.")
        return

    print("\n--- Recent Source Health ---")
    print(f"{'Run At':<20} {'Company':<24} {'Lane':<8} {'ATS':<16} {'Sec':>6} {'Raw':>5} {'Match':>5} Status")
    print("-" * 102)
    for row in rows:
        status = row["status"]
        if row.get("error"):
            status = f"{status}: {row['error']}"
        print(
            f"{row['run_at'][:19]:<20} {row['company'][:24]:<24} {row['source_lane']:<8} "
            f"{(row['ats'] or '')[:16]:<16} {float(row['duration_seconds']):>6.1f} "
            f"{int(row['raw_count']):>5} {int(row['matched_count']):>5} {status}"
        )


def _configured_source_keys(config: dict) -> set[tuple[str, str]]:
    return {
        (str(company.get("name", "")), str(company.get("ats", "")))
        for company in config.get("companies", []) or []
    }


def cmd_health_digest(args):
    config = load_config()
    # source_runs keeps 30 days of history, so companies removed from the
    # config would otherwise show up as "broken" until retention clears them.
    configured = _configured_source_keys(config)
    rows = [
        row for row in get_latest_source_health_per_source()
        if (str(row.get("company", "")), str(row.get("ats") or "")) in configured
    ]
    if not rows:
        print("No source health recorded yet; skipping digest.")
        return
    try:
        sent, message = send_health_digest(rows, config)
        print(message)
    except Exception as e:
        print(f"Health digest failed (non-fatal): {e}")


def cmd_export(args):
    import csv
    from store import _connect

    conn = _connect()
    conn.row_factory = __import__("sqlite3").Row
    cur = conn.execute(
        "SELECT job_id, company, title, location, url, status, first_seen, last_seen, posted_at, source, salary, notified_at "
        "FROM seen_jobs ORDER BY first_seen DESC"
    )
    rows = cur.fetchall()
    conn.close()

    out = args.output or "jobwatch_export.csv"
    with open(out, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "job_id",
                "company",
                "title",
                "location",
                "url",
                "status",
                "first_seen",
                "last_seen",
                "posted_at",
                "source",
                "salary",
                "notified_at",
            ]
        )
        for r in rows:
            writer.writerow(
                [
                    r["job_id"],
                    r["company"],
                    r["title"],
                    r["location"],
                    r["url"],
                    r["status"],
                    r["first_seen"],
                    r["last_seen"],
                    r["posted_at"],
                    r["source"],
                    r["salary"],
                    r["notified_at"],
                ]
            )

    print(f"Exported {len(rows)} jobs to {out}")


def cmd_inbox(args):
    config = load_config()
    inbox_path = render_inbox(limit=args.limit, config=config)
    print(print_summary(limit=args.limit, config=config))
    print(f"\nWorkflow inbox file: {inbox_path}")


def main():
    parser = argparse.ArgumentParser(description="JobWatch — career page monitor")
    sub = parser.add_subparsers(dest="command")

    p_run = sub.add_parser("run", help="Fetch new jobs and send alerts")
    p_run.add_argument(
        "--lane",
        choices=("all", "fast", "browser"),
        default="all",
        help="Source lane to run: all, fast ATS/API sources, or browser-backed Playwright sources",
    )
    p_run.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and rank jobs without updating the database, sending email, or writing workflow inbox files",
    )

    p_mark = sub.add_parser("mark", help="Update a job's application status")
    p_mark.add_argument("job_id", help="Job ID (from search results)")
    p_mark.add_argument("status", choices=VALID_STATUSES, help="New status")

    p_search = sub.add_parser("search", help="Search tracked jobs")
    p_search.add_argument("query", help="Search term (matches title or company)")
    p_search.add_argument("--limit", type=int, default=20, help="Max results")

    sub.add_parser("status", help="Show application pipeline summary")

    p_health = sub.add_parser("health", help="Show recent source health runs")
    p_health.add_argument("--limit", type=int, default=50, help="Max source health rows to show")

    p_export = sub.add_parser("export", help="Export all jobs to CSV")
    p_export.add_argument("--output", "-o", help="Output file path (default: jobwatch_export.csv)")

    p_inbox = sub.add_parser("inbox", help="Show the local workflow inbox summary")
    p_inbox.add_argument("--limit", type=int, default=10, help="Max batches/jobs to show")

    sub.add_parser("health-digest", help="Email a weekly source-health digest")

    args = parser.parse_args()

    commands = {
        "run": cmd_run,
        "mark": cmd_mark,
        "search": cmd_search,
        "status": cmd_status,
        "health": cmd_health,
        "health-digest": cmd_health_digest,
        "export": cmd_export,
        "inbox": cmd_inbox,
    }

    if args.command in commands:
        commands[args.command](args)
    else:
        # Default: run
        cmd_run(args)


if __name__ == "__main__":
    main()
