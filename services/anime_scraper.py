# ============================================================
# anime_scraper.py
# Anime Hindi Info Bot
# ============================================================

from __future__ import annotations

import re
import json
import time
import html
import unicodedata
from dataclasses import dataclass, field, asdict
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup


# ============================================================
# CONFIG
# ============================================================

BASE_URL = "https://www.rareanimes.mov"

# Only metadata pages are used.
# We intentionally do not expose/download episode links.
SEARCH_URL = BASE_URL + "/?s={query}"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0 Safari/537.36"
)

REQUEST_TIMEOUT = 12
SEARCH_TIMEOUT = 10
MAX_SEARCH_RESULTS = 12
MAX_SEASON_PAGES = 30

# Languages that the bot is allowed to display.
DISPLAY_LANGUAGES = [
    "Hindi",
    "English",
    "Tamil",
    "Telugu",
    "Japanese",
]

# Common aliases / short names.
ALIASES = {
    "naruto": [
        "naruto",
        "naruto 2002",
        "naruto classic",
        "naruto season 1",
    ],
    "naruto shippuden": [
        "naruto shippuden",
        "naruto shippuden anime",
        "naruto shippuden series",
    ],
    "re zero": [
        "re zero",
        "rezero",
        "re:zero",
        "re zero starting life in another world",
        "re:zero starting life in another world",
    ],
    "solo leveling": [
        "solo leveling",
        "solo-leveling",
    ],
    "mushoku tensei": [
        "mushoku tensei",
        "jobless reincarnation",
        "mushoku tensei jobless reincarnation",
    ],
    "one piece": [
        "one piece",
        "onepiece",
    ],
    "black torch": [
        "black torch",
        "blacktorch",
    ],
}


# ============================================================
# DATA CLASS
# ============================================================

@dataclass
class AnimeInfo:
    name: str = ""
    original_name: str = ""

    poster: Optional[str] = None

    hindi_dub: bool = False
    hindi_status_known: bool = False

    platform: List[str] = field(default_factory=list)

    season: Optional[int] = None
    seasons: List[int] = field(default_factory=list)

    episodes: Optional[int] = None
    total_episodes: Optional[int] = None

    languages: List[str] = field(default_factory=list)

    status: str = "Unknown"

    last_episode: Optional[int] = None
    last_release: Optional[str] = None

    next_episode: Optional[int] = None
    expected_release: Optional[str] = None
    schedule: Optional[str] = None

    studio: Optional[str] = None
    dub_by: Optional[str] = None

    source: str = "DC"

    source_url: Optional[str] = None

    # Extra internal fields
    matched_title: Optional[str] = None
    confidence: float = 0.0

    # Season-specific raw information
    season_pages: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ============================================================
# SESSION
# ============================================================

session = requests.Session()

session.headers.update(
    {
        "User-Agent": USER_AGENT,
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;"
            "q=0.9,image/avif,image/webp,*/*;q=0.8"
        ),
        "Accept-Language": "en-US,en;q=0.9",
        "Connection": "keep-alive",
    }
)

# ============================================================
# TEXT UTILITIES
# ============================================================

def clean_text(value: Any) -> str:
    """Clean HTML/text safely."""

    if value is None:
        return ""

    if isinstance(value, (list, tuple)):
        value = " ".join(str(x) for x in value)

    value = str(value)

    value = html.unescape(value)
    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"\s+", " ", value)

    return value.strip()


def normalize_text(value: str) -> str:
    """
    Normalize anime names for matching.

    Examples:
        Re Zero
        re:zero
        REZERO
    can become comparable strings.
    """

    if not value:
        return ""

    value = unicodedata.normalize("NFKD", value)

    value = value.lower()

    # Common punctuation replacements.
    value = value.replace(":", " ")
    value = value.replace("-", " ")
    value = value.replace("_", " ")
    value = value.replace("–", " ")
    value = value.replace("—", " ")
    value = value.replace(".", " ")

    # Remove season words only for matching.
    value = re.sub(
        r"\bseason\s+\d+\b",
        " ",
        value,
        flags=re.IGNORECASE,
    )

    value = re.sub(r"\bpart\s+\d+\b", " ", value)

    # Remove common page suffixes.
    value = re.sub(
        r"\b(hindi|dubbed|dub|episodes?|download|hd|subbed|"
        r"english|tamil|telugu|japanese)\b",
        " ",
        value,
        flags=re.IGNORECASE,
    )

    value = re.sub(r"[^a-z0-9]+", " ", value)

    value = re.sub(r"\s+", " ", value).strip()

    return value


def compact_normalize(value: str) -> str:
    return normalize_text(value).replace(" ", "")


def tokenize(value: str) -> List[str]:
    return [
        token
        for token in normalize_text(value).split()
        if len(token) >= 2
    ]


