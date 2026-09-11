# ============================================================
# anime_scraper.py
# Anime Hindi Info Bot - RareAnimes Scraper
# PART 1 / 7
# ============================================================

import re
import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import urljoin, quote

import aiohttp
from bs4 import BeautifulSoup


# ============================================================
# CONFIG
# ============================================================

BASE_URL = "https://www.rareanimes.mov/"
REQUEST_TIMEOUT = 12
MAX_SEARCH_RESULTS = 15
MAX_PAGES_TO_CHECK = 10

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 10; Mobile) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0 Mobile Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

logger = logging.getLogger(__name__)


# ============================================================
# DATA CLASSES
# ============================================================

@dataclass
class Episode:
    number: int | None = None
    title: str = ""
    languages: list[str] = field(default_factory=list)
    release_date: str = ""


@dataclass
class AnimeSeason:
    anime_name: str = ""
    season: int | None = None

    episodes: int = 0
    total_episodes: int | None = None

    languages: list[str] = field(default_factory=list)
    platforms: list[str] = field(default_factory=list)

    release_date: str = ""
    status: str = "Unknown"

    poster: str = ""
    url: str = ""

    schedule: str = ""
    next_episode: int | None = None
    next_release: str = ""

    studio: str = ""
    dub_by: str = ""

    episodes_data: list[Episode] = field(
        default_factory=list
    )


@dataclass
class AnimeResult:
    title: str = ""
    poster: str = ""

    hindi_available: bool = False

    platforms: list[str] = field(
        default_factory=list
    )

    languages: list[str] = field(
        default_factory=list
    )

    seasons: list[AnimeSeason] = field(
        default_factory=list
    )

    status: str = "Unknown"

    last_episode: int | None = None
    last_release: str = ""

    next_episode: int | None = None
    next_release: str = ""
    schedule: str = ""

    studio: str = ""
    dub_by: str = ""

    source: str = "DC"
    url: str = ""


# ============================================================
# BASIC HELPERS
# ============================================================

def clean_text(text: str) -> str:
    if not text:
        return ""

    text = text.replace("\xa0", " ")
    text = re.sub(r"\s+", " ", text)

    return text.strip()


def normalize_title(title: str) -> str:
    if not title:
        return ""

    title = title.lower()
    title = title.replace("&", " and ")

    title = re.sub(
        r"[’'`]",
        "",
        title
    )

    title = re.sub(
        r"[^a-z0-9]+",
        " ",
        title
    )

    remove_words = {
        "anime",
        "series",
        "season",
        "episode",
        "episodes",
        "hindi",
        "dubbed",
        "download",
        "hd",
        "web",
        "webdl",
    }

    words = [
        word
        for word in title.split()
        if word not in remove_words
    ]

    return " ".join(words).strip()


# ============================================================
# LANGUAGE NORMALIZATION
# ============================================================

LANGUAGE_MAP = {
    "hindi": "Hindi",
    "english": "English",
    "tamil": "Tamil",
    "telugu": "Telugu",
    "malayalam": "Malayalam",
    "kannada": "Kannada",
    "bengali": "Bengali",
    "marathi": "Marathi",
    "japanese": "Japanese",
    "chinese": "Chinese",
    "mandarin": "Mandarin",
}


def normalize_language(value: str) -> str:
    value = clean_text(value)

    return LANGUAGE_MAP.get(
        value.lower(),
        value
    )


def unique_list(items: list[str]) -> list[str]:
    result = []

    for item in items:
        item = clean_text(item)

        if not item:
            continue

        if item.lower() not in [
            x.lower() for x in result
        ]:
            result.append(item)

    return result


# ============================================================
# HTTP SCRAPER
# ============================================================

class RareAnimeScraper:

    def __init__(self):
        self.session = None
        self.cache = {}

    async def __aenter__(self):

        timeout = aiohttp.ClientTimeout(
            total=REQUEST_TIMEOUT
        )

        connector = aiohttp.TCPConnector(
            limit=20,
            ttl_dns_cache=300,
            ssl=False
        )

        self.session = aiohttp.ClientSession(
            headers=HEADERS,
            timeout=timeout,
            connector=connector
        )

        return self

    async def __aexit__(
        self,
        exc_type,
        exc_val,
        exc_tb
    ):

        if self.session:
            await self.session.close()

    async def fetch(
        self,
        url: str
    ) -> str | None:

        if not url:
            return None

        if url in self.cache:
            return self.cache[url]

        try:

            async with self.session.get(
                url,
                allow_redirects=True
            ) as response:

                if response.status != 200:
                    logger.warning(
                        "HTTP %s: %s",
                        response.status,
                        url
                    )
                    return None

                html = await response.text(
                    errors="ignore"
                )

                self.cache[url] = html

                return html

        except asyncio.TimeoutError:

            logger.warning(
                "Timeout: %s",
                url
            )

        except Exception as error:

            logger.error(
                "Fetch error: %s",
                error
            )

        return None

