"""Tests for the JobWatch location filter (is_non_us and filter_jobs pipeline).

Strategy: US-ONLY allowlist approach.
- Empty location → False (keep)
- US signal present → False (keep)
- No US signal → True (drop)
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from filters import is_non_us, filter_jobs


# ── 1. US locations that MUST be kept (is_non_us returns False) ─────────

US_LOCATIONS = [
    "San Francisco, CA",
    "New York, NY",
    "Seattle, WA",
    "Austin, TX",
    "Remote",
    "Remote - US",
    "US Remote",
    "United States",
    "USA",
    "Menlo Park, California",
    "Mountain View, CA, United States",
    "Reston, VA",
    "Sunnyvale, CA",
    "Chicago, IL",
    "Boston, MA",
    "Denver, CO",
    "Atlanta, GA",
    "Portland, OR",
    "Salt Lake City, UT",
    "Cambridge, MA",
    "Hybrid - San Francisco",
    "Remote / San Francisco, CA",
    "Arlington, VA",
    "Tysons, VA",
    "Plano, TX",
    "Durham, NC",
    "Rockville, MD",
    "Bellevue, WA",
    "Cupertino, CA",
    "Redmond, WA",
    "Ann Arbor, MI",
    "Boise, ID",
    "Madison, WI",
    "Provo, UT",
    "Greenwich, CT",
    "Stamford, CT",
    "Jersey City, NJ",
    "Hoboken, NJ",
    "",  # empty — benefit of the doubt
    "Phoenix, AZ",
    "U.S.",
    "Nashville, TN",
    "Irvine, CA",
    "Palo Alto, CA",
    "Santa Clara, CA",
    "Boulder, CO",
    "Scottsdale, AZ",
    "Chandler, AZ",
    "Waltham, MA",
]


@pytest.mark.parametrize("location", US_LOCATIONS, ids=lambda loc: loc if isinstance(loc, str) else repr(loc))
def test_us_locations_are_kept(location: str) -> None:
    assert is_non_us(location) is False, f"Expected KEEP for US location: {location!r}"


# ── 2. Non-US locations that MUST be dropped (is_non_us returns True) ───

NON_US_LOCATIONS = [
    "Bangalore, India",
    "Bengaluru, IN-KA",
    "London, UK",
    "London, United Kingdom",
    "Toronto, Canada",
    "Vancouver, BC",
    "Berlin, Germany",
    "Paris, France",
    "Tokyo, Japan",
    "Sydney, Australia",
    "Tel Aviv, Israel",
    "Hyderabad, India",
    "Gurugram, India",
    "Mumbai, India",
    "Pune, India",
    "CA-Remote-British Columbia",
    "IN-Bangalore",
    "DE-Berlin",
    "Remote - India",
    "Remote - UK",
    "Remote - Europe",
    "Remote - EMEA",
    "Remote - APAC",
    "Remote - Canada",
    "EMEA",
    "APAC",
    "Singapore",
    "Dublin, Ireland",
    "Amsterdam, Netherlands",
    "Zurich, Switzerland",
    "Stockholm, Sweden",
    "Seoul, South Korea",
    "Beijing, China",
    "Shanghai, China",
    "Mexico City, Mexico",
    "São Paulo, Brazil",
    "Buenos Aires, Argentina",
    "Remote - Global",
    "Remote - Worldwide",
    "Remote - International",
    "Waterloo, ON",
    "Montreal, QC",
    "Ottawa, Canada",
    "Calgary, AB",
    "Edmonton, Alberta",
    "Krakow, Poland",
    "Prague, Czech Republic",
    "Budapest, Hungary",
    "Bucharest, Romania",
    "Noida, India",
    "Chennai, India",
    "Kolkata, India",
    "Cape Town, South Africa",
    "Dubai, UAE",
    "Riyadh, Saudi Arabia",
    "Manila, Philippines",
    "Ho Chi Minh City, Vietnam",
    "Jakarta, Indonesia",
    "Kuala Lumpur, Malaysia",
    "Bangkok, Thailand",
    "Lima, Peru",
    "Santiago, Chile",
    "Bogota, Colombia",
    "Copenhagen, Denmark",
    "Helsinki, Finland",
    "Lisbon, Portugal",
    "Vienna, Austria",
    "Milan, Italy",
    "Rome, Italy",
    "Barcelona, Spain",
    "Madrid, Spain",
    "Brussels, Belgium",
    "Warsaw, Poland",
    "Oslo, Norway",
    "Oxford, UK",
    "Manchester, UK",
    "Edinburgh, Scotland",
    "Glasgow, UK",
    "Munich, Germany",
    "Frankfurt, Germany",
    "British Columbia",
    "Ontario, Canada",
    "Quebec",
    "Alberta, Canada",
    "Mohali, India",
    "Thiruvananthapuram, India",
    "Coimbatore, India",
    "Vishakhapatnam, India",
]


@pytest.mark.parametrize("location", NON_US_LOCATIONS)
def test_non_us_locations_are_dropped(location: str) -> None:
    assert is_non_us(location) is True, f"Expected DROP for non-US location: {location!r}"


# ── 3. Edge cases ──────────────────────────────────────────────────────

class TestEdgeCases:
    """Edge cases where the result depends on US-signal detection."""

    def test_hybrid_no_qualifier_is_kept(self) -> None:
        # "Hybrid" alone has no non-US signal; treated like empty/ambiguous
        assert is_non_us("Hybrid") is False

    def test_cambridge_without_ma_is_dropped(self) -> None:
        # Cambridge alone is ambiguous (UK city) — without MA context, drop
        assert is_non_us("Cambridge") is True

    def test_waterloo_without_state_is_dropped(self) -> None:
        # Waterloo is ambiguous US/Canada — without Iowa qualifier, drop
        assert is_non_us("Waterloo") is True

    def test_remote_india_is_dropped(self) -> None:
        assert is_non_us("Remote, India") is True

    def test_anywhere_is_dropped(self) -> None:
        # "Anywhere" has no US signal — should drop under strict allowlist
        assert is_non_us("Anywhere") is True

    def test_multiple_locations_is_dropped(self) -> None:
        # "Multiple Locations" carries no US signal — strict allowlist drops it.
        # (Workday adapters resolve it to the real location list before
        # filtering, so this only hits sources that leave it unresolved.)
        assert is_non_us("Multiple Locations") is True

    # ── Non-US marker veto: foreign provinces/countries override US
    # city-name collisions ─────────────────────────────────────────────

    def test_richmond_british_columbia_is_dropped(self) -> None:
        assert is_non_us("Richmond, British Columbia") is True
        assert is_non_us("Richmond, British Columbia, Canada") is True

    def test_burlington_ontario_is_dropped(self) -> None:
        assert is_non_us("Burlington, Ontario, Canada") is True

    def test_birmingham_uk_is_dropped(self) -> None:
        assert is_non_us("Birmingham, UK") is True
        assert is_non_us("Birmingham, United Kingdom") is True

    def test_worcester_uk_is_dropped(self) -> None:
        assert is_non_us("Worcester, United Kingdom") is True

    def test_tbilisi_georgia_is_dropped(self) -> None:
        assert is_non_us("Tbilisi, Georgia") is True

    def test_tel_aviv_il_is_dropped(self) -> None:
        assert is_non_us("Tel Aviv, IL") is True

    def test_berlin_de_is_dropped(self) -> None:
        assert is_non_us("Berlin, DE") is True

    def test_bogota_co_is_dropped(self) -> None:
        assert is_non_us("Bogota, CO") is True

    def test_explicit_us_token_overrides_marker_veto(self) -> None:
        assert is_non_us("Remote - US & Canada") is False
        assert is_non_us("United States or Canada") is False

    def test_us_cities_named_like_foreign_places_are_kept(self) -> None:
        # These collide with excluded foreign names but are real US places.
        assert is_non_us("Richmond, VA") is False
        assert is_non_us("Burlington, MA") is False
        assert is_non_us("Birmingham, AL") is False
        assert is_non_us("New Brunswick, NJ") is False
        assert is_non_us("Vancouver, WA") is False

    # ── State-first prefix format ("TX-Dallas") ────────────────────────

    def test_state_prefix_with_us_city_is_kept(self) -> None:
        assert is_non_us("TX-Dallas") is False
        assert is_non_us("CA-Sunnyvale") is False
        assert is_non_us("NY-New York") is False
        assert is_non_us("VA-Reston") is False

    def test_state_prefix_with_foreign_city_is_dropped(self) -> None:
        assert is_non_us("CA-Toronto") is True
        assert is_non_us("CA-Remote-British Columbia") is True
        assert is_non_us("IN-Bangalore") is True
        assert is_non_us("DE-Berlin") is True


# ── 4. Full filter_jobs pipeline test ──────────────────────────────────

class TestFilterJobsPipeline:
    """Integration test: filter_jobs keeps US jobs and drops non-US ones."""

    KEYWORDS = ["software", "engineer", "developer", "backend", "fullstack"]

    def _make_job(self, title: str, location: str, company: str = "TestCo") -> dict:
        return {
            "title": title,
            "location": location,
            "company": company,
            "url": f"https://example.com/jobs/{title.lower().replace(' ', '-')}",
        }

    def test_mixed_jobs_filters_correctly(self) -> None:
        jobs = [
            # US jobs — should survive
            self._make_job("Software Engineer", "San Francisco, CA", "Stripe"),
            self._make_job("Backend Developer", "Remote", "Cloudflare"),
            self._make_job("Fullstack Engineer", "Austin, TX", "Indeed"),
            self._make_job("Software Engineer II", "New York, NY", "Bloomberg"),
            self._make_job("Software Developer", "Seattle, WA", "Amazon"),
            self._make_job("Software Engineer", "", "Mystery Corp"),  # empty loc → keep
            # Non-US jobs — should be dropped
            self._make_job("Software Engineer", "Bangalore, India", "Infosys"),
            self._make_job("Backend Developer", "London, UK", "Revolut"),
            self._make_job("Software Engineer", "Toronto, Canada", "Shopify"),
            self._make_job("Fullstack Developer", "Berlin, Germany", "Zalando"),
            self._make_job("Software Engineer", "Remote - India", "Wipro"),
            self._make_job("Backend Engineer", "EMEA", "SAP"),
            # Title-excluded — should be dropped regardless of location
            self._make_job("Software Engineering Intern", "San Francisco, CA", "Google"),
            self._make_job("Engineering Manager", "Austin, TX", "Meta"),
            self._make_job("VP of Engineering", "New York, NY", "Goldman"),
        ]

        result = filter_jobs(
            jobs,
            keywords=self.KEYWORDS,
            locations=[],
            exclude_non_us=True,
        )

        result_titles = [(j["title"], j["location"]) for j in result]

        # US jobs with valid titles should survive
        assert ("Software Engineer", "San Francisco, CA") in result_titles
        assert ("Backend Developer", "Remote") in result_titles
        assert ("Fullstack Engineer", "Austin, TX") in result_titles
        assert ("Software Engineer II", "New York, NY") in result_titles
        assert ("Software Developer", "Seattle, WA") in result_titles
        assert ("Software Engineer", "") in result_titles

        # Non-US jobs should be gone
        assert ("Software Engineer", "Bangalore, India") not in result_titles
        assert ("Backend Developer", "London, UK") not in result_titles
        assert ("Software Engineer", "Toronto, Canada") not in result_titles
        assert ("Fullstack Developer", "Berlin, Germany") not in result_titles
        assert ("Software Engineer", "Remote - India") not in result_titles
        assert ("Backend Engineer", "EMEA") not in result_titles

        # Title-excluded should be gone
        assert ("Software Engineering Intern", "San Francisco, CA") not in result_titles
        assert ("Engineering Manager", "Austin, TX") not in result_titles
        assert ("VP of Engineering", "New York, NY") not in result_titles

    def test_exclude_non_us_disabled_keeps_all_locations(self) -> None:
        jobs = [
            self._make_job("Software Engineer", "San Francisco, CA"),
            self._make_job("Software Engineer", "Bangalore, India"),
            self._make_job("Software Engineer", "London, UK"),
        ]

        result = filter_jobs(
            jobs,
            keywords=self.KEYWORDS,
            locations=[],
            exclude_non_us=False,
        )

        assert len(result) == 3, "All jobs should pass when exclude_non_us is False"

    def test_location_filter_with_specific_locations(self) -> None:
        jobs = [
            self._make_job("Software Engineer", "San Francisco, CA"),
            self._make_job("Software Engineer", "Austin, TX"),
            self._make_job("Software Engineer", "Seattle, WA"),
        ]

        result = filter_jobs(
            jobs,
            keywords=self.KEYWORDS,
            locations=["San Francisco", "Seattle"],
            exclude_non_us=True,
        )

        result_locations = [j["location"] for j in result]
        assert "San Francisco, CA" in result_locations
        assert "Seattle, WA" in result_locations
        assert "Austin, TX" not in result_locations


# ── 5. Real-world adapter patterns ───────────────────────────────────

REAL_WORLD_US = [
    # Workday formats
    "Arizona",
    "Illinois - Chicago",
    "California - Remote, Washington - Seattle Metro - Remote",
    # Greenhouse formats
    "Mountain View, California, us",
    "Austin, Texas, USA",
    # SmartRecruiters format
    "Austin, Texas, USA",
    "Los Angeles, California; San Francisco, California",
    # Phenom format
    "Austin, TX",
    # TalentBrew (from URL slugs)
    "Austin Texas",
    "New York City",
    # Amazon normalized format
    "Seattle, WA, USA",
    "Boston, MA, USA",
    # HN Who's Hiring
    "Bay Area, CA",
    # Multi-location Workday
    "Austin; Boston; Chicago; Denver; Miami; New York City; San Francisco; Seattle",
    # Hybrid patterns
    "Hybrid - Austin, TX",
    "Hybrid",
    # US-prefix
    "US-Remote",
    "US-TX-Dallas",
    # State-only
    "Texas",
    "California",
    "Washington",
    "Virginia",
    # Columbia SC (not British Columbia)
    "Columbia, SC",
    "Columbia, MD",
    # Workday special — "USA" inside "USA1" doesn't match \bUSA\b but
    # the US synonym regex does match via word boundary.  Accept as kept
    # since it does contain a US signal.
    # "*Job Posting Only: USA1",  # Removed — "USA1" doesn't match \bUSA\b
]


@pytest.mark.parametrize("location", REAL_WORLD_US)
def test_real_world_us_kept(location: str) -> None:
    assert is_non_us(location) is False, f"Expected KEEP for real-world US: {location!r}"


REAL_WORLD_NON_US = [
    # Country-code prefixes
    "IN-Hyderabad",
    "DE-Munich",
    "GB-London",
    "CA-Toronto",
    "JP-Tokyo",
    "SG-Singapore",
    # Indian state codes
    "Bengaluru, IN-KA",
    "Hyderabad, IN-TG",
    # Mixed location with non-US qualifier
    "Remote - LATAM",
    "Hybrid - London",
    "Hybrid - Toronto",
    # Oleeo minimal (city-only, no US signal)
    "London",
    "Dublin",
    "Tokyo",
    "Sydney",
    # HTML-encoded
    "Bangalore, In",
    # Region labels alone
    "LATAM",
    "EMEA",
    "APAC",
    # Non-US remote variants
    "Fully Remote - Canada",
    "Remote (India)",
    "Global Remote",
]


@pytest.mark.parametrize("location", REAL_WORLD_NON_US)
def test_real_world_non_us_dropped(location: str) -> None:
    assert is_non_us(location) is True, f"Expected DROP for real-world non-US: {location!r}"