def similarity(a: str, b: str) -> float:
    """Combined fuzzy similarity."""

    na = normalize_text(a)
    nb = normalize_text(b)

    if not na or not nb:
        return 0.0

    if na == nb:
        return 1.0

    if compact_normalize(a) == compact_normalize(b):
        return 0.98

    seq = SequenceMatcher(None, na, nb).ratio()

    ta = set(tokenize(a))
    tb = set(tokenize(b))

    if ta and tb:
        overlap = len(ta & tb) / max(len(ta), len(tb))
    else:
        overlap = 0.0

    # Containment is useful for short names.
    containment = 0.0

    if na in nb or nb in na:
        shorter = min(len(na), len(nb))
        longer = max(len(na), len(nb))

        if longer:
            containment = shorter / longer

    return max(
        seq * 0.65 + overlap * 0.35,
        containment * 0.90,
    )


def query_variants(query: str) -> List[str]:
    """
    Generate several safe search variants.

    Example:
        re zero
        re:zero
        rezero
    """

    query = clean_text(query)

    variants: List[str] = []

    def add(value: str) -> None:
        value = clean_text(value)

        if value and value not in variants:
            variants.append(value)

    add(query)

    normalized = normalize_text(query)
    add(normalized)

    compact = compact_normalize(query)
    add(compact)

    # Alias expansion.
    qnorm = normalize_text(query)

    for canonical, aliases in ALIASES.items():
        all_names = [canonical] + aliases

        for alias in all_names:
            if (
                qnorm == normalize_text(alias)
                or compact == compact_normalize(alias)
            ):
                for item in all_names:
                    add(item)

    return variants[:10]


def canonical_alias(query: str) -> Optional[str]:
    """Return canonical alias if known."""

    q = normalize_text(query)

    best = None
    best_score = 0.0

    for canonical, aliases in ALIASES.items():

        candidates = [canonical] + aliases

        for candidate in candidates:
            score = similarity(q, candidate)

            if score > best_score:
                best_score = score
                best = canonical

    if best_score >= 0.78:
        return best

    return None

# ============================================================
# HTTP
# ============================================================

def fetch(url: str, timeout: int = REQUEST_TIMEOUT) -> Optional[str]:
    """
    Fetch HTML safely.

    Returns None on:
      - timeout
      - connection failure
      - blocked request
      - invalid response
    """

    if not url:
        return None

    try:
        response = session.get(
            url,
            timeout=timeout,
            allow_redirects=True,
        )

        if response.status_code != 200:
            return None

        content_type = response.headers.get(
            "content-type",
            "",
        ).lower()

        if "text/html" not in content_type:
            return None

        return response.text

    except requests.RequestException:
        return None

    except Exception:
        return None


# ============================================================
# URL VALIDATION
# ============================================================

def is_our_domain(url: str) -> bool:
    try:
        host = urlparse(url).netloc.lower()

        return (
            host == "rareanimes.mov"
            or host.endswith(".rareanimes.mov")
        )

    except Exception:
        return False


def safe_page_url(url: str) -> Optional[str]:
    """
    Only allow RareAnimes pages.

    This prevents accidentally returning external
    third-party download/watch URLs.
    """

    if not url:
        return None

    url = url.strip()

    if not url.startswith(("http://", "https://")):
        return None

    if not is_our_domain(url):
        return None

    return url


# ============================================================
# SEARCH RESULT PARSER
# ============================================================

def parse_search_results(
    html_text: str,
    query: str,
) -> List[Dict[str, Any]]:

    if not html_text:
        return []

    soup = BeautifulSoup(
        html_text,
        "html.parser",
    )

    results: List[Dict[str, Any]] = []

    # WordPress-style search pages commonly use article/post links.
    candidates = soup.select(
        "article h2 a, "
        "article h3 a, "
        ".post-title a, "
        ".entry-title a, "
        "h2 a, "
        "h3 a"
    )

    seen = set()

    for anchor in candidates:

        href = anchor.get("href")

        if not href:
            continue

        href = urljoin(
            BASE_URL,
            href,
        )

        href = safe_page_url(href)

        if not href:
            continue

        title = clean_text(
            anchor.get_text(" ", strip=True)
        )

        if not title:
            continue

        key = href.rstrip("/")

        if key in seen:
            continue

        seen.add(key)

        # Ignore obvious navigation pages.
        lower_title = title.lower()

        if lower_title in {
            "home",
            "contact us",
            "privacy policy",
            "cookie policy",
            "dmca",
            "request shows",
        }:
            continue

        score = similarity(
            query,
            title,
        )

        results.append(
            {
                "title": title,
                "url": href,
                "score": score,
            }
        )

    # Also inspect generic links if the theme does not use
    # the expected article selectors.
    if not results:

        for anchor in soup.find_all("a"):

            href = anchor.get("href")

            if not href:
                continue

            href = urljoin(
                BASE_URL,
                href,
            )

            href = safe_page_url(href)

            if not href:
                continue

            title = clean_text(
                anchor.get_text(" ", strip=True)
            )

            if not title:
                continue

            score = similarity(
                query,
                title,
            )

            if score < 0.35:
                continue

            key = href.rstrip("/")

            if key in seen:
                continue

            seen.add(key)

            results.append(
                {
                    "title": title,
                    "url": href,
                    "score": score,
                }
            )

    results.sort(
        key=lambda item: item.get("score", 0),
        reverse=True,
    )

    return results[:MAX_SEARCH_RESULTS]


