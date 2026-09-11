# ============================================================
# ANIME HINDI INFO SCRAPER
# PART 1/7
#
# CORE MODELS + CONSTANTS
# ============================================================

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
import hashlib

from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional
from urllib.parse import quote_plus, urljoin, urlparse

import aiohttp
from bs4 import BeautifulSoup

try:
    from rapidfuzz import fuzz
except ImportError:
    fuzz = None


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

logger = logging.getLogger("AnimeHindiBot")


# ============================================================
# WEBSITE
# ============================================================

BASE_URL = "https://www.rareanimes.mov"

SEARCH_URL = (
    BASE_URL
    + "/?s={query}"
)

SOURCE_NAME = "DC"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/140.0.0.0 Safari/537.36"
)

HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": (
        "text/html,application/xhtml+xml,"
        "application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": (
        "en-US,en;q=0.9"
    ),
    "Cache-Control": "no-cache",
}

REQUEST_TIMEOUT = 20


# ============================================================
# CACHE
# ============================================================

CACHE_DIR = Path(
    os.getenv(
        "ANIME_CACHE_DIR",
        "anime_cache"
    )
)

CACHE_DIR.mkdir(
    parents=True,
    exist_ok=True
)

CACHE_TTL = 15 * 60


# ============================================================
# LANGUAGE DEFINITIONS
# ============================================================

LANGUAGE_NAMES = [
    "Hindi",
    "English",
    "Tamil",
    "Telugu",
    "Bengali",
    "Malayalam",
    "Kannada",
    "Marathi",
    "Gujarati",
    "Punjabi",
    "Japanese",
    "Korean",
    "Chinese",
    "Spanish",
    "French",
    "German",
    "Arabic",
]


# ============================================================
# GENERIC BAD VALUES
# ============================================================

BAD_VALUES = {
    "",
    "unknown",
    "n/a",
    "na",
    "none",
    "null",
    "-",
    "--",
    "other",
    "other website",
    "other websites",
    "other platform",
    "other platforms",
}


# ============================================================
# EPISODE MODEL
# ============================================================

@dataclass
class EpisodeInfo:

    number: int

    title: str = ""

    languages: list[str] = field(
        default_factory=list
    )

    hindi_available: bool = False

    release_date: Optional[str] = None


# ============================================================
# SEASON MODEL
# ============================================================

@dataclass
class SeasonInfo:

    series_title: str = ""

    season_number: Optional[int] = None

    season_title: str = ""

    url: str = ""

    poster_url: Optional[str] = None

    episode_count: Optional[int] = None

    hindi_episode_count: int = 0

    languages: list[str] = field(
        default_factory=list
    )

    platform: list[str] = field(
        default_factory=list
    )

    release_year: Optional[int] = None

    status: str = "unknown"

    episodes: list[EpisodeInfo] = field(
        default_factory=list
    )

    hindi_available: bool = False

    dub_by: Optional[str] = None

    last_episode: Optional[int] = None

    last_release: Optional[str] = None

    next_episode: Optional[int] = None

    expected_release: Optional[str] = None

    schedule: Optional[str] = None

    studio: Optional[str] = None


# ============================================================
# SERIES MODEL
# ============================================================

@dataclass
class SeriesInfo:

    title: str = ""

    normalized_title: str = ""

    aliases: list[str] = field(
        default_factory=list
    )

    url: Optional[str] = None

    poster_url: Optional[str] = None

    hindi_available: bool = False

    platform: list[str] = field(
        default_factory=list
    )

    languages: list[str] = field(
        default_factory=list
    )

    seasons: list[SeasonInfo] = field(
        default_factory=list
    )

    total_hindi_episodes: int = 0

    total_episodes: int = 0

    status: str = "unknown"

    dub_by: Optional[str] = None

    studio: Optional[str] = None


# ============================================================
# FRANCHISE MODEL
#
# This is the IMPORTANT new model.
#
# Example:
#
# Dragon Ball
#   ├── Dragon Ball
#   ├── Dragon Ball Z
#   ├── Dragon Ball Super
#   ├── Dragon Ball DAIMA
#   └── Movies
#
# Naruto
#   ├── Naruto
#   └── Naruto Shippuden
#
# ============================================================

@dataclass
class FranchiseInfo:

    name: str = ""

    normalized_name: str = ""

    poster_url: Optional[str] = None

    hindi_available: bool = False

    series: list[SeriesInfo] = field(
        default_factory=list
    )

    movies: list[SeriesInfo] = field(
        default_factory=list
    )

    total_hindi_episodes: int = 0

    total_episodes: int = 0


# ============================================================
# SINGLE ANIME RESULT
#
# Used when user searches:
#
# /anime Naruto Shippuden
# /anime Dragon Ball Super
#
# ============================================================

@dataclass
class AnimeInfo:

    title: str = ""

    canonical_title: str = ""

    source: str = SOURCE_NAME

    source_url: Optional[str] = None

    poster_url: Optional[str] = None

    hindi_available: bool = False

    platform: list[str] = field(
        default_factory=list
    )

    season: Optional[int] = None

    episode_count: Optional[int] = None

    hindi_episode_count: int = 0

    total_series_episodes: Optional[int] = None

    languages: list[str] = field(
        default_factory=list
    )

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

    genres: list[str] = field(
        default_factory=list
    )

    synopsis: Optional[str] = None

    episodes: list[EpisodeInfo] = field(
        default_factory=list
    )

    # True when this result represents
    # multiple related series.
    is_franchise: bool = False

    franchise: Optional[FranchiseInfo] = None

    scraped_at: float = field(
        default_factory=time.time
    )


# ============================================================
# SEARCH RESULT MODEL
# ============================================================

@dataclass
class SearchResult:

    title: str = ""

    url: str = ""

    normalized_title: str = ""

    series_title: str = ""

    normalized_series_title: str = ""

    season_number: Optional[int] = None

    is_movie: bool = False

    score: float = 0.0

    hindi_hint: bool = False


# ============================================================
# EXCEPTIONS
# ============================================================

class AnimeNotFound(Exception):
    pass


class ScraperError(Exception):
    pass


# ============================================================
# CACHE HELPERS
# ============================================================

def make_cache_key(
    value: str
) -> str:

    return hashlib.sha256(
        value.encode(
            "utf-8"
        )
    ).hexdigest()


def get_cache_path(
    url: str
) -> Path:

    return (
        CACHE_DIR
        / f"{make_cache_key(url)}.json"
    )


# ============================================================
# BASIC TEXT HELPERS
# ============================================================

def clean_text(
    value: Optional[str]
) -> str:

    if not value:
        return ""

    value = (
        value
        .replace("\xa0", " ")
        .replace("\r", " ")
        .replace("\n", " ")
        .replace("\t", " ")
    )

    return re.sub(
        r"\s+",
        " ",
        value
    ).strip()


def normalize_title(
    value: Optional[str]
) -> str:

    value = clean_text(
        value
    ).lower()

    # Remove bracket information.
    value = re.sub(
        r"\[[^\]]*\]",
        " ",
        value
    )

    value = re.sub(
        r"\([^)]*\)",
        " ",
        value
    )

    # Common separators.
    value = (
        value
        .replace("&", " and ")
        .replace(":", " ")
        .replace("–", " ")
        .replace("—", " ")
        .replace("_", " ")
        .replace("-", " ")
    )

    # Remove punctuation.
    value = re.sub(
        r"[^a-z0-9]+",
        " ",
        value
    )

    return re.sub(
        r"\s+",
        " ",
        value
    ).strip()


def unique_strings(
    values: list[str]
) -> list[str]:

    result = []
    seen = set()

    for value in values:

        value = clean_text(
            value
        )

        if not value:
            continue

        key = value.lower()

        if key in seen:
            continue

        seen.add(key)
        result.append(value)

    return result


# ============================================================
# NUMBER HELPERS
# ============================================================

def extract_numbers(
    value: Optional[str]
) -> list[int]:

    if not value:
        return []

    return [
        int(number)
        for number in re.findall(
            r"\b\d{1,5}\b",
            value
        )
    ]


def first_number(
    value: Optional[str]
) -> Optional[int]:

    numbers = extract_numbers(
        value
    )

    return (
        numbers[0]
        if numbers
        else None
    )


# ============================================================
# SEASON DETECTION
# ============================================================

