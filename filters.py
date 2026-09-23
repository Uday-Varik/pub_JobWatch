import re
from datetime import datetime, timezone, timedelta

# Always excluded — genuinely non-IC or unwanted roles.
_HARD_NEGATIVE_PATTERNS = [
    r"\bintern\b",
    r"\binternship\b",
    r"\bco-op\b",
    r"\bmanager\b",
    r"\bmanagement\b",
    r"\bchief\b",
    r"\bcto\b",
    r"\bcio\b",
    r"\bceo\b",
    r"\bhead of\b",
    r"\b(?:technical |tech )?lead\b(?!\s+(?:software|engineer|developer))",
    r"\bclearance required\b",
    r"\bts/sci\b",
    r"\bsecurity clearance\b",
    r"\bpolygraph\b",
    # Customer-facing / pre-sales / support titles that share engineering
    # keywords ("AI Infrastructure", "Machine Learning") but are not SWE roles.
    r"\bsolutions? architect\b",
    r"\bsolutions? consultant\b",
    r"\bconsultant\b",
    r"\bsales engineer\b",
    r"\bcustomer engineer\b",
    r"\bsupport engineer\b",
    r"\bproduct support\b",
    r"\btechnical account\b",
    r"\bprofessional services\b",
]

# Seniority ranks that mean "management" at most companies but are mid-level
# INDIVIDUAL-CONTRIBUTOR titles at banks (Morgan Stanley, Goldman, JPMorgan
# use "Vice President" / "Executive Director" as IC levels). Companies with
# allow_seniority_titles: true skip these so their IC engineering roles show.
_SENIORITY_NEGATIVE_PATTERNS = [
    r"\bdirector\b",
    r"\bvice president\b",
    r"\b(?:sr\.?\s*)?vp\b",
    r"\bprincipal\b",
]

# Kept for backward compatibility / external references.
NEGATIVE_TITLE_PATTERNS = _HARD_NEGATIVE_PATTERNS + _SENIORITY_NEGATIVE_PATTERNS

_hard_neg_re = re.compile("|".join(_HARD_NEGATIVE_PATTERNS), re.IGNORECASE)
_seniority_neg_re = re.compile("|".join(_SENIORITY_NEGATIVE_PATTERNS), re.IGNORECASE)
_neg_re = re.compile("|".join(NEGATIVE_TITLE_PATTERNS), re.IGNORECASE)

# ── STRICT US-ONLY ALLOWLIST ─────────────────────────────────────────
# If a location has no clear US signal, it is dropped. No non-US blocklist.

# Two-letter US state abbreviations (all 50 + DC + territories).
# Note: "IN" (Indiana) excluded — too ambiguous with India/IN-KA codes.
# Indiana is caught by full state name "Indiana" and cities like Indianapolis.
_US_STATES = (
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA",
    "HI", "ID", "IL", "IA", "KS", "KY", "LA", "ME", "MD",
    "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ",
    "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC",
    "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY",
    "DC", "PR", "GU", "VI",
)

# Full state names.
_US_STATE_NAMES = (
    "Alabama", "Alaska", "Arizona", "Arkansas", "California", "Colorado",
    "Connecticut", "Delaware", "Florida", "Georgia", "Hawaii", "Idaho",
    "Illinois", "Indiana", "Iowa", "Kansas", "Kentucky", "Louisiana",
    "Maine", "Maryland", "Massachusetts", "Michigan", "Minnesota",
    "Mississippi", "Missouri", "Montana", "Nebraska", "Nevada",
    "New Hampshire", "New Jersey", "New Mexico", "New York",
    "North Carolina", "North Dakota", "Ohio", "Oklahoma", "Oregon",
    "Pennsylvania", "Rhode Island", "South Carolina", "South Dakota",
    "Tennessee", "Texas", "Utah", "Vermont", "Virginia", "Washington",
    "West Virginia", "Wisconsin", "Wyoming", "District of Columbia",
)