def search_rareanimes(query: str) -> List[Dict[str, Any]]:
    """
    Search RareAnimes using several query variants.

    Search requests are performed concurrently.
    """

    variants = query_variants(query)

    all_results: Dict[str, Dict[str, Any]] = {}

    def worker(search_query: str):
        url = SEARCH_URL.format(
            query=quote(search_query)
        )

        page = fetch(
            url,
            timeout=SEARCH_TIMEOUT,
        )

        if not page:
            return []

        return parse_search_results(
            page,
            search_query,
        )

    with ThreadPoolExecutor(
        max_workers=min(5, max(1, len(variants)))
    ) as executor:

        futures = [
            executor.submit(
                worker,
                variant,
            )
            for variant in variants
        ]

        for future in as_completed(futures):

            try:
                items = future.result()

            except Exception:
                continue

            for item in items:

                url = item["url"]

                old = all_results.get(url)

                if old is None:
                    all_results[url] = item

                elif item["score"] > old["score"]:
                    all_results[url] = item

    final = list(
        all_results.values()
    )

    final.sort(
        key=lambda x: x.get("score", 0),
        reverse=True,
    )

    return final[:MAX_SEARCH_RESULTS]

# ============================================================
# PAGE PARSING HELPERS
# ============================================================

def get_meta(
    soup: BeautifulSoup,
    *,
    name: Optional[str] = None,
    prop: Optional[str] = None,
) -> Optional[str]:

    tag = None

    if name:
        tag = soup.find(
            "meta",
            attrs={"name": name},
        )

    if tag is None and prop:
        tag = soup.find(
            "meta",
            attrs={"property": prop},
        )

    if tag is None:
        return None

    content = tag.get("content")

    return clean_text(content)


def extract_poster(
    soup: BeautifulSoup,
) -> Optional[str]:

    # OpenGraph image.
    image = get_meta(
        soup,
        prop="og:image",
    )

    if image:
        return image

    # Twitter image.
    image = get_meta(
        soup,
        name="twitter:image",
    )

    if image:
        return image

    # Article featured image.
    selectors = [
        "article img",
        ".post-thumbnail img",
        ".featured-image img",
        ".entry-content img",
        "main img",
    ]

    for selector in selectors:

        img = soup.select_one(
            selector
        )

        if not img:
            continue

        src = (
            img.get("src")
            or img.get("data-src")
            or img.get("data-lazy-src")
        )

        if not src:
            continue

        return urljoin(
            BASE_URL,
            src,
        )

    return None


def page_title(
    soup: BeautifulSoup,
) -> str:

    og_title = get_meta(
        soup,
        prop="og:title",
    )

    if og_title:
        return og_title

    h1 = soup.find("h1")

    if h1:
        return clean_text(
            h1.get_text(" ", strip=True)
        )

    if soup.title:
        return clean_text(
            soup.title.get_text(
                " ",
                strip=True,
            )
        )

    return ""


def body_text(
    soup: BeautifulSoup,
) -> str:

    # Remove scripts/styles.
    for tag in soup(
        ["script", "style", "noscript"]
    ):
        tag.decompose()

    main = (
        soup.select_one("article")
        or soup.select_one(".entry-content")
        or soup.select_one("main")
        or soup.body
    )

    if not main:
        return ""

    return clean_text(
        main.get_text(
            " ",
            strip=True,
        )
    )


def extract_season(text: str) -> Optional[int]:

    patterns = [
        r"\bseason\s*(\d+)\b",
        r"\bs(\d{1,2})\b",
    ]

    for pattern in patterns:

        match = re.search(
            pattern,
            text,
            re.IGNORECASE,
        )

        if match:
            try:
                return int(match.group(1))
            except ValueError:
                pass

    return None


def extract_episode_numbers(
    text: str,
) -> List[int]:

    numbers = []

    patterns = [
        r"\bepisode\s*(\d{1,4})\b",
        r"\bep\.?\s*(\d{1,4})\b",
        r"\bep\s*(\d{1,4})\b",
    ]

    for pattern in patterns:

        for match in re.finditer(
            pattern,
            text,
            re.IGNORECASE,
        ):

            try:
                number = int(
                    match.group(1)
                )

                if 0 < number <= 5000:
                    numbers.append(number)

            except ValueError:
                continue

    return sorted(set(numbers))


