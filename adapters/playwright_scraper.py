"""
Playwright-based scraper for career pages that require JS rendering.

Each company has a config dict defining:
  - url: starting URL
  - wait_selector: CSS selector to wait for before scraping
  - job_selector: CSS selector for each job card
  - extract: dict mapping field names to CSS selectors within the job card
  - next_selector: (optional) CSS selector for pagination "next" button
  - max_pages: (optional) max pages to scrape (default 5)
"""

import hashlib
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_SALARY_RE = re.compile(
    r"\$[\d,]+(?:\.\d+)?(?:\s*[-–—to]+\s*\$[\d,]+(?:\.\d+)?)?",
    re.IGNORECASE,
)

_COMMON_POSTED_SELECTORS = (
    "time",
    "[datetime]",
    "[data-testid*='posted']",
    "[data-testid*='date']",
    ".posted",
    ".posting-date",
    ".job-date",
    ".date",
)

_COMMON_PAGINATION_SELECTORS = (
    "[rel='next']",
    "button[aria-label*='Next']",
    "a[aria-label*='Next']",
    "button[title*='Next']",
    "a[title*='Next']",
    "[data-testid*='next']",
    ".pagination-next",
    "button:has-text('Next')",
    "a:has-text('Next')",
    "button:has-text('Load more')",
    "a:has-text('Load more')",
    "button:has-text('Show more')",
    "a:has-text('Show more')",
    "button:has-text('See more')",
    "a:has-text('See more')",
    "button:has-text('More jobs')",
    "a:has-text('More jobs')",
)

