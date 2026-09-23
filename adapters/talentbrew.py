import re
import requests

SEARCH_URL = "https://{domain}/search-jobs/results"

# Safety cap: 20 pages x 100 records. TalentBrew scrapes HTML out of a JSON
# envelope, so a markup change could otherwise loop forever.
MAX_PAGES = 20


def fetch_talentbrew(company: dict) -> list[dict]:
    domain = company["domain"]
    url = SEARCH_URL.format(domain=domain)
    company_name = company["name"]

    jobs = []
    page = 1

    while page <= MAX_PAGES:
        params = {
            "ActiveFacetID": 0,
            "CurrentPage": page,
            "RecordsPerPage": 100,
            "Distance": 50,
            "RadiusUnitType": 0,
            "Keywords": "",
            "Location": "",
            "ShowRadius": "False",
            "IsPag498": "False",
            "CustomFacetName": "",
            "FacetTerm": "",
            "FacetType": 0,
            "SearchResultsModuleName": "Search Results",
            "SearchFiltersModuleName": "Search Filters",
            "SortCriteria": 0,
            "SortDirection": 0,
            "SearchType": 5,
        }
        resp = requests.get(url, params=params, headers={"Accept": "application/json"}, timeout=30)
        resp.raise_for_status()
        data = resp.json()

        results_html = data.get("results", "")
        page_jobs = re.findall(
            r'href="(/job/[^"]+)"[^>]*>\s*([^<]+)',
            results_html,
        )

        if not page_jobs:
            break

        for path, title in page_jobs:
            location_match = re.search(
                r'/job/([^/]+)/',
                path,
            )
            location = location_match.group(1).replace("-", " ").title() if location_match else ""
            job_url = f"https://{domain}{path}"

            jobs.append({
                "job_id": f"tb-{domain}-{path}",
                "company": company_name,
                "title": title.strip(),
                "location": location,
                "url": job_url,
            })

        if len(page_jobs) < 100:
            break
        page += 1

    return jobs