def extract_total_episode(
    text: str,
) -> Optional[int]:

    patterns = [
        r"\btotal\s*episodes?\s*[:\-]?\s*(\d{1,4})\b",
        r"\bepisodes?\s*[:\-]?\s*(\d{1,4})\b",
        r"\b(\d{1,4})\s*episodes?\b",
    ]

    for pattern in patterns:

        match = re.search(
            pattern,
            text,
            re.IGNORECASE,
        )

        if match:

            try:
                number = int(
                    match.group(1)
                )

                if 0 < number <= 5000:
                    return number

            except ValueError:
                pass

    return None


def extract_languages(
    text: str,
) -> List[str]:

    lower = text.lower()

    found = []

    language_patterns = {
        "Hindi": [
            r"\bhindi\b",
            r"\bहिंदी\b",
        ],
        "English": [
            r"\benglish\b",
        ],
        "Tamil": [
            r"\btamil\b",
            r"\bதமிழ்\b",
        ],
        "Telugu": [
            r"\btelugu\b",
            r"\bతెలుగు\b",
        ],
        "Japanese": [
            r"\bjapanese\b",
            r"\b日本語\b",
        ],
    }

    for language in DISPLAY_LANGUAGES:

        patterns = language_patterns.get(
            language,
            [],
        )

        for pattern in patterns:

            if re.search(
                pattern,
                lower,
                re.IGNORECASE,
            ):
                found.append(language)
                break

    return found

# ============================================================
# PLATFORM / NETWORK
# ============================================================

PLATFORM_PATTERNS = {
    "Sony YAY!": [
        r"sony\s*yay",
        r"sony\s*yay!",
    ],
    "SonyLIV": [
        r"sonyliv",
        r"sony\s*liv",
    ],
    "Crunchyroll": [
        r"crunchyroll",
    ],
    "Netflix": [
        r"\bnetflix\b",
    ],
    "JioHotstar": [
        r"jiohotstar",
        r"hotstar",
        r"jiocinema",
        r"jio\s*cinema",
    ],
    "Amazon MX Player": [
        r"amazon\s*mx\s*player",
        r"\bmx\s*player\b",
    ],
    "Prime Video": [
        r"prime\s*video",
        r"amazon\s*prime",
    ],
    "Anime Times": [
        r"anime\s*times",
    ],
    "ZEE5": [
        r"\bzee5\b",
    ],
    "Muse India": [
        r"muse\s*india",
    ],
    "Ani-One India": [
        r"ani[-\s]*one\s*india",
        r"anione\s*india",
    ],
}


def extract_platforms(
    text: str,
) -> List[str]:

    found = []

    for platform, patterns in PLATFORM_PATTERNS.items():

        for pattern in patterns:

            if re.search(
                pattern,
                text,
                re.IGNORECASE,
            ):
                found.append(platform)
                break

    return found


# ============================================================
# HINDI DUB
# ============================================================

def detect_hindi_dub(
    title: str,
    text: str,
) -> Tuple[bool, bool]:

    combined = f"{title} {text}".lower()

    positive_patterns = [
        r"hindi\s*dub",
        r"hindi\s*dubbed",
        r"hindi\s*audio",
        r"hindi\s*language",
        r"audio\s*[:\-]\s*.*hindi",
        r"languages?\s*[:\-]\s*.*hindi",
    ]

    negative_patterns = [
        r"hindi\s*sub",
        r"hindi\s*subtitle",
        r"subbed\s*only",
    ]

    positive = any(
        re.search(
            pattern,
            combined,
            re.IGNORECASE,
        )
        for pattern in positive_patterns
    )

    negative = any(
        re.search(
            pattern,
            combined,
            re.IGNORECASE,
        )
        for pattern in negative_patterns
    )

    if positive:
        return True, True

    # If page is explicitly inside Hindi Dub category
    # or title says Hindi Dubbed.
    if (
        "hindi-dubbed" in combined
        or "hindi dub" in combined
    ):
        return True, True

    # Do not treat "Hindi Sub" as Hindi Dub.
    if negative and not positive:
        return False, True

    return False, False


# ============================================================
# STATUS
# ============================================================

def detect_status(
    text: str,
    current_episode: Optional[int],
    total_episode: Optional[int],
) -> str:

    lower = text.lower()

    completed_words = [
        "completed",
        "complete",
        "series completed",
        "all episodes",
    ]

    ongoing_words = [
        "ongoing",
        "currently airing",
        "airing",
        "currently releasing",
        "episode added",
        "new episode",
    ]

    if any(
        word in lower
        for word in completed_words
    ):
        return "Completed"

    if any(
        word in lower
        for word in ongoing_words
    ):
        return "Ongoing"

    if (
        current_episode
        and total_episode
        and current_episode < total_episode
    ):
        return "Ongoing"

    if (
        current_episode
        and total_episode
        and current_episode >= total_episode
    ):
        return "Completed"

    return "Unknown"