# Comprehensive US cities list (150+).
# Top 100 by population + major tech hubs + financial centers.
_US_CITIES = (
    # Top US cities by population
    "New York", "Los Angeles", "Chicago", "Houston", "Phoenix", "Philadelphia",
    "San Antonio", "San Diego", "Dallas", "San Jose", "Austin", "Jacksonville",
    "Fort Worth", "Columbus", "Indianapolis", "Charlotte", "San Francisco",
    "Seattle", "Denver", "Nashville", "Oklahoma City", "El Paso", "Memphis",
    "Louisville", "Milwaukee", "Tucson", "Albuquerque", "Fresno", "Sacramento",
    "Mesa", "Kansas City", "Atlanta", "Omaha", "Raleigh", "Minneapolis",
    "Cleveland", "Tampa", "Aurora", "New Orleans", "Pittsburgh", "St. Louis",
    "Cincinnati", "Orlando", "Irvine", "Baltimore", "Bakersfield",
    "Riverside", "Stockton", "Corpus Christi", "Lexington", "Henderson",
    "St. Paul", "Anchorage", "Newark", "Buffalo", "Honolulu",
    "Greensboro", "Lincoln", "Norfolk", "Chesapeake", "Chula Vista",
    "Chandler", "Scottsdale", "Laredo", "Lubbock", "Gilbert",
    "Winston-Salem", "Glendale", "Hialeah", "Garland", "Irving",
    "North Las Vegas", "Fremont", "Richmond", "Baton Rouge",
    "Des Moines", "Spokane", "Birmingham", "Rochester", "Modesto",
    "Oxnard", "Tacoma", "Fontana", "Moreno Valley", "Fayetteville",
    "Huntsville", "Yonkers", "Knoxville", "Worcester",
    "Providence", "Brownsville", "Little Rock", "Dayton", "Akron",
    "Tallahassee", "Mobile", "Grand Rapids", "Overland Park",
    "Sioux Falls", "Chattanooga", "Fort Lauderdale",
    "Springfield", "Savannah", "Clarksville",
    "Las Vegas", "Virginia Beach", "Long Beach", "Oakland",
    "Miami", "Tulsa", "Colorado Springs", "Wichita",
    "St. Petersburg",
    # Major tech hubs
    "Sunnyvale", "Santa Clara", "Mountain View", "Palo Alto", "Menlo Park",
    "Cupertino", "Redmond", "Bellevue", "Kirkland", "Boulder",
    "Reston", "Herndon", "McLean", "Tysons", "Tysons Corner",
    "Cary", "Durham", "Research Triangle", "Tempe",
    "Burlington", "Waltham", "Cambridge, MA",
    "Ann Arbor", "Detroit", "Portland", "Boise", "Madison",
    "Plano", "Provo", "Salt Lake City",
    "Redwood City", "San Mateo", "Foster City", "Milpitas",
    "Santa Monica", "Venice Beach", "Pasadena", "Burbank",
    "Thousand Oaks", "Calabasas", "Culver City",
    "Schaumburg", "Naperville", "Evanston",
    "Alpharetta", "Sandy Springs", "Roswell",
    "Frisco", "Richardson", "Allen", "McKinney",
    "Round Rock", "Cedar Park", "Georgetown",
    "Bothell", "Issaquah",
    "Lehi", "Draper", "Orem",
    "Raleigh-Durham", "Chapel Hill", "Wake Forest",
    "Ashburn", "Sterling", "Chantilly", "Fairfax", "Manassas",
    "Ellicott City",
    "Troy", "Dearborn", "Southfield",
    "Bloomington", "Eden Prairie", "Minnetonka",
    "King of Prussia", "Conshohocken", "Blue Bell",
    # Financial centers
    "Greenwich", "Stamford", "Hoboken", "Jersey City", "White Plains",
    "Wilmington",
    # Additional notable cities
    "Arlington", "Boston", "San Bernardino",
    "Carlsbad", "Oceanside", "Escondido",
    "Peoria", "Surprise", "Avondale",
    "Reno", "Sparks",
    "Charleston", "Greenville",
    "Pensacola", "Gainesville", "Lakeland",
    "Fort Collins", "Arvada", "Westminster",
)