# ============================================================
# PART 2 / 7 - SEARCH + TITLE MATCHING
# ============================================================

ALIASES = {
    "naruto": [
        "Naruto",
        "Naruto Shippuden",
    ],

    "naruto shippuden": [
        "Naruto Shippuden",
    ],

    "re zero": [
        "Re:ZERO -Starting Life in Another World",
        "Re Zero",
    ],

    "rezero": [
        "Re:ZERO -Starting Life in Another World",
    ],

    "konosuba": [
        "KonoSuba",
        "KonoSuba: God's Blessing on This Wonderful World!",
    ],

    "spy x family": [
        "SPY x FAMILY",
    ],

    "spy family": [
        "SPY x FAMILY",
    ],

    "black torch": [
        "BLACK TORCH",
    ],

    "dragon ball": [
        "Dragon Ball",
        "Dragon Ball Z",
        "Dragon Ball GT",
        "Dragon Ball Super",
        "Dragon Ball DAIMA",
        "Dragon Ball Kai",
    ],
}


def get_aliases(query: str) -> list[str]:

    query = normalize_title(query)

    for key, names in ALIASES.items():

        if query == normalize_title(key):
            return names

    return [query]


def title_matches(
    query: str,
    title: str
) -> bool:

    q = normalize_title(query)
    t = normalize_title(title)

    if not q or not t:
        return False

    if q == t:
        return True

    if q in t:
        return True

    if t in q:
        return True

    return False


# ============================================================
# RAREANIMES SEARCH
# ============================================================

async def search(
    self,
    query: str
) -> list[str]:

    if not query:
        return []

    search_url = (
        BASE_URL
        + "?s="
        + quote(query)
    )

    html = await self.fetch(
        search_url
    )

    if not html:
        return []

    soup = BeautifulSoup(
        html,
        "html.parser"
    )

    urls = []

    for link in soup.find_all(
        "a",
        href=True
    ):

        href = link.get("href", "")

        if not href:
            continue

        full_url = urljoin(
            BASE_URL,
            href
        )

        if "rareanimes.mov" not in full_url:
            continue

        if full_url.rstrip("/") == BASE_URL.rstrip("/"):
            continue

        if full_url in urls:
            continue

        urls.append(full_url)

        if len(urls) >= MAX_SEARCH_RESULTS:
            break

    return urls


# ============================================================
# SEARCH WITH ALIASES
# ============================================================

async def search_all(
    self,
    query: str
) -> list[str]:

    queries = get_aliases(query)

    tasks = [
        self.search(q)
        for q in queries
    ]

    results = await asyncio.gather(
        *tasks,
        return_exceptions=True
    )

    urls = []

    for result in results:

        if isinstance(
            result,
            Exception
        ):
            continue

        for url in result:

            if url not in urls:
                urls.append(url)

            if len(urls) >= MAX_SEARCH_RESULTS:
                return urls

    return urls


# ============================================================
# TITLE SCORE
# ============================================================

def title_score(
    query: str,
    title: str
) -> int:

    q = normalize_title(query)
    t = normalize_title(title)

    if not q or not t:
        return 0

    if q == t:
        return 100

    if t.startswith(q):
        return 90

    if q in t:
        return 80

    query_words = set(q.split())
    title_words = set(t.split())

    common = len(
        query_words & title_words
    )

    if common == len(query_words):
        return 70

    if common:
        return 40 + common

    return 0

# ============================================================
# PART 3 / 7 - PAGE DETAILS
# ============================================================

def find_field(
    self,
    text: str,
    patterns: list[str]
) -> str:

    for pattern in patterns:

        match = re.search(
            pattern,
            text,
            re.I
        )

        if match:
            return clean_text(
                match.group(1)
            )

    return ""


def extract_season(
    self,
    text: str
) -> int | None:

    patterns = [
        r"\bSeason\s*0*(\d{1,3})\b",
        r"\bS\s*0*(\d{1,3})\b",
    ]

    for pattern in patterns:

        match = re.search(
            pattern,
            text,
            re.I
        )

        if match:
            return int(
                match.group(1)
            )

    return None


