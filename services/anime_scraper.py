# ============================================================
# anime_scraper.py
# PART 1/7
# ============================================================

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time

from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from urllib.parse import quote_plus, urljoin, urlparse

import aiohttp
from bs4 import BeautifulSoup


# ------------------------------------------------------------
# Optional fuzzy matching
# ------------------------------------------------------------

try:
    from rapidfuzz import fuzz
except ImportError:
    fuzz = None


# ------------------------------------------------------------
# Logging
# ------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

logger = logging.getLogger("AnimeScraper")


# ------------------------------------------------------------
# Constants
# ------------------------------------------------------------

BASE_URL = "https://www.rareanimes.mov"
SEARCH_URL = BASE_URL + "/?s={query}"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/140.0.0.0 Safari/537.36"
)

REQUEST_TIMEOUT = 12

CACHE_DIR = Path("anime_cache")
CACHE_DIR.mkdir(exist_ok=True)

ONGOING_CACHE_TTL = 5 * 60
COMPLETED_CACHE_TTL = 24 * 60 * 60


# ------------------------------------------------------------
# HTTP headers
# ------------------------------------------------------------

HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": (
        "text/html,application/xhtml+xml,"
        "application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "no-cache",
}


# ------------------------------------------------------------
# Episode model
# ------------------------------------------------------------

@dataclass
class Episode:
    number: int
    title: str = ""
    languages: list[str] = field(default_factory=list)
    release_date: Optional[str] = None


# ------------------------------------------------------------
# Search candidate
# ------------------------------------------------------------

@dataclass
class SearchCandidate:
    title: str
    url: str
    score: float = 0.0


# ------------------------------------------------------------
# Anime model
# ------------------------------------------------------------

@dataclass
class AnimeInfo:

    title: str = ""

    canonical_title: str = ""

    aliases: list[str] = field(default_factory=list)

    poster_url: Optional[str] = None

    source_url: Optional[str] = None

    source: str = "DC"

    hindi_available: bool = False

    platform: list[str] = field(default_factory=list)

    season: Optional[int] = None

    total_episodes: Optional[int] = None

    available_episodes: dict[str, int] = field(
        default_factory=dict
    )

    languages: list[str] = field(default_factory=list)

    status: str = "unknown"

    last_episode: Optional[int] = None

    last_release: Optional[str] = None

    next_episode: Optional[int] = None

    expected_release: Optional[str] = None

    schedule: Optional[str] = None

    studio: Optional[str] = None

    dub_by: Optional[str] = None

    release_year: Optional[int] = None

    runtime: Optional[str] = None

    genres: list[str] = field(default_factory=list)

    synopsis: Optional[str] = None

    episodes: list[Episode] = field(default_factory=list)

    # --------------------------------------------------------
    # Franchise / multi-series support
    # --------------------------------------------------------

    franchise: Optional[str] = None

    series_type: str = "series"

    related_series: list[str] = field(
        default_factory=list
    )

    movies: list[str] = field(
        default_factory=list
    )

    scraped_at: float = field(
        default_factory=time.time
    )


# ------------------------------------------------------------
# Franchise result model
# ------------------------------------------------------------

@dataclass
class FranchiseInfo:

    name: str = ""

    series: list[AnimeInfo] = field(
        default_factory=list
    )

    movies: list[AnimeInfo] = field(
        default_factory=list
    )


# ------------------------------------------------------------
# Exceptions
# ------------------------------------------------------------

class AnimeNotFound(Exception):
    pass


class ScraperError(Exception):
    pass


# ============================================================
# PART 2/7
# HTTP / CACHE / TEXT UTILITIES
# ============================================================


# ------------------------------------------------------------
# HTTP session
# ------------------------------------------------------------

async def create_session() -> aiohttp.ClientSession:

    timeout = aiohttp.ClientTimeout(
        total=REQUEST_TIMEOUT
    )

    connector = aiohttp.TCPConnector(
        limit=10,
        limit_per_host=5,
        ssl=False,
    )

    return aiohttp.ClientSession(
        headers=HEADERS,
        timeout=timeout,
        connector=connector,
    )


# ------------------------------------------------------------
# Fetch URL
# ------------------------------------------------------------

async def fetch(
    session: aiohttp.ClientSession,
    url: str,
) -> str:

    logger.info(
        "Fetching: %s",
        url
    )

    try:

        async with session.get(
            url,
            allow_redirects=True
        ) as response:

            if response.status != 200:

                raise ScraperError(
                    f"HTTP {response.status}: {url}"
                )

            return await response.text(
                errors="ignore"
            )

    except asyncio.TimeoutError:

        raise ScraperError(
            f"Timeout: {url}"
        )

    except aiohttp.ClientError as exc:

        raise ScraperError(
            f"Request failed: {url} -> {exc}"
        )


# ------------------------------------------------------------
# Normalize text
# ------------------------------------------------------------

def clean_text(
    value: str | None
) -> str:

    if not value:
        return ""

    value = value.replace(
        "\xa0",
        " "
    )

    value = value.replace(
        "\r",
        " "
    )

    value = value.replace(
        "\n",
        " "
    )

    value = re.sub(
        r"\s+",
        " ",
        value
    )

    return value.strip()


# ------------------------------------------------------------
# Normalize title for matching
# ------------------------------------------------------------

def normalize_title(
    title: str
) -> str:

    title = title.lower()

    replacements = [
        "–",
        "—",
        "-",
        "_",
        ":",
        ",",
        ".",
        "'",
        '"',
        "!",
        "?",
        "(",
        ")",
        "[",
        "]",
        "{",
        "}",
        "+",
        "/",
    ]

    for char in replacements:

        title = title.replace(
            char,
            " "
        )

    # Common site noise
    noise = [
        "season",
        "hindi",
        "dubbed",
        "dub",
        "episodes",
        "episode",
        "download",
        "hd",
        "watch",
        "online",
        "full",
        "complete",
    ]

    words = title.split()

    words = [
        word
        for word in words
        if word not in noise
    ]

    return " ".join(
        words
    ).strip()


# ------------------------------------------------------------
# Slug normalize
# ------------------------------------------------------------

def normalize_slug(
    value: str
) -> str:

    value = value.lower()

    value = re.sub(
        r"[^a-z0-9]+",
        "-",
        value
    )

    return value.strip("-")


# ------------------------------------------------------------
# Integer extractor
# ------------------------------------------------------------

def extract_int(
    value: str | None
) -> Optional[int]:

    if not value:
        return None

    match = re.search(
        r"\b(\d{1,4})\b",
        value
    )

    if not match:
        return None

    try:

        return int(
            match.group(1)
        )

    except ValueError:

        return None


# ------------------------------------------------------------
# Multiple integer extractor
# ------------------------------------------------------------

def extract_ints(
    value: str | None
) -> list[int]:

    if not value:
        return []

    return [
        int(x)
        for x in re.findall(
            r"\b\d{1,4}\b",
            value
        )
    ]


# ------------------------------------------------------------
# Unique preserving order
# ------------------------------------------------------------

def unique(
    items: list[str]
) -> list[str]:

    result = []

    seen = set()

    for item in items:

        item = clean_text(
            item
        )

        if not item:
            continue

        key = item.lower()

        if key in seen:
            continue

        seen.add(key)

        result.append(
            item
        )

    return result


# ------------------------------------------------------------
# Cache filename
# ------------------------------------------------------------

def cache_file(
    url: str
) -> Path:

    key = normalize_slug(
        url
    )

    if not key:
        key = "home"

    return CACHE_DIR / (
        f"{key[:180]}.json"
    )


# ------------------------------------------------------------
# Cache read
# ------------------------------------------------------------

def read_cache(
    url: str
) -> Optional[dict]:

    path = cache_file(
        url
    )

    if not path.exists():
        return None

    try:

        data = json.loads(
            path.read_text(
                encoding="utf-8"
            )
        )

        timestamp = data.get(
            "timestamp",
            0
        )

        if (
            time.time() - timestamp
            > data.get(
                "ttl",
                ONGOING_CACHE_TTL
            )
        ):

            return None

        return data

    except Exception:

        return None


# ------------------------------------------------------------
# Cache write
# ------------------------------------------------------------

def write_cache(
    url: str,
    html: str,
    ttl: int,
) -> None:

    path = cache_file(
        url
    )

    payload = {
        "timestamp": time.time(),
        "ttl": ttl,
        "html": html,
    }

    try:

        path.write_text(
            json.dumps(
                payload,
                ensure_ascii=False
            ),
            encoding="utf-8"
        )

    except Exception as exc:

        logger.warning(
            "Cache write failed: %s",
            exc
        )


# ------------------------------------------------------------
# Cached fetch
# ------------------------------------------------------------

async def fetch_cached(
    session: aiohttp.ClientSession,
    url: str,
    ttl: int = ONGOING_CACHE_TTL,
) -> str:

    cached = read_cache(
        url
    )

    if cached:

        logger.info(
            "CACHE HIT: %s",
            url
        )

        return cached["html"]

    html = await fetch(
        session,
        url
    )

    write_cache(
        url,
        html,
        ttl
    )

    return html

# ============================================================
# PART 3/7 — SEARCH + TITLE RESOLVER (CONTINUED)
# ============================================================
    original_html = await fetch_cached(
        session,
        original_url,
        ttl=ONGOING_CACHE_TTL
    )

    original_candidates = parse_search_results(
        original_html,
        query
    )

    candidates.extend(
        original_candidates
    )

    # Remove duplicate URLs
    unique = {}

    for candidate in candidates:
        unique[candidate.url] = candidate

    candidates = list(
        unique.values()
    )

    candidates.sort(
        key=lambda x: x.score,
        reverse=True
    )

    logger.info(
        "Search results for %r: %d",
        query,
        len(candidates)
    )

    return candidates