# Unambiguous non-US markers (countries, regions, Canadian provinces, and a
# few foreign cities that co-occur with allowlisted US names).  Used as a
# veto BEFORE the US-positive check: "Richmond, British Columbia" and
# "Birmingham, UK" must not be kept just because "Richmond"/"Birmingham"
# are on the US city list.  An explicit US country token ("US", "USA",
# "United States") overrides the veto so "Remote - US & Canada" stays.
# Deliberately excludes names that are also notable US places
# ("New Brunswick" NJ, "Vancouver" WA, "Manchester" NH, "Paris" TX).
_NON_US_MARKERS = (
    # Countries / regions
    "india", "uk", "united kingdom", "england", "scotland", "wales",
    "ireland", "canada", "mexico", "brazil", "argentina", "colombia",
    "germany", "france", "spain", "italy", "netherlands", "belgium",
    "poland", "romania", "portugal", "switzerland", "austria", "sweden",
    "norway", "denmark", "finland", "israel", "turkey", "egypt",
    "nigeria", "south africa", "japan", "china", "taiwan", "korea",
    "singapore", "malaysia", "indonesia", "philippines", "vietnam",
    "thailand", "australia", "new zealand", "europe", "apac", "emea",
    "latam",
    # Canadian provinces
    "british columbia", "ontario", "quebec", "alberta", "manitoba",
    "saskatchewan", "nova scotia",
    # Foreign cities that defeat US-name collisions
    "toronto", "mississauga", "bengaluru", "bangalore", "hyderabad",
    "mumbai", "delhi", "pune", "chennai", "gurgaon", "noida",
    "tel aviv", "tbilisi", "berlin", "bogota", "tokyo", "sydney",
)
_NON_US_MARKER_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(q) for q in _NON_US_MARKERS) + r")\b",
    re.IGNORECASE,
)

# Explicit US country tokens (subset of _us_re) — used to override the
# non-US marker veto for multi-country listings like "Remote - US & Canada".
_US_COUNTRY_RE = re.compile(
    r"\b(?:usa|united\s+states)\b"
    r"|\bu\.s\.a\.(?:\b|$|(?=[\s,;)/-]))"
    r"|\bu\.s\.(?:\b|$|(?=[\s,;)/-]))"
    r"|(?:^|(?<=[\s,(]))US(?=$|[\s,)/-])",
    re.IGNORECASE,
)

# Country-code prefix pattern: two uppercase letters followed by a dash
# at the start of the location string (e.g. "CA-Remote-British Columbia",
# "IN-Bangalore", "DE-Berlin").  US- prefix is excluded.
_COUNTRY_CODE_PREFIX_RE = re.compile(r"^(?!US-)[A-Z]{2}-")

# Build the main US-positive regex.
_us_re = re.compile(
    # US synonyms (word-boundary protected)
    # "U.S." and "U.S.A." need special handling: \b doesn't work after "."
    r"\b(?:usa|united\s+states)\b"
    r"|\bu\.s\.a\.(?:\b|$|(?=[\s,;)/-]))"
    r"|\bu\.s\.(?:\b|$|(?=[\s,;)/-]))"
    # "US" with strict word boundaries — must not be inside words like
    # "campus", "focus", "status", "versus", "us" (pronoun at sentence end).
    # Require US to be preceded by start-of-string, space, comma, or paren
    # and followed by end-of-string, space, comma, paren, or dash.
    r"|(?:^|(?<=[\s,(]))US(?=$|[\s,)/-])"
    # "America" (but not "Latin America", "South America", "Central America")
    # Variable-width lookbehinds not supported, so use three fixed-width ones.
    r"|(?<!latin )(?<!south )(?<!central )\bamerica\b"
    # State abbreviations after ", " (e.g. ", CA", ", NY")
    # Must NOT be followed by "-" to avoid matching ", IN-KA" as Indiana.
    r"|,\s*(?:" + "|".join(re.escape(s) for s in _US_STATES) + r")(?=[,\s);/]|$)"
    # State abbreviations after whitespace at end of string
    # (e.g. "Dallas TX").  Also must not be followed by "-".
    r"|\s(?:" + "|".join(re.escape(s) for s in _US_STATES) + r")$"
    # Full state names
    r"|\b(?:" + "|".join(re.escape(n) for n in _US_STATE_NAMES) + r")\b"
    # US cities
    r"|\b(?:" + "|".join(re.escape(c) for c in _US_CITIES) + r")\b"
    # "Columbia" is ambiguous (Columbia SC/MD vs British Columbia).
    # Only match when NOT preceded by "British " (case-insensitive via
    # two fixed-width lookbehinds).
    r"|(?<!british )(?<!British )\bcolumbia\b",
    re.IGNORECASE,
)