def extract_episode_count(
    self,
    text: str
) -> int:

    patterns = [
        r"(\d+)\s+Episodes?",
        r"Episodes?\s*:\s*(\d+)",
        r"(\d+)\s+Episodes?\s*\(",
    ]

    for pattern in patterns:

        match = re.search(
            pattern,
            text,
            re.I
        )

        if match:
            return int(
                match.group(1)
            )

    return 0


def extract_total_episodes(
    self,
    text: str
) -> int | None:

    match = re.search(
        r"\((\d+)\s+in\s+Total\)",
        text,
        re.I
    )

    if match:
        return int(
            match.group(1)
        )

    return None


def extract_poster(
    self,
    soup: BeautifulSoup
) -> str:

    selectors = [
        "meta[property='og:image']",
        "meta[name='twitter:image']",
    ]

    for selector in selectors:

        tag = soup.select_one(
            selector
        )

        if tag:

            image = tag.get(
                "content",
                ""
            )

            if image:
                return urljoin(
                    BASE_URL,
                    image
                )

    image = soup.find(
        "img"
    )

    if image:

        for attr in [
            "data-src",
            "data-lazy-src",
            "src",
        ]:

            value = image.get(
                attr,
                ""
            )

            if value:
                return urljoin(
                    BASE_URL,
                    value
                )

    return ""


def extract_platforms(
    self,
    text: str
) -> list[str]:

    value = self.find_field(
        text,
        [
            r"Network\s*:\s*([^\n]+)",
            r"Platform\s*:\s*([^\n]+)",
        ]
    )

    if not value:
        return []

    value = re.sub(
        r"\bIndia\b",
        "",
        value,
        flags=re.I
    )

    parts = re.split(
        r"\s*(?:,|\||•|\+)\s*",
        value
    )

    return unique_list(
        parts
    )


def extract_languages(
    self,
    text: str
) -> list[str]:

    value = self.find_field(
        text,
        [
            r"Language\s*:\s*([^\n]+)",
            r"Languages\s*:\s*([^\n]+)",
        ]
    )

    if not value:
        return []

    result = []

    for key, name in LANGUAGE_MAP.items():

        if re.search(
            rf"\b{re.escape(key)}\b",
            value,
            re.I
        ):
            result.append(name)

    return unique_list(
        result
    )


def extract_release_date(
    self,
    text: str
) -> str:

    value = self.find_field(
        text,
        [
            r"Indian\s+Release\s+Date\s*:\s*([^\n]+)",
            r"Release\s+Date\s*:\s*([^\n]+)",
            r"Year\s*:\s*([^\n]+)",
        ]
    )

    return clean_text(
        value
            )

# ============================================================
# PART 4 / 7 - STATUS + SCHEDULE + NEXT EPISODE
# ============================================================


# ============================================================
# BETTER FIELD EXTRACTOR
# ============================================================

def find_field(
    self,
    text: str,
    patterns: list[str]
) -> str:

    labels = (
        r"Season|Episodes?|Language(?:s)?|Network|Platform|"
        r"Indian\s+Release\s+Date|Release\s+Date|Year|"
        r"Status|Schedule|Studio|Dub\s*By"
    )

    for pattern in patterns:

        match = re.search(
            pattern,
            text,
            re.I
        )

        if match:
            value = clean_text(
                match.group(1)
            )

            # Agar flattened page text hai,
            # next field ke baad ka text hata do.
            value = re.split(
                rf"\s+(?={labels}\s*:)",
                value,
                maxsplit=1,
                flags=re.I
            )[0]

            return clean_text(value)

    return ""


# ============================================================
# STATUS
# ============================================================

def extract_status(
    self,
    text: str
) -> str:

    lower = text.lower()

    # Weekly / next episode ka signal = Ongoing
    ongoing_patterns = [
        r"1\s+new\s+episode\s+every",
        r"new\s+episode\s+every",
        r"next\s+episode",
        r"weekly",
        r"every\s+(?:monday|tuesday|wednesday|"
        r"thursday|friday|saturday|sunday)",
    ]

    for pattern in ongoing_patterns:

        if re.search(
            pattern,
            lower,
            re.I
        ):
            return "Ongoing"

    # Finale / completed signal
    complete_patterns = [
        r"season\s+finale",
        r"series\s+finale",
        r"complete",
        r"completed",
    ]

    for pattern in complete_patterns:

        if re.search(
            pattern,
            lower,
            re.I
        ):
            return "Completed"

    return "Unknown"


# ============================================================
# SCHEDULE
# ============================================================