# ============================================================
# DATE EXTRACTION
# ============================================================

MONTHS = (
    "January|February|March|April|May|June|"
    "July|August|September|October|November|December"
)


def normalize_date_string(
    value: str,
) -> Optional[str]:

    value = clean_text(value)

    patterns = [
        rf"\b\d{{1,2}}\s+(?:{MONTHS})\s+\d{{4}}\b",
        rf"\b(?:{MONTHS})\s+\d{{1,2}},?\s+\d{{4}}\b",
        r"\b\d{1,2}[/-]\d{1,2}[/-]\d{4}\b",
    ]

    for pattern in patterns:

        match = re.search(
            pattern,
            value,
            re.IGNORECASE,
        )

        if match:
            return match.group(0)

    return None


def extract_last_release(
    text: str,
) -> Optional[str]:

    patterns = [
        r"(?:last\s*release|released\s*on|release\s*date)"
        r"\s*[:\-]?\s*([^|.;]{6,40})",

        r"(?:released)\s*[:\-]?\s*([^|.;]{6,40})",
    ]

    for pattern in patterns:

        match = re.search(
            pattern,
            text,
            re.IGNORECASE,
        )

        if match:

            result = normalize_date_string(
                match.group(1)
            )

            if result:
                return result

    return None


# ============================================================
# LAST / NEXT EPISODE
# ============================================================

def extract_last_episode(
    text: str,
) -> Optional[int]:

    patterns = [
        r"last\s*episode\s*[:\-]?\s*(\d{1,4})",
        r"latest\s*episode\s*[:\-]?\s*(\d{1,4})",
        r"episode\s*(\d{1,4})\s*(?:added|released)",
    ]

    for pattern in patterns:

        match = re.search(
            pattern,
            text,
            re.IGNORECASE,
        )

        if match:

            try:
                number = int(
                    match.group(1)
                )

                if 0 < number <= 5000:
                    return number

            except ValueError:
                pass

    episodes = extract_episode_numbers(text)

    if episodes:
        return max(episodes)

    return None


def extract_next_episode(
    text: str,
) -> Optional[int]:

    patterns = [
        r"next\s*episode\s*[:\-]?\s*(\d{1,4})",
        r"upcoming\s*episode\s*[:\-]?\s*(\d{1,4})",
        r"episode\s*(\d{1,4})\s*(?:next|upcoming)",
    ]

    for pattern in patterns:

        match = re.search(
            pattern,
            text,
            re.IGNORECASE,
        )

        if match:

            try:
                number = int(
                    match.group(1)
                )

                if 0 < number <= 5000:
                    return number

            except ValueError:
                pass

    return None


def extract_expected_release(
    text: str,
) -> Optional[str]:

    patterns = [
        r"(?:expected\s*release|next\s*release|"
        r"airing\s*on|releasing\s*on)"
        r"\s*[:\-]?\s*([^|.;]{6,45})",

        r"(?:next\s*episode.*?)(\d{1,2}\s+"
        rf"(?:{MONTHS})\s+\d{{4}})",
    ]

    for pattern in patterns:

        match = re.search(
            pattern,
            text,
            re.IGNORECASE,
        )

        if match:

            result = normalize_date_string(
                match.group(1)
            )

            if result:
                return result

    return None


def extract_schedule(
    text: str,
) -> Optional[str]:

    patterns = [
        r"(every\s+(?:monday|tuesday|wednesday|thursday|"
        r"friday|saturday|sunday))",

        r"(weekly\s+on\s+(?:monday|tuesday|wednesday|"
        r"thursday|friday|saturday|sunday))",

        r"(every\s+week)",
    ]

    for pattern in patterns:

        match = re.search(
            pattern,
            text,
            re.IGNORECASE,
        )

        if match:
            return clean_text(
                match.group(1)
            ).title()

    return None

# ============================================================
# EXTRA FIELD EXTRACTION
# ============================================================

def extract_label_value(
    text: str,
    labels: List[str],
) -> Optional[str]:

    for label in labels:

        pattern = (
            rf"{re.escape(label)}"
            rf"\s*[:\-]\s*"
            rf"([^|•\n;]{{2,100}})"
        )

        match = re.search(
            pattern,
            text,
            re.IGNORECASE,
        )

        if match:

            value = clean_text(
                match.group(1)
            )

            if value:
                return value

    return None


def extract_studio(
    text: str,
) -> Optional[str]:

    return extract_label_value(
        text,
        [
            "Studio",
            "Studios",
        ],
    )


