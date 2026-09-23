"""Eightfold careers scraper (Playwright).

Eightfold's public job API is gated behind a PCSX authorization check that
rejects datacenter requests ("Not authorized for PCSX"), so a plain HTTP
adapter can't read it. The careers SPA, however, renders fine in headless
Chromium. This adapter drives that page, filtered to the US and sorted newest
-first, and reads job cards straight from the DOM.

Config (config.yaml):
    - name: Morgan Stanley
      ats: eightfold_pw
      tenant: morganstanley        # {tenant}.eightfold.ai
      query: software              # optional, defaults to "software engineer"
      location: United States      # optional, defaults to "United States"
      base_url: https://careers.example.com   # optional: custom-domain
                                   # Eightfold sites (e.g. Applied Materials)
"""

import re
import time

DEFAULT_QUERY = "software engineer"
DEFAULT_LOCATION = "United States"
DEFAULT_SCROLL_ROUNDS = 8
DEFAULT_TIME_BUDGET_SECONDS = 120

_JOB_ID_RE = re.compile(r"/careers/job/(\d+)")

# One JS pass reads every rendered job card. Eightfold hashes its class names
# (card-F1ebU) but keeps the /careers/job/<id> anchor stable; the anchor's text
# is "Title\nLocation" and the surrounding card carries a "Posted … ago" line.
_EXTRACT_JS = r"""
() => {
  const out = [];
  const seen = new Set();
  const anchors = document.querySelectorAll('a[href*="/careers/job/"]');
  for (const a of anchors) {
    const href = a.getAttribute('href') || '';
    const m = href.match(/\/careers\/job\/(\d+)/);
    if (!m) continue;
    const id = m[1];
    if (seen.has(id)) continue;
    seen.add(id);
    const lines = (a.innerText || '').split('\n').map(s => s.trim()).filter(Boolean);
    const title = lines[0] || '';
    const location = lines[1] || '';
    // Walk up a few levels to find the card and its "Posted ... ago" text.
    let posted = '';
    let node = a;
    for (let i = 0; i < 6 && node; i++) {
      const txt = node.innerText || '';
      const pm = txt.match(/Posted\s+[^\n]+ago/i);
      if (pm) { posted = pm[0].trim(); break; }
      node = node.parentElement;
    }
    if (title) out.push({ id, href, title, location, posted });
  }
  return out;
}
"""


def _base_url(company: dict) -> str:
    custom = str(company.get("base_url", "") or "").strip().rstrip("/")
    return custom or f"https://{company['tenant']}.eightfold.ai"


def _careers_url(company: dict) -> str:
    query = company.get("query", DEFAULT_QUERY)
    location = company.get("location", DEFAULT_LOCATION)
    from urllib.parse import quote
    return (
        f"{_base_url(company)}/careers"
        f"?query={quote(query)}&location={quote(location)}&sort_by=timestamp"
    )


def fetch_eightfold_pw(company: dict) -> list[dict]:
    tenant = company["tenant"]
    company_name = company["name"]
    base = _base_url(company)
    url = _careers_url(company)

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise RuntimeError("playwright not installed. Run: pip install playwright && playwright install chromium")

    scroll_rounds = int(company.get("scroll_rounds", DEFAULT_SCROLL_ROUNDS))
    time_budget = float(company.get("time_budget_seconds", DEFAULT_TIME_BUDGET_SECONDS))
    start = time.monotonic()

    jobs: list[dict] = []
    seen_ids: set[str] = set()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
            )
        )
        try:
            page.goto(url, wait_until="networkidle", timeout=45000)
            try:
                page.wait_for_selector('a[href*="/careers/job/"]', timeout=15000)
            except Exception:
                return jobs  # no results rendered (empty search or blocked)

            # Eightfold lazy-loads on scroll. Scroll until the job count stops
            # growing, we hit the round cap, or we run out of time budget.
            last_count = 0
            for _ in range(scroll_rounds):
                if time.monotonic() - start > time_budget:
                    break
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                page.wait_for_timeout(1500)
                count = len(page.query_selector_all('a[href*="/careers/job/"]'))
                if count <= last_count:
                    break
                last_count = count

            for rec in page.evaluate(_EXTRACT_JS):
                job_id = rec["id"]
                if job_id in seen_ids:
                    continue
                seen_ids.add(job_id)
                jobs.append({
                    "job_id": f"ef-{tenant}-{job_id}",
                    "company": company_name,
                    "title": rec["title"],
                    "location": rec["location"],
                    "url": f"{base}{rec['href']}",
                    "posted_at": rec.get("posted") or None,
                    "source": "eightfold_pw",
                })
        finally:
            browser.close()

    return jobs