# ------------------------------------------------------------
# Find best anime page
# ------------------------------------------------------------

async def find_anime_page(
    session: aiohttp.ClientSession,
    query: str,
) -> Optional[SearchCandidate]:

    candidates = await search_anime(
        session,
        query
    )

    if not candidates:
        logger.warning(
            "No search results found for %r",
            query
        )

        return None

    # Prefer strong exact / near-exact matches.
    for candidate in candidates:

        score = candidate.score

        if score >= 90:
            logger.info(
                "Strong match: %s (%s)",
                candidate.title,
                candidate.url
            )

            return candidate

    # Otherwise use best result.
    best = candidates[0]

    logger.info(
        "Best match: %s (score %.1f)",
        best.title,
        best.score
    )

    return best


# ============================================================
# FRANCHISE SEARCH HELPERS
# ============================================================

async def scrape_franchise_series(
    scraper: "AnimeScraper",
    franchise_key: str,
) -> list["AnimeInfo"]:

    """
    Search every known separately-named series belonging to a
    franchise.

    IMPORTANT:
    We intentionally scrape each title separately instead of
    treating them as seasons of one AnimeInfo object.

    Example:
        Dragon Ball
        Dragon Ball Z
        Dragon Ball GT
        Dragon Ball Super
        Dragon Ball DAIMA

    Each remains a separate series in the final output.
    """

    titles = FRANCHISE_SERIES.get(
        franchise_key,
        []
    )

    results: list[AnimeInfo] = []

    for title in titles:

        try:

            info = await scraper.scrape_single(
                title
            )

            if not info:
                continue

            # Only show entries where Hindi is actually available.
            if not info.hindi_available:
                logger.info(
                    "Skipping non-Hindi franchise series: %s",
                    title
                )
                continue

            results.append(
                info
            )

        except Exception as exc:

            logger.warning(
                "Franchise series failed: %s -> %s",
                title,
                exc
            )

    return results


async def scrape_franchise_movies(
    scraper: "AnimeScraper",
    franchise_key: str,
) -> list[str]:

    """
    Search known franchise movie titles and keep only movies
    for which the source page indicates Hindi availability.
    """

    titles = FRANCHISE_MOVIES.get(
        franchise_key,
        []
    )

    movies: list[str] = []

    for title in titles:

        try:

            info = await scraper.scrape_single(
                title
            )

            if not info:
                continue

            if not info.hindi_available:
                continue

            # Prefer the actual page title if available.
            movie_title = (
                info.title.strip()
                if info.title
                else title
            )

            if movie_title not in movies:
                movies.append(
                    movie_title
                )

        except Exception as exc:

            logger.warning(
                "Franchise movie failed: %s -> %s",
                title,
                exc
            )

    return movies


# ------------------------------------------------------------
# Build franchise wrapper
# ------------------------------------------------------------

def build_franchise_info(
    query: str,
    franchise_key: str,
    series: list["AnimeInfo"],
    movies: list[str],
) -> "AnimeInfo":

    """
    Keep the original scraper API compatible by returning one
    AnimeInfo object.

    The object itself contains the franchise's separate series
    and movie information.
    """

    franchise_name = query.strip()

    total_episodes = 0
    available_episodes = 0

    for anime in series:

        total_episodes += (
            anime.total_episodes or 0
        )

        available_episodes += (
            anime.available_episodes or 0
        )

    # Use the first Hindi series as the base object so existing
    # code which expects AnimeInfo continues to work.
    if series:

        base = series[0]

        base.title = franchise_name
        base.franchise_key = franchise_key
        base.franchise_series = series
        base.franchise_movies = movies

        base.total_episodes = total_episodes
        base.available_episodes = available_episodes

        return base

    # If no Hindi series was found, still return a valid object.
    return AnimeInfo(
        title=franchise_name,
        franchise_key=franchise_key,
        franchise_series=[],
        franchise_movies=movies,
        hindi_available=bool(movies),
        total_episodes=total_episodes,
        available_episodes=available_episodes,
    )


# ============================================================
# PART 4/7
# HTML PARSING
# ============================================================


# ------------------------------------------------------------
# Generic text extraction helpers
# ------------------------------------------------------------

def get_meta_content(
    soup: BeautifulSoup,
    *,
    name: Optional[str] = None,
    prop: Optional[str] = None,
) -> str:

    tag = None

    if name:

        tag = soup.find(
            "meta",
            attrs={
                "name": name
            }
        )

    if not tag and prop:

        tag = soup.find(
            "meta",
            attrs={
                "property": prop
            }
        )

    if not tag:
        return ""

    return clean_text(
        tag.get(
            "content",
            ""
        )
    )


def first_non_empty(
    *values: Optional[str],
) -> str:

    for value in values:

        if value:

            value = clean_text(
                value
            )

            if value:
                return value

    return ""


def extract_title(
    soup: BeautifulSoup,
) -> str:

    # OpenGraph title is usually cleaner.
    title = get_meta_content(
        soup,
        prop="og:title"
    )

    if title:
        return title

    h1 = soup.find("h1")

    if h1:

        text = clean_text(
            h1.get_text(
                " ",
                strip=True
            )
        )

        if text:
            return text

    if soup.title:

        text = clean_text(
            soup.title.get_text(
                " ",
                strip=True
            )
        )

        if text:
            return text

    return ""


def extract_description(
    soup: BeautifulSoup,
) -> str:

    description = get_meta_content(
        soup,
        name="description"
    )

    if description:
        return description

    description = get_meta_content(
        soup,
        prop="og:description"
    )

    if description:
        return description

    # Common WordPress post-content areas.
    selectors = [
        ".entry-content",
        ".post-content",
        ".post-content-single",
        ".td-post-content",
        "article",
    ]

    for selector in selectors:

        node = soup.select_one(
            selector
        )

        if not node:
            continue

        text = clean_text(
            node.get_text(
                " ",
                strip=True
            )
        )

        if text:
            return text[:1000]

    return ""


def extract_poster(
    soup: BeautifulSoup,
) -> str:

    # Keep poster information internally because other parts of
    # the scraper may use it, but DO NOT print it in the final
    # bot output.
    poster = get_meta_content(
        soup,
        prop="og:image"
    )

    if poster:
        return poster

    image = soup.find(
        "img",
        src=True
    )

    if image:

        return clean_text(
            image.get(
                "src",
                ""
            )
        )

    return ""


# ------------------------------------------------------------
# Number extraction
# ------------------------------------------------------------

def extract_first_number(
    text: str,
) -> Optional[int]:

    if not text:
        return None

    match = re.search(
        r"(?<!\d)(\d{1,5})(?!\d)",
        text
    )

    if not match:
        return None

    try:
        return int(
            match.group(1)
        )
    except ValueError:
        return None


def extract_episode_number(
    text: str,
) -> Optional[int]:

    if not text:
        return None

    patterns = [
        r"\bepisode\s*[-:#]?\s*(\d+)",
        r"\bep\s*[-:#]?\s*(\d+)",
        r"\bepi\s*[-:#]?\s*(\d+)",
        r"\bepisod(?:e|es)?\s*[-:#]?\s*(\d+)",
        r"\bepisode\s*(\d+)",
        r"\bep\.\s*(\d+)",
    ]

    for pattern in patterns:

        match = re.search(
            pattern,
            text,
            flags=re.IGNORECASE
        )

        if match:

            try:
                return int(
                    match.group(1)
                )
            except ValueError:
                pass

    return None


def extract_season_number(
    text: str,
) -> Optional[int]:

    if not text:
        return None

    patterns = [
        r"\bseason\s*[-:#]?\s*(\d+)",
        r"\bs\s*[-.:]?\s*(\d+)\b",
        r"\bseason\s*(\d+)",
    ]

    for pattern in patterns:

        match = re.search(
            pattern,
            text,
            flags=re.IGNORECASE
        )

        if match:

            try:
                return int(
                    match.group(1)
                )
            except ValueError:
                pass

    return None


# ------------------------------------------------------------
# Language detection
# ------------------------------------------------------------

def detect_languages(
    text: str,
) -> list[str]:

    if not text:
        return []

    lower = text.lower()

    languages = []

    checks = [
        (
            "Hindi",
            [
                "hindi",
                "हिंदी",
                "hindidub",
                "hindi dub",
                "hindi dubbed",
            ],
        ),
        (
            "English",
            [
                "english",
                "eng dub",
                "english dub",
                "english dubbed",
            ],
        ),
        (
            "Japanese",
            [
                "japanese",
                "japan",
                "jpn",
            ],
        ),
        (
            "Tamil",
            [
                "tamil",
            ],
        ),
        (
            "Telugu",
            [
                "telugu",
            ],
        ),
        (
            "Bengali",
            [
                "bengali",
                "bangla",
            ],
        ),
        (
            "Malayalam",
            [
                "malayalam",
            ],
        ),
        (
            "Kannada",
            [
                "kannada",
            ],
        ),
    ]

    for language, keywords in checks:

        if any(
            keyword in lower
            for keyword in keywords
        ):
            languages.append(
                language
            )

    return languages


def detect_hindi(
    text: str,
) -> bool:

    if not text:
        return False

    lower = text.lower()

    hindi_keywords = [
        "hindi",
        "hindi dub",
        "hindi dubbed",
        "hindi audio",
        "hindi language",
        "हिंदी",
        "hindidub",
    ]

    return any(
        keyword in lower
        for keyword in hindi_keywords
    )