def extract_schedule(
    self,
    text: str
) -> str:

    patterns = [

        r"1\s+New\s+Episode\s+Every\s+"
        r"(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)",

        r"New\s+Episode\s+Every\s+"
        r"(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)",

        r"Every\s+"
        r"(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)",

        r"Schedule\s*:\s*"
        r"([^|]+?)(?=\s+(?:Studio|Dub\s*By|Language|Network)\s*:|$)",
    ]

    for pattern in patterns:

        match = re.search(
            pattern,
            text,
            re.I
        )

        if not match:
            continue

        value = clean_text(
            match.group(1)
        )

        if value.lower().startswith(
            ("monday", "tuesday", "wednesday",
             "thursday", "friday", "saturday",
             "sunday")
        ):
            return "Every " + value

        return value

    return ""


# ============================================================
# NEXT EPISODE
# ============================================================

def extract_next_episode(
    self,
    text: str
) -> int | None:

    patterns = [

        r"Next\s+Episode\s*:?\s*"
        r"(?:Episode\s*)?(\d+)",

        r"Episode\s+(\d+)\s+will\s+be",

        r"Episode\s+(\d+)\s+coming",

    ]

    for pattern in patterns:

        match = re.search(
            pattern,
            text,
            re.I
        )

        if match:
            return int(
                match.group(1)
            )

    return None


# ============================================================
# NEXT RELEASE DATE
# ============================================================

def extract_next_release(
    self,
    text: str
) -> str:

    patterns = [

        r"Expected\s+Release\s*:\s*"
        r"(.+?)(?=\s+(?:Schedule|Studio|Dub\s*By)\s*:|$)",

        r"Next\s+Episode\s+.*?"
        r"on\s+([A-Z][a-z]+\s+\d{1,2},?\s+\d{4})",

        r"(\d{1,2}\s+"
        r"(?:January|February|March|April|May|June|"
        r"July|August|September|October|November|December)"
        r"\s+\d{4})",
    ]

    for pattern in patterns:

        match = re.search(
            pattern,
            text,
            re.I
        )

        if match:

            value = clean_text(
                match.group(1)
            )

            if value:
                return value

    return ""


# ============================================================
# STUDIO
# ============================================================

def extract_studio(
    self,
    text: str
) -> str:

    return self.find_field(
        text,
        [
            r"Studio\s*:\s*"
            r"(.+?)(?=\s+(?:Dub\s*By|Language|Network|Platform)\s*:|$)",
        ]
    )


# ============================================================
# DUB BY
# ============================================================

def extract_dub_by(
    self,
    text: str
) -> str:

    return self.find_field(
        text,
        [
            r"Dub\s*By\s*:\s*"
            r"(.+?)(?=\s+(?:Studio|Language|Network|Platform)\s*:|$)",
        ]
    )


# ============================================================
# EPISODE LIST
# ============================================================

def extract_episodes_data(
    self,
    soup: BeautifulSoup
) -> list[Episode]:

    episodes = []

    # RareAnimes episode links / headings
    for tag in soup.find_all(
        ["a", "h2", "h3", "h4", "li"]
    ):

        text = clean_text(
            tag.get_text(" ", strip=True)
        )

        if not text:
            continue

        match = re.search(
            r"\bEpisode\s*[-:]?\s*(\d{1,4})\b",
            text,
            re.I
        )

        if not match:
            continue

        number = int(
            match.group(1)
        )

        if any(
            ep.number == number
            for ep in episodes
        ):
            continue

        episodes.append(
            Episode(
                number=number,
                title=text
            )
        )

    episodes.sort(
        key=lambda x: (
            x.number is None,
            x.number or 0
        )
    )

    return episodes


# ============================================================
# BIND PART 2 + PART 3 + PART 4 METHODS
# ============================================================

RareAnimeScraper.search = search
RareAnimeScraper.search_all = search_all

RareAnimeScraper.find_field = find_field
RareAnimeScraper.extract_season = extract_season
RareAnimeScraper.extract_episode_count = extract_episode_count
RareAnimeScraper.extract_total_episodes = extract_total_episodes
RareAnimeScraper.extract_poster = extract_poster
RareAnimeScraper.extract_platforms = extract_platforms
RareAnimeScraper.extract_languages = extract_languages
RareAnimeScraper.extract_release_date = extract_release_date

RareAnimeScraper.extract_status = extract_status
RareAnimeScraper.extract_schedule = extract_schedule
RareAnimeScraper.extract_next_episode = extract_next_episode
RareAnimeScraper.extract_next_release = extract_next_release
RareAnimeScraper.extract_studio = extract_studio
RareAnimeScraper.extract_dub_by = extract_dub_by
RareAnimeScraper.extract_episodes_data = extract_episodes_data


# ============================================================
# PART 5 / 7 - PAGE PARSER + SEASON DATA
# ============================================================


