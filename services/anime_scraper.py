# ============================================================
# anime_scraper.py
# Fixed and consolidated version
# ============================================================

from __future__ import annotations

import asyncio
import html
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Optional
from urllib.parse import quote_plus, urljoin, urlparse

import aiohttp
from bs4 import BeautifulSoup, Tag

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

REQUEST_TIMEOUT = 12  # seconds
CACHE_DIR = Path("anime_cache")
CACHE_DIR.mkdir(exist_ok=True)

ONGOING_CACHE_TTL = 5 * 60          # 5 minutes
COMPLETED_CACHE_TTL = 24 * 60 * 60  # 24 hours

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
# Dataclasses
# ------------------------------------------------------------

@dataclass
class Episode:
    number: int
    title: str = ""
    languages: list[str] = field(default_factory=list)
    release_date: Optional[str] = None

# Alias for backward compatibility
EpisodeInfo = Episode

@dataclass
class SearchCandidate:
    title: str
    url: str
    score: float = 0.0

@dataclass
class AnimeInfo:
    title: str = ""
    canonical_title: str = ""
    aliases: list[str] = field(default_factory=list)
    poster_url: Optional[str] = None
    source_url: Optional[str] = None
    url: Optional[str] = None  # kept for formatter compatibility
    source: str = "DC"
    hindi_available: bool = False
    platform: list[str] = field(default_factory=list)
    season: Optional[int] = None
    total_episodes: Optional[int] = None
    available_episodes: dict[str, int] = field(default_factory=dict)
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
    # Franchise / multi-series support
    franchise: Optional[str] = None
    franchise_key: Optional[str] = None
    franchise_series: list[AnimeInfo] = field(default_factory=list)
    franchise_movies: list[str] = field(default_factory=list)
    series_type: str = "series"
    related_series: list[str] = field(default_factory=list)
    movies: list[str] = field(default_factory=list)
    seasons: list[str] = field(default_factory=list)  # ← added
    scraped_at: float = field(default_factory=time.time)

@dataclass
class FranchiseInfo:
    name: str = ""
    series: list[AnimeInfo] = field(default_factory=list)
    movies: list[AnimeInfo] = field(default_factory=list)

# ------------------------------------------------------------
# Exceptions
# ------------------------------------------------------------
class AnimeNotFound(Exception):
    pass

class ScraperError(Exception):
    pass

# ============================================================
# HTTP / CACHE / TEXT UTILITIES
# ============================================================

async def create_session() -> aiohttp.ClientSession:
    timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
    connector = aiohttp.TCPConnector(limit=10, limit_per_host=5, ssl=False)
    return aiohttp.ClientSession(headers=HEADERS, timeout=timeout, connector=connector)

async def fetch(session: aiohttp.ClientSession, url: str) -> str:
    logger.info("Fetching: %s", url)
    try:
        async with session.get(url, allow_redirects=True) as response:
            if response.status != 200:
                raise ScraperError(f"HTTP {response.status}: {url}")
            return await response.text(errors="ignore")
    except asyncio.TimeoutError:
        raise ScraperError(f"Timeout: {url}")
    except aiohttp.ClientError as exc:
        raise ScraperError(f"Request failed: {url} -> {exc}")

