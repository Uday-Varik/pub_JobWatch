import re

import requests

API_BASE = "https://api.smartrecruiters.com/v1/companies/{slug}/postings"


def _slugify_title(title: str) -> str:
    return re.sub(r"-{2,}", "-", re.sub(r"[^a-z0-9]+", "-", title.lower())).strip("-")


def _public_job_url(company_slug: str, job_id: str, title: str) -> str:
    slug = _slugify_title(title)
    if slug:
        return f"https://jobs.smartrecruiters.com/{company_slug}/{job_id}-{slug}"
    return f"https://jobs.smartrecruiters.com/{company_slug}/{job_id}"


def fetch_smartrecruiters(company: dict) -> list[dict]:
    slug = company["slug"]
    url = API_BASE.format(slug=slug)

    jobs = []
    offset = 0
    limit = 100

    while True:
        resp = requests.get(url, params={"limit": limit, "offset": offset}, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        content = data.get("content", [])
        if not content:
            break

        for j in content:
            loc = j.get("location", {})
            location = loc.get("fullLocation") or ", ".join(
                [p for p in [loc.get("city", ""), loc.get("region", ""), loc.get("country", "")] if p]
            )

            job_id = str(j.get("id", "") or str(j.get("ref", "")).rsplit("/", 1)[-1])
            salary = None
            comp = j.get("compensation", {})
            if comp:
                min_val = comp.get("min", "")
                max_val = comp.get("max", "")
                currency = comp.get("currency", "USD")
                if min_val and max_val:
                    salary = f"${min_val:,} - ${max_val:,} {currency}" if isinstance(min_val, (int, float)) else f"{min_val} - {max_val}"
                elif min_val:
                    salary = f"${min_val:,}+" if isinstance(min_val, (int, float)) else str(min_val)

            jobs.append({
                "job_id": f"sr-{slug}-{job_id}",
                "company": company["name"],
                "title": j.get("name", ""),
                "location": location,
                "url": j.get("postingUrl") or j.get("applyUrl") or _public_job_url(slug, job_id, j.get("name", "")),
                "posted_at": j.get("releasedDate"),
                "salary": salary,
            })

        if len(content) < limit:
            break
        offset += limit

    return jobs