def parse_page(
    self,
    url: str
) -> AnimeSeason | None:

    html = await self.fetch(url)

    if not html:
        return None

    soup = BeautifulSoup(
        html,
        "html.parser"
    )

    # --------------------------------------------------------
    # PAGE TEXT
    # --------------------------------------------------------

    text = clean_text(
        soup.get_text(
            " ",
            strip=True
        )
    )

    if not text:
        return None

    # --------------------------------------------------------
    # TITLE
    # --------------------------------------------------------

    title = ""

    title_tag = soup.find(
        "h1"
    )

    if title_tag:
        title = clean_text(
            title_tag.get_text(
                " ",
                strip=True
            )
        )

    if not title:

        meta = soup.find(
            "meta",
            property="og:title"
        )

        if meta:
            title = clean_text(
                meta.get(
                    "content",
                    ""
                )
            )

    if not title:

        title = self.find_field(
            text,
            [
                r"Full\s+Name\s*:\s*(.+?)(?=\s+(?:Season|Episodes?|Language|Network)\s*:|$)"
            ]
        )

    # Remove common site suffixes
    title = re.sub(
        r"\s*[-|]\s*(Rare\s*Anime|RareAnimes).*$",
        "",
        title,
        flags=re.I
    )

    title = clean_text(title)

    if not title:
        return None

    # --------------------------------------------------------
    # SEASON
    # --------------------------------------------------------

    season = self.extract_season(
        text
    )

    # --------------------------------------------------------
    # EPISODES
    # --------------------------------------------------------

    episodes = self.extract_episode_count(
        text
    )

    total_episodes = self.extract_total_episodes(
        text
    )

    # --------------------------------------------------------
    # LANGUAGE
    # --------------------------------------------------------

    languages = self.extract_languages(
        text
    )

    # --------------------------------------------------------
    # PLATFORM
    # --------------------------------------------------------

    platforms = self.extract_platforms(
        text
    )

    # --------------------------------------------------------
    # RELEASE DATE
    # --------------------------------------------------------

    release_date = self.extract_release_date(
        text
    )

    # --------------------------------------------------------
    # SCHEDULE
    # --------------------------------------------------------

    schedule = self.extract_schedule(
        text
    )

    # --------------------------------------------------------
    # STATUS
    # --------------------------------------------------------

    status = self.extract_status(
        text
    )

    # --------------------------------------------------------
    # NEXT EPISODE
    # --------------------------------------------------------

    next_episode = self.extract_next_episode(
        text
    )

    next_release = self.extract_next_release(
        text
    )

    # --------------------------------------------------------
    # STUDIO / DUB
    # --------------------------------------------------------

    studio = self.extract_studio(
        text
    )

    dub_by = self.extract_dub_by(
        text
    )

    # --------------------------------------------------------
    # POSTER
    # --------------------------------------------------------

    poster = self.extract_poster(
        soup
    )

    # --------------------------------------------------------
    # EPISODE DATA
    # --------------------------------------------------------

    episodes_data = self.extract_episode_data(
        soup
    )

    # --------------------------------------------------------
    # HINDI CHECK
    # --------------------------------------------------------

    hindi_available = any(
        language.lower() == "hindi"
        for language in languages
    )

    # --------------------------------------------------------
    # RETURN SEASON
    # --------------------------------------------------------

    return AnimeSeason(
        anime_name=title,
        season=season,

        episodes=episodes,
        total_episodes=total_episodes,

        languages=languages,
        platforms=platforms,

        release_date=release_date,
        status=status,

        poster=poster,
        url=url,

        schedule=schedule,
        next_episode=next_episode,
        next_release=next_release,

        studio=studio,
        dub_by=dub_by,

        episodes_data=episodes_data,
    )


# ============================================================
# PARSE MULTIPLE PAGES
# ============================================================

async def parse_pages(
    self,
    urls: list[str]
) -> list[AnimeSeason]:

    if not urls:
        return []

    tasks = [
        self.parse_page(url)
        for url in urls[
            :MAX_PAGES_TO_CHECK
        ]
    ]

    results = await asyncio.gather(
        *tasks,
        return_exceptions=True
    )

    seasons = []

    for result in results:

        if isinstance(
            result,
            Exception
        ):
            logger.warning(
                "Page parse error: %s",
                result
            )
            continue

        if not result:
            continue

        # Sirf Hindi available pages
        if result.languages:

            hindi = any(
                x.lower() == "hindi"
                for x in result.languages
            )

            if not hindi:
                continue

        seasons.append(
            result
        )

    # Season order
    seasons.sort(
        key=lambda x: (
            x.season is None,
            x.season or 999
        )
    )

    return seasons


