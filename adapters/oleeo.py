import re
import requests

BASE_URL = "https://{subdomain}.tal.net/vx/lang-en-GB/mobile-0/brand-{brand}/candidate/jobboard/vacancy/{board}/adv/"


def fetch_oleeo(company: dict) -> list[dict]:
    subdomain = company["subdomain"]
    brand = company.get("brand", "2")
    board = company.get("board", "1")
    company_name = company["name"]

    url = BASE_URL.format(subdomain=subdomain, brand=brand, board=board)
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    html = resp.text

    jobs = []
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.DOTALL)

    for row in rows:
        cells = re.findall(r"<td[^>]*>(.*?)</td>", row, re.DOTALL)
        if len(cells) < 2:
            continue

        title_match = re.search(r'<a[^>]*href="([^"]+)"[^>]*>([^<]+)', cells[0])
        if not title_match:
            continue

        job_url = title_match.group(1)
        title = title_match.group(2).strip()
        location = re.sub(r"<[^>]+>", "", cells[1]).strip()

        path = re.search(r"/vacancy/(\d+)/", job_url)
        job_id_part = path.group(1) if path else title

        jobs.append({
            "job_id": f"ol-{subdomain}-{job_id_part}",
            "company": company_name,
            "title": title,
            "location": location,
            "url": job_url,
        })

    return jobs
