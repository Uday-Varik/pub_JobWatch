import re
import time

import requests

CXS_URL = "https://{tenant}.myworkdayjobs.com/wday/cxs/{company}/{site}/jobs"
DETAIL_URL = "https://{tenant}.myworkdayjobs.com/wday/cxs/{company}/{site}{path}"

_MULTI_LOC_RE = re.compile(r"^\d+ Locations?$", re.IGNORECASE)
DEFAULT_MAX_PAGES = 25
# Stop paginating before the lane's hard per-source timeout (fast=150s,
# browser=210s) so a huge board returns the jobs it has instead of being
# killed with zero. Large enterprise boards (1400+ jobs) plus per-job
# location-resolution calls used to blow the budget and return nothing.
DEFAULT_TIME_BUDGET_SECONDS = 110


def _resolve_locations(tenant: str, company_slug: str, site: str, external_path: str) -> str:
    try:
        url = DETAIL_URL.format(tenant=tenant, company=company_slug, site=site, path=external_path)
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        info = resp.json().get("jobPostingInfo", {})
        locs = [info.get("location", "")]
        locs.extend(info.get("additionalLocations", []))
        return ", ".join(loc for loc in locs if loc)
    except Exception:
        return ""


def _max_pages(company: dict) -> int:
    try:
        value = int(company.get("max_pages", DEFAULT_MAX_PAGES))
    except (TypeError, ValueError):
        value = DEFAULT_MAX_PAGES
    return value if value > 0 else DEFAULT_MAX_PAGES


def _time_budget(company: dict) -> float:
    try:
        value = float(company.get("time_budget_seconds", DEFAULT_TIME_BUDGET_SECONDS))
    except (TypeError, ValueError):
        value = DEFAULT_TIME_BUDGET_SECONDS
    return value if value > 0 else DEFAULT_TIME_BUDGET_SECONDS


def fetch_workday(company: dict) -> list[dict]:
    tenant = company["tenant"]
    company_slug = tenant.split(".")[0]
    site = company["site"]
    url = CXS_URL.format(tenant=tenant, company=company_slug, site=site)

    headers = {"Content-Type": "application/json"}
    jobs = []
    seen_job_ids = set()
    offset = 0
    limit = 20
    expected_total = None
    max_pages = _max_pages(company)
    time_budget = _time_budget(company)
    start = time.monotonic()
    truncated_by_time = False
    pages = 0

    while pages < max_pages:
        if time.monotonic() - start > time_budget:
            truncated_by_time = True
            break
        payload = {
            "appliedFacets": {},
            "limit": limit,
            "offset": offset,
            "searchText": "",
        }
        resp = requests.post(url, json=payload, headers=headers, timeout=30)
        resp.raise_for_status()
        data = resp.json()

        job_postings = data.get("jobPostings", [])
        if not job_postings:
            break

        if expected_total is None:
            total = data.get("total", 0)
            expected_total = total if isinstance(total, int) and total > 0 else None

        added_this_page = 0

        for j in job_postings:
            title = j.get("title", "")
            location = j.get("locationsText", "").strip()

            external_path = j.get("externalPath", "")
            job_url = f"https://{tenant}.myworkdayjobs.com/en-US/{site}{external_path}" if external_path else ""
            job_key = external_path or title

            if job_key in seen_job_ids:
                continue
            seen_job_ids.add(job_key)
            added_this_page += 1

            # Resolving "N Locations" costs an extra HTTP round-trip per job.
            # Once we're over budget, stop resolving so we keep returning the
            # remaining jobs quickly rather than burning the budget on detail
            # fetches (the raw "N Locations" text is left in place).
            if (
                _MULTI_LOC_RE.match(location)
                and external_path
                and time.monotonic() - start <= time_budget
            ):
                location = _resolve_locations(tenant, company_slug, site, external_path)

            bullet_fields = j.get("bulletFields", [])
            posted_at = j.get("postedOn") or (bullet_fields[1] if len(bullet_fields) > 1 else None)
            salary = None
            for field in bullet_fields:
                if field and "$" in str(field):
                    salary = str(field)
                    break
            jobs.append({
                "job_id": f"wd-{company_slug}-{external_path or title}",
                "company": company["name"],
                "title": title,
                "location": location,
                "url": job_url,
                "posted_at": posted_at,
                "salary": salary,
            })

        if expected_total and len(seen_job_ids) >= expected_total:
            break
        if added_this_page == 0 or len(job_postings) < limit:
            break
        offset += limit
        pages += 1

    if truncated_by_time and expected_total and len(seen_job_ids) < expected_total:
        print(
            f"  NOTE: {company['name']} workday board returned "
            f"{len(seen_job_ids)}/{expected_total} jobs (hit {time_budget:.0f}s "
            "time budget); remaining pages skipped to stay within the lane timeout."
        )
    elif expected_total and len(seen_job_ids) < expected_total and pages >= max_pages:
        print(
            f"  WARNING: {company['name']} workday board truncated at "
            f"{len(seen_job_ids)}/{expected_total} jobs (max_pages={max_pages}); "
            "set max_pages in config.yaml to raise the cap."
        )

    return jobs