# ------------------------------------------------------------
# Hindi availability from page
# ------------------------------------------------------------

def extract_language_text(
    soup: BeautifulSoup,
) -> str:

    chunks = []

    # Page title / metadata
    if soup.title:

        chunks.append(
            soup.title.get_text(
                " ",
                strip=True
            )
        )

    for meta in soup.find_all(
        "meta"
    ):

        content = meta.get(
            "content",
            ""
        )

        if content:
            chunks.append(
                content
            )

    # Relevant labels / content.
    selectors = [
        ".entry-content",
        ".post-content",
        "article",
        ".anime-info",
        ".info",
        ".details",
        ".description",
    ]

    for selector in selectors:

        for node in soup.select(
            selector
        ):

            text = node.get_text(
                " ",
                strip=True
            )

            if text:
                chunks.append(
                    text
                )

    return clean_text(
        " ".join(chunks)
    )


def extract_hindi_available(
    soup: BeautifulSoup,
) -> bool:

    language_text = extract_language_text(
        soup
    )

    return detect_hindi(
        language_text
    )


# ------------------------------------------------------------
# Status extraction
# ------------------------------------------------------------

def extract_status(
    text: str,
) -> str:

    if not text:
        return "Unknown"

    lower = text.lower()

    if any(
        word in lower
        for word in [
            "completed",
            "complete",
            "finished",
        ]
    ):
        return "Completed"

    if any(
        word in lower
        for word in [
            "ongoing",
            "airing",
            "currently airing",
            "on-going",
        ]
    ):
        return "Ongoing"

    if any(
        word in lower
        for word in [
            "upcoming",
            "coming soon",
        ]
    ):
        return "Upcoming"

    return "Unknown"


# ------------------------------------------------------------
# Platform extraction
# ------------------------------------------------------------

def extract_platform(
    text: str,
) -> str:

    if not text:
        return ""

    platforms = [
        "JioCinema",
        "Jio Cinema",
        "Netflix",
        "Crunchyroll",
        "Disney+",
        "Disney Plus",
        "Disney",
        "Amazon Prime",
        "Prime Video",
        "YouTube",
        "Sony YAY",
        "Sony YAY!",
        "Hungama",
        "Cartoon Network",
        "Discovery Kids",
        "Tata Play",
    ]

    lower = text.lower()

    for platform in platforms:

        if platform.lower() in lower:
            return platform

    return ""


# ------------------------------------------------------------
# Season extraction
# ------------------------------------------------------------

def extract_season(
    soup: BeautifulSoup,
    title: str,
    page_text: str,
) -> Optional[int]:

    season = extract_season_number(
        title
    )

    if season:
        return season

    season = extract_season_number(
        page_text
    )

    if season:
        return season

    # Some pages use "S01", "S02", etc.
    match = re.search(
        r"\bS(?:EASON)?\s*0?(\d{1,2})\b",
        page_text,
        flags=re.IGNORECASE
    )

    if match:

        try:
            return int(
                match.group(1)
            )
        except ValueError:
            pass

    return None


# ------------------------------------------------------------
# Episode link parsing
# ------------------------------------------------------------

def parse_episode_links(
    soup: BeautifulSoup,
) -> list[EpisodeInfo]:

    episodes: list[EpisodeInfo] = []

    seen = set()

    for link in soup.find_all(
        "a",
        href=True
    ):

        href = link.get(
            "href",
            ""
        ).strip()

        text = clean_text(
            link.get_text(
                " ",
                strip=True
            )
        )

        if not href:
            continue

        if not href.startswith("http"):
            href = urljoin(
                BASE_URL,
                href
            )

        if "rareanimes.mov" not in href:
            continue

        episode_no = extract_episode_number(
            text
        )

        if episode_no is None:
            episode_no = extract_episode_number(
                href
            )

        if episode_no is None:
            continue

        key = (
            episode_no,
            href
        )

        if key in seen:
            continue

        seen.add(key)

        episodes.append(
            EpisodeInfo(
                number=episode_no,
                title=text or f"Episode {episode_no}",
                url=href,
                hindi_available=True,
            )
        )

    episodes.sort(
        key=lambda ep: ep.number
    )

    return episodes
# ============================================================
# PART 3/7
# SEARCH + FRANCHISE HANDLING
# ============================================================


# ------------------------------------------------------------
# Search anime
# ------------------------------------------------------------

async def search_anime(
    session: aiohttp.ClientSession,
    query: str,
) -> list[SearchCandidate]:

    query = clean_text(query)

    if not query:
        return []

    candidates: list[SearchCandidate] = []

    # --------------------------------------------------------
    # Search using the site's search endpoint
    # --------------------------------------------------------

    search_urls = [
        f"{BASE_URL}/?s={quote_plus(query)}",
        f"{BASE_URL}/search/{quote_plus(query)}/",
    ]

    for search_url in search_urls:

        try:

        html = await fetch_cached(
             session,
             search_url
        )
            

            if not html:
                continue

            soup = BeautifulSoup(
                html,
                "html.parser"
            )

            # --------------------------------------------
            # Collect links
            # --------------------------------------------

            for link in soup.find_all(
                "a",
                href=True
            ):

                href = link.get(
                    "href",
                    ""
                ).strip()

                title = clean_text(
                    link.get_text(
                        " ",
                        strip=True
                    )
                )

                if not href or not title:
                    continue

                href = urljoin(
                    BASE_URL,
                    href
                )

                if not href.startswith(
                    BASE_URL
                ):
                    continue

                # Ignore obvious non-anime pages.
                lower_url = href.lower()

                if any(
                    x in lower_url
                    for x in [
                        "/category/",
                        "/tag/",
                        "/author/",
                        "/page/",
                        "/feed/",
                    ]
                ):
                    continue

                score = title_match_score(
                    query,
                    title
                )

                if score <= 0:
                    continue

                candidates.append(
                    SearchCandidate(
                        title=title,
                        url=href,
                        score=score,
                    )
                )

        except Exception as exc:

            logger.warning(
                "Search failed for %r: %s",
                query,
                exc
            )

    # --------------------------------------------------------
    # Search through direct WordPress-style endpoint
    # --------------------------------------------------------

    if not candidates:

        try:

            api_url = (
                f"{BASE_URL}/wp-json/wp/v2/search"
                f"?search={quote_plus(query)}"
                f"&per_page=20"
            )

            html = await fetch_text(
                session,
                api_url
            )

            if html:

                try:
                    data = json.loads(
                        html
                    )
                except Exception:
                    data = []

                if isinstance(
                    data,
                    list
                ):

                    for item in data:

                        title_data = item.get(
                            "title",
                            {}
                        )

                        title = clean_text(
                            title_data.get(
                                "rendered",
                                ""
                            )
                        )

                        url = item.get(
                            "url",
                            ""
                        )

                        if not title or not url:
                            continue

                        score = title_match_score(
                            query,
                            title
                        )

                        if score <= 0:
                            continue

                        candidates.append(
                            SearchCandidate(
                                title=title,
                                url=url,
                                score=score,
                            )
                        )

        except Exception as exc:

            logger.debug(
                "WP API search unavailable: %s",
                exc
            )

    # --------------------------------------------------------
    # Deduplicate
    # --------------------------------------------------------

    unique_candidates = {}

    for candidate in candidates:

        key = candidate.url.rstrip(
            "/"
        ).lower()

        old = unique_candidates.get(
            key
        )

        if (
            old is None
            or candidate.score > old.score
        ):
            unique_candidates[key] = candidate

    candidates = list(
        unique_candidates.values()
    )

    candidates.sort(
        key=lambda item: item.score,
        reverse=True
    )

    logger.info(
        "Search results for %r: %d",
        query,
        len(candidates)
    )

    return candidates[:20]


# ------------------------------------------------------------
# Find best anime page
# ------------------------------------------------------------

async def find_anime_page(
    session: aiohttp.ClientSession,
    query: str,
) -> SearchCandidate:

    candidates = await search_anime(
        session,
        query
    )

    if not candidates:
        raise AnimeNotFound(
            f"No anime found for: {query}"
        )

    # Minimum reasonable score.
    good = [
        candidate
        for candidate in candidates
        if candidate.score >= 50
    ]

    if not good:
        raise AnimeNotFound(
            f"Anime not confidently matched: {query}"
        )

    best = good[0]

    logger.info(
        "MATCH: %s -> %s [%.1f]",
        query,
        best.title,
        best.score
    )

    return best


# ------------------------------------------------------------
# Get multiple season candidates
# ------------------------------------------------------------

async def find_season_candidates(
    session: aiohttp.ClientSession,
    query: str,
    limit: int = 8,
) -> list[SearchCandidate]:

    candidates = await search_anime(
        session,
        query
    )

    if not candidates:
        return []

    best_score = candidates[0].score

    # Keep candidates reasonably close.
    selected = [
        candidate
        for candidate in candidates
        if candidate.score >= max(
            55,
            best_score - 18
        )
    ]

    return selected[:limit]


# ============================================================
# FRANCHISE SEARCH
# ============================================================

# These aliases are intentionally limited to franchise names.
# A query such as "Dragon Ball Z" will still be treated as a
# normal single anime, while "Dragon Ball" expands to the
# separately named Dragon Ball series.
FRANCHISE_QUERY_ALIASES = {

    "dragon ball": "dragon_ball",

    "dragonball": "dragon_ball",

    "naruto": "naruto",

    "one piece": "one_piece",

    "bleach": "bleach",

}


# ------------------------------------------------------------
# Separately named series
# ------------------------------------------------------------

