import re
import requests

CAREERS_URL = "https://app.eightfold.ai/careers?domain={domain}&query={query}&location="


def fetch_eightfold(company: dict) -> list[dict]:
    domain = company["domain"]
    company_name = company["name"]

    url = f"https://app.eightfold.ai/careers?domain={domain}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    }
    resp = requests.get(url, headers=headers, timeout=30)
    resp.raise_for_status()
    html = resp.text

    jobs = []

    positions = re.findall(
        r'class="position-card[^"]*"[^>]*>.*?'
        r'href="([^"]+)"[^>]*>\s*<h[34][^>]*>([^<]+)</h[34]>'
        r'.*?class="position-location[^"]*"[^>]*>([^<]*)',
        html,
        re.DOTALL,
    )

    if not positions:
        positions = re.findall(
            r'<a[^>]*href="(/careers/[^"]*position/[^"]*)"[^>]*>\s*'
            r'(?:<[^>]+>)*\s*([^<]+)',
            html,
        )
        positions = [(url, title, "") for url, title in positions]

    if not positions:
        links = re.findall(
            r'href="([^"]*eightfold\.ai/careers[^"]*position[^"]*)"[^>]*>([^<]+)',
            html,
        )
        positions = [(url, title, "") for url, title in links]

    for job_url, title, location in positions:
        title = title.strip()
        location = location.strip() if location else ""
        if not job_url.startswith("http"):
            job_url = f"https://app.eightfold.ai{job_url}"

        uid = re.search(r"position/([^/?]+)", job_url)
        job_id_part = uid.group(1) if uid else title

        jobs.append({
            "job_id": f"ef-{domain}-{job_id_part}",
            "company": company_name,
            "title": title,
            "location": location,
            "url": job_url,
        })

    return jobs