def extract_dub_by(
    text: str,
) -> Optional[str]:

    return extract_label_value(
        text,
        [
            "Dub By",
            "Dubbed By",
            "Hindi Dub By",
            "Hindi Dubbed By",
        ],
    )


# ============================================================
# SEASON LINK DISCOVERY
# ============================================================

def discover_season_links(
    soup: BeautifulSoup,
    anime_name: str,
) -> List[Tuple[int, str]]:

    results: Dict[int, str] = {}

    for anchor in soup.find_all("a"):

        href = anchor.get("href")

        if not href:
            continue

        href = urljoin(
            BASE_URL,
            href,
        )

        href = safe_page_url(href)

        if not href:
            continue

        title = clean_text(
            anchor.get_text(
                " ",
                strip=True,
            )
        )

        combined = f"{title} {href}"

        season = extract_season(
            combined
        )

        if season is None:
            continue

        # Keep only links that resemble the same anime.
        score = similarity(
            anime_name,
            title,
        )

        url_score = similarity(
            anime_name,
            href.replace("-", " "),
        )

        if max(score, url_score) < 0.30:
            continue

        results[season] = href

    return sorted(
        results.items(),
        key=lambda item: item[0],
    )


# ============================================================
# PAGE -> ANIME INFO
# ============================================================

def parse_anime_page(
    url: str,
    query: str = "",
) -> Optional[AnimeInfo]:

    url = safe_page_url(url)

    if not url:
        return None

    html_text = fetch(url)

    if not html_text:
        return None

    soup = BeautifulSoup(
        html_text,
        "html.parser",
    )

    title = page_title(soup)

    if not title:
        return None

    text = body_text(soup)

    season = extract_season(
        f"{title} {text}"
    )

    episodes_found = extract_episode_numbers(
        text
    )

    total_episodes = extract_total_episode(
        text
    )

    last_episode = extract_last_episode(
        text
    )

    # If the page clearly lists episode numbers,
    # use the highest number.
    if episodes_found:

        highest = max(
            episodes_found
        )

        if not last_episode:
            last_episode = highest

        if not total_episodes:
            total_episodes = highest

    next_episode = extract_next_episode(
        text
    )

    hindi_dub, hindi_known = detect_hindi_dub(
        title,
        text,
    )

    languages = extract_languages(
        f"{title} {text}"
    )

    platforms = extract_platforms(
        f"{title} {text}"
    )

    status = detect_status(
        text,
        last_episode,
        total_episodes,
    )

    # If there is no explicit status but next episode
    # exists, consider it ongoing.
    if (
        status == "Unknown"
        and next_episode is not None
    ):
        status = "Ongoing"

    # If current >= total, completed.
    if (
        last_episode
        and total_episodes
        and last_episode >= total_episodes
    ):
        status = "Completed"

    info = AnimeInfo()

    info.name = clean_text(title)
    info.original_name = clean_text(title)

    info.poster = extract_poster(soup)

    info.hindi_dub = hindi_dub
    info.hindi_status_known = hindi_known

    info.platform = platforms

    info.season = season

    if season is not None:
        info.seasons = [season]

    info.episodes = last_episode
    info.total_episodes = total_episodes

    info.languages = languages

    info.status = status

    info.last_episode = last_episode
    info.last_release = extract_last_release(
        text
    )

    info.next_episode = next_episode
    info.expected_release = extract_expected_release(
        text
    )
    info.schedule = extract_schedule(
        text
    )

    info.studio = extract_studio(
        text
    )

    info.dub_by = extract_dub_by(
        text
    )

    info.source = "DC"
    info.source_url = url

    info.matched_title = title

    info.confidence = similarity(
        query,
        title,
    )

    # Discover other season pages.
    season_links = discover_season_links(
        soup,
        title,
    )

    info.season_pages = [
        season_url
        for _, season_url in season_links
    ]

    info.seasons = sorted(
        set(
            [x for x in info.seasons]
            + [season_no for season_no, _ in season_links]
        )
    )

    return info


# ============================================================
# BEST MATCH
# ============================================================