COMPANY_CONFIGS = {
    "google": {
        "url": "https://www.google.com/about/careers/applications/jobs/results/?q=&location=United+States&page=1",
        "wait_selector": "li.lLd3Je",
        "job_selector": "li.lLd3Je",
        "extract": {
            "title": "h3.QJPWVe",
            "location": ".pwO9Dc .r0wTof",
            "url": "",
        },
        "base_url": "https://www.google.com",
        "max_pages": 10,
    },
    "apple": {
        "url": "https://jobs.apple.com/en-us/search?search=software+engineer&sort=newest&location=united-states-USA",
        "wait_selector": "table tbody tr, .results-list a",
        "job_selector": "table tbody tr, .results-list a",
        "extract": {
            "title": "td:first-child a, .results-list__title",
            "location": "td:nth-child(2), .results-list__location",
            "url": "td:first-child a, a",
            "posted_at": "td:nth-child(3), .results-list__date",
        },
        "base_url": "https://jobs.apple.com",
        "max_pages": 10,
    },
    "netflix": {
        "url": "https://explore.jobs.netflix.net/careers?query=software+engineer&location=United+States&sort_by=new",
        "wait_selector": "[data-testid='job-card'], .card-job",
        "job_selector": "[data-testid='job-card'], .card-job",
        "extract": {
            "title": "h3, .card-job__title, [data-testid='job-title']",
            "location": "[data-testid='job-location'], .card-job__location, .location",
            "url": "a",
        },
        "base_url": "https://explore.jobs.netflix.net",
        "max_pages": 5,
    },
    "meta": {
        "url": "https://www.metacareers.com/jobs?q=software+engineer&location[0]=United+States",
        "wait_selector": "[data-testid='job-card'], .job-card, a[href*='/jobs/']",
        "job_selector": "a[href*='/jobs/']",
        "extract": {
            "title": "div:first-child",
            "location": "div:nth-child(2)",
            "url": "",
        },
        "base_url": "https://www.metacareers.com",
        "max_pages": 5,
    },
    "microsoft": {
        "url": "https://careers.microsoft.com/us/en/search-results?keywords=software%20engineer&country=United%20States",
        "wait_selector": "[data-ph-at-id='jobs-list'] li, .jobs-list-item",
        "job_selector": "[data-ph-at-id='jobs-list'] li, .jobs-list-item",
        "extract": {
            "title": "a, .job-title",
            "location": ".job-location, [data-ph-at-id='jobLocation-value']",
            "url": "a",
            "posted_at": ".job-date, [data-ph-at-id='jobPostedDate-value']",
        },
        "base_url": "https://careers.microsoft.com",
        "max_pages": 10,
    },
    "uber": {
        "url": "https://www.uber.com/us/en/careers/list/?query=software+engineer&location=USA",
        "wait_selector": "[data-testid='job-card'], .css-1q2dra3, a[href*='/careers/list/']",
        "job_selector": "a[href*='/careers/list/']",
        "extract": {
            "title": "h3, div:first-child",
            "location": "span, div:nth-child(2)",
            "url": "",
        },
        "base_url": "https://www.uber.com",
        "max_pages": 5,
    },
    "doordash": {
        "url": "https://careers.doordash.com/open-positions?search=software+engineer",
        "wait_selector": "[data-testid='job-card'], .open-positions__list a",
        "job_selector": "[data-testid='job-card'], .open-positions__list a, a[href*='/open-positions/']",
        "extract": {
            "title": "h3, .title, div:first-child",
            "location": ".location, span, div:nth-child(2)",
            "url": "",
        },
        "base_url": "https://careers.doordash.com",
        "max_pages": 5,
    },
    "goldman_sachs": {
        "url": "https://higher.gs.com/roles?query=software+engineer&location=United+States",
        "wait_selector": "[data-testid='role-card'], .role-card, a[href*='/roles/']",
        "job_selector": "[data-testid='role-card'], .role-card, a[href*='/roles/']",
        "extract": {
            "title": "h3, .role-title, div:first-child",
            "location": ".role-location, span, div:nth-child(2)",
            "url": "a",
        },
        "base_url": "https://higher.gs.com",
        "max_pages": 5,
    },
    "intuit": {
        "url": "https://jobs.intuit.com/search-jobs/software+engineer/United+States/27595/1/2/6252001/39x76/-98x5/50/2",
        "wait_selector": "#search-results-list li, .search-results-list a",
        "job_selector": "#search-results-list li, .search-results-list a[href*='/job/']",
        "extract": {
            "title": "h2, .job-title",
            "location": ".job-location, span.location",
            "url": "a",
            "posted_at": ".job-date-posted",
        },
        "base_url": "https://jobs.intuit.com",
        "max_pages": 5,
    },
    "cisco": {
        "url": "https://jobs.cisco.com/jobs/SearchJobs/software+engineer?listFilterMode=1&3_56_3=16478",
        "wait_selector": ".job-listing, .results-list tr, a[href*='/job/']",
        "job_selector": ".job-listing, a[href*='/job/']",
        "extract": {
            "title": "h2, .job-title, span",
            "location": ".job-location, .location",
            "url": "a",
        },
        "base_url": "https://jobs.cisco.com",
        "max_pages": 5,
    },
    "bloomberg": {
        "url": "https://careers.bloomberg.com/job/search?qe=software+engineer&lc=United+States",
        "wait_selector": ".job-results-card, a[href*='/job/detail/']",
        "job_selector": ".job-results-card, a[href*='/job/detail/']",
        "extract": {
            "title": "h3, .job-title",
            "location": ".job-location, span",
            "url": "a",
        },
        "base_url": "https://careers.bloomberg.com",
        "max_pages": 5,
    },
    "tesla": {
        "url": "https://www.tesla.com/careers/search/?query=software+engineer&country=US",
        "wait_selector": "[data-testid='job-card'], .job-card, a[href*='/careers/search/job/']",
        "job_selector": "[data-testid='job-card'], a[href*='/careers/search/job/']",
        "extract": {
            "title": "h3, .job-title, div:first-child",
            "location": ".job-location, span",
            "url": "a",
        },
        "base_url": "https://www.tesla.com",
        "max_pages": 5,
    },
    "paypal": {
        "url": "https://paypal.eightfold.ai/careers/search?query=software+engineer&location=United+States",
        "wait_selector": "[data-testid='job-card'], .position-card, a[href*='/careers/job/']",
        "job_selector": "[data-testid='job-card'], .position-card, a[href*='/careers/job/']",
        "extract": {
            "title": "h3, .position-title",
            "location": ".position-location, span",
            "url": "a",
        },
        "base_url": "https://paypal.eightfold.ai",
        "max_pages": 5,
    },
    "atlassian": {
        "url": "https://www.atlassian.com/company/careers/all-jobs?search=software+engineer&location=United+States",
        "wait_selector": "[data-testid='job-card'], .job-card, a[href*='/careers/']",
        "job_selector": "[data-testid='job-card'], a[href*='/careers/']",
        "extract": {
            "title": "h3, .job-title, div:first-child",
            "location": ".job-location, span",
            "url": "a",
        },
        "base_url": "https://www.atlassian.com",
        "max_pages": 5,
    },
}


