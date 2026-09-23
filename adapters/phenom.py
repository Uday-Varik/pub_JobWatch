import re
import requests

API_URL = "https://{domain}/api/jobs"

_SALARY_RE = re.compile(
    r"\$[\d,]+(?:\.\d+)?(?:\s*[-–—to]+\s*\$[\d,]+(?:\.\d+)?)?(?:\s*/\s*(?:yr|year|hr|hour|annually))?",
    re.IGNORECASE,
)


def fetch_phenom(company: dict) -> list[dict]:
    domain = company["domain"]
    company_name = company["name"]
    url = API_URL.format(domain=domain)

    jobs = []
    page = 1
    limit = 100

    while True:
        resp = requests.get(
            url,
            params={"page": page, "limit": limit, "sortBy": "relevance"},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()

        job_list = data.get("jobs", [])
        if not job_list:
            break

        for j in job_list:
            d = j.get("data", j)
            title = d.get("title", "")
            city = d.get("city", "")
            state = d.get("state", "")
            country = d.get("country", "")
            loc_parts = [p for p in [city, state, country] if p]
            location = ", ".join(loc_parts)

            req_id = str(d.get("req_id", d.get("id", "")))
            slug = d.get("slug", req_id)
            job_url = f"https://{domain}/careers-home/jobs/{slug}" if slug else ""

            salary = None
            description = d.get("description", "")
            if description:
                m = _SALARY_RE.search(description)
                if m:
                    salary = m.group(0).strip()

            jobs.append({
                # Full domain (not domain.split('.')[0]) so two phenom boards
                # both at careers.<co>.com don't share the "ph-careers-" prefix
                # and collide on req_id (e.g. AMD vs Rivian).
                "job_id": f"ph-{domain}-{req_id}",
                "company": company_name,
                "title": title,
                "location": location,
                "url": job_url,
                "posted_at": d.get("postedDate") or d.get("updateDate"),
                "salary": salary,
            })

        total = data.get("totalCount", 0)
        if page * limit >= total:
            break
        page += 1

    return jobs
