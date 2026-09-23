from __future__ import annotations

import re
from datetime import datetime, timezone

from filters import _parse_timestamp
from sponsors import is_h1b_sponsor


_REMOTE_RE = re.compile(r"\bremote\b|\bwork from home\b", re.IGNORECASE)
_HYBRID_RE = re.compile(r"\bhybrid\b", re.IGNORECASE)

_SOURCE_WEIGHTS = {
    "greenhouse": 8,
    "lever": 8,
    "ashby": 8,
    "smartrecruiters": 7,
    "workday": 7,
    "talentbrew": 6,
    "oleeo": 5,
    # First-party career APIs / boards: as trustworthy as an ATS feed.
    "amazon": 7,
    "netflix": 7,
    "uber": 7,
    "oracle": 7,
    "phenom": 6,
    "eightfold": 6,
    "eightfold_pw": 6,
    # Playwright scrapes of official pages: real postings, noisier parsing.
    "playwright": 4,
    "hn_hiring": -6,
}


def _normalize(text: str | None) -> str:
    if not text:
        return ""
    return " ".join(str(text).lower().split())


def _posted_at_sort_value(job: dict) -> float:
    posted_at = _parse_timestamp(job.get("posted_at"))
    if not posted_at:
        return 0.0
    return posted_at.timestamp()


def _best_keyword_match(title: str, keywords: list[str]) -> tuple[str | None, int]:
    normalized_title = _normalize(title)
    matches = []
    for keyword in keywords:
        normalized_keyword = _normalize(keyword)
        if normalized_keyword and normalized_keyword in normalized_title:
            matches.append(str(keyword))
    if not matches:
        return None, 0
    matches.sort(key=lambda item: (len(item), item.lower()), reverse=True)
    return matches[0], len(matches)


def _freshness_points(posted_at: str | None) -> tuple[int, list[str]]:
    normalized_posted = _normalize(posted_at)
    if normalized_posted in ("posted today", "today"):
        return 45, ["posted today"]
    if normalized_posted in ("posted yesterday", "yesterday"):
        return 10, ["posted yesterday"]

    posted = _parse_timestamp(posted_at)
    if not posted:
        return 12, ["posting time unavailable"]

    age_seconds = max(0.0, (datetime.now(timezone.utc) - posted).total_seconds())
    age_hours = age_seconds / 3600

    if age_hours <= 1:
        return 90, ["posted in the last hour"]
    if age_hours <= 3:
        return 75, ["posted in the last 3 hours"]
    if age_hours <= 6:
        return 60, ["posted in the last 6 hours"]
    if age_hours <= 12:
        return 45, ["posted today"]
    if age_hours <= 24:
        return 30, ["posted in the last 24 hours"]
    return 10, [posted.astimezone(timezone.utc).strftime("posted %Y-%m-%d")]


def _keyword_points(title: str, keywords: list[str]) -> tuple[int, list[str]]:
    best_keyword, match_count = _best_keyword_match(title, keywords)
    if not best_keyword:
        return 0, []

    normalized_title = _normalize(title)
    normalized_keyword = _normalize(best_keyword)
    is_exact = normalized_title == normalized_keyword

    points = min(35, 12 + len(best_keyword) // 2)
    if is_exact:
        points += 10
    if match_count > 1:
        points += min(8, (match_count - 1) * 3)

    reasons = [f'matched "{best_keyword}"']
    if is_exact:
        reasons[0] = f'exact "{best_keyword}" match'
    if match_count > 1:
        reasons.append(f"{match_count} keyword hits")
    return points, reasons


def _preferred_company_points(company: str, config: dict) -> tuple[int, list[str]]:
    ranking = config.get("ranking", {}) if config else {}
    preferred_companies = ranking.get("preferred_companies", []) or []
    normalized_company = _normalize(company)
    preferred = {_normalize(name) for name in preferred_companies if str(name).strip()}
    if normalized_company and normalized_company in preferred:
        return 25, ["preferred company"]
    return 0, []


def _location_points(location: str | None) -> tuple[int, list[str]]:
    normalized_location = location or ""
    if _REMOTE_RE.search(normalized_location):
        return 10, ["remote-friendly"]
    if _HYBRID_RE.search(normalized_location):
        return 4, ["hybrid"]
    return 0, []


def _source_points(source: str | None) -> tuple[int, list[str]]:
    weight = _SOURCE_WEIGHTS.get((source or "").strip().lower(), 0)
    if weight > 0:
        return weight, ["official ATS source"]
    if weight < 0:
        return weight, ["community-sourced listing"]
    return 0, []


def _sponsor_points(company: str) -> tuple[int, list[str]]:
    if is_h1b_sponsor(company):
        return 16, ["H-1B sponsor"]
    return 0, []


def _rank_band(score: int) -> str:
    if score >= 120:
        return "Top"
    if score >= 90:
        return "Strong"
    return "Watch"


def rank_job(job: dict, config: dict | None = None) -> dict:
    config = config or {}
    keywords = [str(keyword) for keyword in config.get("keywords", [])]

    score = 0
    reasons: list[str] = []

    for points, new_reasons in (
        _freshness_points(job.get("posted_at")),
        _keyword_points(job.get("title", ""), keywords),
        _preferred_company_points(job.get("company", ""), config),
        _location_points(job.get("location")),
        _source_points(job.get("source")),
        _sponsor_points(job.get("company", "")),
    ):
        score += points
        reasons.extend(new_reasons)

    unique_reasons = list(dict.fromkeys(reasons))
    return {
        **job,
        "rank_score": score,
        "rank_band": _rank_band(score),
        "rank_reasons": unique_reasons,
        "rank_reason_text": "; ".join(unique_reasons[:4]),
    }


def rank_jobs(jobs: list[dict], config: dict | None = None) -> list[dict]:
    ranked = [rank_job(job, config=config) for job in jobs]
    ranked.sort(
        key=lambda job: (
            -int(job.get("rank_score", 0)),
            -_posted_at_sort_value(job),
            (job.get("company") or "").lower(),
            (job.get("title") or "").lower(),
        )
    )
    return ranked


def top_picks_limit(config: dict | None = None) -> int:
    ranking = config.get("ranking", {}) if config else {}
    try:
        limit = int(ranking.get("top_picks", 5))
    except (TypeError, ValueError):
        limit = 5
    return max(1, limit)


def rank_summary(job: dict) -> str:
    label = job.get("rank_band") or "Watch"
    reason_text = job.get("rank_reason_text") or ""
    if reason_text:
        return f"{label} | {reason_text}"
    return label
