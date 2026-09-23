import re

import requests

API_URL = "https://www.amazon.jobs/en/search.json"


RESULT_LIMIT = 100
TARGET_US_JOBS = 500
MAX_PAGES = 12

_US_LOCATION_RE = re.compile(r"\b(?:usa|united states|u\.s\.a\.|u\.s\.)\b", re.IGNORECASE)


def _job_location(job: dict) -> str:
    return job.get("normalized_location") or job.get("location") or ""


def _is_us_job(job: dict) -> bool:
    return bool(_US_LOCATION_RE.search(_job_location(job)))


def fetch_amazon(company: dict = None) -> list[dict]:
    jobs = []
    offset = 0
    pages = 0

    while pages < MAX_PAGES and len(jobs) < TARGET_US_JOBS:
        resp = requests.get(
            API_URL,
            params={
                "base_query": "",
                "loc_query": "United States",
                "offset": offset,
                "result_limit": RESULT_LIMIT,
                "sort": "recent",
            },
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
                "Accept-Encoding": "gzip, deflate",
            },
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()

        for j in data.get("jobs", []):
            if not _is_us_job(j):
                continue
            # .get with a default doesn't cover explicit nulls — a posting
            # with "id_icims": null would produce the colliding id "amz-None"
            # and break dedup. Skip postings with no usable id instead.
            job_id = j.get("id_icims") or j.get("id")
            if not job_id:
                continue
            jobs.append({
                "job_id": f"amz-{job_id}",
                "company": "Amazon",
                "title": j.get("title", ""),
                "location": _job_location(j),
                "url": f"https://www.amazon.jobs{j['job_path']}" if j.get("job_path") else "",
                "posted_at": j.get("posted_date", ""),
                "salary": None,
            })
            if len(jobs) >= TARGET_US_JOBS:
                break

        total = data.get("hits", 0)
        offset += RESULT_LIMIT
        pages += 1
        if offset >= total or not data.get("jobs"):
            break

    return jobs