# Separate regex for "Remote" / "Hybrid" / "Anywhere" — these are US-positive
# ONLY if they don't have non-US qualifiers nearby and don't have a
# country-code prefix.
_REMOTE_RE = re.compile(
    r"\b(?:remote|hybrid)\b",
    re.IGNORECASE,
)


def matches_keywords(title: str, keywords: list[str]) -> bool:
    title_lower = title.lower()
    for kw in keywords:
        if kw.lower() in title_lower:
            return True
    return False


def is_excluded(title: str, allow_seniority_titles: bool = False) -> bool:
    if _hard_neg_re.search(title):
        return True
    if not allow_seniority_titles and _seniority_neg_re.search(title):
        return True
    return False


# New-grad / entry-level full-time roles (NOT internships — those are excluded
# upstream by the hard filter). Used to route these into a dedicated stream
# during new-grad hiring season.
_NEW_GRAD_PATTERNS = [
    r"\bnew[\s-]?grad(?:uate)?s?\b",
    r"\brecent[\s-]?grad(?:uate)?s?\b",
    r"\bnew college grad(?:uate)?s?\b",
    r"\b(?:university|college)[\s-]?grad(?:uate)?s?\b",
    r"\bgraduate (?:(?:software|cloud|data|platform|backend|frontend|research)\s+)?engineer\b",
    r"\bearly[\s-]?career\b",
    r"\bearly talent\b",
    r"\bentry[\s-]?level\b",
    r"\bassociate software engineer\b",
    r"\bclass of 20\d{2}\b",
    r"\b20\d{2}\s+(?:new\s+)?grad(?:uate)?s?\b",
    r"\b(?:rotational|graduate) (?:engineering |software |technology )?program\b",
    r"\bncg\b",
]
_new_grad_re = re.compile("|".join(_NEW_GRAD_PATTERNS), re.IGNORECASE)


def is_new_grad(title: str) -> bool:
    """True if the title looks like a new-grad / entry-level full-time role."""
    return bool(_new_grad_re.search(title or ""))


def is_non_us(location: str) -> bool:
    """Strict US-only allowlist.

    Returns True (= drop the job) unless there is a clear US signal.
    - Empty/missing location → KEEP (benefit of the doubt).
    - Explicit non-US marker (country/province/foreign city) without an
      explicit US country token → DROP, even if a US city name collides.
    - Explicit US signal (state, city, country name) → KEEP.
    - Standalone "Remote"/"Hybrid" → KEEP.
    - Everything else → DROP.
    """
    if not location or not location.strip():
        return False

    loc = location.strip()

    # 0. Country-code prefix (e.g. "CA-Remote", "IN-Bangalore", "DE-Berlin").
    #    If the location starts with a 2-letter non-US code + dash, it is
    #    non-US even if the code happens to match a US state abbreviation
    #    (e.g. "CA" = Canada here, not California) — UNLESS the prefix is a
    #    US state abbreviation AND the remainder carries its own US signal
    #    ("TX-Dallas", "CA-Sunnyvale" are state-first US formats).
    if _COUNTRY_CODE_PREFIX_RE.search(loc):
        prefix = loc[:2].upper()
        rest = loc[3:]
        if (
            prefix in _US_STATES
            and _us_re.search(rest)
            and not _NON_US_MARKER_RE.search(rest)
        ):
            return False
        return True

    # 1. Non-US marker veto: a foreign country/province/city in the string
    #    overrides US city-name collisions ("Richmond, British Columbia",
    #    "Tbilisi, Georgia", "Tel Aviv, IL"), unless an explicit US country
    #    token is also present ("Remote - US & Canada").
    if _NON_US_MARKER_RE.search(loc) and not _US_COUNTRY_RE.search(loc):
        return True

    # 2. Check for explicit US signals (cities, states, country synonyms).
    if _us_re.search(loc):
        return False

    # 3. Check for "Remote" / "Hybrid".
    #    US-positive ONLY if:
    #    a) standalone ("Remote", "Hybrid") — no other meaningful content, OR
    #    b) paired with US signal that step 1 already found (which returned False).
    #    If Remote/Hybrid appears alongside non-US qualifiers or unrecognized
    #    content (e.g. "Hybrid - London"), it is NOT US-positive.
    if _REMOTE_RE.search(loc):
        # Strip out the remote/hybrid word and separators; if nothing
        # meaningful remains, it's standalone → US-positive.
        stripped = _REMOTE_RE.sub("", loc).strip(" -–—/|,;()")
        if not stripped:
            return False

    # 4. No US signal found → treat as non-US.
    return True