# ============================================================
# MERGE UNIQUE VALUES
# ============================================================

def merge_values(
    values: list[list[str]]
) -> list[str]:

    merged = []

    for value_list in values:

        for value in value_list:

            if not value:
                continue

            if value.lower() not in [
                x.lower()
                for x in merged
            ]:
                merged.append(value)

    return merged


# ============================================================
# GET LAST SEASON
# ============================================================

def get_last_season(
    seasons: list[AnimeSeason]
) -> AnimeSeason | None:

    if not seasons:
        return None

    valid = [
        x for x in seasons
        if x.season is not None
    ]

    if valid:

        return max(
            valid,
            key=lambda x: x.season
        )

    return seasons[-1]


# ============================================================
# BIND PART 5 METHODS
# ============================================================

RareAnimeScraper.parse_page = parse_page
RareAnimeScraper.parse_pages = parse_pages

RareAnimeScraper.merge_values = merge_values
RareAnimeScraper.get_last_season = get_last_season


# ============================================================
# PART 6 / 7 - RESULT MERGE + GROUPING
# ============================================================


def same_anime_title(
    title1: str,
    title2: str
) -> bool:

    a = normalize_title(title1)
    b = normalize_title(title2)

    if not a or not b:
        return False

    return (
        a == b
        or a in b
        or b in a
    )


# ============================================================
# GROUP SEASONS
# ============================================================

def group_seasons(
    self,
    seasons: list[AnimeSeason]
) -> list[list[AnimeSeason]]:

    groups = []

    for season in seasons:

        placed = False

        for group in groups:

            if same_anime_title(
                season.anime_name,
                group[0].anime_name
            ):
                group.append(season)
                placed = True
                break

        if not placed:
            groups.append(
                [season]
            )

    return groups


# ============================================================
# BUILD ANIME RESULT
# ============================================================

def build_result(
    self,
    seasons: list[AnimeSeason]
) -> AnimeResult | None:

    if not seasons:
        return None

    # --------------------------------------------------------
    # TITLE
    # --------------------------------------------------------

    title = seasons[0].anime_name

    # Prefer longest useful title
    for season in seasons:

        if len(season.anime_name) > len(title):
            title = season.anime_name

    # --------------------------------------------------------
    # POSTER
    # --------------------------------------------------------

    poster = ""

    for season in seasons:

        if season.poster:
            poster = season.poster
            break

    # --------------------------------------------------------
    # PLATFORMS
    # --------------------------------------------------------

    platforms = self.merge_values(
        [
            season.platforms
            for season in seasons
        ]
    )

    # --------------------------------------------------------
    # LANGUAGES
    # --------------------------------------------------------

    languages = self.merge_values(
        [
            season.languages
            for season in seasons
        ]
    )

    # --------------------------------------------------------
    # HINDI
    # --------------------------------------------------------

    hindi_available = any(
        any(
            language.lower() == "hindi"
            for language in season.languages
        )
        for season in seasons
    )

    # --------------------------------------------------------
    # LAST SEASON
    # --------------------------------------------------------

    last_season = self.get_last_season(
        seasons
    )

    # --------------------------------------------------------
    # STATUS
    # --------------------------------------------------------

    status = "Unknown"

    if any(
        season.status == "Ongoing"
        for season in seasons
    ):
        status = "Ongoing"

    elif all(
        season.status == "Completed"
        for season in seasons
    ):
        status = "Completed"

    elif last_season:
        status = last_season.status

    # --------------------------------------------------------
    # LAST EPISODE
    # --------------------------------------------------------

    last_episode = None

    for season in seasons:

        count = season.episodes

        if season.total_episodes:
            count = season.total_episodes

        if count:

            if (
                last_episode is None
                or count > last_episode
            ):
                last_episode = count

    # --------------------------------------------------------
    # LAST RELEASE
    # --------------------------------------------------------

    last_release = ""

    for season in seasons:

        if season.release_date:
            last_release = season.release_date

    # --------------------------------------------------------
    # NEXT EPISODE
    # --------------------------------------------------------

    next_episode = None
    next_release = ""
    schedule = ""

    if last_season:

        next_episode = last_season.next_episode
        next_release = last_season.next_release
        schedule = last_season.schedule

    # --------------------------------------------------------
    # STUDIO
    # --------------------------------------------------------

    studio = ""

    for season in seasons:

        if season.studio:
            studio = season.studio
            break

    # --------------------------------------------------------
    # DUB BY
    # --------------------------------------------------------

    dub_by = ""

    for season in seasons:

        if season.dub_by:
            dub_by = season.dub_by
            break

    # --------------------------------------------------------
    # URL
    # --------------------------------------------------------

    url = ""

    if last_season:
        url = last_season.url

    # --------------------------------------------------------
    # RESULT
    # --------------------------------------------------------

    return AnimeResult(
        title=title,
        poster=poster,

        hindi_available=hindi_available,

        platforms=platforms,
        languages=languages,

        seasons=seasons,

        status=status,

        last_episode=last_episode,
        last_release=last_release,

        next_episode=next_episode,
        next_release=next_release,
        schedule=schedule,

        studio=studio,
        dub_by=dub_by,

        source="DC",
        url=url,
    )


