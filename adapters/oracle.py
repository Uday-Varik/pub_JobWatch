"""Oracle Cloud HCM (Candidate Experience) recruiting adapter.

Oracle Corp and many Oracle-HCM tenants expose a public REST API:
    https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions
        ?finder=findReqs;siteNumber={site},keyword=...,limit=...,offset=...,sortBy=POSTING_DATES_DESC

Results are sorted newest-first, so a bounded fetch under a time budget still
captures every fresh role (older pages are dropped by the 24h staleness gate).

Config:
    - name: Oracle
      ats: oracle
      host: eeho.fa.us2.oraclecloud.com
      site: CX_1
      keyword: software        # optional; omit to fetch all, newest-first
"""

import time
from urllib.parse import quote

import requests

API_PATH = "/hcmRestApi/resources/latest/recruitingCEJobRequisitions"
DEFAULT_SITE = "CX_1"
PAGE_LIMIT = 50
DEFAULT_MAX_PAGES = 8
DEFAULT_TIME_BUDGET_SECONDS = 90


def _detail_url(host: str, site: str, req_id: str) -> str:
    return f"https://{host}/hcmUI/CandidateExperience/en/sites/{site}/job/{req_id}"


def fetch_oracle(company: dict) -> list[dict]:
    host = company["host"]
    site = company.get("site", DEFAULT_SITE)
    company_name = company["name"]
    keyword = company.get("keyword", "")
    max_pages = int(company.get("max_pages", DEFAULT_MAX_PAGES))
    time_budget = float(company.get("time_budget_seconds", DEFAULT_TIME_BUDGET_SECONDS))

    headers = {
        "Accept": "application/json",
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
        ),
    }

    jobs: list[dict] = []
    seen_ids: set[str] = set()
    start = time.monotonic()
    offset = 0
    expected_total = None

    for _ in range(max_pages):
        if time.monotonic() - start > time_budget:
            break

        finder = (
            f"findReqs;siteNumber={site},"
            f"sortBy=POSTING_DATES_DESC,limit={PAGE_LIMIT},offset={offset}"
        )
        if keyword:
            finder += f",keyword={quote(keyword)}"
        url = f"https://{host}{API_PATH}?onlyData=true&expand=requisitionList&finder={finder}"

        resp = requests.get(url, headers=headers, timeout=30)
        resp.raise_for_status()
        items = resp.json().get("items", [])
        if not items:
            break

        block = items[0]
        if expected_total is None:
            expected_total = block.get("TotalJobsCount")
        reqs = block.get("requisitionList", [])
        if not reqs:
            break

        added = 0
        for r in reqs:
            req_id = str(r.get("Id", "")).strip()
            if not req_id or req_id in seen_ids:
                continue
            seen_ids.add(req_id)
            added += 1
            jobs.append({
                "job_id": f"orc-{host.split('.')[0]}-{req_id}",
                "company": company_name,
                "title": r.get("Title", ""),
                "location": r.get("PrimaryLocation", ""),
                "url": _detail_url(host, site, req_id),
                "posted_at": r.get("PostedDate"),
                "source": "oracle",
            })

        if added == 0 or len(reqs) < PAGE_LIMIT:
            break
        if expected_total and len(seen_ids) >= expected_total:
            break
        offset += PAGE_LIMIT

    return jobs
