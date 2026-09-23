from html import unescape
import re

import requests

ALGOLIA_SEARCH_URL = "https://hn.algolia.com/api/v1/search"
ALGOLIA_SEARCH_BY_DATE_URL = "https://hn.algolia.com/api/v1/search_by_date"


def _find_latest_hiring_thread() -> int | None:
    resp = requests.get(
        ALGOLIA_SEARCH_BY_DATE_URL,
        params={
            "query": "Ask HN: Who is hiring?",
            "tags": "story,author_whoishiring",
            "hitsPerPage": 1,
        },
        timeout=15,
    )
    resp.raise_for_status()
    hits = resp.json().get("hits", [])
    if hits:
        return int(hits[0]["objectID"])
    return None


def _fetch_comments(story_id: int) -> list[dict]:
    comments = []
    page = 0
    while True:
        resp = requests.get(
            ALGOLIA_SEARCH_BY_DATE_URL,
            params={
                "tags": f"comment,story_{story_id}",
                "hitsPerPage": 100,
                "page": page,
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        hits = data.get("hits", [])
        if not hits:
            break
        comments.extend(hits)
        if page >= data.get("nbPages", 0) - 1:
            break
        page += 1
    return comments


_ROLE_SIGNALS = re.compile(
    r"engineer|developer|sre|devops|full.?stack|backend|frontend|platform|cloud|data|ai |ml |machine learning|fde",
    re.IGNORECASE,
)
_NOISE = re.compile(r"^(full.?time|part.?time|remote|onsite|hybrid|contract|https?://)$", re.IGNORECASE)


def _parse_comment(comment: dict) -> dict | None:
    text = comment.get("comment_text", "")
    if not text:
        return None

    clean = re.sub(r"<[^>]+>", "\n", unescape(text))
    lines = [l.strip() for l in clean.split("\n") if l.strip()]
    if not lines:
        return None

    header = lines[0]
    parts = [p.strip() for p in header.split("|")]
    if len(parts) < 2:
        return None

    company = parts[0]
    title = ""
    location = ""

    for part in parts[1:]:
        if _NOISE.match(part):
            continue
        if not title and _ROLE_SIGNALS.search(part):
            title = part
        elif not location and not _NOISE.match(part):
            location = part

    if not title:
        body = " ".join(lines[1:5])
        m = _ROLE_SIGNALS.search(body)
        if m:
            sent_start = max(0, m.start() - 30)
            sent_end = min(len(body), m.end() + 50)
            title = body[sent_start:sent_end].strip()

    if not title:
        return None

    hn_url = f"https://news.ycombinator.com/item?id={comment['objectID']}"
    url_match = re.search(r'href="(https?://[^"]+)"', text)
    apply_url = url_match.group(1) if url_match else hn_url

    return {
        "job_id": f"hn-{comment['objectID']}",
        "company": company,
        "title": title,
        "location": location,
        "url": apply_url,
        "posted_at": comment.get("created_at"),
        "source": "hn_hiring",
    }


def fetch_hn_hiring(company: dict = None) -> list[dict]:
    from sponsors import is_h1b_sponsor

    story_id = _find_latest_hiring_thread()
    if not story_id:
        return []

    comments = _fetch_comments(story_id)
    jobs = []
    for c in comments:
        if c.get("parent_id") != story_id:
            continue
        parsed = _parse_comment(c)
        if parsed and is_h1b_sponsor(parsed["company"]):
            jobs.append(parsed)

    return jobs