# ============================================================
# SEARCH ONE ANIME
# ============================================================

async def search_anime(
    self,
    query: str
) -> AnimeResult | None:

    if not query:
        return None

    logger.info(
        "Searching anime: %s",
        query
    )

    # --------------------------------------------------------
    # SEARCH RAREANIMES
    # --------------------------------------------------------

    urls = await self.search_all(
        query
    )

    if not urls:
        return None

    # --------------------------------------------------------
    # PARSE PAGES IN PARALLEL
    # --------------------------------------------------------

    seasons = await self.parse_pages(
        urls
    )

    if not seasons:
        return None

    # --------------------------------------------------------
    # FILTER TITLE
    # --------------------------------------------------------

    aliases = get_aliases(
        query
    )

    filtered = []

    for season in seasons:

        matched = False

        for alias in aliases:

            if title_matches(
                alias,
                season.anime_name
            ):
                matched = True
                break

        if matched:
            filtered.append(
                season
            )

    # If strict match fails,
    # use all Hindi results
    if filtered:
        seasons = filtered

    # --------------------------------------------------------
    # BUILD FINAL RESULT
    # --------------------------------------------------------

    return self.build_result(
        seasons
    )


# ============================================================
# SEARCH MULTIPLE ANIME RESULTS
# ============================================================

async def search_anime_list(
    self,
    query: str
) -> list[AnimeResult]:

    if not query:
        return []

    urls = await self.search_all(
        query
    )

    if not urls:
        return []

    seasons = await self.parse_pages(
        urls
    )

    if not seasons:
        return []

    groups = self.group_seasons(
        seasons
    )

    results = []

    for group in groups:

        result = self.build_result(
            group
        )

        if result and result.hindi_available:
            results.append(
                result
            )

    return results


# ============================================================
# FINAL CLASS BINDINGS
# ============================================================

RareAnimeScraper.same_anime_title = (
    same_anime_title
)

RareAnimeScraper.group_seasons = (
    group_seasons
)

RareAnimeScraper.build_result = (
    build_result
)

RareAnimeScraper.search_anime = (
    search_anime
)

RareAnimeScraper.search_anime_list = (
    search_anime_list
    )

# ============================================================
# PART 7 / 7 - FINAL FORMATTER + PUBLIC API
# ============================================================


def format_date(
    value: str
) -> str:

    value = clean_text(value)

    if not value:
        return ""

    # Common date formats ko readable banaye
    formats = [
        "%B %d %Y",
        "%b %d %Y",
        "%d %B %Y",
        "%d %b %Y",
        "%B %d, %Y",
        "%b %d, %Y",
        "%d %B, %Y",
        "%d %b, %Y",
    ]

    for fmt in formats:

        try:

            date = datetime.strptime(
                value,
                fmt
            )

            return date.strftime(
                "%d %B %Y"
            )

        except ValueError:
            continue

    return value


# ============================================================
# SEASON DISPLAY
# ============================================================

def format_season(
    season: AnimeSeason
) -> str:

    if season.season is not None:

        return (
            f"Season {season.season}"
        )

    return ""


# ============================================================
# EPISODE DISPLAY
# ============================================================

def format_episode_count(
    season: AnimeSeason
) -> str:

    if season.episodes <= 0:
        return ""

    if (
        season.status == "Ongoing"
        and season.total_episodes
    ):

        return (
            f"{season.episodes} / "
            f"{season.total_episodes}"
        )

    if season.total_episodes:

        return str(
            season.total_episodes
        )

    return str(
        season.episodes
    )


# ============================================================
# FORMAT ONE RESULT
# ============================================================