FRANCHISE_SERIES = {

    "dragon_ball": [
        "Dragon Ball",
        "Dragon Ball Z",
        "Dragon Ball GT",
        "Dragon Ball Super",
        "Super Dragon Ball Heroes",
        "Dragon Ball DAIMA",
    ],

    "naruto": [
        "Naruto",
        "Naruto Shippuden",
    ],

    "one_piece": [
        "One Piece",
    ],

    "bleach": [
        "Bleach",
        "Bleach: Thousand-Year Blood War",
    ],

}


# ------------------------------------------------------------
# Movies
# ------------------------------------------------------------

FRANCHISE_MOVIES = {

    "dragon_ball": [
        "Dragon Ball: Curse of the Blood Rubies",
        "Dragon Ball: Sleeping Princess in Devil's Castle",
        "Dragon Ball: Mystical Adventure",
        "Dragon Ball Z: Dead Zone",
        "Dragon Ball Z: The World's Strongest",
        "Dragon Ball Z: The Tree of Might",
        "Dragon Ball Z: Lord Slug",
        "Dragon Ball Z: Cooler's Revenge",
        "Dragon Ball Z: The Return of Cooler",
        "Dragon Ball Z: Super Android 13",
        "Dragon Ball Z: Broly - The Legendary Super Saiyan",
        "Dragon Ball Z: Bojack Unbound",
        "Dragon Ball Z: Broly - Second Coming",
        "Dragon Ball Z: Bio-Broly",
        "Dragon Ball Z: Fusion Reborn",
        "Dragon Ball Z: Wrath of the Dragon",
        "Dragon Ball Super: Broly",
        "Dragon Ball Super: Super Hero",
    ],

    "naruto": [
        "Naruto the Movie: Ninja Clash in the Land of Snow",
        "Naruto the Movie: Legend of the Stone of Gelel",
        "Naruto the Movie: Guardians of the Crescent Moon Kingdom",
        "Naruto Shippuden the Movie",
        "Naruto Shippuden the Movie: Bonds",
        "Naruto Shippuden the Movie: The Will of Fire",
        "Naruto Shippuden the Movie: The Lost Tower",
        "Naruto Shippuden the Movie: Blood Prison",
        "Naruto Shippuden the Movie: Road to Ninja",
        "The Last: Naruto the Movie",
        "Boruto: Naruto the Movie",
    ],

    "one_piece": [
        "One Piece: The Movie",
        "Clockwork Island Adventure",
        "Chopper's Kingdom on the Island of Strange Animals",
        "Dead End Adventure",
        "The Cursed Holy Sword",
        "Baron Omatsuri and the Secret Island",
        "The Giant Mechanical Soldier of Karakuri Castle",
        "One Piece Film: Strong World",
        "One Piece 3D: Mugiwara Chase",
        "One Piece Film: Z",
        "One Piece Film: Gold",
        "One Piece: Stampede",
        "One Piece Film: Red",
    ],

    "bleach": [
        "Bleach the Movie: Memories of Nobody",
        "Bleach the Movie: The DiamondDust Rebellion",
        "Bleach the Movie: Fade to Black",
        "Bleach the Movie: Hell Verse",
    ],

}


# ------------------------------------------------------------
# Get franchise key
# ------------------------------------------------------------

def get_franchise_key(
    query: str,
) -> Optional[str]:

    normalized = normalize_title(
        query
    )

    return FRANCHISE_QUERY_ALIASES.get(
        normalized
    )


def is_franchise_query(
    query: str,
) -> bool:

    return (
        get_franchise_key(
            query
        )
        is not None
    )


# ------------------------------------------------------------
# Scrape franchise series
# ------------------------------------------------------------

async def scrape_franchise_series(
    scraper: "AnimeScraper",
    franchise_key: str,
) -> list[AnimeInfo]:

    titles = FRANCHISE_SERIES.get(
        franchise_key,
        []
    )

    results: list[AnimeInfo] = []

    for title in titles:

        try:

            info = await scraper.scrape_single(
                title
            )

            if not info:
                continue

            # Only include Hindi available series.
            if not info.hindi_available:
                logger.info(
                    "Skipping non-Hindi series: %s",
                    title
                )
                continue

            results.append(
                info
            )

        except Exception as exc:

            logger.warning(
                "Franchise series failed: %s -> %s",
                title,
                exc
            )

    return results


# ------------------------------------------------------------
# Scrape franchise movies
# ------------------------------------------------------------

async def scrape_franchise_movies(
    scraper: "AnimeScraper",
    franchise_key: str,
) -> list[str]:

    titles = FRANCHISE_MOVIES.get(
        franchise_key,
        []
    )

    movies: list[str] = []

    for title in titles:

        try:

            info = await scraper.scrape_single(
                title
            )

            if not info:
                continue

            if not info.hindi_available:
                continue

            movie_title = (
                info.title.strip()
                if info.title
                else title
            )

            if movie_title not in movies:

                movies.append(
                    movie_title
                )

        except Exception as exc:

            logger.warning(
                "Franchise movie failed: %s -> %s",
                title,
                exc
            )

    return movies


# ------------------------------------------------------------
# Build franchise result
# ------------------------------------------------------------

def build_franchise_info(
    query: str,
    franchise_key: str,
    series: list[AnimeInfo],
    movies: list[str],
) -> AnimeInfo:

    total_episodes = 0
    available_episodes = 0

    for anime in series:

        total_episodes += (
            anime.total_episodes or 0
        )

        available_episodes += (
            anime.available_episodes or 0
        )

    # Use the first result as the base object so the old bot
    # code can continue to expect AnimeInfo.
    if series:

        base = series[0]

        base.title = query.strip()

        base.franchise_key = (
            franchise_key
        )

        base.franchise_series = (
            series
        )

        base.franchise_movies = (
            movies
        )

        base.total_episodes = (
            total_episodes
        )

        base.available_episodes = (
            available_episodes
        )

        return base

    # Valid fallback when no Hindi series was found.
    return AnimeInfo(
        title=query.strip(),
        franchise_key=franchise_key,
        franchise_series=[],
        franchise_movies=movies,
        hindi_available=bool(movies),
        total_episodes=total_episodes,
        available_episodes=available_episodes,
)

# ============================================================
# PART 4/7
# PAGE INFO PARSER
# ============================================================


# ------------------------------------------------------------
# Find value after label
# ------------------------------------------------------------

def find_labeled_value(
    text: str,
    label: str,
) -> Optional[str]:

    pattern = re.compile(
        rf"{re.escape(label)}\s*:\s*(.+?)(?=\s+(?:"
        r"Full Name|Season|Episodes|Release Year|RunTime|"
        r"Genre|Language|Quality|Network|Year|Synopsis"
        r")\s*:|$)",
        re.I
    )

    match = pattern.search(text)

    if not match:
        return None

    return clean_text(
        match.group(1)
    )


# ------------------------------------------------------------
# Extract poster
# ------------------------------------------------------------

def extract_poster(
    soup: BeautifulSoup,
) -> Optional[str]:

    # Poster is still collected internally, but it is NOT shown
    # in the final bot output.
    candidates = []

    for img in soup.find_all("img"):

        src = (
            img.get("data-src")
            or img.get("data-lazy-src")
            or img.get("src")
        )

        if not src:
            continue

        src = urljoin(
            BASE_URL,
            src
        )

        alt = clean_text(
            img.get("alt")
        ).lower()

        candidates.append(
            (
                src,
                alt
            )
        )

    for src, alt in candidates:

        if (
            "season" in alt
            or "anime" in alt
            or "episode" in alt
        ):
            return src

    return (
        candidates[0][0]
        if candidates
        else None
    )


# ------------------------------------------------------------
# Extract page title
# ------------------------------------------------------------

def extract_page_title(
    soup: BeautifulSoup,
) -> str:

    h1 = soup.find("h1")

    if h1:

        title = clean_text(
            h1.get_text(
                " ",
                strip=True
            )
        )

        if title:
            return title

    if soup.title:

        title = clean_text(
            soup.title.get_text(
                " ",
                strip=True
            )
        )

        if title:
            return title

    return ""


# ------------------------------------------------------------
# Extract information section
# ------------------------------------------------------------

def extract_info_text(
    soup: BeautifulSoup,
) -> str:

    marker = soup.find(
        string=re.compile(
            r"Anime\s+Series\s+Info",
            re.I
        )
    )

    if marker:

        parent = marker.parent

        for _ in range(4):

            if not parent:
                break

            text = clean_text(
                parent.get_text(
                    " ",
                    strip=True
                )
            )

            if len(text) > 150:
                return text

            parent = parent.parent

    return clean_text(
        soup.get_text(
            " ",
            strip=True
        )
    )


# ------------------------------------------------------------
# Extract genres
# ------------------------------------------------------------

def parse_genres(
    value: Optional[str],
) -> list[str]:

    if not value:
        return []

    value = value.replace(
        " and ",
        ", "
    )

    return unique(
        [
            item.strip()
            for item in value.split(",")
            if item.strip()
        ]
    )


# ------------------------------------------------------------
# Extract languages
# ------------------------------------------------------------

def parse_languages(
    value: Optional[str],
) -> list[str]:

    if not value:
        return []

    value = re.sub(
        r"\{|\}",
        "",
        value
    )

    value = value.replace(
        "/",
        ","
    )

    value = value.replace(
        "•",
        ","
    )

    return unique(
        [
            item.strip()
            for item in value.split(",")
            if item.strip()
        ]
    )


# ------------------------------------------------------------
# Extract network/platform
# ------------------------------------------------------------

