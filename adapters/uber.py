import requests

API_URL = "https://www.uber.com/api/loadSearchJobsResults"


def fetch_uber(company: dict) -> list[dict]:
    company_name = company["name"]
    jobs = []

    resp = requests.post(
        API_URL,
        json={"params": {"location": [], "department": [], "team": []}},
        headers={
            "Content-Type": "application/json",
            "x-csrf-token": "x",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            ),
        },
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()

    results = data.get("results", data.get("data", {}).get("results", []))
    if isinstance(results, dict):
        results = results.get("jobs", results.get("items", []))

    for r in results:
        rid = r.get("id", r.get("jobId", ""))
        title = r.get("title", r.get("name", ""))
        location = r.get("location", r.get("city", ""))
        if isinstance(location, dict):
            location = location.get("name", str(location))
        url = r.get("url", r.get("absoluteUrl", ""))
        if not url and rid:
            url = f"https://www.uber.com/global/en/careers/list/{rid}/"

        jobs.append({
            "job_id": f"uber-{rid}",
            "title": title,
            "company": company_name,
            "location": location,
            "url": url,
            "posted_at": r.get("postedAt", r.get("createdAt", "")),
            "source": "uber",
        })

    return jobs
