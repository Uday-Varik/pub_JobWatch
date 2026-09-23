#!/usr/bin/env python3
"""Auto-detect ATS platform for a list of companies and generate config entries."""

import re
import sys
import requests
import yaml

TIMEOUT = 10
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}


def _try_greenhouse(slug: str) -> dict | None:
    try:
        resp = requests.get(
            f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs",
            timeout=TIMEOUT,
        )
        if resp.status_code == 200:
            data = resp.json()
            count = len(data.get("jobs", []))
            return {"ats": "greenhouse", "slug": slug, "jobs": count}
    except Exception:
        pass
    return None


def _try_lever(slug: str) -> dict | None:
    try:
        resp = requests.get(
            f"https://api.lever.co/v0/postings/{slug}",
            params={"mode": "json", "limit": 1},
            timeout=TIMEOUT,
        )
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, list):
                return {"ats": "lever", "slug": slug, "jobs": len(data)}
    except Exception:
        pass
    return None


def _try_ashby(slug: str) -> dict | None:
    try:
        resp = requests.get(
            f"https://api.ashbyhq.com/posting-api/job-board/{slug}",
            timeout=TIMEOUT,
        )
        if resp.status_code == 200:
            data = resp.json()
            count = len(data.get("jobs", []))
            return {"ats": "ashby", "slug": slug, "jobs": count}
    except Exception:
        pass
    return None


def _try_smartrecruiters(slug: str) -> dict | None:
    try:
        resp = requests.get(
            f"https://api.smartrecruiters.com/v1/companies/{slug}/postings",
            params={"limit": 1},
            timeout=TIMEOUT,
        )
        if resp.status_code == 200:
            data = resp.json()
            total = data.get("totalFound", len(data.get("content", [])))
            return {"ats": "smartrecruiters", "slug": slug, "jobs": total}
    except Exception:
        pass
    return None


def _try_workday_from_url(url: str) -> dict | None:
    match = re.search(r"([\w.-]+)\.myworkdayjobs\.com.*?/([\w-]+)", url)
    if not match:
        return None
    tenant = match.group(1)
    site = match.group(2)
    if site in ("en-US", "en-GB", "wday"):
        return None
    company_slug = tenant.split(".")[0]
    try:
        api_url = f"https://{tenant}.myworkdayjobs.com/wday/cxs/{company_slug}/{site}/jobs"
        resp = requests.post(
            api_url,
            json={"appliedFacets": {}, "limit": 1, "offset": 0, "searchText": ""},
            headers={"Content-Type": "application/json"},
            timeout=TIMEOUT,
        )
        if resp.status_code == 200:
            data = resp.json()
            total = data.get("total", 0)
            return {"ats": "workday", "tenant": tenant, "site": site, "jobs": total}
    except Exception:
        pass
    return None


def _detect_from_careers_page(url: str) -> dict | None:
    try:
        resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT, allow_redirects=True)
        html = resp.text
        final_url = resp.url

        if "myworkdayjobs.com" in final_url:
            return _try_workday_from_url(final_url)

        if "greenhouse.io" in html or "boards-api.greenhouse.io" in html:
            slug_match = re.search(r"greenhouse\.io/[\w/]*?(\w+)/jobs", html)
            if slug_match:
                return _try_greenhouse(slug_match.group(1))

        if "lever.co" in html or "api.lever.co" in html:
            slug_match = re.search(r"lever\.co/[\w/]*?(\w+)", html)
            if slug_match:
                return _try_lever(slug_match.group(1))

        if "ashbyhq.com" in html:
            slug_match = re.search(r"ashbyhq\.com/[\w/]*?(\w+)", html)
            if slug_match:
                return _try_ashby(slug_match.group(1))

        if "smartrecruiters.com" in html:
            slug_match = re.search(r"smartrecruiters\.com/[\w/]*?(\w+)/postings", html)
            if slug_match:
                return _try_smartrecruiters(slug_match.group(1))

        if "myworkdayjobs.com" in html:
            wd_match = re.search(r"([\w.-]+\.wd\d+)\.myworkdayjobs\.com.*?/([\w-]+)", html)
            if wd_match:
                return _try_workday_from_url(f"https://{wd_match.group(1)}.myworkdayjobs.com/{wd_match.group(2)}")

    except Exception:
        pass
    return None


def detect_ats(company_name: str, careers_url: str = None) -> dict | None:
    slug_variants = [
        company_name.lower().replace(" ", ""),
        company_name.lower().replace(" ", "-"),
        company_name.lower().replace(" ", "_"),
        company_name.lower().split()[0] if " " in company_name else company_name.lower(),
    ]
    slug_variants = list(dict.fromkeys(slug_variants))

    for slug in slug_variants:
        for probe in [_try_greenhouse, _try_lever, _try_ashby, _try_smartrecruiters]:
            result = probe(slug)
            if result:
                result["name"] = company_name
                return result

    if careers_url:
        result = _detect_from_careers_page(careers_url)
        if result:
            result["name"] = company_name
            return result

    return None


def generate_config_entry(result: dict) -> dict:
    entry = {"name": result["name"], "ats": result["ats"]}
    if result["ats"] in ("greenhouse", "lever", "ashby", "smartrecruiters"):
        entry["slug"] = result["slug"]
    elif result["ats"] == "workday":
        entry["tenant"] = result["tenant"]
        entry["site"] = result["site"]
    return entry


def main():
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python discover.py 'Company Name'")
        print("  python discover.py 'Company Name' 'https://company.com/careers'")
        print("  python discover.py --file companies.txt")
        print()
        print("companies.txt format (one per line):")
        print("  Company Name")
        print("  Company Name | https://careers.company.com")
        sys.exit(1)

    entries = []

    if sys.argv[1] == "--file":
        with open(sys.argv[2]) as f:
            lines = [l.strip() for l in f if l.strip() and not l.startswith("#")]
    else:
        lines = [" | ".join(sys.argv[1:])]

    for line in lines:
        parts = [p.strip() for p in line.split("|")]
        name = parts[0]
        url = parts[1] if len(parts) > 1 else None

        print(f"  [{name}] Detecting ATS...", end=" ", flush=True)
        result = detect_ats(name, url)

        if result:
            print(f"Found: {result['ats']} ({result.get('jobs', '?')} jobs)")
            entries.append(generate_config_entry(result))
        else:
            print("Not detected — try providing the careers page URL")

    if entries:
        print("\n--- Add to config.yaml ---")
        print(yaml.dump(entries, default_flow_style=False))


if __name__ == "__main__":
    main()