def parse_platforms(
    page_text: str,
) -> list[str]:

    platforms = []

    network = re.search(
        r"\bNetwork\s*:\s*(.+?)(?=\s+(?:"
        r"Year|Language|Genre|Quality|Synopsis"
        r")\s*:|$)",
        page_text,
        re.I
    )

    if network:

        raw = clean_text(
            network.group(1)
        )

        raw = re.sub(
            r"\s+and\s+",
            ",",
            raw,
            flags=re.I
        )

        parts = re.split(
            r"[,|•;/]+",
            raw
        )

        platforms.extend(
            [
                item.strip()
                for item in parts
                if item.strip()
            ]
        )

    patterns = [
        r"telecasted\s+by\s+([A-Za-z0-9 .&+'-]+)",
        r"stream(?:ed)?\s+on\s+([A-Za-z0-9 .&+'-]+)",
        r"available\s+on\s+([A-Za-z0-9 .&+'-]+)",
        r"produced\s+by\s+([A-Za-z0-9 .&+'-]+)",
    ]

    for pattern in patterns:

        for match in re.finditer(
            pattern,
            page_text,
            re.I
        ):

            value = clean_text(
                match.group(1)
            )

            value = value.split(".")[0]

            if 1 <= len(value) <= 60:
                platforms.append(
                    value
                )

    return unique(platforms)


# ------------------------------------------------------------
# Extract dub provider
# ------------------------------------------------------------

def parse_dub_by(
    page_text: str,
) -> Optional[str]:

    patterns = [

        r"dubbed\s+by\s+([A-Za-z0-9 .&+'-]+)",

        r"dub\s+by\s+([A-Za-z0-9 .&+'-]+)",

        r"hindi\s+dub\s+by\s+([A-Za-z0-9 .&+'-]+)",

    ]

    for pattern in patterns:

        match = re.search(
            pattern,
            page_text,
            re.I
        )

        if match:

            value = clean_text(
                match.group(1)
            )

            value = value.split(".")[0]

            if len(value) <= 80:
                return value

    return None


# ------------------------------------------------------------
# Parse complete AnimeInfo from page
# ------------------------------------------------------------

def parse_page_info(
    html: str,
    source_url: str,
) -> AnimeInfo:

    soup = BeautifulSoup(
        html,
        "html.parser"
    )

    page_text = clean_text(
        soup.get_text(
            " ",
            strip=True
        )
    )

    info_text = extract_info_text(
        soup
    )

    title = find_labeled_value(
        info_text,
        "Full Name"
    )

    if not title:
        title = extract_page_title(
            soup
        )

    season_raw = find_labeled_value(
        info_text,
        "Season"
    )

    episodes_raw = find_labeled_value(
        info_text,
        "Episodes"
    )

    release_year_raw = find_labeled_value(
        info_text,
        "Release Year"
    )

    runtime = find_labeled_value(
        info_text,
        "RunTime"
    )

    genre_raw = find_labeled_value(
        info_text,
        "Genre"
    )

    language_raw = find_labeled_value(
        info_text,
        "Language"
    )

    synopsis = find_labeled_value(
        info_text,
        "Synopsis"
    )

    anime = AnimeInfo(

        title=clean_text(
            title
        ),

        canonical_title=clean_text(
            title
        ),

        aliases=[],

        poster_url=extract_poster(
            soup
        ),

        source_url=source_url,

        source="DC",

        platform=parse_platforms(
            page_text
        ),

        languages=parse_languages(
            language_raw
        ),

        runtime=(
            clean_text(runtime)
            if runtime
            else None
        ),

        genres=parse_genres(
            genre_raw
        ),

        synopsis=(
            clean_text(synopsis)
            if synopsis
            else None
        ),

        dub_by=parse_dub_by(
            page_text
        ),
    )

    # --------------------------------------------------------
    # Season
    # --------------------------------------------------------

    if season_raw:

        season_number = extract_int(
            season_raw
        )

        if season_number is not None:

            anime.season = (
                season_number
            )

    # --------------------------------------------------------
    # Episodes
    # --------------------------------------------------------

    if episodes_raw:

        numbers = extract_ints(
            episodes_raw
        )

        if numbers:

            anime.total_episodes = max(
                numbers
            )

    # --------------------------------------------------------
    # Release year
    # --------------------------------------------------------

    if release_year_raw:

        match = re.search(
            r"\b(19|20)\d{2}\b",
            release_year_raw
        )

        if match:

            anime.release_year = int(
                match.group(0)
            )

    # --------------------------------------------------------
    # Hindi availability
    # --------------------------------------------------------

    page_lower = page_text.lower()

    anime.hindi_available = (

        "hindi dub" in page_lower

        or "hindi dubbed" in page_lower

        or re.search(
            r"\bhindi\s+(?:dub|sub)\b",
            page_lower
        ) is not None

        or "language: hindi" in page_lower

        or re.search(
            r"\blanguage\s*:\s*.*\bhindi\b",
            page_lower,
            re.I
        ) is not None
    )

    return anime


# ============================================================
# PART 5/7
# EPISODE PARSER
# ============================================================


LANGUAGE_NAMES = [
    "Hindi",
    "English",
    "Japanese",
    "Tamil",
    "Telugu",
    "Malayalam",
    "Kannada",
    "Bengali",
    "Marathi",
    "Korean",
    "Chinese",
    "Spanish",
    "French",
    "German",
    "Arabic",
]


# ------------------------------------------------------------
# Detect languages in episode block
# ------------------------------------------------------------

def detect_episode_languages(
    text: str,
) -> list[str]:

    result = []

    lower = text.lower()

    for language in LANGUAGE_NAMES:

        if re.search(
            rf"\b{re.escape(language.lower())}\b",
            lower
        ):
            result.append(
                language
            )

    return result


# ------------------------------------------------------------
# Parse episode number
# ------------------------------------------------------------

def parse_episode_number(
    text: str,
) -> Optional[int]:

    patterns = [

        r"\bEpisode\s*[-:]?\s*(\d{1,4})\b",

        r"\bEp\.?\s*[-:]?\s*(\d{1,4})\b",

        r"^\s*(\d{1,4})\s*[-:.]",

    ]

    for pattern in patterns:

        match = re.search(
            pattern,
            text,
            re.I
        )

        if match:

            try:

                return int(
                    match.group(1)
                )

            except ValueError:
                pass

    return None


# ------------------------------------------------------------
# Parse episode title
# ------------------------------------------------------------

def parse_episode_title(
    text: str,
    episode_number: int,
) -> str:

    cleaned = re.sub(
        rf"^\s*Episode\s*[-:]?\s*0*"
        rf"{episode_number}\s*[-:–—]?\s*",
        "",
        text,
        flags=re.I
    )

    cleaned = re.sub(
        r"\bEpisode\s*[-:]?\s*\d{1,4}\b",
        "",
        cleaned,
        count=1,
        flags=re.I
    )

    cleaned = re.sub(
        r"\b(?:Hindi|English|Japanese|Tamil|Telugu)"
        r"\s+(?:DUB|Sub)\b",
        "",
        cleaned,
        flags=re.I
    )

    cleaned = re.sub(
        r"\b(?:Hindi|English|Japanese|Tamil|Telugu)\b",
        "",
        cleaned,
        flags=re.I
    )

    cleaned = re.sub(
        r"\b(?:WatchMultQuality|StreamBeta|DLBeta|Mega)\b",
        "",
        cleaned,
        flags=re.I
    )

    cleaned = re.sub(
        r"\bNEW!?\b",
        "",
        cleaned,
        flags=re.I
    )

    cleaned = re.sub(
        r"\bSeason\s+Finale\b",
        "",
        cleaned,
        flags=re.I
    )

    cleaned = clean_text(
        cleaned
    )

    cleaned = cleaned.strip(
        " -–—:|"
    )

    return cleaned


# ------------------------------------------------------------
# Find episode containers
# ------------------------------------------------------------

def get_episode_blocks(
    soup: BeautifulSoup,
) -> list[str]:

    blocks = []

    elements = soup.find_all(
        [
            "h1",
            "h2",
            "h3",
            "h4",
            "h5",
            "p",
            "div",
            "article",
        ]
    )

    for element in elements:

        text = clean_text(
            element.get_text(
                " ",
                strip=True
            )
        )

        if not text:
            continue

        if not re.search(
            r"\bEpisode\s+\d{1,4}\b",
            text,
            re.I
        ):
            continue

        if len(text) > 2000:
            continue

        blocks.append(
            text
        )

    result = []

    seen = set()

    for block in blocks:

        key = re.sub(
            r"\s+",
            " ",
            block
        ).lower()

        if key in seen:
            continue

        seen.add(key)

        result.append(
            block
        )

    return result


# ------------------------------------------------------------
# Parse all episodes
# ------------------------------------------------------------

def parse_episodes(
    soup: BeautifulSoup,
) -> list[Episode]:

    episodes: dict[int, Episode] = {}

    all_text_elements = soup.find_all(
        [
            "h1",
            "h2",
            "h3",
            "h4",
            "h5",
            "p",
            "div",
            "li",
        ]
    )

    for element in all_text_elements:

        text = clean_text(
            element.get_text(
                " ",
                strip=True
            )
        )

        if not text:
            continue

        number = parse_episode_number(
            text
        )

        if number is None:
            continue

        if len(text) > 1200:
            continue

        languages = detect_episode_languages(
            text
        )

        title = parse_episode_title(
            text,
            number
        )

        if number not in episodes:

            episodes[number] = Episode(
                number=number,
                title=title,
                languages=languages,
            )

        else:

            existing = episodes[
                number
            ]

            existing.languages = unique(
                existing.languages
                + languages
            )

            if (
                len(title)
                > len(existing.title)
            ):
                existing.title = title
                # ============================================================
