import re
import requests
from datetime import datetime, timezone

API_BASE = "https://api.lever.co/v0/postings/{slug}"

_SALARY_RE = re.compile(r"\$[\d,]+(?:\.\d+)?(?:\s*[-–—to]+\s*\$[\d,]+(?:\.\d+)?)?(?:\s*/\s*(?:yr|year|hr|hour|annually))?", re.IGNORECASE)


def fetch_lever(company: dict) -> list[dict]:
    slug = company["slug"]
    url = API_BASE.format(slug=slug)

    jobs = []
    offset = 0
    limit = 100

    while True:
        resp = requests.get(url, params={"mode": "json", "limit": limit, "skip": offset}, timeout=30)
        resp.raise_for_status()
        batch = resp.json()
        if not batch:
            break

        for j in batch:
            location = j.get("categories", {}).get("location", "")
            created_ms = j.get("createdAt")
            posted_at = None
            if created_ms:
                posted_at = datetime.fromtimestamp(created_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            salary = None
            desc = j.get("descriptionPlain") or j.get("description") or ""
            m = _SALARY_RE.search(desc)
            if m:
                salary = m.group(0).strip()
            if not salary:
                comp = j.get("categories", {}).get("commitment", "")
                if "$" in comp:
                    salary = comp
            jobs.append({
                "job_id": f"lv-{slug}-{j['id']}",
                "company": company["name"],
                "title": j.get("text", ""),
                "location": location,
                "url": j.get("hostedUrl", ""),
                "posted_at": posted_at,
                "salary": salary,
            })

        if len(batch) < limit:
            break
        offset += limit

    return jobs