# ------------------------------------------------------------
# Text helpers
# ------------------------------------------------------------
def clean_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value)
    text = html.unescape(text)
    text = text.replace("\xa0", " ").replace("\r", " ").replace("\n", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()

def normalize_title(title: str) -> str:
    title = clean_text(title).lower()
    # remove punctuation
    title = re.sub(r"[^\w\s]", " ", title, flags=re.UNICODE)
    title = re.sub(r"\s+", " ", title)
    # common site noise
    noise = {
        "season", "hindi", "dubbed", "dub", "episodes", "episode",
        "download", "hd", "watch", "online", "full", "complete"
    }
    words = [w for w in title.split() if w not in noise]
    return " ".join(words).strip()

def title_match_score(query: str, title: str) -> float:
    q = normalize_title(query)
    t = normalize_title(title)
    if not q or not t:
        return 0.0
    if q == t:
        return 100.0
    if q in t:
        return 90.0
    if t in q:
        return 80.0
    q_words = set(q.split())
    t_words = set(t.split())
    if not q_words or not t_words:
        return 0.0
    overlap = len(q_words & t_words)
    return (overlap / len(q_words)) * 70.0

def normalize_slug(value: str) -> str:
    value = value.lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-")

def extract_int(value: Optional[str]) -> Optional[int]:
    if not value:
        return None
    match = re.search(r"\b(\d{1,4})\b", value)
    return int(match.group(1)) if match else None

def extract_ints(value: Optional[str]) -> list[int]:
    if not value:
        return []
    return [int(x) for x in re.findall(r"\b\d{1,4}\b", value)]

def unique(items: Iterable[str]) -> list[str]:
    result = []
    seen = set()
    for item in items:
        item = clean_text(item)
        if not item:
            continue
        key = item.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result

# ------------------------------------------------------------
# Cache helpers (file‑based)
# ------------------------------------------------------------
def cache_file(url: str) -> Path:
    key = normalize_slug(url) or "home"
    return CACHE_DIR / f"{key[:180]}.json"

def read_cache(url: str) -> Optional[dict]:
    path = cache_file(url)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        timestamp = data.get("timestamp", 0)
        ttl = data.get("ttl", ONGOING_CACHE_TTL)
        if time.time() - timestamp > ttl:
            return None
        return data
    except Exception:
        return None

def write_cache(url: str, html: str, ttl: int) -> None:
    path = cache_file(url)
    payload = {"timestamp": time.time(), "ttl": ttl, "html": html}
    try:
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    except Exception as exc:
        logger.warning("Cache write failed: %s", exc)

async def fetch_cached(session: aiohttp.ClientSession, url: str, ttl: int = ONGOING_CACHE_TTL) -> str:
    cached = read_cache(url)
    if cached:
        logger.info("CACHE HIT: %s", url)
        return cached["html"]
    html_text = await fetch(session, url)
    write_cache(url, html_text, ttl)
    return html_text

# ============================================================
# SEARCH + TITLE RESOLVER
# ============================================================
def parse_search_results(html_text: str, query: str) -> list[SearchCandidate]:
    soup = BeautifulSoup(html_text, "html.parser")
    candidates = []
    selectors = [
        "article a", ".post a", ".item a", ".anime a",
        "h2 a", "h3 a"
    ]
    seen = set()
    for selector in selectors:
        for tag in soup.select(selector):
            href = tag.get("href")
            title = clean_text(tag.get_text(" ", strip=True))
            if not href or not title:
                continue
            href = urljoin(BASE_URL, href)
            if href in seen:
                continue
            seen.add(href)
            score = title_match_score(query, title)
            candidates.append(SearchCandidate(title=title, url=href, score=score))
    return candidates

async def search_anime(session: aiohttp.ClientSession, query: str) -> list[SearchCandidate]:
    query = clean_text(query)
    if not query:
        return []
    encoded = quote_plus(query)
    url = SEARCH_URL.format(query=encoded)
    html_text = await fetch_cached(session, url, ttl=ONGOING_CACHE_TTL)
    candidates = parse_search_results(html_text, query)
    # deduplicate by URL
    unique_c = {}
    for c in candidates:
        unique_c[c.url] = c
    candidates = list(unique_c.values())
    candidates.sort(key=lambda x: x.score, reverse=True)
    logger.info("Search results for %r: %d", query, len(candidates))
    return candidates

async def find_anime_page(session: aiohttp.ClientSession, query: str) -> Optional[SearchCandidate]:
    candidates = await search_anime(session, query)
    if not candidates:
        logger.warning("No search results found for %r", query)
        return None
    # prefer strong matches
    for candidate in candidates:
        if candidate.score >= 90:
            logger.info("Strong match: %s (%s)", candidate.title, candidate.url)
            return candidate
    best = candidates[0]
    logger.info("Best match: %s (score %.1f)", best.title, best.score)
    return best

# ============================================================
# FRANCHISE DEFINITIONS
# ============================================================
FRANCHISE_SERIES = {
    "dragon ball": [
        "Dragon Ball", "Dragon Ball Z", "Dragon Ball GT",
        "Dragon Ball Super", "Dragon Ball DAIMA"
    ],
    "naruto": ["Naruto", "Naruto Shippuden"],
    "one piece": ["One Piece"],
    "bleach": ["Bleach", "Bleach Thousand-Year Blood War"],
    "pokemon": [
        "Pokémon", "Pokémon Indigo League", "Pokémon Advanced",
        "Pokémon Diamond and Pearl", "Pokémon Black and White",
        "Pokémon XY", "Pokémon Sun and Moon", "Pokémon Journeys",
        "Pokémon Horizons"
    ],
    "digimon": [
        "Digimon Adventure", "Digimon Adventure 02", "Digimon Tamers",
        "Digimon Frontier", "Digimon Data Squad", "Digimon Fusion",
        "Digimon Adventure tri.", "Digimon Ghost Game"
    ],
    "yu gi oh": [
        "Yu-Gi-Oh!", "Yu-Gi-Oh! GX", "Yu-Gi-Oh! 5D's",
        "Yu-Gi-Oh! ZEXAL", "Yu-Gi-Oh! ARC-V", "Yu-Gi-Oh! VRAINS"
    ],
}

FRANCHISE_MOVIES = {
    "dragon ball": [
        "Dragon Ball Z: Dead Zone",
        "Dragon Ball Z: The World's Strongest",
        "Dragon Ball Z: The Tree of Might",
        "Dragon Ball Z: Lord Slug",
        "Dragon Ball Z: Cooler's Revenge",
        "Dragon Ball Z: Return of Cooler",
        "Dragon Ball Z: Super Android 13",
        "Dragon Ball Z: Broly",
        "Dragon Ball Z: Bojack Unbound",
        "Dragon Ball Z: Broly Second Coming",
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
        "Road to Ninja: Naruto the Movie",
        "The Last: Naruto the Movie",
        "Boruto: Naruto the Movie",
    ],
    "bleach": [
        "Bleach the Movie: Memories of Nobody",
        "Bleach the Movie: The DiamondDust Rebellion",
        "Bleach the Movie: Fade to Black",
        "Bleach the Movie: Hell Verse",
    ],
}

# ============================================================
# ANIME SCRAPER CLASS
# ============================================================
class AnimeScraper:
    def __init__(self, session: Optional[aiohttp.ClientSession] = None):
        self.session = session
        self._own_session = session is None

    async def __aenter__(self):
        if self.session is None:
            self.session = await create_session()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        if self._own_session and self.session:
            await self.session.close()

    async def ensure_session(self):
        if self.session is None:
            self.session = await create_session()

    # --------------------------------------------------------
    # Scrape one anime
    # --------------------------------------------------------
    async def scrape_single(self, query: str) -> Optional[AnimeInfo]:
        await self.ensure_session()
        logger.info("Scraping anime: %s", query)
        candidate = await find_anime_page(self.session, query)
        if not candidate:
            return None
        try:
            html_text = await fetch_cached(self.session, candidate.url, ttl=ONGOING_CACHE_TTL)
        except Exception as exc:
            logger.error("Failed to fetch anime page: %s", exc)
            return None
        try:
            anime = parse_anime_page(html_text, candidate.url, candidate.title)
        except Exception as exc:
            logger.exception("Anime page parsing failed: %s", exc)
            return None
        return anime

    # --------------------------------------------------------
    # Scrape franchise
    # --------------------------------------------------------
    async def scrape_franchise(self, query: str, franchise_key: str) -> Optional[AnimeInfo]:
        await self.ensure_session()
        logger.info("Scraping franchise: %s", franchise_key)

        series = await scrape_franchise_series(self, franchise_key)
        movies = await scrape_franchise_movies(self, franchise_key)

        if not series and not movies:
            logger.warning("No Hindi franchise data found: %s", franchise_key)
            return None

        return build_franchise_info(
            query=query,
            franchise_key=franchise_key,
            series=series,
            movies=movies
        )

    # --------------------------------------------------------
    # Main search method
    # --------------------------------------------------------
    async def search(self, query: str) -> Optional[AnimeInfo]:
        query = clean_text(query)
        if not query:
            return None
        normalized = normalize_title(query)

        # detect known franchise
        franchise_key = None
        for key in FRANCHISE_SERIES:
            nk = normalize_title(key)
            if normalized == nk or nk in normalized or normalized in nk:
                franchise_key = key
                break

        if franchise_key:
            logger.info("Known franchise detected: %s", franchise_key)
            try:
                franchise = await self.scrape_franchise(query, franchise_key)
                if franchise:
                    return franchise
            except Exception as exc:
                logger.exception("Franchise scrape failed: %s", exc)

        # normal single‑anime search
        return await self.scrape_single(query)

# ============================================================
# FRANCHISE HELPERS
# ============================================================
async def scrape_franchise_series(scraper: AnimeScraper, franchise_key: str) -> list[AnimeInfo]:
    titles = FRANCHISE_SERIES.get(franchise_key, [])
    results: list[AnimeInfo] = []
    for title in titles:
        try:
            info = await scraper.scrape_single(title)
            if not info:
                continue
            if not info.hindi_available:
                logger.info("Skipping non‑Hindi franchise series: %s", title)
                continue
            results.append(info)
        except Exception as exc:
            logger.warning("Franchise series failed: %s -> %s", title, exc)
    return results

async def scrape_franchise_movies(scraper: AnimeScraper, franchise_key: str) -> list[str]:
    titles = FRANCHISE_MOVIES.get(franchise_key, [])
    movies: list[str] = []
    for title in titles:
        try:
            info = await scraper.scrape_single(title)
            if not info or not info.hindi_available:
                continue
            movie_title = info.title.strip() if info.title else title
            if movie_title not in movies:
                movies.append(movie_title)
        except Exception as exc:
            logger.warning("Franchise movie failed: %s -> %s", title, exc)
    return movies

def build_franchise_info(query: str, franchise_key: str, series: list[AnimeInfo], movies: list[str]) -> AnimeInfo:
    franchise_name = query.strip()
    total_episodes = sum((a.total_episodes or 0) for a in series)
    hindi_total = sum((a.available_episodes.get("Hindi", 0) if a.available_episodes else 0) for a in series)

    if series:
        base = series[0]
        base.title = franchise_name
        base.franchise_key = franchise_key
        base.franchise_series = series
        base.franchise_movies = movies
        base.total_episodes = total_episodes
        base.available_episodes = {"Hindi": hindi_total}
        return base

    return AnimeInfo(
        title=franchise_name,
        franchise_key=franchise_key,
        franchise_series=[],
        franchise_movies=movies,
        hindi_available=bool(movies),
        total_episodes=total_episodes,
        available_episodes={"Hindi": hindi_total}
    )

# ============================================================
# PAGE PARSING
# ============================================================
def parse_anime_page(html_text: str, url: str, fallback_title: str = "") -> Optional[AnimeInfo]:
    soup = BeautifulSoup(html_text, "html.parser")

    # ----- Title -----
    title = ""
    title_selectors = ["h1", ".entry-title", ".anime-title", ".post-title", "meta[property='og:title']"]
    for sel in title_selectors:
        node = soup.select_one(sel)
        if not node:
            continue
        if node.name == "meta":
            value = node.get("content", "")
        else:
            value = node.get_text(" ", strip=True)
        value = clean_text(value)
        if value:
            title = value
            break
    if not title:
        title = fallback_title

    # ----- Poster -----
    poster_url = None
    poster_selectors = [
        "meta[property='og:image']", ".poster img", ".anime-poster img",
        ".post-thumbnail img", ".thumbnail img", "img"
    ]
    for sel in poster_selectors:
        node = soup.select_one(sel)
        if not node:
            continue
        if node.name == "meta":
            image = node.get("content", "")
        else:
            image = node.get("src") or node.get("data-src") or node.get("data-lazy-src") or ""
        if not image:
            continue
        poster_url = urljoin(url, image)
        break

    # ----- Page text -----
    text = clean_text(soup.get_text("\n", strip=True))

    # ----- Total episodes -----
    total_episodes = extract_total_episodes(text)

    # ----- Hindi availability -----
    hindi_available = detect_hindi(text)

    # ----- Seasons -----
    seasons = extract_seasons(text)

    # ----- Episode data -----
    episodes = extract_episode_data(soup)

    # ----- Hindi episode count -----
    hindi_count = sum(1 for ep in episodes if "Hindi" in ep.languages)
    available_episodes = {"Hindi": hindi_count}

    # ----- Movies -----
    movies = extract_movies(soup, text)

    # ----- Build AnimeInfo -----
    anime = AnimeInfo(
        title=title,
        url=url,
        source_url=url,
        poster_url=poster_url,
        total_episodes=total_episodes,
        available_episodes=available_episodes,
        hindi_available=hindi_available,
        episodes=episodes,
        seasons=seasons,
        movies=movies,
        source="DC"
    )
    return anime

# ------------------------------------------------------------
# Total episode extraction
# ------------------------------------------------------------
def extract_total_episodes(text: str) -> int:
    patterns = [
        r"total\s*episodes?\s*[:\-]?\s*(\d+)",
        r"episodes?\s*[:\-]?\s*(\d+)",
        r"episode\s*count\s*[:\-]?\s*(\d+)",
        r"(\d+)\s*episodes?",
    ]
    for pat in patterns:
        match = re.search(pat, text, re.IGNORECASE)
        if match:
            try:
                value = int(match.group(1))
                if value > 0:
                    return value
            except (TypeError, ValueError):
                continue
    return 0

# ------------------------------------------------------------
# Hindi detection
# ------------------------------------------------------------
def detect_hindi(text: str) -> bool:
    if not text:
        return False
    patterns = [
        r"\bhindi\b",
        r"\bhindi dubbed\b",
        r"\bhindi dub\b",
        r"\bdubbed in hindi\b",
        r"\blanguage\s*[:\-]?\s*hindi\b",
    ]
    for pat in patterns:
        if re.search(pat, text, re.IGNORECASE):
            return True
    return False

# ------------------------------------------------------------
# Season extraction
# ------------------------------------------------------------
def extract_seasons(text: str) -> list[str]:
    seasons = []
    patterns = [r"\bseason\s+\d+\b", r"\bs\d+\b", r"\bpart\s+\d+\b"]
    for pat in patterns:
        for match in re.findall(pat, text, re.IGNORECASE):
            match = clean_text(match)
            if match and match not in seasons:
                seasons.append(match)
    return seasons

# ------------------------------------------------------------
# Episode data extraction
# ------------------------------------------------------------
def extract_episode_data(soup: BeautifulSoup) -> list[Episode]:
    episodes = []
    selectors = [
        ".episode", ".episodes a", ".episode-list a",
        ".ep-list a", ".episodelist a", "a[href*='episode']"
    ]
    seen = set()
    for sel in selectors:
        for node in soup.select(sel):
            href = node.get("href")
            title = clean_text(node.get_text(" ", strip=True))
            if not title and not href:
                continue
            key = (href, title)
            if key in seen:
                continue
            seen.add(key)
            number = extract_episode_number(title)
            if number <= 0:
                number = len(episodes) + 1
            languages = detect_episode_languages(title)
            episodes.append(Episode(number=number, title=title, languages=languages))
    episodes.sort(key=lambda e: e.number)
    return episodes

def extract_episode_number(text: str) -> int:
    patterns = [
        r"\bepisode\s*(\d+)\b",
        r"\bep\.?\s*(\d+)\b",
        r"\bep\s*[-:]?\s*(\d+)\b",
        r"\bE(\d+)\b",
    ]
    for pat in patterns:
        match = re.search(pat, text, re.IGNORECASE)
        if match:
            try:
                return int(match.group(1))
            except (TypeError, ValueError):
                pass
    return 0

def detect_episode_languages(text: str) -> list[str]:
    langs = []
    if re.search(r"\bhindi\b", text, re.IGNORECASE):
        langs.append("Hindi")
    if re.search(r"\benglish\b", text, re.IGNORECASE):
        langs.append("English")
    if re.search(r"\bjapanese\b", text, re.IGNORECASE):
        langs.append("Japanese")
    return langs

# ------------------------------------------------------------
# Movie extraction
# ------------------------------------------------------------
def extract_movies(soup: BeautifulSoup, text: str) -> list[str]:
    movies = []
    selectors = [".movie a", ".movies a", ".movie-list a", "a[href*='movie']"]
    seen = set()
    for sel in selectors:
        for node in soup.select(sel):
            title = clean_text(node.get_text(" ", strip=True))
            if not title:
                continue
            if title.lower() in seen:
                continue
            seen.add(title.lower())
            movies.append(title)
    return movies

# ============================================================
# FORMATTERS
# ============================================================
def format_episode(episode: Episode) -> str:
    number = episode.number
    title = episode.title.strip() if episode.title else ""
    if title:
        return f"Episode {number} — {title}"
    return f"Episode {number}"

def format_single_anime_info(anime: AnimeInfo) -> str:
    lines = []
    title = anime.title.strip() if anime.title else "Unknown Anime"
    lines.append(f"🎌 {title}")

    if getattr(anime, "url", None):
        lines.append(f"🔗 {anime.url}")

    seasons = getattr(anime, "seasons", None)
    if seasons:
        lines.append("")
        lines.append("📂 Seasons:")
        for season in seasons:
            lines.append(f"   • {season}")

    total = getattr(anime, "total_episodes", 0)
    if total:
        lines.append("")
        lines.append(f"🎞 Total Episodes: {total}")

    hindi_count = anime.available_episodes.get("Hindi", 0) if anime.available_episodes else 0
    if hindi_count:
        lines.append("")
        lines.append(f"📚 Hindi Episodes: {hindi_count}")

    if anime.hindi_available:
        lines.append("🇮🇳 Hindi Available: Yes")
    else:
        lines.append("🇮🇳 Hindi Available: No")

    movies = getattr(anime, "movies", None)
    if movies:
        lines.append("")
        lines.append("🎬 Movies:")
        for movie in movies:
            if isinstance(movie, str):
                movie_title = movie
            else:
                movie_title = getattr(movie, "title", str(movie))
            if movie_title:
                lines.append(f"   • {movie_title}")

    return "\n".join(lines)

def format_franchise_info(anime: AnimeInfo) -> str:
    lines = []
    franchise_title = anime.title.strip() if anime.title else "Anime Franchise"
    lines.append(f"🎌 {franchise_title}")

    series = getattr(anime, "franchise_series", [])
    if series:
        lines.append("")
        lines.append("📺 Hindi Available Series:")
        for item in series:
            item_title = getattr(item, "title", "") or "Unknown"
            lines.append(f"\n🔹 {item_title}")
            item_seasons = getattr(item, "seasons", [])
            if item_seasons:
                lines.append("   📂 Seasons:")
                for season in item_seasons:
                    lines.append(f"      • {season}")
            item_total = getattr(item, "total_episodes", 0)
            if item_total:
                lines.append(f"   🎞 Total Episodes: {item_total}")
            item_hindi = item.available_episodes.get("Hindi", 0) if item.available_episodes else 0
            if item_hindi:
                lines.append(f"   📚 Hindi Episodes: {item_hindi}")
            lines.append("   🇮🇳 Hindi Available: Yes")

    movies = getattr(anime, "franchise_movies", [])
    if movies:
        lines.append("")
        lines.append("🎬 Movies:")
        for movie in movies:
            if isinstance(movie, str):
                movie_title = movie
            else:
                movie_title = getattr(movie, "title", str(movie))
            if movie_title:
                lines.append(f"   • {movie_title}")

    return "\n".join(lines)

def format_anime_info(anime: AnimeInfo) -> str:
    franchise_series = getattr(anime, "franchise_series", None)
    franchise_movies = getattr(anime, "franchise_movies", None)
    if franchise_series or franchise_movies:
        result = format_franchise_info(anime)
    else:
        result = format_single_anime_info(anime)

    if not result.strip():
        title = (
            getattr(anime, "canonical_title", None)
            or getattr(anime, "title", None)
            or "Unknown Anime"
        )
        return f"🎌 {title}\n\n❌ No anime information found."
    return result

# ============================================================
# HELPERS
# ============================================================
def get_hindi_episode_count(anime: AnimeInfo) -> int:
    available = getattr(anime, "available_episodes", {})
    if not available:
        return 0
    if isinstance(available, dict):
        try:
            return int(available.get("Hindi", 0))
        except (TypeError, ValueError):
            return 0
    try:
        return int(available)
    except (TypeError, ValueError):
        return 0

def get_poster_url(anime: AnimeInfo) -> Optional[str]:
    poster = getattr(anime, "poster_url", None)
    if not poster:
        poster = getattr(anime, "poster", None)
    if not poster:
        return None
    poster = str(poster).strip()
    if not poster or not re.match(r"^https?://", poster, re.IGNORECASE):
        return None
    return poster

def safe_format_text(anime: Optional[AnimeInfo]) -> str:
    if anime is None:
        return "❌ No anime information found."
    try:
        return format_anime_info(anime)
    except Exception as exc:
        logger.exception("Anime formatter failed: %s", exc)
        title = getattr(anime, "title", None) or "Unknown Anime"
        return f"🎌 {title}\n\n❌ Unable to format anime information."

# ============================================================
# PUBLIC API
# ============================================================
async def get_anime_info(query: str) -> Optional[AnimeInfo]:
    query = clean_text(query)
    if not query:
        return None
    try:
        async with AnimeScraper() as scraper:
            return await scraper.search(query)
    except Exception as exc:
        logger.exception("Anime search failed for %r: %s", query, exc)
        return None

async def search_and_format(query: str) -> tuple[Optional[AnimeInfo], str]:
    query = clean_text(query)
    if not query:
        return None, "❌ Please enter an anime name."
    try:
        anime = await get_anime_info(query)
        if anime is None:
            return None, f"❌ Unable to fetch anime information for: {query}\n\nYe temporary problem ho sakti hai.\nThodi der baad dobara try karo."
        log_anime_summary(anime)
        result = safe_format_text(anime)
        if not result.strip():
            return anime, f"❌ Unable to fetch anime information for: {query}\n\nYe temporary problem ho sakti hai.\nThodi der baad dobara try karo."
        return anime, result
    except Exception as exc:
        logger.exception("search_and_format failed: %s", exc)
        return None, f"❌ Unable to fetch anime information for: {query}\n\nYe temporary problem ho sakti hai.\nThodi der baad dobara try karo."

def log_anime_summary(anime: Optional[AnimeInfo]) -> None:
    if anime is None:
        logger.info("Anime result: None")
        return
    title = getattr(anime, "title", "Unknown")
    total = getattr(anime, "total_episodes", 0)
    hindi = get_hindi_episode_count(anime)
    logger.info("Anime result: title=%s total=%d hindi=%d", title, total, hindi)

async def get_formatted_anime_info(query: str) -> str:
    try:
        anime = await get_anime_info(query)
        if not anime:
            return "❌ Anime information not found."
        return format_anime_info(anime)
    except AnimeNotFound:
        return "❌ Anime not found.\n\nAnime ka exact naam try karo."
    except asyncio.TimeoutError:
        return "⏳ Request timeout.\n\nThodi der baad dobara try karo."
    except Exception as exc:
        logger.exception("Anime lookup failed: %s", query)
        return "❌ Error: Unable to fetch anime information.\n\nYe temporary problem ho sakti hai.\nThodi der baad dobara try karo."

# ============================================================
# OPTIONAL CLI TEST
# ============================================================
async def _cli_test(query: str) -> None:
    try:
        result = await get_formatted_anime_info(query)
        print(result)
    except KeyboardInterrupt:
        print("\nStopped.")

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        query = " ".join(sys.argv[1:])
        asyncio.run(_cli_test(query))
    else:
        print("Usage: python anime_scraper.py <anime name>")
