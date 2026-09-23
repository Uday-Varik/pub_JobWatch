import re
import requests

API_BASE = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"

_SALARY_RE = re.compile(r"\$[\d,]+(?:\.\d+)?(?:\s*[-–—to]+\s*\$[\d,]+(?:\.\d+)?)?(?:\s*/\s*(?:yr|year|hr|hour|annually))?", re.IGNORECASE)


def _extract_salary(content: str) -> str | None:
    if not content:
        return None
    m = _SALARY_RE.search(content)
    return m.group(0).strip() if m else None


def fetch_greenhouse(company: dict) -> list[dict]:
    slug = company["slug"]
    url = API_BASE.format(slug=slug)
    resp = requests.get(url, params={"content": "true"}, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    jobs = []
    for j in data.get("jobs", []):
        location = j.get("location", {}).get("name", "")
        content = j.get("content", "")
        jobs.append({
            "job_id": f"gh-{slug}-{j['id']}",
            "company": company["name"],
            "title": j.get("title", ""),
            "location": location,
            "url": j.get("absolute_url", ""),
            # first_published is when the role actually went live. updated_at
            # moves on ANY edit, so a bulk touch of a board (template or footer
            # change) made months-old roles look posted "in the last hour" and
            # re-alerted them. Fall back to updated_at only when the board
            # omits first_published. (The API has no created_at.)
            "posted_at": j.get("first_published") or j.get("updated_at"),
            "salary": _extract_salary(content),
        })
    return jobs