# PART 5/7
# EPISODE PARSER
# ============================================================

LANGUAGE_NAMES = [
    "Hindi",
    "English",
    "Japanese",
    "Tamil",
    "Telugu",
    "Malayalam",
    "Kannada",
    "Bengali",
    "Marathi",
    "Korean",
    "Chinese",
    "Spanish",
    "French",
    "German",
    "Arabic",
]


def detect_episode_languages(
    text: str,
) -> list[str]:

    result = []

    lower = text.lower()

    for language in LANGUAGE_NAMES:

        if re.search(
            rf"\b{re.escape(language.lower())}\b",
            lower
        ):
            result.append(language)

    return result


def parse_episode_number(
    text: str,
) -> Optional[int]:

    patterns = [
        r"\bEpisode\s*[-:]?\s*(\d{1,4})\b",
        r"\bEp\.?\s*[-:]?\s*(\d{1,4})\b",
        r"^\s*(\d{1,4})\s*[-:.]",
    ]

    for pattern in patterns:

        match = re.search(
            pattern,
            text,
            re.I
        )

        if match:

            try:
                return int(
                    match.group(1)
                )
            except ValueError:
                pass

    return None


def parse_episode_title(
    text: str,
    episode_number: int,
) -> str:

    cleaned = re.sub(
        rf"^\s*Episode\s*[-:]?\s*0*{episode_number}\s*[-:–—]?\s*",
        "",
        text,
        flags=re.I
    )

    cleaned = re.sub(
        r"\bEpisode\s*[-:]?\s*\d{1,4}\b",
        "",
        cleaned,
        count=1,
        flags=re.I
    )

    cleaned = re.sub(
        r"\b(?:Hindi|English|Japanese|Tamil|Telugu)\s+(?:DUB|Sub)\b",
        "",
        cleaned,
        flags=re.I
    )

    cleaned = re.sub(
        r"\b(?:Hindi|English|Japanese|Tamil|Telugu)\b",
        "",
        cleaned,
        flags=re.I
    )

    cleaned = re.sub(
        r"\b(?:WatchMultQuality|StreamBeta|DLBeta|Mega)\b",
        "",
        cleaned,
        flags=re.I
    )

    cleaned = re.sub(
        r"\bNEW!?\b",
        "",
        cleaned,
        flags=re.I
    )

    cleaned = re.sub(
        r"\bSeason\s+Finale\b",
        "",
        cleaned,
        flags=re.I
    )

    cleaned = clean_text(
        cleaned
    )

    return cleaned.strip(
        " -–—:|"
    )


def get_episode_blocks(
    soup: BeautifulSoup,
) -> list[str]:

    blocks = []

    elements = soup.find_all(
        [
            "h1",
            "h2",
            "h3",
            "h4",
            "h5",
            "p",
            "div",
            "article",
        ]
    )

    for element in elements:

        text = clean_text(
            element.get_text(
                " ",
                strip=True
            )
        )

        if not text:
            continue

        if not re.search(
            r"\bEpisode\s+\d{1,4}\b",
            text,
            re.I
        ):
            continue

        if len(text) > 2000:
            continue

        blocks.append(text)

    result = []
    seen = set()

    for block in blocks:

        key = re.sub(
            r"\s+",
            " ",
            block
        ).lower()

        if key in seen:
            continue

        seen.add(key)
        result.append(block)

    return result


def parse_episodes(
    soup: BeautifulSoup,
) -> list[Episode]:

    episodes: dict[int, Episode] = {}

    elements = soup.find_all(
        [
            "h1",
            "h2",
            "h3",
            "h4",
            "h5",
            "p",
            "div",
            "li",
        ]
    )

    for element in elements:

        text = clean_text(
            element.get_text(
                " ",
                strip=True
            )
        )

        if not text:
            continue

        number = parse_episode_number(
            text
        )

        if number is None:
            continue

        if len(text) > 1200:
            continue

        languages = detect_episode_languages(
            text
        )

        title = parse_episode_title(
            text,
            number
        )

        if number not in episodes:

            episodes[number] = Episode(
                number=number,
                title=title,
                languages=languages,
            )

        else:

            existing = episodes[number]

            existing.languages = unique(
                existing.languages
                + languages
            )

            if (
                len(title)
                > len(existing.title)
            ):
                existing.title = title

    # --------------------------------------------------------
    # Second pass
    # --------------------------------------------------------

    page_text = clean_text(
        soup.get_text(
            " ",
            strip=True
        )
    )

    pattern = re.compile(
        r"(Episode\s+\d{1,4}.*?)(?=Episode\s+\d{1,4}|$)",
        re.I
    )

    for match in pattern.finditer(
        page_text
    ):

        block = clean_text(
            match.group(1)
        )

        number = parse_episode_number(
            block
        )

        if number is None:
            continue

        if len(block) > 3000:
            continue

        languages = detect_episode_languages(
            block
        )

        title = parse_episode_title(
            block,
            number
        )

        if number not in episodes:

            episodes[number] = Episode(
                number=number,
                title=title,
                languages=languages,
            )

        else:

            episodes[number].languages = unique(
                episodes[number].languages
                + languages
            )

            if (
                len(title)
                > len(episodes[number].title)
            ):
                episodes[number].title = title

    result = list(
        episodes.values()
    )

    result.sort(
        key=lambda x: x.number
    )

    return result


def calculate_available_episodes(
    episodes: list[Episode],
) -> dict[str, int]:

    counts = {}

    for episode in episodes:

        for language in episode.languages:

            counts[language] = (
                counts.get(language, 0)
                + 1
            )

    return counts


def merge_languages(
    anime: AnimeInfo,
) -> None:

    languages = list(
        anime.languages
    )

    for episode in anime.episodes:

        languages.extend(
            episode.languages
        )

    anime.languages = unique(
        languages
    )

    anime.available_episodes = (
        calculate_available_episodes(
            anime.episodes
        )
    )

    anime.hindi_available = (
        anime.available_episodes.get(
            "Hindi",
            0
        ) > 0
        or anime.hindi_available
    )


def determine_last_episode(
    anime: AnimeInfo,
) -> None:

    if not anime.episodes:
        return

    anime.last_episode = max(
        episode.number
        for episode in anime.episodes
    )


# ============================================================
# FRANCHISE SCRAPING HELPERS
# ============================================================

async def scrape_franchise_series(
    scraper: "AnimeScraper",
    series_name: str,
) -> list[AnimeInfo]:

    if not scraper.session:
        raise RuntimeError(
            "Use AnimeScraper with async context"
        )

    candidates = await find_season_candidates(
        scraper.session,
        series_name,
        limit=8,
    )

    if not candidates:

        candidates = await search_anime(
            scraper.session,
            series_name,
        )

    results = []

    async def scrape_candidate(
        candidate: SearchCandidate,
    ):

        try:

            return await scraper.scrape_url(
                candidate.url
            )

        except Exception as exc:

            logger.warning(
                "Franchise page failed: %s -> %s",
                series_name,
                exc,
            )

            return None

    scraped = await asyncio.gather(
        *[
            scrape_candidate(candidate)
            for candidate in candidates
        ]
    )

    target = normalize_title(
        series_name
    )

    seen = set()

    for anime in scraped:

        if not anime:
            continue

        parsed_title = normalize_title(
            anime.canonical_title
            or anime.title
        )

        if (
            target not in parsed_title
            and parsed_title not in target
        ):
            continue

        if not anime.hindi_available:
            continue

        key = (
            parsed_title,
            anime.season,
            anime.total_episodes,
            anime.last_episode,
        )

        if key in seen:
            continue

        seen.add(key)
        results.append(anime)

    results.sort(
        key=lambda item: (
            normalize_title(
                item.canonical_title
                or item.title
            ),
            item.season or 0,
        )
    )

    return results


async def scrape_franchise_movies(
    scraper: "AnimeScraper",
    franchise: str,
) -> list[str]:

    if not scraper.session:
        raise RuntimeError(
            "Use AnimeScraper with async context"
        )

    names = list(
        FRANCHISE_MOVIES.get(
            franchise,
            []
        )
    )

    display_name = franchise.title()

    candidates = await search_anime(
        scraper.session,
        f"{display_name} movie",
    )

    urls = []
    seen_urls = set()

    for candidate in candidates:

        if candidate.url in seen_urls:
            continue

        seen_urls.add(
            candidate.url
        )

        urls.append(
            candidate
        )

    async def scrape_candidate(
        candidate: SearchCandidate,
    ):

        try:

            return await scraper.scrape_url(
                candidate.url
            )

        except Exception:

            return None

    scraped = await asyncio.gather(
        *[
            scrape_candidate(candidate)
            for candidate in urls[:12]
        ]
    )

    movies = []

    for anime in scraped:

        if not anime:
            continue

        if not anime.hindi_available:
            continue

        title = clean_text(
            anime.canonical_title
            or anime.title
        )

        lower = title.lower()

        if (
            "movie" in lower
            or "film" in lower
            or "broly" in lower
            or "hero" in lower
            or "super hero" in lower
        ):

            movies.append(
                title
            )

    for name in names:

        candidates_for_name = (
            await search_anime(
                scraper.session,
                name,
            )
        )

        if not candidates_for_name:
            continue

        best = candidates_for_name[0]

        if best.score < 55:
            continue

        anime = await scrape_candidate(
            best
        )

        if not anime:
            continue

        if not anime.hindi_available:
            continue

        title = clean_text(
            anime.canonical_title
            or anime.title
        )

        if title:
            movies.append(
                title
            )

    return unique(
        movies
    )


