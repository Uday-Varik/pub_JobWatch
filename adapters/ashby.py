import re
import requests

API_URL = "https://api.ashbyhq.com/posting-api/job-board/{slug}"

_SALARY_RE = re.compile(r"\$[\d,]+(?:\.\d+)?(?:\s*[-–—to]+\s*\$[\d,]+(?:\.\d+)?)?(?:\s*/\s*(?:yr|year|hr|hour|annually))?", re.IGNORECASE)


def fetch_ashby(company: dict) -> list[dict]:
    slug = company["slug"]
    url = API_URL.format(slug=slug)
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    jobs = []
    for j in data.get("jobs", []):
        location = j.get("location", "")
        job_id = j.get("id", j.get("title", ""))
        salary = None
        comp = j.get("compensationTierSummary") or ""
        if comp:
            salary = comp
        elif j.get("descriptionHtml"):
            m = _SALARY_RE.search(j["descriptionHtml"])
            if m:
                salary = m.group(0).strip()
        jobs.append({
            "job_id": f"ab-{slug}-{job_id}",
            "company": company["name"],
            "title": j.get("title", ""),
            "location": location,
            "url": j.get("jobUrl", j.get("applyUrl", "")),
            "posted_at": j.get("publishedAt") or j.get("updatedAt"),
            "salary": salary,
        })
    return jobs
