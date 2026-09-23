import requests

API_URL = "https://explore.jobs.netflix.net/api/apply/v2/jobs"
NUM_PER_PAGE = 100
MAX_PAGES = 5


def fetch_netflix(company: dict) -> list[dict]:
    company_name = company["name"]
    jobs = []

    for page in range(MAX_PAGES):
        start = page * NUM_PER_PAGE
        resp = requests.get(
            API_URL,
            params={
                "domain": "netflix.com",
                "start": start,
                "num": NUM_PER_PAGE,
                "sort_by": "relevance",
            },
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/125.0.0.0 Safari/537.36"
                ),
                "Accept": "application/json",
            },
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()

        positions = data.get("positions", [])
        if not positions:
            break

        for p in positions:
            pid = p.get("id", "")
            jobs.append({
                "job_id": f"netflix-{pid}",
                "title": p.get("name", ""),
                "company": company_name,
                "location": p.get("location", ""),
                "url": f"https://explore.jobs.netflix.net/careers/{pid}",
                "posted_at": "",
                "source": "netflix",
            })

        page_count = data.get("page_count", 0)
        if page + 1 >= page_count:
            break

    return jobs