# ============================================================
# PART 6/7
# STATUS / SCHEDULE / MAIN SCRAPER
# ============================================================

def parse_schedule(
    page_text: str,
) -> Optional[str]:

    patterns = [

        r"1\s+New\s+Episode\s+Every\s+([A-Za-z]+)",

        r"New\s+Episode\s+Every\s+([A-Za-z]+)",

        r"Every\s+([A-Za-z]+)",

    ]

    for pattern in patterns:

        match = re.search(
            pattern,
            page_text,
            re.I
        )

        if match:

            day = match.group(1).strip()

            return f"Every {day}"

    if re.search(
        r"New\s+Episode\s+Every\s+Week",
        page_text,
        re.I
    ):
        return "Every Week"

    return None


def parse_explicit_next_episode(
    page_text: str,
) -> Optional[int]:

    patterns = [

        r"Next\s+Episode\s*[:\-]?\s*(\d{1,4})",

        r"Upcoming\s+Episode\s*[:\-]?\s*(\d{1,4})",

    ]

    for pattern in patterns:

        match = re.search(
            pattern,
            page_text,
            re.I
        )

        if match:

            return int(
                match.group(1)
            )

    return None


def detect_completed(
    page_text: str,
) -> bool:

    completed_patterns = [

        r"\bCOMPLETED\b",

        r"\bCOMPLETE\b",

        r"\bSeason\s+Finale\b",

        r"\bSeries\s+Finale\b",

        r"\bFinal\s+Episode\b",

    ]

    for pattern in completed_patterns:

        if re.search(
            pattern,
            page_text,
            re.I
        ):
            return True

    return False


def detect_ongoing(
    page_text: str,
) -> bool:

    ongoing_patterns = [

        r"New\s+Episode\s+Every",

        r"Every\s+(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)",

        r"\bOngoing\b",

        r"\bNext\s+Episode\b",

        r"\bExpected\s+Release\b",

        r"\bAirs?\b",

    ]

    for pattern in ongoing_patterns:

        if re.search(
            pattern,
            page_text,
            re.I
        ):
            return True

    return False


def determine_status(
    anime: AnimeInfo,
    page_text: str,
) -> None:

    completed = detect_completed(
        page_text
    )

    ongoing = detect_ongoing(
        page_text
    )

    if completed and not ongoing:

        anime.status = "completed"

        return

    if ongoing:

        anime.status = "ongoing"

        return

    if (
        anime.total_episodes
        and anime.last_episode
        and anime.last_episode
        >= anime.total_episodes
    ):

        anime.status = "completed"

        return

    if anime.last_episode:

        anime.status = "ongoing"

        return

    anime.status = "unknown"

# ============================================================
# PART 6/7 — MAIN SCRAPER
# ============================================================


class AnimeScraper:

    def __init__(
        self,
        source: str = "DC",
        timeout: int = REQUEST_TIMEOUT,
    ):

        self.source = source

        self.timeout = aiohttp.ClientTimeout(
            total=timeout
        )

        self.session: Optional[
            aiohttp.ClientSession
        ] = None


    # --------------------------------------------------------
    # Context manager
    # --------------------------------------------------------

    async def __aenter__(self):

        self.session = aiohttp.ClientSession(
            timeout=self.timeout,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": (
                    "text/html,application/xhtml+xml,"
                    "application/xml;q=0.9,*/*;q=0.8"
                ),
            },
        )

        return self


    async def __aexit__(
        self,
        exc_type,
        exc_val,
        exc_tb,
    ):

        if self.session:

            await self.session.close()

            self.session = None


    # --------------------------------------------------------
    # Scrape single anime
    # --------------------------------------------------------

    async def scrape_single(
        self,
        query: str,
    ) -> Optional[AnimeInfo]:

        if not self.session:

            raise RuntimeError(
                "AnimeScraper must be used with "
                "'async with AnimeScraper(...)'"
            )

        candidate = await find_anime_page(
            self.session,
            query,
        )

        if not candidate:
            return None

        try:

            anime = await self.scrape_url(
                candidate.url
            )

            if not anime:
                return None

            # If the source returned a slightly different title,
            # keep the actual page title.
            if not anime.title:

                anime.title = (
                    candidate.title
                )

            if not anime.canonical_title:

                anime.canonical_title = (
                    candidate.title
                )

            return anime

        except Exception as exc:

            logger.exception(
                "Failed scraping %s",
                candidate.url
            )

            raise AnimeScraperError(
                str(exc)
            ) from exc


    # --------------------------------------------------------
    # Scrape URL
    # --------------------------------------------------------

    async def scrape_url(
        self,
        url: str,
    ) -> Optional[AnimeInfo]:

        if not self.session:

            raise RuntimeError(
                "AnimeScraper session is not initialized"
            )

        html = await fetch_cached(
            self.session,
            url,
        )

        if not html:

            return None

        anime = parse_page_info(
            html,
            url,
        )

        soup = BeautifulSoup(
            html,
            "html.parser"
        )

        # ----------------------------------------------------
        # Episodes
        # ----------------------------------------------------

        episodes = parse_episodes(
            soup
        )

        anime.episodes = episodes

        # ----------------------------------------------------
        # Episode count
        # ----------------------------------------------------

        if episodes:

            anime.last_episode = max(
                ep.number
                for ep in episodes
            )

            # If total episodes isn't present on the page,
            # use the latest available episode as fallback.
            if not anime.total_episodes:

                anime.total_episodes = (
                    anime.last_episode
                )

        # ----------------------------------------------------
        # Hindi detection
        # ----------------------------------------------------

        page_text = clean_text(
            soup.get_text(
                " ",
                strip=True
            )
        )

        hindi_episode_count = sum(
            1
            for episode in episodes
            if "Hindi" in episode.languages
        )

        if hindi_episode_count > 0:

            anime.hindi_available = True

        if (
            "Hindi" in anime.languages
            and anime.total_episodes
        ):

            anime.hindi_available = True

        # ----------------------------------------------------
        # Language counts
        # ----------------------------------------------------

        merge_languages(
            anime
        )

        # ----------------------------------------------------
        # Status
        # ----------------------------------------------------

        anime.schedule = parse_schedule(
            page_text
        )

        anime.next_episode = (
            parse_explicit_next_episode(
                page_text
            )
        )

        determine_status(
            anime,
            page_text
        )

        determine_last_episode(
            anime
        )

        # ----------------------------------------------------
        # Platform
        # ----------------------------------------------------

        if not anime.platform:

            anime.platform = parse_platforms(
                page_text
            )

        # ----------------------------------------------------
        # Season fallback
        # ----------------------------------------------------

        if anime.season is None:

            anime.season = extract_season(
                soup,
                anime.title,
                page_text,
            )

        return anime


    # --------------------------------------------------------
    # Main scrape
    # --------------------------------------------------------

    async def scrape(
        self,
        query: str,
    ) -> AnimeInfo:

        query = clean_text(
            query
        )

        if not query:

            raise AnimeNotFound(
                "Anime name is empty."
            )

        # ====================================================
        # FRANCHISE MODE
        # ====================================================

        franchise_key = get_franchise_key(
            query
        )

        if franchise_key:

            logger.info(
                "Franchise query detected: %s",
                query
            )

            series_results = []

            # ------------------------------------------------
            # Scrape separately named series
            # ------------------------------------------------

            series_names = FRANCHISE_SERIES.get(
                franchise_key,
                []
            )

            for series_name in series_names:

                try:

                    # First try the exact series name.
                    anime = await self.scrape_single(
                        series_name
                    )

                    if not anime:
                        continue

                    if not anime.hindi_available:

                        logger.info(
                            "Hindi unavailable: %s",
                            series_name
                        )

                        continue

                    # Avoid duplicate entries.
                    duplicate = False

                    for old in series_results:

                        if normalize_title(
                            old.title
                        ) == normalize_title(
                            anime.title
                        ):

                            duplicate = True
                            break

                    if not duplicate:

                        series_results.append(
                            anime
                        )

                except Exception as exc:

                    logger.warning(
                        "Could not scrape franchise "
                        "series %s: %s",
                        series_name,
                        exc
                    )

            # ------------------------------------------------
            # Search additional pages for series/seasons
            # ------------------------------------------------

            expanded_series = []

            for series_name in series_names:

                try:

                    extra = (
                        await scrape_franchise_series(
                            self,
                            series_name
                        )
                    )

                    expanded_series.extend(
                        extra
                    )

                except Exception as exc:

                    logger.debug(
                        "Extra franchise search failed "
                        "for %s: %s",
                        series_name,
                        exc
                    )

            # ------------------------------------------------
            # Merge exact + expanded results
            # ------------------------------------------------

            all_series = (
                series_results
                + expanded_series
            )

            final_series = []

            seen = set()

            for anime in all_series:

                key = (
                    normalize_title(
                        anime.canonical_title
                        or anime.title
                    ),
                    anime.season or 0,
                )

                if key in seen:
                    continue

                seen.add(
                    key
                )

                final_series.append(
                    anime
                )

            # ------------------------------------------------
            # Movies
            # ------------------------------------------------

            movies = []

            try:

                movies = (
                    await scrape_franchise_movies(
                        self,
                        franchise_key
                    )
                )

            except Exception as exc:

                logger.warning(
                    "Movie search failed for %s: %s",
                    query,
                    exc
                )

            # ------------------------------------------------
            # Build combined result
            # ------------------------------------------------

            return build_franchise_info(
                query=query,
                franchise_key=franchise_key,
                series=final_series,
                movies=movies,
            )

        # ====================================================
        # NORMAL SINGLE ANIME MODE
        # ====================================================

        return await self.scrape_single(
            query
        )