def format_anime_result(
    result: AnimeResult
) -> str:

    if not result:
        return "❌ Anime not found."

    lines = []

    # --------------------------------------------------------
    # TITLE
    # --------------------------------------------------------

    lines.append(
        f"🎬 Anime: {result.title}"
    )

    lines.append("")

    # --------------------------------------------------------
    # HINDI
    # --------------------------------------------------------

    if result.hindi_available:

        lines.append(
            "🇮🇳 Hindi Dub: ✅ Available"
        )

    else:

        lines.append(
            "🇮🇳 Hindi Dub: ❌ Not Available"
        )

    # --------------------------------------------------------
    # PLATFORM
    # --------------------------------------------------------

    if result.platforms:

        lines.append(
            "📺 Platform: "
            + " • ".join(
                result.platforms
            )
        )

    # --------------------------------------------------------
    # SEASONS
    # --------------------------------------------------------

    for season in result.seasons:

        if season.season is not None:

            lines.append(
                f"📀 Season: {season.season}"
            )

        episode_text = (
            format_episode_count(
                season
            )
        )

        if episode_text:

            lines.append(
                f"🎬 Episodes: {episode_text}"
            )

        # One season block is enough
        # for normal single-season result
        if len(result.seasons) > 1:
            break

    # --------------------------------------------------------
    # LANGUAGES
    # --------------------------------------------------------

    if result.languages:

        lines.append("")

        lines.append(
            "🌐 Languages: "
            + " • ".join(
                result.languages
            )
        )

    # --------------------------------------------------------
    # STATUS
    # --------------------------------------------------------

    lines.append("")

    if result.status == "Ongoing":

        lines.append(
            "📊 Status: 🔴 Ongoing"
        )

    elif result.status == "Completed":

        lines.append(
            "📊 Status: ✅ Completed"
        )

    else:

        lines.append(
            "📊 Status: ⚪ Unknown"
        )

    # --------------------------------------------------------
    # LAST EPISODE
    # --------------------------------------------------------

    if result.last_episode:

        lines.append("")

        lines.append(
            "📅 Last Episode: "
            f"Episode {result.last_episode}"
        )

    # --------------------------------------------------------
    # LAST RELEASE
    # --------------------------------------------------------

    if result.last_release:

        lines.append(
            "🗓 Last Release: "
            + format_date(
                result.last_release
            )
        )

    # --------------------------------------------------------
    # ONGOING DETAILS
    # --------------------------------------------------------

    if result.status == "Ongoing":

        if result.next_episode:

            lines.append("")

            lines.append(
                "⏭ Next Episode: "
                f"Episode {result.next_episode}"
            )

        if result.next_release:

            lines.append(
                "📅 Expected Release: "
                + format_date(
                    result.next_release
                )
            )

        if result.schedule:

            lines.append(
                "⏰ Schedule: "
                + result.schedule
            )

    # --------------------------------------------------------
    # STUDIO
    # --------------------------------------------------------

    if result.studio:

        lines.append("")

        lines.append(
            "🏢 Studio: "
            + result.studio
        )

    # --------------------------------------------------------
    # DUB BY
    # --------------------------------------------------------

    if result.dub_by:

        lines.append(
            "🎙 Dub By: "
            + result.dub_by
        )

    # --------------------------------------------------------
    # SOURCE
    # --------------------------------------------------------

    lines.append("")

    lines.append(
        "🔎 Source: "
        + result.source
    )

    return "\n".join(
        lines
    )


# ============================================================
# SEARCH + FORMAT
# ============================================================

async def get_anime_info(
    query: str
) -> str:

    query = clean_text(
        query
    )

    if not query:
        return (
            "❌ Please enter anime name.\n\n"
            "Example:\n"
            "/anime Naruto"
        )

    async with RareAnimeScraper() as scraper:

        result = await scraper.search_anime(
            query
        )

    if not result:

        return (
            f"❌ Anime not found: {query}"
        )

    return format_anime_result(
        result
    )


# ============================================================
# SEARCH RAW RESULT
# ============================================================

async def get_anime_result(
    query: str
) -> AnimeResult | None:

    query = clean_text(
        query
    )

    if not query:
        return None

    async with RareAnimeScraper() as scraper:

        return await scraper.search_anime(
            query
        )


# ============================================================
# MULTIPLE RESULTS
# ============================================================

async def get_anime_results(
    query: str
) -> list[AnimeResult]:

    query = clean_text(
        query
    )

    if not query:
        return []

    async with RareAnimeScraper() as scraper:

        return await scraper.search_anime_list(
            query
        )


# ============================================================
# SIMPLE TEST
# ============================================================

async def test_scraper():

    query = "Naruto"

    result = await get_anime_result(
        query
    )

    if not result:

        print(
            "❌ Anime not found:",
            query
        )

        return

    print(
        format_anime_result(
            result
        )
    )


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":

    asyncio.run(
        test_scraper()
    )