def _extract_text(element, selector: str) -> str:
    if not selector:
        return element.inner_text().strip().split("\n")[0]
    target = element.query_selector(selector)
    if target:
        return target.inner_text().strip()
    return ""


def _extract_url(element, selector: str, base_url: str) -> str:
    if not selector:
        href = element.get_attribute("href")
    else:
        link = element.query_selector(selector)
        href = link.get_attribute("href") if link else None
    if not href:
        return ""
    if href.startswith("/"):
        return base_url + href
    return href


def _extract_posted_at(element, extract: dict) -> str | None:
    configured_selector = extract.get("posted_at")
    if configured_selector:
        posted_at = _extract_text(element, configured_selector)
        if posted_at:
            return posted_at

    for selector in _COMMON_POSTED_SELECTORS:
        posted_at = _extract_text(element, selector)
        if posted_at:
            return posted_at
    return None


def _extract_identity_hint(element) -> str:
    for attr in ("data-job-id", "data-id", "data-posting-id", "data-req-id", "id", "jsdata"):
        value = element.get_attribute(attr)
        if value:
            return value
    return ""


def _build_job_id(
    slug: str,
    url: str,
    title: str,
    location: str,
    posted_at: str | None,
    identity_hint: str = "",
) -> str:
    identity = url or identity_hint or " | ".join(
        part.strip() for part in (title, location, posted_at or "") if part and part.strip()
    )
    digest = hashlib.sha1(f"{slug}|{identity}".encode("utf-8")).hexdigest()[:16]
    return f"pw-{slug}-{digest}"


def _job_list_signature(page, job_selector: str, sample_size: int = 3) -> tuple[str, int, tuple[str, ...]]:
    elements = page.query_selector_all(job_selector)
    preview = []
    for element in elements[:sample_size]:
        text = " ".join(element.inner_text().split())
        if text:
            preview.append(text[:120])
    return page.url, len(elements), tuple(preview)


def _expand_results(page, job_selector: str, rounds: int = 3) -> None:
    for _ in range(rounds):
        before = _job_list_signature(page, job_selector)
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(1200)
        after = _job_list_signature(page, job_selector)
        if after == before:
            break


def _increment_page_url(current_url: str) -> str | None:
    parts = urlsplit(current_url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))

    for key in ("page", "pg", "pageNumber", "p"):
        value = query.get(key)
        if value and value.isdigit():
            query[key] = str(int(value) + 1)
            return urlunsplit(parts._replace(query=urlencode(query, doseq=True)))

    path_match = re.search(r"(/page/)(\d+)(/?$)", parts.path)
    if path_match:
        next_page = str(int(path_match.group(2)) + 1)
        next_path = f"{parts.path[:path_match.start(2)]}{next_page}{path_match.group(3)}"
        return urlunsplit(parts._replace(path=next_path))

    return None