def matches_location(location: str, locations: list[str]) -> bool:
    if not locations:
        return True
    loc_lower = location.lower()
    for loc in locations:
        if loc.lower() in loc_lower:
            return True
    return False


MAX_AGE_HOURS = 24

_RELATIVE_RE = re.compile(r"Posted\s+(\d+)(?:\+)?\s+(day|hour|minute)s?\s+ago", re.IGNORECASE)


def _parse_timestamp(raw) -> datetime | None:
    if not raw:
        return None
    if isinstance(raw, datetime):
        if raw.tzinfo is None:
            raw = raw.replace(tzinfo=timezone.utc)
        return raw
    s = str(raw).strip()

    if s.lower() in ("posted today", "today"):
        return datetime.now(timezone.utc)
    if s.lower() in ("posted yesterday", "yesterday"):
        # Use the latest possible moment of "yesterday" (end of the previous
        # UTC day).  Parsing it as exactly now-24h made every such job fail
        # the 24h staleness cutoff by microseconds, silently dropping jobs
        # that could be only hours old (posted 11pm, scraped 1am).
        start_of_today = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        return start_of_today - timedelta(seconds=1)
    m = _RELATIVE_RE.match(s)
    if m:
        n, unit = int(m.group(1)), m.group(2).lower()
        delta = timedelta(**{f"{unit}s": n})
        return datetime.now(timezone.utc) - delta

    try:
        from datetime import datetime as dt_cls
        parsed = dt_cls.fromisoformat(s.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        # Date-only strings ("2026-06-09") parse to midnight UTC; treat them
        # as end-of-day so a date-resolution posting isn't declared stale up
        # to 24h early.
        if len(s) == 10:
            parsed += timedelta(hours=23, minutes=59, seconds=59)
        return parsed
    except (ValueError, TypeError):
        pass

    for fmt in (
        "%B %d, %Y",
        "%b %d, %Y",
        "%B %d %Y",
        "%b %d %Y",
        "%m/%d/%Y",
    ):
        try:
            # Date-only formats: anchor to end-of-day (see ISO branch above).
            return datetime.strptime(s, fmt).replace(
                tzinfo=timezone.utc, hour=23, minute=59, second=59
            )
        except ValueError:
            continue
    return None


def is_stale(job: dict, max_age_hours: int = MAX_AGE_HOURS) -> bool:
    posted = _parse_timestamp(job.get("posted_at"))
    if not posted:
        return False
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
    return posted < cutoff


def filter_jobs(
    jobs: list[dict],
    keywords: list[str],
    locations: list[str],
    exclude_non_us: bool = True,
    allow_seniority_titles: bool = False,
    exclude_new_grad: bool = False,
    max_age_hours: int = MAX_AGE_HOURS,
) -> list[dict]:
    matched = []
    for job in jobs:
        title = job.get("title", "")
        location = job.get("location", "")

        if not matches_keywords(title, keywords):
            continue
        if is_excluded(title, allow_seniority_titles=allow_seniority_titles):
            continue
        if exclude_new_grad and is_new_grad(title):
            continue
        if exclude_non_us and is_non_us(location):
            continue
        if not matches_location(location, locations):
            continue
        if is_stale(job, max_age_hours=max_age_hours):
            continue

        matched.append(job)
    return matched