def extract_season_number(
    value: Optional[str]
) -> Optional[int]:

    if not value:
        return None

    patterns = [

        r"\bseason\s*[-:]?\s*(\d{1,3})\b",

        r"\bs\s*[-:]?\s*(\d{1,3})\b",

        r"\bseries\s*[-:]?\s*(\d{1,3})\b",

    ]

    for pattern in patterns:

        match = re.search(
            pattern,
            value,
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


# ============================================================
# MOVIE DETECTION
# ============================================================

def looks_like_movie(
    title: str
) -> bool:

    normalized = normalize_title(
        title
    )

    movie_words = (
        "movie",
        "film",
        "the movie",
        "feature",
    )

    if any(
        word in normalized
        for word in movie_words
    ):
        return True

    # Common movie naming pattern:
    # "Dragon Ball Super Broly"
    # does NOT automatically become a movie.
    #
    # Actual page parsing in PART 4/5 will verify
    # whether the page contains episode blocks.

    return False


# ============================================================
# SERIES TITLE EXTRACTION
# ============================================================

def extract_series_title(
    title: str
) -> str:

    title = clean_text(
        title
    )

    # Remove season notation.
    title = re.sub(
        r"\bseason\s*[-:]?\s*\d{1,3}\b",
        " ",
        title,
        flags=re.I
    )

    title = re.sub(
        r"\bs\s*[-:]?\s*\d{1,3}\b",
        " ",
        title,
        flags=re.I
    )

    title = re.sub(
        r"\bseries\s*[-:]?\s*\d{1,3}\b",
        " ",
        title,
        flags=re.I
    )

    # Remove common RareAnimes page suffixes.
    title = re.sub(
        r"\bepisodes?\b.*$",
        "",
        title,
        flags=re.I
    )

    title = re.sub(
        r"\bhindi\s+dubbed\b.*$",
        "",
        title,
        flags=re.I
    )

    title = re.sub(
        r"\bhindi\s+episodes?\b.*$",
        "",
        title,
        flags=re.I
    )

    title = re.sub(
        r"\bdownload\b.*$",
        "",
        title,
        flags=re.I
    )

    title = re.sub(
        r"\bwatch\b.*$",
        "",
        title,
        flags=re.I
    )

    return clean_text(
        title
    )


# ============================================================
# HINDI DETECTION
# ============================================================

def contains_hindi(
    value: Optional[str]
) -> bool:

    if not value:
        return False

    return bool(
        re.search(
            r"\bhindi\b",
            value,
            re.I
        )
    )


# ============================================================
# DATA CONVERSION
# ============================================================

def dataclass_to_dict(
    value
) -> dict:

    return asdict(
        value
    )


# ============================================================
# END OF PART 1
# ============================================================

# ============================================================
# PART 2/7
# HTTP / CACHE / SEARCH PAGE CRAWLER
# ============================================================


# ============================================================
# HTTP SESSION
# ============================================================

async def create_session() -> aiohttp.ClientSession:
    """
    Creates the HTTP session used by the scraper.
    """

    timeout = aiohttp.ClientTimeout(
        total=REQUEST_TIMEOUT,
        connect=10,
        sock_read=REQUEST_TIMEOUT
    )

    connector = aiohttp.TCPConnector(
        limit=10,
        limit_per_host=5,
        ssl=False
    )

    return aiohttp.ClientSession(
        headers=HEADERS,
        timeout=timeout,
        connector=connector
    )


# ============================================================
# HTTP FETCH
# ============================================================

async def fetch(
    session: aiohttp.ClientSession,
    url: str
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
            f"Request timeout: {url}"
        )

    except aiohttp.ClientError as exc:

        raise ScraperError(
            f"Request failed: {url} -> {exc}"
        )


# ============================================================
# CACHE READ
# ============================================================

def read_cache(
    url: str
) -> Optional[str]:

    path = get_cache_path(
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

        saved_at = float(
            data.get(
                "saved_at",
                0
            )
        )

        if (
            time.time()
            - saved_at
            > CACHE_TTL
        ):
            return None

        html = data.get(
            "html"
        )

        if isinstance(
            html,
            str
        ):
            return html

    except Exception as exc:

        logger.debug(
            "Cache read failed: %s",
            exc
        )

    return None


# ============================================================
# CACHE WRITE
# ============================================================

def write_cache(
    url: str,
    html: str
) -> None:

    path = get_cache_path(
        url
    )

    try:

        path.write_text(
            json.dumps(
                {
                    "saved_at": time.time(),
                    "html": html,
                },
                ensure_ascii=False
            ),
            encoding="utf-8"
        )

    except Exception as exc:

        logger.debug(
            "Cache write failed: %s",
            exc
        )


# ============================================================
# FETCH WITH CACHE
# ============================================================

async def fetch_cached(
    session: aiohttp.ClientSession,
    url: str
) -> str:

    cached = read_cache(
        url
    )

    if cached is not None:

        logger.info(
            "Cache hit: %s",
            url
        )

        return cached

    html = await fetch(
        session,
        url
    )

    write_cache(
        url,
        html
    )

    return html


# ============================================================
# URL HELPERS
# ============================================================

def normalize_url(
    url: str
) -> str:

    url = urljoin(
        BASE_URL,
        url
    )

    parsed = urlparse(
        url
    )

    # Remove fragment.
    return parsed._replace(
        fragment=""
    ).geturl().rstrip("/")


def is_rare_animes_url(
    url: str
) -> bool:

    try:

        parsed = urlparse(
            url
        )

        host = (
            parsed.netloc
            .lower()
            .replace(
                "www.",
                ""
            )
        )

        return host == (
            urlparse(BASE_URL)
            .netloc
            .lower()
            .replace(
                "www.",
                ""
            )
        )

    except Exception:
        return False


# ============================================================
# SEARCH URL
# ============================================================

def make_search_url(
    query: str,
    page: int = 1
) -> str:

    query = quote_plus(
        clean_text(query)
    )

    if page <= 1:

        return (
            BASE_URL
            + "/?s="
            + query
        )

    # WordPress pagination.
    return (
        BASE_URL
        + f"/page/{page}/?s={query}"
    )


# ============================================================
# SEARCH LINK FILTER
# ============================================================

def looks_like_anime_result(
    text: str,
    href: str
) -> bool:

    text_norm = normalize_title(
        text
    )

    href_norm = href.lower()

    if not text_norm:
        return False

    if not is_rare_animes_url(
        href
    ):
        return False

    # Ignore site navigation.
    ignored_text = {
        "home",
        "contact",
        "about",
        "privacy policy",
        "dmca",
        "login",
        "register",
        "search",
        "menu",
        "next",
        "previous",
        "older posts",
        "newer posts",
    }

    if text_norm in ignored_text:
        return False

    # Ignore obvious category/tag links.
    ignored_path_words = (
        "/category/",
        "/tag/",
        "/author/",
        "/feed/",
        "/wp-json/",
        "/wp-admin/",
        "/page/",
    )

    if any(
        word in href_norm
        for word in ignored_path_words
    ):
        return False

    # Anime pages on this site generally contain
    # meaningful title text.
    if len(text_norm) < 2:
        return False

    return True


# ============================================================
# SEARCH RESULT EXTRACTION
# ============================================================

def extract_search_results(
    soup: BeautifulSoup
) -> list[SearchResult]:

    results = []

    seen_urls = set()

    # --------------------------------------------------------
    # First priority: article/post cards.
    # --------------------------------------------------------

    article_nodes = soup.select(
        "article"
    )

    # --------------------------------------------------------
    # If theme does not expose articles, use common
    # result containers.
    # --------------------------------------------------------

    if not article_nodes:

        article_nodes = soup.select(
            ".post, "
            ".item, "
            ".post-item, "
            ".anime-item, "
            ".search-item, "
            ".entry"
        )

    # --------------------------------------------------------
    # Extract links from result containers.
    # --------------------------------------------------------

    for node in article_nodes:

        links = node.select(
            "a[href]"
        )

        if not links:
            continue

        best_link = None

        for link in links:

            href = link.get(
                "href",
                ""
            )

            text = clean_text(
                link.get_text(
                    " ",
                    strip=True
                )
            )

            if not looks_like_anime_result(
                text,
                href
            ):
                continue

            # Prefer links with a substantial title.
            if (
                best_link is None
                or len(text)
                > len(
                    clean_text(
                        best_link.get_text(
                            " ",
                            strip=True
                        )
                    )
                )
            ):
                best_link = link

        if best_link is None:
            continue

        href = normalize_url(
            best_link.get(
                "href",
                ""
            )
        )

        title = clean_text(
            best_link.get_text(
                " ",
                strip=True
            )
        )

        if not href or not title:
            continue

        if href in seen_urls:
            continue

        seen_urls.add(
            href
        )

        series_title = extract_series_title(
            title
        )

        result = SearchResult(
            title=title,
            url=href,
            normalized_title=normalize_title(
                title
            ),
            series_title=series_title,
            normalized_series_title=normalize_title(
                series_title
            ),
            season_number=extract_season_number(
                title
            ),
            is_movie=looks_like_movie(
                title
            ),
            hindi_hint=contains_hindi(
                title
            )
        )

        results.append(
            result
        )

    # --------------------------------------------------------
    # FALLBACK:
    # Some pages/themes don't wrap results in article tags.
    #
    # Scan headings + nearby links.
    # --------------------------------------------------------

    if not results:

        selectors = (
            "h1 a[href]",
            "h2 a[href]",
            "h3 a[href]",
            "h4 a[href]",
            ".entry-title a[href]",
            ".post-title a[href]",
            "a[href]",
        )

        for selector in selectors:

            for link in soup.select(
                selector
            ):

                href = link.get(
                    "href",
                    ""
                )

                title = clean_text(
                    link.get_text(
                        " ",
                        strip=True
                    )
                )

                if not looks_like_anime_result(
                    title,
                    href
                ):
                    continue

                href = normalize_url(
                    href
                )

                if href in seen_urls:
                    continue

                seen_urls.add(
                    href
                )

                series_title = extract_series_title(
                    title
                )

                results.append(
                    SearchResult(
                        title=title,
                        url=href,
                        normalized_title=normalize_title(
                            title
                        ),
                        series_title=series_title,
                        normalized_series_title=normalize_title(
                            series_title
                        ),
                        season_number=extract_season_number(
                            title
                        ),
                        is_movie=looks_like_movie(
                            title
                        ),
                        hindi_hint=contains_hindi(
                            title
                        )
                    )
                )

    return results


# ============================================================
# PAGINATION DETECTION
# ============================================================

def extract_next_search_page(
    soup: BeautifulSoup
) -> Optional[int]:

    # Standard next link.
    next_link = soup.select_one(
        'a[rel="next"]'
    )

    if next_link:

        href = next_link.get(
            "href",
            ""
        )

        match = re.search(
            r"/page/(\d+)/",
            href
        )

        if match:
            return int(
                match.group(1)
            )

    # Generic "Next" text.
    for link in soup.select(
        "a[href]"
    ):

        text = clean_text(
            link.get_text(
                " ",
                strip=True
            )
        ).lower()

        if text in {
            "next",
            "next page",
            "older posts",
            "older",
            "»",
            "›",
        }:

            href = link.get(
                "href",
                ""
            )

            match = re.search(
                r"/page/(\d+)/",
                href
            )

            if match:
                return int(
                    match.group(1)
                )

    return None


# ============================================================
# SEARCH ONE PAGE
# ============================================================

async def search_page(
    session: aiohttp.ClientSession,
    query: str,
    page: int = 1
) -> tuple[
    list[SearchResult],
    Optional[int]
]:

    url = make_search_url(
        query,
        page
    )

    try:

        html = await fetch_cached(
            session,
            url
        )

    except Exception as exc:

        logger.warning(
            "Search page failed: %s",
            exc
        )

        return [], None

    soup = BeautifulSoup(
        html,
        "html.parser"
    )

    results = extract_search_results(
        soup
    )

    next_page = extract_next_search_page(
        soup
    )

    logger.info(
        "Search '%s' page %s -> %d results",
        query,
        page,
        len(results)
    )

    return results, next_page


# ============================================================
# SEARCH MULTIPLE PAGES
#
# IMPORTANT:
# We don't stop after the first search page.
#
# This is what allows:
#
# Dragon Ball
#   -> Dragon Ball
#   -> Dragon Ball Z
#   -> Dragon Ball Super
#   -> Dragon Ball DAIMA
#   -> etc.
#
# Naruto
#   -> Naruto Season 1
#   -> Naruto Season 2
#   -> ...
#   -> Naruto Shippuden Season 1
#   -> ...
#
# ============================================================

async def search_all_pages(
    session: aiohttp.ClientSession,
    query: str,
    max_pages: int = 8
) -> list[SearchResult]:

    query = clean_text(
        query
    )

    if not query:
        return []

    all_results = []

    seen_urls = set()

    page = 1

    while page <= max_pages:

        results, next_page = (
            await search_page(
                session,
                query,
                page
            )
        )

        if not results and page > 1:
            break

        for result in results:

            if result.url in seen_urls:
                continue

            seen_urls.add(
                result.url
            )

            all_results.append(
                result
            )

        # No pagination -> finished.
        if next_page is None:
            break

        # Prevent broken pagination loops.
        if next_page <= page:
            break

        page = next_page

        # Small delay to avoid hammering server.
        await asyncio.sleep(
            0.15
        )

    logger.info(
        "Search '%s' -> total unique results: %d",
        query,
        len(all_results)
    )

    return all_results


# ============================================================
# SEARCH MULTIPLE QUERIES
#
# This is another IMPORTANT part.
#
# A franchise may have different names.
#
# Example:
#
# User searches:
#     Dragon Ball
#
# We can search:
#     Dragon Ball
#     Dragon Ball Z
#     Dragon Ball Super
#     Dragon Ball DAIMA
#
# But this is NOT hardcoded to Dragon Ball.
# It derives candidate names from search results first.
#
# ============================================================

async def discover_related_results(
    session: aiohttp.ClientSession,
    query: str,
    max_pages: int = 8
) -> list[SearchResult]:

    query = clean_text(
        query
    )

    if not query:
        return []

    primary_results = await search_all_pages(
        session,
        query,
        max_pages=max_pages
    )

    if not primary_results:
        return []

    # --------------------------------------------------------
    # Collect series names discovered from the primary search.
    # --------------------------------------------------------

    discovered_titles = []

    for result in primary_results:

        series_title = clean_text(
            result.series_title
        )

        if not series_title:
            continue

        if series_title not in discovered_titles:

            discovered_titles.append(
                series_title
            )

    # --------------------------------------------------------
    # Only search discovered names that are reasonably
    # related to the original query.
    # --------------------------------------------------------

    query_norm = normalize_title(
        query
    )

    related_queries = []

    for title in discovered_titles:

        title_norm = normalize_title(
            title
        )

        similarity = title_similarity(
            query_norm,
            title_norm
        )

        if (
            similarity >= 55
            or query_norm in title_norm
            or title_norm in query_norm
        ):

            if title not in related_queries:
                related_queries.append(
                    title
                )

    # --------------------------------------------------------
    # Don't create an endless search chain.
    # Primary query + strongest discovered names.
    # --------------------------------------------------------

    related_queries = related_queries[:12]

    combined = []
    seen_urls = set()

    for result in primary_results:

        if result.url in seen_urls:
            continue

        seen_urls.add(
            result.url
        )

        combined.append(
            result
        )

    # Search related series names.
    for related_query in related_queries:

        # Don't search the exact same query twice.
        if normalize_title(
            related_query
        ) == query_norm:
            continue

        related_results = await search_all_pages(
            session,
            related_query,
            max_pages=max_pages
        )

        for result in related_results:

            if result.url in seen_urls:
                continue

            seen_urls.add(
                result.url
            )

            combined.append(
                result
            )

        # Keep request count controlled.
        if len(combined) >= 250:
            break

    logger.info(
        "Related discovery '%s' -> %d results",
        query,
        len(combined)
    )
    return combined


# ============================================================
# END OF PART 2
# ============================================================

# ============================================================
# PART 3/7
# TITLE RESOLVER / FRANCHISE GROUPING
# ============================================================


# ============================================================
# TITLE SIMILARITY
# ============================================================

def title_similarity(
    first: str,
    second: str
) -> float:

    first = normalize_title(
        first
    )

    second = normalize_title(
        second
    )

    if not first or not second:
        return 0.0

    if first == second:
        return 100.0

    if fuzz is not None:

        ratio = fuzz.ratio(
            first,
            second
        )

        partial = fuzz.partial_ratio(
            first,
            second
        )

        token = fuzz.token_set_ratio(
            first,
            second
        )

        return max(
            ratio,
            partial,
            token
        )

    first_words = set(
        first.split()
    )

    second_words = set(
        second.split()
    )

    if not first_words or not second_words:
        return 0.0

    common = (
        first_words
        & second_words
    )

    return (
        len(common)
        / max(
            len(first_words),
            len(second_words)
        )
        * 100
    )


# ============================================================
# REMOVE SEASON FROM TITLE
# ============================================================

def remove_season_text(
    title: str
) -> str:

    title = clean_text(
        title
    )

    patterns = [

        r"\bseason\s*[-:]?\s*\d{1,3}\b",

        r"\bs\s*[-:]?\s*\d{1,3}\b",

        r"\bseries\s*[-:]?\s*\d{1,3}\b",

    ]

    for pattern in patterns:

        title = re.sub(
            pattern,
            " ",
            title,
            flags=re.I
        )

    return clean_text(
        title
    )


# ============================================================
# REMOVE WEBSITE SUFFIXES
# ============================================================

def clean_result_title(
    title: str
) -> str:

    title = clean_text(
        title
    )

    # Remove common website naming noise.
    patterns = [

        r"\bhindi\s+dubbed\b.*$",

        r"\bhindi\s+subbed\b.*$",

        r"\bhindi\s+episodes?\b.*$",

        r"\bepisodes?\s+download\b.*$",

        r"\bdownload\s+hd\b.*$",

        r"\bwatch\s+online\b.*$",

        r"\bdownload\b.*$",

    ]

    for pattern in patterns:

        title = re.sub(
            pattern,
            "",
            title,
            flags=re.I
        )

    return clean_text(
        title
    )


# ============================================================
# CANONICAL SERIES TITLE
# ============================================================

def canonical_series_title(
    title: str
) -> str:

    title = clean_result_title(
        title
    )

    title = remove_season_text(
        title
    )

    return clean_text(
        title
    )


# ============================================================
# FRANCHISE BASE TITLE
#
# This deliberately does NOT use a hardcoded list such as:
#
# Naruto -> Naruto Shippuden
# Dragon Ball -> Dragon Ball Z
#
# Instead, it uses words actually found in the search results.
#
# This means the same mechanism can work for other franchises.
# ============================================================

def franchise_base_title(
    title: str
) -> str:

    title = canonical_series_title(
        title
    )

    normalized = normalize_title(
        title
    )

    if not normalized:
        return ""

    words = normalized.split()

    if len(words) <= 1:
        return normalized

    # --------------------------------------------------------
    # Common franchise naming pattern:
    #
    # Main title + subtitle
    #
    # Examples:
    #
    # Naruto Shippuden
    # Dragon Ball Super
    # Dragon Ball Z
    # Sword Art Online
    #
    # We do NOT blindly remove the final word.
    #
    # Instead, grouping is later confirmed using search
    # relationships and title overlap.
    # --------------------------------------------------------

    return normalized


# ============================================================
# QUERY MATCH
# ============================================================

def result_matches_query(
    query: str,
    result: SearchResult
) -> bool:

    query_clean = remove_season_text(
        query
    )

    query_norm = normalize_title(
        query_clean
    )

    title_norm = normalize_title(
        result.series_title
    )

    if not query_norm or not title_norm:
        return False

    # Exact.
    if query_norm == title_norm:
        return True

    # One is a complete prefix of the other.
    if (
        title_norm.startswith(
            query_norm + " "
        )
        or query_norm.startswith(
            title_norm + " "
        )
    ):
        return True

    # Token overlap.
    query_words = set(
        query_norm.split()
    )

    title_words = set(
        title_norm.split()
    )

    common = (
        query_words
        & title_words
    )

    if not common:
        return False

    overlap = (
        len(common)
        / min(
            len(query_words),
            len(title_words)
        )
    )

    return overlap >= 0.75


# ============================================================
# SEARCH RESULT SCORING
# ============================================================

def score_result(
    query: str,
    result: SearchResult
) -> float:

    query_clean = remove_season_text(
        query
    )

    query_norm = normalize_title(
        query_clean
    )

    title_norm = normalize_title(
        result.series_title
    )

    score = title_similarity(
        query_norm,
        title_norm
    )

    # --------------------------------------------------------
    # Exact title = strongest.
    # --------------------------------------------------------

    if query_norm == title_norm:
        score += 100

    # --------------------------------------------------------
    # Explicit season request.
    # --------------------------------------------------------

    requested_season = extract_season_number(
        query
    )

    if requested_season is not None:

        if (
            result.season_number
            == requested_season
        ):
            score += 150

        elif result.season_number is not None:

            distance = abs(
                result.season_number
                - requested_season
            )

            score -= min(
                100,
                distance * 20
            )

    # --------------------------------------------------------
    # Protect exact parent title.
    #
    # "Naruto" should beat
    # "Naruto Shippuden"
    #
    # "Dragon Ball" should beat
    # "Dragon Ball Super"
    #
    # when the user asks the exact base title.
    # --------------------------------------------------------

    if (
        query_norm
        and title_norm != query_norm
        and title_norm.startswith(
            query_norm + " "
        )
    ):
        score -= 35

    # --------------------------------------------------------
    # Hindi result gets a small preference.
    # --------------------------------------------------------

    if result.hindi_hint:
        score += 8

    return score


def rank_results(
    query: str,
    results: list[SearchResult]
) -> list[SearchResult]:

    for result in results:

        result.score = score_result(
            query,
            result
        )

    results.sort(
        key=lambda item: item.score,
        reverse=True
    )

    return results


# ============================================================
# DEDUPLICATE SEARCH RESULTS
# ============================================================

def deduplicate_results(
    results: list[SearchResult]
) -> list[SearchResult]:

    output = []

    seen_urls = set()

    for result in results:

        url = normalize_url(
            result.url
        )

        if not url:
            continue

        if url in seen_urls:
            continue

        seen_urls.add(
            url
        )

        result.url = url

        output.append(
            result
        )

    return output


# ============================================================
# FIND EXACT SERIES
# ============================================================

def find_exact_series_results(
    query: str,
    results: list[SearchResult]
) -> list[SearchResult]:

    query_clean = remove_season_text(
        query
    )

    query_norm = normalize_title(
        query_clean
    )

    exact = []

    for result in results:

        title_norm = normalize_title(
            result.series_title
        )

        if title_norm == query_norm:
            exact.append(
                result
            )

    return exact


# ============================================================
# FIND SEASON RESULTS
# ============================================================

def find_requested_season(
    query: str,
    results: list[SearchResult]
) -> Optional[SearchResult]:

    requested_season = extract_season_number(
        query
    )

    if requested_season is None:
        return None

    exact_series = find_exact_series_results(
        query,
        results
    )

    if exact_series:

        for result in exact_series:

            if (
                result.season_number
                == requested_season
            ):
                return result

    # If exact series wasn't found,
    # use strongest matching series.
    ranked = rank_results(
        query,
        results
    )

    for result in ranked:

        if (
            result.season_number
            == requested_season
        ):
            return result

    return None


# ============================================================
# RESOLVE SINGLE ANIME
# ============================================================

def resolve_single_result(
    query: str,
    results: list[SearchResult]
) -> SearchResult:

    if not results:

        raise AnimeNotFound(
            f"No results found for: {query}"
        )

    # --------------------------------------------------------
    # Season request gets priority.
    # --------------------------------------------------------

    season_result = find_requested_season(
        query,
        results
    )

    if season_result is not None:

        return season_result

    # --------------------------------------------------------
    # Exact title match.
    # --------------------------------------------------------

    exact = find_exact_series_results(
        query,
        results
    )

    if exact:

        # Prefer a result that explicitly hints Hindi.
        exact.sort(
            key=lambda item: (
                not item.hindi_hint,
                item.season_number
                if item.season_number is not None
                else 999,
            )
        )

        return exact[0]

    # --------------------------------------------------------
    # Fuzzy ranking.
    # --------------------------------------------------------

    ranked = rank_results(
        query,
        results
    )

    best = ranked[0]

    if best.score < 45:

        raise AnimeNotFound(
            f"Could not confidently resolve: {query}"
        )

    return best


# ============================================================
# GROUP RESULTS BY SERIES TITLE
# ============================================================

def group_by_series(
    results: list[SearchResult]
) -> dict[str, list[SearchResult]]:

    groups = {}

    for result in results:

        title = canonical_series_title(
            result.series_title
            or result.title
        )

        if not title:
            continue

        key = normalize_title(
            title
        )

        if not key:
            continue

        groups.setdefault(
            key,
            []
        ).append(
            result
        )

    return groups


# ============================================================
# RELATED SERIES DETECTION
#
# Example:
#
# Search = Dragon Ball
#
# Candidate:
# Dragon Ball
# Dragon Ball Z
# Dragon Ball Super
#
# They share "Dragon Ball".
#
# Search = Naruto
#
# Candidate:
# Naruto
# Naruto Shippuden
#
# They share "Naruto".
#
# But unrelated:
# Naruto x Boruto
# may be kept separate unless similarity is high.
#
# ============================================================

def are_related_series(
    first: str,
    second: str
) -> bool:

    first_norm = normalize_title(
        first
    )

    second_norm = normalize_title(
        second
    )

    if not first_norm or not second_norm:
        return False

    if first_norm == second_norm:
        return True

    # One complete title contains the other.
    if (
        first_norm.startswith(
            second_norm + " "
        )
        or second_norm.startswith(
            first_norm + " "
        )
    ):
        return True

    first_words = set(
        first_norm.split()
    )

    second_words = set(
        second_norm.split()
    )

    common = (
        first_words
        & second_words
    )

    if not common:
        return False

    # At least two meaningful shared words
    # is strong evidence for multi-word franchises.
    if len(common) >= 2:
        return True

    similarity = title_similarity(
        first_norm,
        second_norm
    )

    return similarity >= 72


# ============================================================
# BUILD FRANCHISE GROUPS
# ============================================================

def build_franchise_groups(
    query: str,
    results: list[SearchResult]
) -> list[list[SearchResult]]:

    results = deduplicate_results(
        results
    )

    groups = []

    used = set()

    # --------------------------------------------------------
    # First, create series-level groups.
    # --------------------------------------------------------

    series_groups = group_by_series(
        results
    )

    series_items = []

    for key, items in series_groups.items():

        if not items:
            continue

        title = items[0].series_title

        series_items.append(
            (
                title,
                items
            )
        )

    # --------------------------------------------------------
    # Compare each discovered series with other discovered
    # series. This makes the grouping generic.
    # --------------------------------------------------------

    for index, (
        title,
        items
    ) in enumerate(series_items):

        if index in used:
            continue

        current_group = list(
            items
        )

        used.add(
            index
        )

        for other_index in range(
            index + 1,
            len(series_items)
        ):

            if other_index in used:
                continue

            other_title, other_items = (
                series_items[other_index]
            )

            if are_related_series(
                title,
                other_title
            ):

                current_group.extend(
                    other_items
                )

                used.add(
                    other_index
                )

        groups.append(
            current_group
        )

    # --------------------------------------------------------
    # Put the group most relevant to the user's query first.
    # --------------------------------------------------------

    query_norm = normalize_title(
        query
    )

    def group_score(
        group: list[SearchResult]
    ) -> float:

        if not group:
            return 0

        titles = [
            item.series_title
            for item in group
        ]

        return max(
            title_similarity(
                query_norm,
                title
            )
            for title in titles
        )

    groups.sort(
        key=group_score,
        reverse=True
    )

    return groups


# ============================================================
# SHOULD SHOW FRANCHISE?
#
# We don't want:
#
# /anime Jujutsu Kaisen
#
# to unnecessarily show a giant franchise list if
# only one series was discovered.
#
# But:
#
# /anime Dragon Ball
#
# should show multiple related series.
#
# ============================================================

def should_show_franchise(
    query: str,
    results: list[SearchResult]
) -> bool:

    groups = build_franchise_groups(
        query,
        results
    )

    if not groups:
        return False

    # Count distinct series names.
    names = set()

    for result in results:

        name = normalize_title(
            result.series_title
        )

        if name:
            names.add(
                name
            )

    # Multiple related series.
    if len(names) >= 2:

        first_group = groups[0]

        first_names = {
            normalize_title(
                result.series_title
            )
            for result in first_group
        }

        if len(first_names) >= 2:
            return True

    return False


# ============================================================
# PICK PRIMARY GROUP
# ============================================================

def get_primary_group(
    query: str,
    results: list[SearchResult]
) -> list[SearchResult]:

    groups = build_franchise_groups(
        query,
        results
    )

    if not groups:
        return []

    return groups[0]


# ============================================================
# SORT SEASONS
# ============================================================

def sort_season_results(
    results: list[SearchResult]
) -> list[SearchResult]:

    return sorted(
        results,
        key=lambda item: (
            item.season_number
            is None,

            item.season_number
            if item.season_number is not None
            else 999,

            item.title.lower()
        )
    )


# ============================================================
# REMOVE DUPLICATE SEASONS
#
# A search may return:
#
# Naruto Season 1
# Naruto Season 1 Hindi Dubbed
#
# pointing to the same page or duplicate pages.
#
# Keep one URL per season.
# ============================================================

def unique_seasons(
    results: list[SearchResult]
) -> list[SearchResult]:

    output = []

    seen = set()

    for result in sort_season_results(
        results
    ):

        key = (
            normalize_title(
                result.series_title
            ),
            result.season_number,
            result.url
        )

        if key in seen:
            continue

        seen.add(
            key
        )

        output.append(
            result
        )

    return output


# ============================================================
# END OF PART 3
# ============================================================

# ============================================================
# ANIME HINDI INFO SCRAPER
# PART 4/7
# RAREANIMES DETAIL PAGE PARSER
# ============================================================


# ============================================================
# GENERIC HTML HELPERS
# ============================================================

def get_soup(
    html: str
) -> BeautifulSoup:

    return BeautifulSoup(
        html,
        "lxml"
    )


def soup_text(
    soup: BeautifulSoup
) -> str:

    return clean_text(
        soup.get_text(
            " ",
            strip=True
        )
    )


def first_non_empty(
    *values
) -> str:

    for value in values:

        value = clean_text(
            value
        )

        if value:
            return value

    return ""


# ============================================================
# FIND LABEL VALUE
#
# Supports structures such as:
#
# Full Name: Naruto Shippuden
# Season: 01
# Episodes: 32
# Network: Sony Yay
#
# ============================================================

def extract_labeled_value(
    soup: BeautifulSoup,
    labels: list[str]
) -> str:

    wanted = {
        normalize_title(label)
        for label in labels
    }

    # --------------------------------------------------------
    # Method 1: normal text nodes / paragraphs / divs.
    # --------------------------------------------------------

    for element in soup.find_all(
        [
            "p",
            "li",
            "div",
            "span",
            "td",
            "strong",
            "b"
        ]
    ):

        text = clean_text(
            element.get_text(
                " ",
                strip=True
            )
        )

        if not text:
            continue

        for label in labels:

            pattern = (
                r"^\s*"
                + re.escape(label)
                + r"\s*[:\-]\s*(.+)$"
            )

            match = re.search(
                pattern,
                text,
                flags=re.I
            )

            if match:

                value = clean_text(
                    match.group(1)
                )

                if value:
                    return value

    # --------------------------------------------------------
    # Method 2: complete page text.
    # --------------------------------------------------------

    text = soup_text(
        soup
    )

    for label in labels:

        pattern = (
            r"\b"
            + re.escape(label)
            + r"\s*[:\-]\s*"
            r"([^\n|]+?)"
            r"(?=\s+(?:"
            + "|".join(
                re.escape(x)
                for x in labels
                if x != label
            )
            + r")\s*[:\-]|$)"
        )

        match = re.search(
            pattern,
            text,
            flags=re.I
        )

        if match:

            value = clean_text(
                match.group(1)
            )

            if value:
                return value

    return ""


# ============================================================
# FULL NAME
# ============================================================

def extract_full_name(
    soup: BeautifulSoup,
    fallback_title: str = ""
) -> str:

    value = extract_labeled_value(
        soup,
        [
            "Full Name",
            "Anime Name",
            "Name",
            "Title"
        ]
    )

    if value:

        return canonical_series_title(
            value
        )

    # --------------------------------------------------------
    # Try page title / H1.
    # --------------------------------------------------------

    for selector in [
        "h1",
        "h2",
        ".entry-title",
        ".post-title",
        "title"
    ]:

        element = soup.select_one(
            selector
        )

        if element:

            value = clean_result_title(
                element.get_text(
                    " ",
                    strip=True
                )
            )

            if value:
                return canonical_series_title(
                    value
                )

    return canonical_series_title(
        fallback_title
    )


# ============================================================
# SEASON NUMBER
# ============================================================

def extract_page_season(
    soup: BeautifulSoup,
    fallback_title: str = ""
) -> Optional[int]:

    value = extract_labeled_value(
        soup,
        [
            "Season",
            "Season No",
            "Season Number"
        ]
    )

    number = extract_season_number(
        value
    )

    if number is not None:
        return number

    return extract_season_number(
        fallback_title
    )


# ============================================================
# EPISODE COUNT
#
# IMPORTANT:
#
# "Episodes: 26 (220 in Total)"
#
# must return 26, NOT 220.
#
# ============================================================

def extract_episode_count(
    soup: BeautifulSoup
) -> Optional[int]:

    value = extract_labeled_value(
        soup,
        [
            "Episodes",
            "Episode",
            "Total Episodes"
        ]
    )

    if not value:
        return None

    # --------------------------------------------------------
    # Prefer the first number.
    #
    # Example:
    # 26 (220 in Total)
    #
    # => 26
    # --------------------------------------------------------

    match = re.search(
        r"\b(\d{1,4})\b",
        value
    )

    if not match:
        return None

    number = safe_int(
        match.group(1)
    )

    if number is None:
        return None

    return number


# ============================================================
# RUNTIME
# ============================================================

def extract_runtime(
    soup: BeautifulSoup
) -> str:

    return extract_labeled_value(
        soup,
        [
            "RunTime",
            "Runtime",
            "Run Time",
            "Duration"
        ]
    )


# ============================================================
# RELEASE YEAR
# ============================================================

def extract_release_year(
    soup: BeautifulSoup
) -> Optional[int]:

    value = extract_labeled_value(
        soup,
        [
            "Release Year",
            "Year",
            "Released"
        ]
    )

    match = re.search(
        r"\b(19\d{2}|20\d{2})\b",
        value
    )

    if not match:
        return None

    return safe_int(
        match.group(1)
    )


# ============================================================
# GENRE
# ============================================================

def extract_genre(
    soup: BeautifulSoup
) -> str:

    return extract_labeled_value(
        soup,
        [
            "Genre",
            "Genres"
        ]
    )


# ============================================================
# SYNOPSIS
# ============================================================

def extract_synopsis(
    soup: BeautifulSoup
) -> str:

    value = extract_labeled_value(
        soup,
        [
            "Synopsis",
            "Story",
            "Description"
        ]
    )

    if value:
        return value

    # Common WordPress content fallback.
    for selector in [
        ".entry-content",
        ".post-content",
        "article"
    ]:

        element = soup.select_one(
            selector
        )

        if element:

            text = clean_text(
                element.get_text(
                    " ",
                    strip=True
                )
            )

            if len(text) > 80:

                # Don't accidentally return the whole page.
                return text[:2500]

    return ""


# ============================================================
# NETWORK / PLATFORM
# ============================================================

def extract_network(
    soup: BeautifulSoup
) -> str:

    value = extract_labeled_value(
        soup,
        [
            "Network",
            "Platform",
            "Broadcast",
            "Channel"
        ]
    )

    return clean_text(
        value
    )


# ============================================================
# LANGUAGE FIELD
# ============================================================

def extract_language_field(
    soup: BeautifulSoup
) -> str:

    return extract_labeled_value(
        soup,
        [
            "Language",
            "Languages",
            "Audio",
            "Dub Language"
        ]
    )


# ============================================================
# QUALITY
# ============================================================

def extract_quality(
    soup: BeautifulSoup
) -> str:

    return extract_labeled_value(
        soup,
        [
            "Quality",
            "Video Quality"
        ]
    )


# ============================================================
# HINDI DETECTION FROM PAGE
# ============================================================

def page_has_hindi(
    soup: BeautifulSoup
) -> bool:

    # --------------------------------------------------------
    # First inspect metadata fields.
    # --------------------------------------------------------

    language = extract_language_field(
        soup
    )

    if contains_hindi(
        language
    ):
        return True

    # --------------------------------------------------------
    # Then inspect page text.
    # --------------------------------------------------------

    text = soup_text(
        soup
    )

    if contains_hindi(
        text
    ):
        return True

    # --------------------------------------------------------
    # Inspect episode headings/buttons.
    # --------------------------------------------------------

    for element in soup.find_all(
        [
            "a",
            "h2",
            "h3",
            "h4",
            "li",
            "button"
        ]
    ):

        value = clean_text(
            element.get_text(
                " ",
                strip=True
            )
        )

        if contains_hindi(
            value
        ):
            return True

    return False


# ============================================================
# HINDI TYPE
# ============================================================

def detect_hindi_type(
    soup: BeautifulSoup
) -> str:

    text = soup_text(
        soup
    ).lower()

    # Uncut has priority.
    if (
        "hindi uncut" in text
        or "hindi - uncut" in text
        or "hindi original" in text
    ):
        return "Hindi Uncut"

    if (
        "hindi dub" in text
        or "hindi dubbed" in text
        or "hindi-dub" in text
    ):
        return "Hindi DUB"

    if (
        "hindi sub" in text
        or "hindi subbed" in text
        or "hindi subtitles" in text
    ):
        return "Hindi SUB"

    language = extract_language_field(
        soup
    )

    if contains_hindi(
        language
    ):
        return "Hindi"

    return ""


# ============================================================
# ALL LANGUAGES
# ============================================================

def extract_languages(
    soup: BeautifulSoup
) -> list[str]:

    values = []

    # Metadata language.
    language = extract_language_field(
        soup
    )

    if language:
        values.append(
            language
        )

    # --------------------------------------------------------
    # Scan episode entries because a page can have:
    #
    # Hindi DUB
    # Tamil DUB
    # Telugu DUB
    # Hindi SUB
    #
    # --------------------------------------------------------

    for element in soup.find_all(
        [
            "a",
            "li",
            "h2",
            "h3",
            "h4",
            "span"
        ]
    ):

        text = clean_text(
            element.get_text(
                " ",
                strip=True
            )
        )

        lower = text.lower()

        for language_name in LANGUAGE_NAMES:

            if language_name.lower() in lower:

                # Preserve useful label.
                if (
                    "dub" in lower
                    or "sub" in lower
                    or language_name.lower()
                    in lower
                ):
                    values.append(
                        text
                    )

    # --------------------------------------------------------
    # Convert raw values to clean language names.
    # --------------------------------------------------------

    found = []

    for value in values:

        lower = value.lower()

        for language_name in LANGUAGE_NAMES:

            if language_name.lower() in lower:

                if language_name not in found:

                    found.append(
                        language_name
                    )

    return found


# ============================================================
# STATUS
# ============================================================

def extract_status(
    soup: BeautifulSoup
) -> str:

    text = soup_text(
        soup
    ).lower()

    # --------------------------------------------------------
    # Strong completed indicators.
    # --------------------------------------------------------

    completed_patterns = [
        "season finale",
        "series finale",
        "completed",
        "complete",
        "all episodes",
        "complete series"
    ]

    for pattern in completed_patterns:

        if pattern in text:

            return "Completed"

    # --------------------------------------------------------
    # Strong ongoing indicators.
    # --------------------------------------------------------

    ongoing_patterns = [
        "ongoing",
        "new episode every",
        "new episodes every",
        "episode every",
        "weekly episode",
        "airing"
    ]

    for pattern in ongoing_patterns:

        if pattern in text:

            return "Ongoing"

    return "Unknown"


# ============================================================
# SCHEDULE
# ============================================================

def extract_schedule(
    soup: BeautifulSoup
) -> str:

    text = soup_text(
        soup
    )

    patterns = [

        r"(\d+\s+New\s+Episode[s]?\s+Every\s+[A-Za-z]+)",

        r"(New\s+Episode[s]?\s+Every\s+[A-Za-z]+)",

        r"(Episode[s]?\s+Every\s+[A-Za-z]+)",

        r"(Weekly\s+Episode[s]?)",

    ]

    for pattern in patterns:

        match = re.search(
            pattern,
            text,
            flags=re.I
        )

        if match:

            return clean_text(
                match.group(1)
            )

    return ""


# ============================================================
# LAST EPISODE
# ============================================================

def extract_last_episode(
    soup: BeautifulSoup
) -> Optional[int]:

    candidates = []

    # --------------------------------------------------------
    # Look at links/headings containing Episode.
    # --------------------------------------------------------

    for element in soup.find_all(
        [
            "a",
            "h2",
            "h3",
            "h4",
            "li"
        ]
    ):

        text = clean_text(
            element.get_text(
                " ",
                strip=True
            )
        )

        if "episode" not in text.lower():
            continue

        numbers = re.findall(
            r"\b(?:episode|ep)\s*[-.#:]?\s*(\d{1,4})\b",
            text,
            flags=re.I
        )

        for number in numbers:

            value = safe_int(
                number
            )

            if value is not None:
                candidates.append(
                    value
                )

    if candidates:
        return max(
            candidates
        )

    return None


# ============================================================
# NEXT EPISODE / EXPECTED RELEASE
# ============================================================

def extract_next_episode_text(
    soup: BeautifulSoup
) -> str:

    text = soup_text(
        soup
    )

    patterns = [

        r"(next\s+episode.{0,120})",

        r"(expected\s+.{0,120})",

        r"(coming\s+.{0,120})",

        r"(release[s]?\s+every\s+.{0,120})",

        r"(new\s+episode[s]?\s+every\s+.{0,120})",

    ]

    for pattern in patterns:

        match = re.search(
            pattern,
            text,
            flags=re.I
        )

        if match:

            value = clean_text(
                match.group(1)
            )

            # Prevent giant page text.
            return value[:250]

    return ""


# ============================================================
# STUDIO
# ============================================================

def extract_studio(
    soup: BeautifulSoup
) -> str:

    value = extract_labeled_value(
        soup,
        [
            "Studio",
            "Studios",
            "Animation Studio"
        ]
    )

    return clean_text(
        value
    )


# ============================================================
# VERIFIED "DUB BY"
#
# IMPORTANT:
# Do NOT guess a dubbing company from random page text.
#
# We only accept an explicit "Dub By" label.
# ============================================================

def extract_dub_by(
    soup: BeautifulSoup
) -> str:

    value = extract_labeled_value(
        soup,
        [
            "Dub By",
            "Dubbed By",
            "Hindi Dub By",
            "Hindi DUB By"
        ]
    )

    if not value:
        return ""

    lower = value.lower()

    # Reject generic / unsafe values.
    if lower in BAD_VALUES:
        return ""

    if (
        "download" in lower
        or "episode" in lower
        or "rare anime" in lower
        or "rareanimes" in lower
    ):
        return ""

    return clean_text(
        value
    )


# ============================================================
# POSTER EXTRACTION
# ============================================================

def is_bad_poster_url(
    url: str
) -> bool:

    if not url:
        return True

    lower = url.lower()

    bad_names = [

        "logo",

        "cropped-rare",

        "rare-animes",

        "rare_animes",

        "favicon",

        "icon",

        "avatar",

        "placeholder",

        "default-image",

        "no-image",

        "wp-logo",

    ]

    for name in bad_names:

        if name in lower:
            return True

    return False


def poster_score(
    url: str,
    element=None
) -> int:

    if not url:
        return -999

    score = 0

    lower = url.lower()

    # --------------------------------------------------------
    # Reject known website assets.
    # --------------------------------------------------------

    if is_bad_poster_url(
        url
    ):
        return -999

    # --------------------------------------------------------
    # Anime/poster naming clues.
    # --------------------------------------------------------

    for keyword in [
        "poster",
        "cover",
        "thumbnail",
        "featured",
        "anime"
    ]:

        if keyword in lower:
            score += 10

    # --------------------------------------------------------
    # Images inside article/content are more likely to be
    # actual anime artwork.
    # --------------------------------------------------------

    if element is not None:

        parent = element.parent

        if parent:

            parent_text = clean_text(
                parent.get_text(
                    " ",
                    strip=True
                )
            ).lower()

            if (
                "anime series info"
                in parent_text
            ):
                score += 20

        classes = " ".join(
            element.get(
                "class",
                []
            )
        ).lower()

        if "featured" in classes:
            score += 15

        if "poster" in classes:
            score += 15

        if "thumbnail" in classes:
            score += 10

    return score


def extract_poster(
    soup: BeautifulSoup,
    page_url: str
) -> str:

    candidates = []

    # --------------------------------------------------------
    # 1. OpenGraph image.
    # --------------------------------------------------------

    for meta in soup.find_all(
        "meta"
    ):

        prop = (
            meta.get("property")
            or meta.get("name")
            or ""
        ).lower()

        if prop in [
            "og:image",
            "twitter:image",
            "twitter:image:src"
        ]:

            url = meta.get(
                "content",
                ""
            )

            url = urljoin(
                page_url,
                url
            )

            if url:

                candidates.append(
                    (
                        poster_score(
                            url
                        ) + 25,
                        url
                    )
                )

    # --------------------------------------------------------
    # 2. Article images.
    # --------------------------------------------------------

    for image in soup.find_all(
        "img"
    ):

        url = first_non_empty(
            image.get("src"),
            image.get("data-src"),
            image.get("data-lazy-src"),
            image.get("data-original")
        )

        if not url:
            continue

        url = urljoin(
            page_url,
            url
        )

        score = poster_score(
            url,
            image
        )

        if score > -999:

            candidates.append(
                (
                    score,
                    url
                )
            )

    # --------------------------------------------------------
    # 3. Prefer larger WordPress image URLs.
    # --------------------------------------------------------

    for image in soup.find_all(
        "img"
    ):

        srcset = image.get(
            "srcset",
            ""
        )

        if not srcset:
            continue

        parts = [
            part.strip()
            for part in srcset.split(",")
        ]

        for part in parts:

            chunks = part.split()

            if not chunks:
                continue

            url = urljoin(
                page_url,
                chunks[0]
            )

            score = poster_score(
                url,
                image
            )

            if score > -999:

                if len(chunks) > 1:

                    descriptor = chunks[1]

                    match = re.search(
                        r"(\d+)w",
                        descriptor
                    )

                    if match:

                        width = safe_int(
                            match.group(1)
                        )

                        if width:
                            score += min(
                                25,
                                width // 100
                            )

                candidates.append(
                    (
                        score,
                        url
                    )
                )

    if not candidates:
        return ""

    # Highest score first.
    candidates.sort(
        key=lambda item: item[0],
        reverse=True
    )

    return candidates[0][1]


# ============================================================
# EPISODE LANGUAGE PARSER
# ============================================================

def parse_episode_language(
    text: str
) -> list[str]:

    text = clean_text(
        text
    )

    lower = text.lower()

    found = []

    for language in LANGUAGE_NAMES:

        if language.lower() in lower:

            if language not in found:

                found.append(
                    language
                )

    return found


def parse_episode_number_from_text(
    text: str
) -> Optional[int]:

    patterns = [

        r"\bepisode\s*[-.#:]?\s*(\d{1,4})\b",

        r"\bep\s*[-.#:]?\s*(\d{1,4})\b",

        r"^\s*(\d{1,4})\s*[-:.]",

    ]

    for pattern in patterns:

        match = re.search(
            pattern,
            text,
            flags=re.I
        )

        if match:

            return safe_int(
                match.group(1)
            )

    return None


# ============================================================
# EPISODE LIST
# ============================================================

def extract_episode_list(
    soup: BeautifulSoup
) -> list[EpisodeInfo]:

    episodes = []

    seen = set()

    # --------------------------------------------------------
    # Episode links/headings.
    # --------------------------------------------------------

    elements = soup.find_all(
        [
            "a",
            "h2",
            "h3",
            "h4",
            "li"
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

        if (
            "episode" not in text.lower()
            and " ep " not in (
                " " + text.lower() + " "
            )
        ):
            continue

        episode_number = (
            parse_episode_number_from_text(
                text
            )
        )

        if episode_number is None:
            continue

        languages = (
            parse_episode_language(
                text
            )
        )

        # Only useful episode entries.
        if not languages:
            continue

        href = element.get(
            "href",
            ""
        )

        href = urljoin(
            BASE_URL,
            href
        )

        key = (
            episode_number,
            tuple(languages),
            href
        )

        if key in seen:
            continue

        seen.add(
            key
        )

        episodes.append(
            EpisodeInfo(
                number=episode_number,
                title=text,
                languages=languages,
                url=href
            )
        )

    # --------------------------------------------------------
    # Sort by episode number.
    # --------------------------------------------------------

    episodes.sort(
        key=lambda item: item.number
    )

    return episodes


# ============================================================
# HINDI EPISODE COUNT
# ============================================================

def count_hindi_episodes(
    episodes: list[EpisodeInfo]
) -> int:

    count = 0

    for episode in episodes:

        if any(
            "hindi"
            in language.lower()
            for language
            in episode.languages
        ):
            count += 1

    return count


# ============================================================
# MOVIE DETECTION FROM PAGE
# ============================================================

def detect_page_is_movie(
    soup: BeautifulSoup,
    title: str
) -> bool:

    if looks_like_movie(
        title
    ):
        return True

    text = soup_text(
        soup
    ).lower()

    movie_patterns = [
        "movie",
        "film",
        "feature film",
        "the movie"
    ]

    for pattern in movie_patterns:

        if pattern in text:
            return True

    return False


# ============================================================
# COMPLETE PAGE PARSER
# ============================================================

def parse_anime_page(
    html: str,
    page_url: str,
    fallback_title: str = ""
) -> SeriesInfo:

    soup = get_soup(
        html
    )

    full_name = extract_full_name(
        soup,
        fallback_title
    )

    season_number = extract_page_season(
        soup,
        fallback_title
    )

    episode_count = extract_episode_count(
        soup
    )

    runtime = extract_runtime(
        soup
    )

    release_year = extract_release_year(
        soup
    )

    genre = extract_genre(
        soup
    )

    synopsis = extract_synopsis(
        soup
    )

    network = extract_network(
        soup
    )

    language_field = (
        extract_language_field(
            soup
        )
    )

    quality = extract_quality(
        soup
    )

    languages = extract_languages(
        soup
    )

    status = extract_status(
        soup
    )

    schedule = extract_schedule(
        soup
    )

    last_episode = extract_last_episode(
        soup
    )

    next_episode = (
        extract_next_episode_text(
            soup
        )
    )

    studio = extract_studio(
        soup
    )

    dub_by = extract_dub_by(
        soup
    )

    poster = extract_poster(
        soup,
        page_url
    )

    episodes = extract_episode_list(
        soup
    )

    hindi_available = page_has_hindi(
        soup
    )

    hindi_type = detect_hindi_type(
        soup
    )

    hindi_episode_count = (
        count_hindi_episodes(
            episodes
        )
    )

    # If detailed episode parsing found Hindi,
    # trust it.
    if hindi_episode_count > 0:
        hindi_available = True

    # If episode count wasn't present in metadata,
    # derive it from actual episode entries.
    if episode_count is None:

        if episodes:

            episode_count = max(
                episode.number
                for episode in episodes
            )

    is_movie = detect_page_is_movie(
        soup,
        full_name
    )

    # --------------------------------------------------------
    # Season display name.
    # --------------------------------------------------------

    if season_number is not None:

        season_name = (
            f"Season {season_number:02d}"
        )

    elif is_movie:

        season_name = "Movie"

    else:

        season_name = "Main"

    # --------------------------------------------------------
    # Construct SeriesInfo.
    # --------------------------------------------------------

    return SeriesInfo(
        title=full_name,
        normalized_title=normalize_title(
            full_name
        ),
        url=page_url,
        poster_url=poster,
        hindi_available=hindi_available,
        seasons=[],
        movies=[],
        total_hindi_episodes=hindi_episode_count,
        total_episodes=episode_count or 0
    )


# ============================================================
# DETAIL PAGE -> SEASON INFO
#
# This converts one RareAnimes page into one SeasonInfo.
# ============================================================

def parse_season_info(
    html: str,
    page_url: str,
    fallback_title: str = ""
) -> SeasonInfo:

    soup = get_soup(
        html
    )

    title = extract_full_name(
        soup,
        fallback_title
    )

    season_number = extract_page_season(
        soup,
        fallback_title
    )

    episode_count = extract_episode_count(
        soup
    )

    episodes = extract_episode_list(
        soup
    )

    if episode_count is None:

        if episodes:

            episode_count = max(
                episode.number
                for episode in episodes
            )

    languages = extract_languages(
        soup
    )

    hindi_available = page_has_hindi(
        soup
    )

    hindi_episode_count = (
        count_hindi_episodes(
            episodes
        )
    )

    if hindi_episode_count > 0:

        hindi_available = True

    status = extract_status(
        soup
    )

    schedule = extract_schedule(
        soup
    )

    last_episode = extract_last_episode(
        soup
    )

    next_episode = (
        extract_next_episode_text(
            soup
        )
    )

    network = extract_network(
        soup
    )

    studio = extract_studio(
        soup
    )

    dub_by = extract_dub_by(
        soup
    )

    poster = extract_poster(
        soup,
        page_url
    )

    # --------------------------------------------------------
    # Never count a non-Hindi page as a Hindi season.
    # --------------------------------------------------------

    if not hindi_available:

        hindi_episode_count = 0

    return SeasonInfo(
        number=season_number or 1,
        title=title,
        url=page_url,
        poster_url=poster,
        hindi_available=hindi_available,
        hindi_episode_count=hindi_episode_count,
        episode_count=episode_count or 0,
        languages=languages,
        status=status,
        network=network,
        studio=studio,
        dub_by=dub_by,
        last_episode=last_episode,
        next_episode=next_episode,
        schedule=schedule,
        episodes=episodes
    )


# ============================================================
# END OF PART 4
# ============================================================

# ============================================================
# ANIME HINDI INFO SCRAPER
# PART 5/7
# FRANCHISE BUILDER
# ============================================================


# ============================================================
# SEASON OBJECT BUILDER
# ============================================================

def season_sort_key(
    season: SeasonInfo
):
    number = season.number

    if number is None:
        return 9999

    return number


def deduplicate_seasons(
    seasons: list[SeasonInfo]
) -> list[SeasonInfo]:

    unique = {}

    for season in seasons:

        url_key = normalize_url(
            season.url
        )

        # Prefer URL as primary identity.
        if url_key:

            key = url_key

        else:

            key = (
                season.number,
                normalize_title(
                    season.title
                )
            )

        existing = unique.get(
            key
        )

        if existing is None:

            unique[key] = season

            continue

        # ----------------------------------------------------
        # If duplicate exists, keep the richer one.
        # ----------------------------------------------------

        existing_score = (
            (1 if existing.hindi_available else 0)
            + len(existing.episodes)
            + len(existing.languages)
            + (1 if existing.poster_url else 0)
            + (1 if existing.network else 0)
            + (1 if existing.studio else 0)
            + (1 if existing.dub_by else 0)
        )

        new_score = (
            (1 if season.hindi_available else 0)
            + len(season.episodes)
            + len(season.languages)
            + (1 if season.poster_url else 0)
            + (1 if season.network else 0)
            + (1 if season.studio else 0)
            + (1 if season.dub_by else 0)
        )

        if new_score > existing_score:

            unique[key] = season

    result = list(
        unique.values()
    )

    result.sort(
        key=season_sort_key
    )

    return result


# ============================================================
# SERIES BUILDER
# ============================================================

def build_series_from_seasons(
    title: str,
    seasons: list[SeasonInfo]
) -> SeriesInfo:

    seasons = deduplicate_seasons(
        seasons
    )

    hindi_seasons = [
        season
        for season in seasons
        if season.hindi_available
    ]

    total_episodes = sum(
        season.episode_count
        for season in seasons
    )

    total_hindi_episodes = sum(
        season.hindi_episode_count
        for season in seasons
    )

    poster = ""

    # Prefer a Hindi season poster.
    for season in hindi_seasons:

        if season.poster_url:

            poster = season.poster_url
            break

    # Fallback to any season.
    if not poster:

        for season in seasons:

            if season.poster_url:

                poster = season.poster_url
                break

    return SeriesInfo(
        title=title,
        normalized_title=normalize_title(
            title
        ),
        url=(
            hindi_seasons[0].url
            if hindi_seasons
            else (
                seasons[0].url
                if seasons
                else ""
            )
        ),
        poster_url=poster,
        hindi_available=bool(
            hindi_seasons
        ),
        seasons=hindi_seasons,
        movies=[],
        total_hindi_episodes=(
            total_hindi_episodes
        ),
        total_episodes=(
            total_episodes
        )
    )


# ============================================================
# MERGE SAME SERIES
#
# Example:
#
# Naruto Season 1
# Naruto Season 2
# Naruto Season 3
#
# => One SeriesInfo:
#
# Naruto
#   Season 1
#   Season 2
#   Season 3
#
# ============================================================

def merge_series_groups(
    series_groups: dict[str, list[SeasonInfo]]
) -> list[SeriesInfo]:

    result = []

    for title, seasons in series_groups.items():

        series = build_series_from_seasons(
            title,
            seasons
        )

        # Only keep series with Hindi.
        if not series.hindi_available:
            continue

        result.append(
            series
        )

    # --------------------------------------------------------
    # Stable alphabetical ordering.
    # --------------------------------------------------------

    result.sort(
        key=lambda item: normalize_title(
            item.title
        )
    )

    return result


# ============================================================
# ADD SEASON TO GROUP
# ============================================================

def add_season_to_series_group(
    groups: dict[str, list[SeasonInfo]],
    season: SeasonInfo
) -> None:

    if not season.hindi_available:
        return

    title = clean_text(
        season.title
    )

    if not title:

        title = "Unknown Series"

    canonical = canonical_series_title(
        title
    )

    key = normalize_title(
        canonical
    )

    if not key:
        return

    groups.setdefault(
        key,
        []
    ).append(
        season
    )


# ============================================================
# BUILD SERIES FROM PARSED PAGES
# ============================================================

def build_series_from_pages(
    parsed_pages: list[SeasonInfo]
) -> list[SeriesInfo]:

    groups = {}

    for season in parsed_pages:

        add_season_to_series_group(
            groups,
            season
        )

    return merge_series_groups(
        groups
    )


# ============================================================
# MOVIE BUILDER
# ============================================================

def build_movie_series(
    movie_pages: list[SeasonInfo]
) -> list[SeriesInfo]:

    groups = {}

    for movie in movie_pages:

        if not movie.hindi_available:
            continue

        title = canonical_series_title(
            movie.title
        )

        key = normalize_title(
            title
        )

        if not key:
            continue

        groups.setdefault(
            key,
            []
        ).append(
            movie
        )

    result = []

    for title, movies in groups.items():

        movies = deduplicate_seasons(
            movies
        )

        total_hindi = sum(
            movie.hindi_episode_count
            for movie in movies
        )

        total_eps = sum(
            movie.episode_count
            for movie in movies
        )

        poster = ""

        for movie in movies:

            if movie.poster_url:

                poster = movie.poster_url
                break

        result.append(
            SeriesInfo(
                title=title,
                normalized_title=normalize_title(
                    title
                ),
                url=(
                    movies[0].url
                    if movies
                    else ""
                ),
                poster_url=poster,
                hindi_available=True,
                seasons=[],
                movies=movies,
                total_hindi_episodes=total_hindi,
                total_episodes=total_eps
            )
        )

    result.sort(
        key=lambda item: normalize_title(
            item.title
        )
    )

    return result


# ============================================================
# FRANCHISE NAME
# ============================================================

def make_franchise_name(
    query: str,
    series: list[SeriesInfo]
) -> str:

    query = clean_text(
        query
    )

    if query:
        return query.title()

    if not series:
        return "Anime Franchise"

    # Find common franchise base.
    names = [
        item.title
        for item in series
    ]

    base = franchise_base_title(
        names[0]
    )

    if base:
        return base

    return names[0]


# ============================================================
# FRANCHISE POSTER
# ============================================================

def get_franchise_poster(
    series: list[SeriesInfo],
    movies: list[SeriesInfo]
) -> str:

    # --------------------------------------------------------
    # First Hindi series poster.
    # --------------------------------------------------------

    for item in series:

        if item.poster_url:

            return item.poster_url

    # --------------------------------------------------------
    # Then movie poster.
    # --------------------------------------------------------

    for item in movies:

        if item.poster_url:

            return item.poster_url

    return ""


# ============================================================
# TOTALS
# ============================================================

def calculate_franchise_totals(
    series: list[SeriesInfo],
    movies: list[SeriesInfo]
) -> tuple[int, int]:

    total_hindi = 0
    total_episodes = 0

    for item in series:

        total_hindi += (
            item.total_hindi_episodes
        )

        total_episodes += (
            item.total_episodes
        )

    for item in movies:

        total_hindi += (
            item.total_hindi_episodes
        )

        total_episodes += (
            item.total_episodes
        )

    return (
        total_hindi,
        total_episodes
    )


# ============================================================
# BUILD COMPLETE FRANCHISE
# ============================================================

def build_franchise_info(
    query: str,
    parsed_pages: list[SeasonInfo]
) -> FranchiseInfo:

    # --------------------------------------------------------
    # Separate movies and normal seasons.
    # --------------------------------------------------------

    series_pages = []
    movie_pages = []

    for page in parsed_pages:

        if not page.hindi_available:
            continue

        if looks_like_movie(
            page.title
        ):

            movie_pages.append(
                page
            )

        else:

            series_pages.append(
                page
            )

    # --------------------------------------------------------
    # Build series.
    # --------------------------------------------------------

    series = build_series_from_pages(
        series_pages
    )

    # --------------------------------------------------------
    # Build movies.
    # --------------------------------------------------------

    movies = build_movie_series(
        movie_pages
    )

    # --------------------------------------------------------
    # Remove accidental unrelated results.
    # --------------------------------------------------------

    filtered_series = []

    for item in series:

        if should_show_franchise(
            query,
            [item]
        ):

            filtered_series.append(
                item
            )

    series = filtered_series

    # --------------------------------------------------------
    # Franchise name.
    # --------------------------------------------------------

    franchise_name = make_franchise_name(
        query,
        series
    )

    total_hindi, total_episodes = (
        calculate_franchise_totals(
            series,
            movies
        )
    )

    poster = get_franchise_poster(
        series,
        movies
    )

    return FranchiseInfo(
        name=franchise_name,
        normalized_name=normalize_title(
            franchise_name
        ),
        poster_url=poster,
        hindi_available=bool(
            series or movies
        ),
        series=series,
        movies=movies,
        total_hindi_episodes=total_hindi,
        total_episodes=total_episodes
    )


# ============================================================
# DETERMINE WHETHER QUERY IS A FRANCHISE
# ============================================================

def is_franchise_query(
    query: str,
    series: list[SeriesInfo]
) -> bool:

    if len(series) > 1:
        return True

    # --------------------------------------------------------
    # Multiple seasons also mean we should show a franchise-
    # style result rather than only one page.
    # --------------------------------------------------------

    if len(series) == 1:

        if len(series[0].seasons) > 1:
            return True

    # --------------------------------------------------------
    # Multiple distinct series names.
    # --------------------------------------------------------

    names = {
        normalize_title(
            item.title
        )
        for item in series
    }

    if len(names) > 1:
        return True

    return False


# ============================================================
# GET ALL HINDI SEASONS
# ============================================================

def get_hindi_seasons(
    series: SeriesInfo
) -> list[SeasonInfo]:

    return [
        season
        for season in series.seasons
        if season.hindi_available
    ]


# ============================================================
# SEASON LABEL
# ============================================================

def format_season_label(
    season: SeasonInfo
) -> str:

    if season.number:

        return (
            f"Season {season.number:02d}"
        )

    return "Season"


# ============================================================
# SERIES SUMMARY
# ============================================================

def make_series_summary(
    series: SeriesInfo
) -> str:

    seasons = get_hindi_seasons(
        series
    )

    if not seasons:

        return (
            f"{series.title} — "
            f"Hindi unavailable"
        )

    season_count = len(
        seasons
    )

    return (
        f"{series.title} — "
        f"{season_count} Hindi season"
        + (
            ""
            if season_count == 1
            else "s"
        )
        + f" • "
        f"{series.total_hindi_episodes} "
        f"Hindi episodes"
    )


# ============================================================
# FRANCHISE SUMMARY
# ============================================================

def make_franchise_summary(
    franchise: FranchiseInfo
) -> str:

    series_count = len(
        franchise.series
    )

    movie_count = len(
        franchise.movies
    )

    parts = []

    if series_count:

        parts.append(
            f"{series_count} series"
        )

    if movie_count:

        parts.append(
            f"{movie_count} movie"
            + (
                ""
                if movie_count == 1
                else "s"
            )
        )

    if not parts:

        return (
            "Hindi content not found"
        )

    return (
        f"{franchise.name}: "
        + " • ".join(parts)
        + f" • "
        f"{franchise.total_hindi_episodes} "
        f"Hindi episodes"
    )


# ============================================================
# FINAL FRANCHISE VALIDATION
# ============================================================

def validate_franchise(
    franchise: FranchiseInfo
) -> bool:

    if not franchise.hindi_available:
        return False

    if (
        not franchise.series
        and not franchise.movies
    ):
        return False

    return True


# ============================================================
# END OF PART 5
# ============================================================

# ============================================================
# ANIME HINDI INFO SCRAPER
# PART 6/7
# ACTUAL SCRAPE ENGINE
# ============================================================


# ============================================================
# FETCH ONE ANIME PAGE
# ============================================================

async def fetch_anime_page(
    scraper,
    result: SearchResult
) -> Optional[SeasonInfo]:

    try:

        html = await scraper.fetch_cached(
            result.url
        )

        if not html:
            return None

        season = parse_season_info(
            html=html,
            page_url=result.url,
            fallback_title=result.title
        )

        # ----------------------------------------------------
        # Page title fallback.
        # ----------------------------------------------------

        if not season.title:

            season.title = result.series_title

        # ----------------------------------------------------
        # Hindi check.
        #
        # Search result may say Hindi but detail page is
        # authoritative.
        # ----------------------------------------------------

        if not season.hindi_available:

            return None

        return season

    except Exception as exc:

        logger.warning(
            "Failed to parse %s: %s",
            result.url,
            exc
        )

        return None


# ============================================================
# FETCH MANY PAGES
# ============================================================

async def fetch_anime_pages(
    scraper,
    results: list[SearchResult],
    concurrency: int = 5
) -> list[SeasonInfo]:

    if not results:
        return []

    semaphore = asyncio.Semaphore(
        max(1, concurrency)
    )

    async def worker(
        result: SearchResult
    ):

        async with semaphore:

            return await fetch_anime_page(
                scraper,
                result
            )

    tasks = [
        asyncio.create_task(
            worker(result)
        )
        for result in results
    ]

    parsed = await asyncio.gather(
        *tasks,
        return_exceptions=True
    )

    final = []

    for item in parsed:

        if isinstance(
            item,
            SeasonInfo
        ):

            final.append(
                item
            )

    return final


# ============================================================
# REMOVE DUPLICATE PAGE RESULTS
# ============================================================

def unique_search_results(
    results: list[SearchResult]
) -> list[SearchResult]:

    seen = set()
    output = []

    for result in results:

        url = normalize_url(
            result.url
        )

        if not url:
            continue

        if url in seen:
            continue

        seen.add(
            url
        )

        output.append(
            result
        )

    return output


# ============================================================
# FILTER SEARCH RESULTS
#
# Don't send obviously unrelated search results to the
# detail-page parser.
# ============================================================

def filter_search_results(
    query: str,
    results: list[SearchResult]
) -> list[SearchResult]:

    filtered = []

    for result in results:

        title = clean_text(
            result.title
        )

        if not title:
            continue

        # ----------------------------------------------------
        # Keep if resolver thinks it belongs to query.
        # ----------------------------------------------------

        if result_matches_query(
            query,
            result
        ):

            filtered.append(
                result
            )

            continue

        # ----------------------------------------------------
        # Related result can still be kept when the title is
        # strongly related to another result.
        # ----------------------------------------------------

        if result.series_title:

            if title_similarity(
                query,
                result.series_title
            ) >= 45:

                filtered.append(
                    result
                )

    return filtered


# ============================================================
# LIMIT RESULTS
#
# Protect Render from accidentally crawling hundreds of pages.
# ============================================================

def limit_search_results(
    results: list[SearchResult],
    maximum: int = 80
) -> list[SearchResult]:

    if len(results) <= maximum:

        return results

    return results[:maximum]


# ============================================================
# SEARCH + DISCOVER + PARSE
# ============================================================

async def discover_and_parse(
    scraper,
    query: str
) -> list[SeasonInfo]:

    query = clean_text(
        query
    )

    if not query:
        return []

    # --------------------------------------------------------
    # Step 1:
    # Search RareAnimes.
    # --------------------------------------------------------

    search_results = await scraper.discover_related_results(
        query
    )

    logger.info(
        "Search results for '%s': %d",
        query,
        len(search_results)
    )

    # --------------------------------------------------------
    # Step 2:
    # Remove duplicates.
    # --------------------------------------------------------

    search_results = unique_search_results(
        search_results
    )

    # --------------------------------------------------------
    # Step 3:
    # Remove obviously unrelated results.
    # --------------------------------------------------------

    search_results = filter_search_results(
        query,
        search_results
    )

    # --------------------------------------------------------
    # Step 4:
    # Rank again after filtering.
    # --------------------------------------------------------

    search_results = rank_results(
        query,
        search_results
    )

    # --------------------------------------------------------
    # Step 5:
    # Safety limit.
    # --------------------------------------------------------

    search_results = limit_search_results(
        search_results,
        maximum=80
    )

    if not search_results:

        return []

    logger.info(
        "Parsing %d detail pages for '%s'",
        len(search_results),
        query
    )

    # --------------------------------------------------------
    # Step 6:
    # Fetch actual pages.
    # --------------------------------------------------------

    parsed_pages = await fetch_anime_pages(
        scraper,
        search_results,
        concurrency=5
    )

    logger.info(
        "Successfully parsed %d pages for '%s'",
        len(parsed_pages),
        query
    )

    return parsed_pages


# ============================================================
# BUILD FINAL FRANCHISE
# ============================================================

async def scrape_franchise(
    scraper,
    query: str
) -> Optional[FranchiseInfo]:

    pages = await discover_and_parse(
        scraper,
        query
    )

    if not pages:

        return None

    franchise = build_franchise_info(
        query,
        pages
    )

    if not validate_franchise(
        franchise
    ):

        return None

    return franchise


# ============================================================
# SELECT BEST SINGLE SERIES
# ============================================================

def choose_best_series(
    query: str,
    series: list[SeriesInfo]
) -> Optional[SeriesInfo]:

    if not series:
        return None

    normalized_query = normalize_title(
        query
    )

    # --------------------------------------------------------
    # Exact match first.
    # --------------------------------------------------------

    for item in series:

        if (
            normalize_title(
                item.title
            )
            == normalized_query
        ):

            return item

    # --------------------------------------------------------
    # Strongest similarity.
    # --------------------------------------------------------

    ranked = sorted(
        series,
        key=lambda item: title_similarity(
            query,
            item.title
        ),
        reverse=True
    )

    return ranked[0]


# ============================================================
# BUILD SINGLE ANIME INFO
#
# This keeps compatibility with the Telegram bot.
# ============================================================

def build_single_anime_info(
    query: str,
    franchise: FranchiseInfo
) -> Optional[AnimeInfo]:

    best = choose_best_series(
        query,
        franchise.series
    )

    # --------------------------------------------------------
    # If there is no series, try movies.
    # --------------------------------------------------------

    if best is None:

        if franchise.movies:

            best = franchise.movies[0]

        else:

            return None

    # --------------------------------------------------------
    # Pick first useful Hindi season.
    # --------------------------------------------------------

    seasons = [
        season
        for season in best.seasons
        if season.hindi_available
    ]

    first_season = (
        seasons[0]
        if seasons
        else None
    )

    # --------------------------------------------------------
    # Compatibility AnimeInfo.
    #
    # The richer franchise object is attached separately
    # when supported by the dataclass.
    # --------------------------------------------------------

    return AnimeInfo(
        title=best.title,
        normalized_title=best.normalized_title,
        poster_url=best.poster_url,
        hindi_available=best.hindi_available,
        platform=(
            first_season.network
            if first_season
            else ""
        ),
        season=(
            first_season.number
            if first_season
            else None
        ),
        total_episodes=(
            best.total_episodes
        ),
        hindi_episodes=(
            best.total_hindi_episodes
        ),
        languages=(
            first_season.languages
            if first_season
            else []
        ),
        status=(
            first_season.status
            if first_season
            else "Unknown"
        ),
        last_episode=(
            first_season.last_episode
            if first_season
            else None
        ),
        next_episode=(
            first_season.next_episode
            if first_season
            else ""
        ),
        schedule=(
            first_season.schedule
            if first_season
            else ""
        ),
        studio=(
            first_season.studio
            if first_season
            else ""
        ),
        dub_by=(
            first_season.dub_by
            if first_season
            else ""
        ),
        url=best.url
    )


# ============================================================
# ATTACH FRANCHISE DATA
#
# Python dataclasses can be extended safely at runtime.
# This lets the old Telegram handler continue to receive
# AnimeInfo while the new franchise data is available.
# ============================================================

def attach_franchise_data(
    anime_info: AnimeInfo,
    franchise: FranchiseInfo
) -> AnimeInfo:

    try:

        setattr(
            anime_info,
            "is_franchise",
            True
        )

        setattr(
            anime_info,
            "franchise",
            franchise
        )

    except Exception as exc:

        logger.warning(
            "Could not attach franchise data: %s",
            exc
        )

    return anime_info


# ============================================================
# MAIN SCRAPER METHOD
#
# IMPORTANT:
# commands.py calls:
#
# await get_anime_info(query)
#
# ============================================================

async def scrape_query(
    scraper,
    query: str
):

    query = clean_text(
        query
    )

    if not query:

        raise AnimeNotFound(
            "Anime name is empty."
        )

    # --------------------------------------------------------
    # Search and parse complete franchise.
    # --------------------------------------------------------

    franchise = await scrape_franchise(
        scraper,
        query
    )

    if franchise is None:

        raise AnimeNotFound(
            f"No Hindi anime found for: {query}"
        )

    # --------------------------------------------------------
    # Multiple series / seasons:
    # return franchise-aware AnimeInfo.
    # --------------------------------------------------------

    anime_info = build_single_anime_info(
        query,
        franchise
    )

    if anime_info is None:

        raise AnimeNotFound(
            f"No matching Hindi anime found for: {query}"
        )

    return attach_franchise_data(
        anime_info,
        franchise
    )


# ============================================================
# PUBLIC API
# ============================================================

async def get_anime_info(
    query: str
) -> AnimeInfo:

    query = clean_text(
        query
    )

    if not query:

        raise AnimeNotFound(
            "Please enter an anime name."
        )

    async with AnimeScraper(
        source=SOURCE_NAME
    ) as scraper:

        return await scrape_query(
            scraper,
            query
        )


# ============================================================
# END OF PART 6
# ============================================================
# ============================================================
# ANIME HINDI INFO SCRAPER
# PART 7/7
# FINAL FORMATTER + OUTPUT
# ============================================================


# ============================================================
# SAFE GETTER
# ============================================================

def obj_get(
    obj,
    name,
    default=None
):
    return getattr(
        obj,
        name,
        default
    )


# ============================================================
# FORMAT EPISODE LANGUAGES
# ============================================================

def format_episode_languages(
    episode: EpisodeInfo
) -> str:

    languages = obj_get(
        episode,
        "languages",
        []
    )

    if not languages:
        return "Unknown"

    return ", ".join(
        languages
    )


# ============================================================
# FORMAT ONE SEASON
# ============================================================

def format_season_info(
    season: SeasonInfo
) -> str:

    number = obj_get(
        season,
        "number",
        None
    )

    if number:
        label = f"S{number:02d}"
    else:
        label = "Season"

    episode_count = obj_get(
        season,
        "episode_count",
        0
    )

    hindi_count = obj_get(
        season,
        "hindi_episode_count",
        0
    )

    status = obj_get(
        season,
        "status",
        "Unknown"
    )

    network = obj_get(
        season,
        "network",
        ""
    )

    schedule = obj_get(
        season,
        "schedule",
        ""
    )

    last_episode = obj_get(
        season,
        "last_episode",
        None
    )

    next_episode = obj_get(
        season,
        "next_episode",
        ""
    )

    languages = obj_get(
        season,
        "languages",
        []
    )

    lines = []

    # --------------------------------------------------------
    # Basic season information.
    # --------------------------------------------------------

    lines.append(
        f"• {label} — "
        f"{episode_count} Episodes"
    )

    if hindi_count:

        lines.append(
            f"  🇮🇳 Hindi: "
            f"{hindi_count}/{episode_count}"
        )

    if languages:

        lines.append(
            "  🌐 Languages: "
            + ", ".join(
                languages
            )
        )

    if network:

        lines.append(
            f"  📺 Platform: {network}"
        )

    if status and status != "Unknown":

        if status == "Ongoing":
            icon = "🟢"
        elif status == "Completed":
            icon = "✅"
        else:
            icon = "⚪"

        lines.append(
            f"  {icon} Status: {status}"
        )

    if last_episode:

        lines.append(
            f"  🎞 Last Episode: "
            f"{last_episode}"
        )

    if schedule:

        lines.append(
            f"  📅 Schedule: "
            f"{schedule}"
        )

    if next_episode:

        lines.append(
            f"  ⏭ Next: "
            f"{next_episode}"
        )

    return "\n".join(
        lines
    )


# ============================================================
# FORMAT ONE SERIES
# ============================================================

def format_series_info(
    series: SeriesInfo,
    index: int
) -> str:

    title = obj_get(
        series,
        "title",
        "Unknown"
    )

    seasons = obj_get(
        series,
        "seasons",
        []
    )

    hindi_available = obj_get(
        series,
        "hindi_available",
        False
    )

    total_hindi = obj_get(
        series,
        "total_hindi_episodes",
        0
    )

    total_episodes = obj_get(
        series,
        "total_episodes",
        0
    )

    lines = []

    lines.append(
        f"{index}️⃣ {title}"
    )

    if hindi_available:
        lines.append(
            "   🇮🇳 Hindi: ✅ Available"
        )
    else:
        lines.append(
            "   🇮🇳 Hindi: ❌ Not Available"
        )

    if seasons:

        lines.append(
            f"   📚 Hindi Seasons: "
            f"{len(seasons)}"
        )

        lines.append(
            f"   🎞 Hindi Episodes: "
            f"{total_hindi}"
        )

        # ----------------------------------------------------
        # Every Hindi season.
        # ----------------------------------------------------

        for season in seasons:

            season_text = (
                format_season_info(
                    season
                )
            )

            for line in season_text.split(
                "\n"
            ):

                lines.append(
                    "   " + line
                )

    else:

        lines.append(
            "   📚 Seasons: Unknown"
        )

    return "\n".join(
        lines
    )


# ============================================================
# FORMAT MOVIES
# ============================================================

def format_movies(
    movies: list[SeriesInfo]
) -> str:

    if not movies:
        return ""

    lines = []

    lines.append(
        "🎥 Movies"
    )

    for index, movie in enumerate(
        movies,
        start=1
    ):

        title = obj_get(
            movie,
            "title",
            "Unknown"
        )

        hindi_count = obj_get(
            movie,
            "total_hindi_episodes",
            0
        )

        lines.append(
            f"{index}️⃣ {title}"
        )

        lines.append(
            "   🇮🇳 Hindi: "
            "✅ Available"
        )

        if hindi_count:

            lines.append(
                f"   🎞 Episodes: "
                f"{hindi_count}"
            )

    return "\n".join(
        lines
    )


# ============================================================
# FORMAT COMPLETE FRANCHISE
# ============================================================

def format_franchise_info(
    franchise: FranchiseInfo
) -> str:

    name = obj_get(
        franchise,
        "name",
        "Anime"
    )

    series = obj_get(
        franchise,
        "series",
        []
    )

    movies = obj_get(
        franchise,
        "movies",
        []
    )

    total_hindi = obj_get(
        franchise,
        "total_hindi_episodes",
        0
    )

    lines = []

    lines.append(
        "『Anime Hindi Info』"
    )

    lines.append("")

    lines.append(
        f"🎬 {name}"
    )

    lines.append(
        "🇮🇳 Hindi: "
        + (
            "✅ Available"
            if (
                series
                or movies
            )
            else "❌ Not Available"
        )
    )

    if series:

        lines.append(
            f"📚 Related Series: "
            f"{len(series)}"
        )

    if total_hindi:

        lines.append(
            f"🎞 Total Hindi Episodes: "
            f"{total_hindi}"
        )

    lines.append("")
    lines.append(
        "━━━━━━━━━━━━━━━━"
    )

    # --------------------------------------------------------
    # Series.
    # --------------------------------------------------------

    for index, item in enumerate(
        series,
        start=1
    ):

        lines.append("")

        lines.append(
            format_series_info(
                item,
                index
            )
        )

        lines.append("")

        lines.append(
            "━━━━━━━━━━━━━━━━"
        )

    # --------------------------------------------------------
    # Movies.
    # --------------------------------------------------------

    movie_text = format_movies(
        movies
    )

    if movie_text:

        lines.append("")

        lines.append(
            movie_text
        )

        lines.append("")

        lines.append(
            "━━━━━━━━━━━━━━━━"
        )

    lines.append("")

    lines.append(
        f"🔎 Source: {SOURCE_NAME}"
    )

    return "\n".join(
        lines
    )


# ============================================================
# FORMAT SINGLE ANIME
#
# Used by old commands.py if it expects format_anime_info().
# ============================================================

def format_single_anime_info(
    anime_info: AnimeInfo
) -> str:

    title = obj_get(
        anime_info,
        "title",
        "Unknown"
    )

    poster = obj_get(
        anime_info,
        "poster_url",
        ""
    )

    hindi = obj_get(
        anime_info,
        "hindi_available",
        False
    )

    platform = obj_get(
        anime_info,
        "platform",
        ""
    )

    total_episodes = obj_get(
        anime_info,
        "total_episodes",
        0
    )

    hindi_episodes = obj_get(
        anime_info,
        "hindi_episodes",
        0
    )

    languages = obj_get(
        anime_info,
        "languages",
        []
    )

    status = obj_get(
        anime_info,
        "status",
        "Unknown"
    )

    last_episode = obj_get(
        anime_info,
        "last_episode",
        None
    )

    next_episode = obj_get(
        anime_info,
        "next_episode",
        ""
    )

    schedule = obj_get(
        anime_info,
        "schedule",
        ""
    )

    studio = obj_get(
        anime_info,
        "studio",
        ""
    )

    dub_by = obj_get(
        anime_info,
        "dub_by",
        ""
    )

    lines = []

    lines.append(
        "『Anime Hindi Info』"
    )

    lines.append("")

    lines.append(
        f"🎬 Anime: {title}"
    )

    lines.append(
        "🇮🇳 Hindi Dub: "
        + (
            "✅ Available"
            if hindi
            else "❌ Not Available"
        )
    )

    if platform:

        lines.append(
            f"📺 Platform: {platform}"
        )

    if total_episodes:

        lines.append(
            f"🎞 Episodes: "
            f"{total_episodes}"
        )

    if hindi_episodes:

        lines.append(
            f"🇮🇳 Hindi Episodes: "
            f"{hindi_episodes}"
        )

    if languages:

        lines.append(
            "🌐 Languages: "
            + ", ".join(
                languages
            )
        )

    if status:

        lines.append(
            f"📊 Status: {status}"
        )

    if last_episode:

        lines.append(
            f"🎞 Last Episode: "
            f"{last_episode}"
        )

    if schedule:

        lines.append(
            f"📅 Schedule: "
            f"{schedule}"
        )

    if next_episode:

        lines.append(
            f"⏭ Next Episode: "
            f"{next_episode}"
        )

    if studio:

        lines.append(
            f"🏢 Studio: {studio}"
        )

    # --------------------------------------------------------
    # Only show verified Dub By.
    # --------------------------------------------------------

    if dub_by:

        lines.append(
            f"🎙 Dub By: {dub_by}"
        )

    if poster:

        lines.append(
            f"\n🖼 Poster: {poster}"
        )

    lines.append("")

    lines.append(
        f"🔎 Source: {SOURCE_NAME}"
    )

    return "\n".join(
        lines
    )


# ============================================================
# PUBLIC FORMAT FUNCTION
#
# commands.py already imports:
#
# from services.anime_scraper import format_anime_info
#
# So this function MUST exist.
# ============================================================

def format_anime_info(
    anime_info: AnimeInfo
) -> str:

    # --------------------------------------------------------
    # If franchise data was attached in PART 6,
    # use the richer franchise output.
    # --------------------------------------------------------

    franchise = obj_get(
        anime_info,
        "franchise",
        None
    )

    if (
        franchise is not None
        and isinstance(
            franchise,
            FranchiseInfo
        )
    ):

        return format_franchise_info(
            franchise
        )

    # --------------------------------------------------------
    # Otherwise old single-anime format.
    # --------------------------------------------------------

    return format_single_anime_info(
        anime_info
    )


# ============================================================
# POSTER HELPER
# ============================================================

def get_poster_url(
    anime_info: AnimeInfo
) -> str:

    franchise = obj_get(
        anime_info,
        "franchise",
        None
    )

    if franchise is not None:

        poster = obj_get(
            franchise,
            "poster_url",
            ""
        )

        if poster:
            return poster

    return obj_get(
        anime_info,
        "poster_url",
        ""
    )


# ============================================================
# FINAL SEARCH RESULT
#
# This function is useful for testing from Python.
# ============================================================

async def search_anime(
    query: str
) -> AnimeInfo:

    return await get_anime_info(
        query
    )


# ============================================================
# END OF PART 7
# ============================================================