def _try_click_pagination(page, selector: str, job_selector: str) -> bool:
    try:
        locator = page.locator(selector)
        count = min(locator.count(), 3)
    except Exception:
        return False

    for idx in range(count):
        try:
            candidate = locator.nth(idx)
            if not candidate.is_visible():
                continue
            if candidate.get_attribute("disabled") is not None:
                continue
            if (candidate.get_attribute("aria-disabled") or "").lower() == "true":
                continue

            before = _job_list_signature(page, job_selector)
            candidate.click(timeout=4000)
            try:
                page.wait_for_load_state("networkidle", timeout=8000)
            except Exception:
                page.wait_for_timeout(1200)

            after = _job_list_signature(page, job_selector)

            # Some career sites navigate into a role detail instead of
            # paginating — the job list vanishes and the URL changes. This
            # must be checked BEFORE the success check (a URL change alone
            # makes after != before) or the misclick counts as pagination
            # and silently truncates the company at page 1.
            if after[1] == 0 and after[0] != before[0]:
                try:
                    page.go_back(wait_until="networkidle", timeout=8000)
                except Exception:
                    pass
                continue

            if after != before:
                return True
        except Exception:
            continue
    return False


def _advance_page(page, config: dict, job_selector: str) -> bool:
    next_selector = config.get("next_selector")
    if next_selector and _try_click_pagination(page, next_selector, job_selector):
        return True

    for selector in _COMMON_PAGINATION_SELECTORS:
        if _try_click_pagination(page, selector, job_selector):
            return True

    next_url = _increment_page_url(page.url)
    if next_url and next_url != page.url:
        before = _job_list_signature(page, job_selector)
        try:
            page.goto(next_url, wait_until="networkidle", timeout=30000)
        except Exception:
            return False
        after = _job_list_signature(page, job_selector)
        return after != before

    return False


def fetch_playwright(company: dict) -> list[dict]:
    slug = company.get("slug", company.get("name", "").lower().replace(" ", "_"))
    config = COMPANY_CONFIGS.get(slug)
    if not config:
        raise ValueError(f"No Playwright config for '{slug}'. Available: {list(COMPANY_CONFIGS.keys())}")

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise RuntimeError("playwright not installed. Run: pip install playwright && playwright install chromium")

    company_name = company["name"]
    base_url = config.get("base_url", "")
    max_pages = config.get("max_pages", 5)
    extract = config["extract"]
    jobs = []
    seen_job_ids = set()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )

        page.goto(config["url"], wait_until="networkidle", timeout=30000)

        for page_num in range(max_pages):
            try:
                page.wait_for_selector(config["wait_selector"], timeout=10000)
            except Exception:
                break

            _expand_results(page, config["job_selector"])
            elements = page.query_selector_all(config["job_selector"])
            if not elements:
                break

            new_this_page = 0
            for el in elements:
                title = _extract_text(el, extract.get("title", ""))
                if not title:
                    continue
                location = _extract_text(el, extract.get("location", ""))
                url = _extract_url(el, extract.get("url", ""), base_url)
                posted_at = _extract_posted_at(el, extract)
                job_id = _build_job_id(
                    slug,
                    url,
                    title,
                    location,
                    posted_at,
                    identity_hint=_extract_identity_hint(el),
                )

                if job_id in seen_job_ids:
                    continue
                seen_job_ids.add(job_id)

                salary_text = _extract_text(el, extract.get("salary", "")) if "salary" in extract else ""
                salary = None
                if salary_text:
                    m = _SALARY_RE.search(salary_text)
                    if m:
                        salary = m.group(0).strip()

                jobs.append({
                    "job_id": job_id,
                    "company": company_name,
                    "title": title,
                    "location": location,
                    "url": url,
                    "posted_at": posted_at,
                    "salary": salary,
                })
                new_this_page += 1

            if new_this_page == 0:
                break

            if not _advance_page(page, config, config["job_selector"]):
                break

        browser.close()

    return jobs