def choose_best_result(
    query: str,
    results: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:

    if not results:
        return None

    canonical = canonical_alias(
        query
    )

    best_item = None
    best_score = -1.0

    for item in results:

        title = item.get(
            "title",
            "",
        )

        score = similarity(
            query,
            title,
        )

        if canonical:
            alias_score = similarity(
                canonical,
                title,
            )

            score = max(
                score,
                alias_score,
            )

        # Penalize obvious movie results when
        # the user did not ask for a movie.
        qlower = query.lower()
        tlower = title.lower()

        if "movie" in tlower and "movie" not in qlower:
            score -= 0.12

        if score > best_score:
            best_score = score
            best_item = dict(item)
            best_item["score"] = score

    if not best_item:
        return None

    # Require a reasonable match.
    if best_item["score"] < 0.42:
        return None

    return best_item


# ============================================================
# SEASON MERGING
# ============================================================

def merge_season_info(
    main_info: AnimeInfo,
    season_info: AnimeInfo,
) -> None:

    if season_info.season is not None:

        main_info.seasons = sorted(
            set(
                main_info.seasons
                + [season_info.season]
            )
        )

    # Keep useful languages.
    main_info.languages = sorted(
        set(
            main_info.languages
            + season_info.languages
        ),
        key=lambda x: DISPLAY_LANGUAGES.index(x)
        if x in DISPLAY_LANGUAGES
        else 99,
    )

    # Hindi status.
    if season_info.hindi_status_known:
        main_info.hindi_status_known = True

    if season_info.hindi_dub:
        main_info.hindi_dub = True

    # Platforms.
    main_info.platform = sorted(
        set(
            main_info.platform
            + season_info.platform
        )
    )

    # Studio / dub-by.
    if not main_info.studio:
        main_info.studio = season_info.studio

    if not main_info.dub_by:
        main_info.dub_by = season_info.dub_by


# ============================================================
# LOAD SEASONS
# ============================================================

def load_additional_seasons(
    info: AnimeInfo,
) -> AnimeInfo:

    urls = list(
        dict.fromkeys(
            info.season_pages
        )
    )

    if not urls:
        return info

    urls = urls[:MAX_SEASON_PAGES]

    def worker(url: str):
        return parse_anime_page(
            url,
            info.name,
        )

    with ThreadPoolExecutor(
        max_workers=min(6, len(urls))
    ) as executor:

        futures = [
            executor.submit(
                worker,
                url,
            )
            for url in urls
        ]

        for future in as_completed(futures):

            try:
                season_info = future.result()

            except Exception:
                continue

            if not season_info:
                continue

            merge_season_info(
                info,
                season_info,
            )

    return info

# ============================================================
# FINAL CLEANUP
# ============================================================

def finalize_info(
    info: AnimeInfo,
) -> AnimeInfo:

    # Remove duplicate platforms.
    info.platform = list(
        dict.fromkeys(
            info.platform
        )
    )

    # Only requested languages.
    filtered_languages = []

    for language in DISPLAY_LANGUAGES:

        if language in info.languages:
            filtered_languages.append(
                language
            )

    info.languages = filtered_languages

    # If Hindi is known to be available,
    # make sure Hindi appears.
    if info.hindi_dub:
        if "Hindi" not in info.languages:
            info.languages.insert(
                0,
                "Hindi",
            )

    # If episodes are known but total is not,
    # use current number as total only when completed.
    if (
        info.episodes is not None
        and info.total_episodes is None
        and info.status == "Completed"
    ):
        info.total_episodes = info.episodes

    # Current episode should normally be last episode.
    if info.last_episode is None:
        info.last_episode = info.episodes

    # For ongoing anime, if total is unknown,
    # don't fabricate "10 / 12".
    if info.status == "Ongoing":
        if info.total_episodes is not None:
            info.episodes = info.last_episode
        else:
            info.episodes = info.last_episode

    # Completed.
    if (
        info.status == "Completed"
        and info.last_episode is not None
    ):
        info.episodes = info.last_episode

    return info


# ============================================================
# MAIN SCRAPER FUNCTION
# ============================================================

def get_anime_info(
    query: str,
    load_seasons: bool = True,
) -> Optional[AnimeInfo]:
    """
    Main function used by anime_service.py / bot.py.

    Example:

        info = get_anime_info("Re Zero")

    Returns AnimeInfo or None.
    """

    query = clean_text(query)

    if not query:
        return None

    # Search.
    results = search_rareanimes(
        query
    )

    if not results:
        return None

    # Best matching page.
    best = choose_best_result(
        query,
        results,
    )

    if not best:
        return None

    info = parse_anime_page(
        best["url"],
        query,
    )

    if not info:
        return None

    # Load season pages.
    if load_seasons:
        info = load_additional_seasons(
            info
        )

    return finalize_info(
        info
    )


# ============================================================
# DICT API
# ============================================================

def get_anime_info_dict(
    query: str,
) -> Optional[Dict[str, Any]]:

    info = get_anime_info(
        query
    )

    if not info:
        return None

    return info.to_dict()


# ============================================================
# TELEGRAM MESSAGE FORMATTER
# ============================================================

def format_anime_info(
    info: AnimeInfo,
) -> str:

    lines = []

    # --------------------------------------------------------
    # NAME
    # --------------------------------------------------------

    name = info.name or "Unknown"

    lines.append(
        f"🎬 Anime: {name}"
    )

    lines.append("")

    # --------------------------------------------------------
    # HINDI DUB
    # --------------------------------------------------------

    if info.hindi_dub:
        hindi_text = "🇮🇳 Hindi Dub: ✅ Available"

    elif info.hindi_status_known:
        hindi_text = "🇮🇳 Hindi Dub: ❌ Not Available"

    else:
        hindi_text = "🇮🇳 Hindi Dub: ⚠️ Unknown"

    lines.append(
        hindi_text
    )

    # --------------------------------------------------------
    # PLATFORM
    # --------------------------------------------------------

    if info.platform:

        lines.append(
            "📺 Platform: "
            + " • ".join(
                info.platform
            )
        )

    # --------------------------------------------------------
    # SEASON
    # --------------------------------------------------------

    if info.season is not None:

        lines.append(
            f"📀 Season: {info.season}"
        )

    elif len(info.seasons) == 1:

        lines.append(
            f"📀 Season: {info.seasons[0]}"
        )

    elif info.seasons:

        season_text = ", ".join(
            f"Season {x}"
            for x in info.seasons
        )

        lines.append(
            f"📀 Seasons: {season_text}"
        )

    # --------------------------------------------------------
    # EPISODES
    # --------------------------------------------------------

    if info.status == "Ongoing":

        if (
            info.last_episode is not None
            and info.total_episodes is not None
        ):
            lines.append(
                f"🎬 Episodes: "
                f"{info.last_episode} / "
                f"{info.total_episodes}"
            )

        elif info.last_episode is not None:

            lines.append(
                f"🎬 Episodes: "
                f"{info.last_episode} released"
            )

    elif info.episodes is not None:

        lines.append(
            f"🎬 Episodes: {info.episodes}"
        )

    # --------------------------------------------------------
    # LANGUAGES
    # --------------------------------------------------------

    if info.languages:

        lines.append("")

        lines.append(
            "🌐 Languages: "
            + " • ".join(
                info.languages
            )
        )

    # --------------------------------------------------------
    # STATUS
    # --------------------------------------------------------

    lines.append("")

    if info.status == "Completed":

        lines.append(
            "📊 Status: ✅ Completed"
        )

    elif info.status == "Ongoing":

        lines.append(
            "📊 Status: 🔴 Ongoing"
        )

    else:

        lines.append(
            "📊 Status: ⚪ Unknown"
        )

    # --------------------------------------------------------
    # LAST EPISODE
    # --------------------------------------------------------

    if info.last_episode is not None:

        lines.append("")

        lines.append(
            f"📅 Last Episode: "
            f"Episode {info.last_episode}"
        )

    if info.last_release:

        lines.append(
            f"🗓 Last Release: "
            f"{info.last_release}"
        )

    # --------------------------------------------------------
    # NEXT EPISODE
    # --------------------------------------------------------

    if (
        info.status == "Ongoing"
        and info.next_episode is not None
    ):

        lines.append("")

        lines.append(
            f"⏭ Next Episode: "
            f"Episode {info.next_episode}"
        )

        if info.expected_release:

            lines.append(
                f"📅 Expected Release: "
                f"{info.expected_release}"
            )

        if info.schedule:

            lines.append(
                f"⏰ Schedule: "
                f"{info.schedule}"
            )

    # --------------------------------------------------------
    # STUDIO
    # --------------------------------------------------------

    if info.studio:

        lines.append("")

        lines.append(
            f"🏢 Studio: {info.studio}"
        )

    # --------------------------------------------------------
    # DUB BY
    # --------------------------------------------------------

    if info.dub_by:

        lines.append(
            f"🎙 Dub By: {info.dub_by}"
        )

    # --------------------------------------------------------
    # SOURCE
    # --------------------------------------------------------

    lines.append("")

    lines.append(
        f"🔎 Source: {info.source}"
    )

    return "\n".join(
        lines
    )


# ============================================================
# SAFE SEARCH FUNCTION
# ============================================================

def search_anime(
    query: str,
) -> Optional[str]:

    try:

        info = get_anime_info(
            query,
            load_seasons=True,
        )

        if not info:
            return None

        return format_anime_info(
            info
        )

    except Exception:
        # Never crash the Telegram bot because
        # scraper failed.
        return None


# ============================================================
# JSON DEBUG / API
# ============================================================

def anime_to_json(
    query: str,
) -> Optional[str]:

    info = get_anime_info(
        query
    )

    if not info:
        return None

    return json.dumps(
        info.to_dict(),
        ensure_ascii=False,
        indent=2,
    )


# ============================================================
# SIMPLE CLI TEST
# ============================================================

if __name__ == "__main__":

    import sys

    if len(sys.argv) < 2:

        print(
            "Usage: python anime_scraper.py \"Naruto\""
        )

        raise SystemExit(0)

    query = " ".join(
        sys.argv[1:]
    )

    print(
        "Searching:",
        query,
    )

    result = search_anime(
        query
    )

    if result:

        print()
        print(result)

    else:

        print(
            "Anime not found or source unavailable."
        )