# ============================================================
# FORMAT HELPERS
# ============================================================


def format_episode(
    episode: Episode,
) -> str:

    title = episode.title.strip()

    if title:

        line = (
            f"Episode {episode.number}"
            f" — {title}"
        )

    else:

        line = (
            f"Episode {episode.number}"
        )

    if episode.languages:

        language_text = ", ".join(
            episode.languages
        )

        line += (
            f" [{language_text}]"
        )

    return line


def format_episode_list(
    episodes: list[Episode],
) -> list[str]:

    lines = []

    for episode in episodes:

        lines.append(
            format_episode(
                episode
            )
        )

    return lines


def format_language_list(
    languages: list[str],
) -> str:

    if not languages:
        return "Unknown"

    return ", ".join(
        unique(languages)
    )


def format_platform_list(
    platforms,
) -> str:

    if not platforms:
        return "Unknown"

    if isinstance(
        platforms,
        str
    ):

        return platforms

    return ", ".join(
        unique(platforms)
    )


def format_status(
    status: str,
) -> str:

    mapping = {

        "completed":
            "✅ Completed",

        "ongoing":
            "🔄 Ongoing",

        "upcoming":
            "⏳ Upcoming",

        "unknown":
            "❔ Unknown",
    }

    return mapping.get(
        status,
        "❔ Unknown"
    )


# ============================================================
# FORMAT NORMAL ANIME
# ============================================================


def format_single_anime_info(
    anime: AnimeInfo,
) -> str:

    lines = []

    title = (
        anime.title
        or anime.canonical_title
        or "Unknown Anime"
    )

    lines.append(
        f"🎌 {title}"
    )

    lines.append("")

    if anime.season:

        lines.append(
            f"📺 Season: {anime.season}"
        )

    if anime.release_year:

        lines.append(
            f"📅 Year: {anime.release_year}"
        )

    if anime.total_episodes:

        lines.append(
            f"🎞 Total Episodes: "
            f"{anime.total_episodes}"
        )

    if anime.last_episode:

        lines.append(
            f"▶️ Latest Episode: "
            f"{anime.last_episode}"
        )

    if anime.available_episodes:

        hindi_count = (
            anime.available_episodes.get(
                "Hindi",
                0
            )
        )

        if hindi_count:

            lines.append(
                f"🇮🇳 Hindi Episodes: "
                f"{hindi_count}"
            )

    if anime.hindi_available:

        lines.append(
            "🗣 Hindi: ✅ Available"
        )

    else:

        lines.append(
            "🗣 Hindi: ❌ Not Available"
        )

    if anime.languages:

        lines.append(
            f"🌐 Languages: "
            f"{format_language_list(anime.languages)}"
        )

    if anime.platform:

        lines.append(
            f"📡 Platform: "
            f"{format_platform_list(anime.platform)}"
        )

    if anime.dub_by:

        lines.append(
            f"🎙 Dub: {anime.dub_by}"
        )

    if anime.runtime:

        lines.append(
            f"⏱ Runtime: {anime.runtime}"
        )

    if anime.genres:

        lines.append(
            f"🏷 Genre: "
            f"{', '.join(anime.genres)}"
        )

    if anime.status:

        lines.append(
            f"📌 Status: "
            f"{format_status(anime.status)}"
        )

    if anime.schedule:

        lines.append(
            f"🗓 Schedule: "
            f"{anime.schedule}"
        )

    if anime.next_episode:

        lines.append(
            f"⏭ Next Episode: "
            f"{anime.next_episode}"
        )

    if anime.synopsis:

        lines.append("")

        lines.append(
            f"📝 {anime.synopsis}"
        )

    # --------------------------------------------------------
    # Episodes
    # --------------------------------------------------------

    if anime.episodes:

        lines.append("")

        lines.append(
            "📚 Hindi Episodes:"
        )

        hindi_episodes = [

            episode
            for episode in anime.episodes
            if (
                "Hindi"
                in episode.languages
            )
        ]

        if hindi_episodes:

            for episode in hindi_episodes:

                lines.append(
                    format_episode(
                        episode
                    )
                )

        else:

            lines.append(
                "No Hindi episode data found."
            )

    return "\n".join(
        lines
        )
    # ============================================================
# PART 7/7 — FRANCHISE FORMATTER + PUBLIC API
# ============================================================


def format_franchise_info(
    anime: AnimeInfo,
) -> str:

    lines = []

    title = (
        anime.title
        or "Anime Franchise"
    )

    lines.append(
        f"🎌 {title}"
    )

    lines.append("")

    # --------------------------------------------------------
    # Separately named series
    # --------------------------------------------------------

    series = getattr(
        anime,
        "franchise_series",
        []
    )

    if series:

        lines.append(
            "📺 Series:"
        )

        for index, item in enumerate(
            series,
            start=1
        ):

            item_title = (
                item.canonical_title
                or item.title
                or "Unknown"
            )

            lines.append(
                f"{index}. {item_title}"
            )

            if item.season:

                lines.append(
                    f"   └─ Season: "
                    f"{item.season}"
                )

            if item.total_episodes:

                lines.append(
                    f"   └─ Total Episodes: "
                    f"{item.total_episodes}"
                )

            if item.last_episode:

                lines.append(
                    f"   └─ Latest Episode: "
                    f"{item.last_episode}"
                )

            hindi_count = (
                item.available_episodes.get(
                    "Hindi",
                    0
                )
                if item.available_episodes
                else 0
            )

            if hindi_count:

                lines.append(
                    f"   └─ Hindi Episodes: "
                    f"{hindi_count}"
                )

            lines.append(
                "   └─ Hindi: ✅ Available"
            )

            # ------------------------------------------------
            # Show all Hindi episodes
            # ------------------------------------------------

            hindi_episodes = [

                episode
                for episode in item.episodes
                if (
                    "Hindi"
                    in episode.languages
                )
            ]

            if hindi_episodes:

                lines.append(
                    "   └─ Episodes:"
                )

                for episode in hindi_episodes:

                    episode_title = (
                        episode.title.strip()
                    )

                    if episode_title:

                        lines.append(
                            f"      • "
                            f"Episode "
                            f"{episode.number}"
                            f" — "
                            f"{episode_title}"
                        )

                    else:

                        lines.append(
                            f"      • "
                            f"Episode "
                            f"{episode.number}"
                        )

    else:

        lines.append(
            "📺 Series:"
        )

        lines.append(
            "No Hindi series found."
        )

    # --------------------------------------------------------
    # Movies
    # --------------------------------------------------------

    movies = getattr(
        anime,
        "franchise_movies",
        []
    )

    if movies:

        lines.append("")

        lines.append(
            "🎬 Movies:"
        )

        for index, movie in enumerate(
            movies,
            start=1
        ):

            lines.append(
                f"{index}. {movie}"
            )

    return "\n".join(
        lines
    )


# ============================================================
# MAIN FORMAT FUNCTION
# ============================================================

def format_anime_info(
    anime: AnimeInfo,
) -> str:

    """
    Format AnimeInfo for the Telegram bot.

    Franchise searches are displayed as:

        📺 Series
        🎬 Movies

    Normal anime searches retain the normal output.

    NOTE:
    Poster URL is intentionally NOT printed.
    """

    franchise_key = getattr(
        anime,
        "franchise_key",
        None
    )

    franchise_series = getattr(
        anime,
        "franchise_series",
        []
    )

    franchise_movies = getattr(
        anime,
        "franchise_movies",
        []
    )

    if (
        franchise_key
        or franchise_series
        or franchise_movies
    ):

        return format_franchise_info(
            anime
        )

    return format_single_anime_info(
        anime
    )


# ============================================================
# BOT HELPER
# ============================================================

async def get_anime_info(
    query: str,
) -> AnimeInfo:

    async with AnimeScraper(
        source="DC"
    ) as scraper:

        return await scraper.scrape(
            query
        )


# ============================================================
# SAFE BOT HELPER
# ============================================================

async def get_formatted_anime_info(
    query: str,
) -> str:

    try:

        anime = await get_anime_info(
            query
        )

        if not anime:

            return (
                "❌ Anime information "
                "not found."
            )

        return format_anime_info(
            anime
        )

    except AnimeNotFound:

        return (
            "❌ Anime not found.\n\n"
            "Anime ka exact naam "
            "try karo."
        )

    except asyncio.TimeoutError:

        return (
            "⏳ Request timeout.\n\n"
            "Thodi der baad dobara "
            "try karo."
        )

    except Exception as exc:

        logger.exception(
            "Anime lookup failed: %s",
            query
        )

        return (
            "❌ Error: Unable to fetch "
            "anime information.\n\n"
            "Ye temporary problem ho "
            "sakti hai.\n"
            "Thodi der baad dobara "
            "try karo."
        )


# ============================================================
# OPTIONAL CLI TEST
# ============================================================

async def _cli_test(
    query: str,
) -> None:

    try:

        result = (
            await get_formatted_anime_info(
                query
            )
        )

        print(result)

    except KeyboardInterrupt:

        print(
            "\nStopped."
        )


# ============================================================
# MODULE ENTRY POINT
# ============================================================

if __name__ == "__main__":

    import sys

    if len(sys.argv) > 1:

        query = " ".join(
            sys.argv[1:]
        )

        asyncio.run(
            _cli_test(
                query
            )
        )

    else:

        print(
            "Usage: python anime_scraper.py "
            "<anime name>"
)
