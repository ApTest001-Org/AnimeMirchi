# ============================================================
# anime_scraper.py
# Anime Hindi Info Bot - RareAnimes Scraper
# PART 1 - Complete scraper foundation
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
HINDI_URL = urljoin(BASE_URL, "hindi/")

REQUEST_TIMEOUT = 12
MAX_SEARCH_RESULTS = 15
MAX_PAGES_TO_CHECK = 12

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

logger = logging.getLogger(__name__)

if not logger.handlers:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s"
    )


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
    status: str = ""

    poster: str = ""
    url: str = ""

    schedule: str = ""
    next_episode: int | None = None
    next_release: str = ""

    studio: str = ""
    dub_by: str = ""

    episodes_data: list[Episode] = field(default_factory=list)


@dataclass
class AnimeResult:
    title: str = ""
    poster: str = ""

    hindi_available: bool = False

    platforms: list[str] = field(default_factory=list)
    languages: list[str] = field(default_factory=list)

    seasons: list[AnimeSeason] = field(default_factory=list)

    status: str = ""
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
# NORMALIZATION
# ============================================================

def clean_text(text: str) -> str:
    if not text:
        return ""

    text = text.replace("\xa0", " ")
    text = re.sub(r"\s+", " ", text)

    return text.strip()


def normalize_title(title: str) -> str:
    """
    Search matching के लिए title normalize करता है.
    """

    if not title:
        return ""

    title = title.lower()

    title = title.replace("&", " and ")

    # punctuation हटाओ
    title = re.sub(r"[’'`]", "", title)
    title = re.sub(r"[^a-z0-9]+", " ", title)

    # common words जो search में unnecessary हैं
    remove_words = {
        "season",
        "episodes",
        "episode",
        "hindi",
        "dubbed",
        "download",
        "hd",
        "webdl",
        "web",
        "series",
        "anime",
    }

    words = [
        w for w in title.split()
        if w not in remove_words
    ]

    return " ".join(words).strip()


# ============================================================
# SEARCH ALIASES
# ============================================================

ALIASES = {

    "re zero": [
        "re zero",
        "re:zero",
        "rezero",
        "re starting life in another world",
    ],

    "konosuba": [
        "konosuba",
        "konosuba gods blessing on this wonderful world",
        "konosuba gods blessing",
        "kono suba",
    ],

    "spy x family": [
        "spy x family",
        "spy family",
        "spyxfamily",
    ],

    "naruto": [
        "naruto",
        "naruto shippuden",
        "naruto shippuden anime",
    ],

    "dragon ball": [
        "dragon ball",
        "dragonball",
        "dragon ball z",
        "dragon ball super",
        "dragon ball daima",
        "dragon ball gt",
    ],
}


def get_search_aliases(query: str) -> list[str]:

    normalized = normalize_title(query)

    aliases = [query]

    for key, values in ALIASES.items():

        if (
            normalized == key
            or normalized in values
            or key in normalized
        ):
            aliases.extend(values)

    # duplicate remove
    output = []

    for item in aliases:
        item = clean_text(item)

        if item and item.lower() not in [
            x.lower() for x in output
        ]:
            output.append(item)

    return output


# ============================================================
# HTTP SESSION
# ============================================================

class RareAnimeScraper:

    def __init__(self):

        self.session: aiohttp.ClientSession | None = None

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

    # ========================================================
    # FETCH
    # ========================================================

    async def fetch(self, url: str) -> str | None:

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

        except Exception as e:

            logger.error(
                "Fetch error %s: %s",
                url,
                e
            )

        return None

    # ========================================================
    # SEARCH
    # ========================================================

    async def search(self, query: str) -> list[str]:

        """
        RareAnimes WordPress search को use करता है.
        """

        aliases = get_search_aliases(query)

        tasks = []

        for alias in aliases[:6]:

            url = (
                BASE_URL
                + "?s="
                + quote(alias)
            )

            tasks.append(
                self.fetch(url)
            )

        pages = await asyncio.gather(
            *tasks,
            return_exceptions=True
        )

        found = {}

        for html in pages:

            if not isinstance(html, str):
                continue

            soup = BeautifulSoup(
                html,
                "html.parser"
            )

            for link in soup.select(
                "a[href]"
            ):

                href = link.get("href", "")

                text = clean_text(
                    link.get_text(" ", strip=True)
                )

                if not href:
                    continue

                if "/hindi/" not in href:
                    continue

                if not text:
                    continue

                # junk links
                bad_words = [
                    "download all",
                    "watch more",
                    "home",
                    "contact",
                    "privacy",
                    "disclaimer",
                    "telegram",
                ]

                if any(
                    bad in text.lower()
                    for bad in bad_words
                ):
                    continue

                found[href] = text

        # ====================================================
        # SORT BY TITLE MATCH
        # ====================================================

        q = normalize_title(query)

        scored = []

        for url, title in found.items():

            ntitle = normalize_title(title)

            score = 0

            if q == ntitle:
                score += 100

            if q in ntitle:
                score += 60

            query_words = q.split()

            for word in query_words:

                if word in ntitle:
                    score += 10

            # season page को थोड़ा priority
            if re.search(
                r"\bseason[\s\-]*\d+",
                title,
                re.I
            ):
                score += 5

            scored.append(
                (score, url)
            )

        scored.sort(
            reverse=True
        )

        return [
            url
            for _, url in scored[
                :MAX_SEARCH_RESULTS
            ]
        ]

    # ========================================================
    # PARSE IMAGE / POSTER
    # ========================================================

    def extract_poster(
        self,
        soup: BeautifulSoup
    ) -> str:

        selectors = [
            "meta[property='og:image']",
            "meta[name='twitter:image']",
            ".post-thumbnail img",
            ".entry-content img",
            "article img",
            "img",
        ]

        for selector in selectors:

            element = soup.select_one(
                selector
            )

            if not element:
                continue

            if element.name == "meta":

                src = (
                    element.get("content")
                    or ""
                )

            else:

                src = (
                    element.get("src")
                    or element.get(
                        "data-src"
                    )
                    or ""
                )

            if src:

                return urljoin(
                    BASE_URL,
                    src
                )

        return ""

    # ========================================================
    # FIND FIELD
    # ========================================================

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

    # ========================================================
    # SEASON
    # ========================================================

    def extract_season(
        self,
        text: str,
        title: str = ""
    ) -> int | None:

        patterns = [

            r"Season\s*[:\-]?\s*0*(\d{1,3})",

            r"Season\s+0*(\d{1,3})",

            r"\bS(?:eason)?\s*0*(\d{1,3})\b",

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

        # title fallback
        for pattern in patterns:

            match = re.search(
                pattern,
                title,
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

    # ========================================================
    # EPISODE COUNT
    # ========================================================

    def extract_episode_count(
        self,
        text: str
    ) -> int:

        patterns = [

            r"Episodes?\s*[:\-]?\s*(\d{1,4})",

            r"This Season has\s+(\d{1,4})\s+episodes",

            r"(\d{1,4})\s+episodes",

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

        return 0

    # ========================================================
    # TOTAL EPISODES
    # ========================================================

    def extract_total_episodes(
        self,
        text: str
    ) -> int | None:

        patterns = [

            r"\((\d{1,4})\s+in\s+Total\)",

            r"(\d{1,4})\s+in\s+Total",

            r"Total\s*Episodes?\s*[:\-]?\s*(\d{1,4})",

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

    # ========================================================
    # NETWORK / PLATFORM
    # ========================================================

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

        value = value.split(
            "Year:"
        )[0]

        value = clean_text(value)

        parts = re.split(
            r"\s*(?:,|\||/|•|\+)\s*",
            value
        )

        output = []

        for part in parts:

            part = clean_text(part)

            if not part:
                continue

            if part.lower() in [
                "original",
                "india",
            ]:
                continue

            output.append(part)

        return list(
            dict.fromkeys(output)
        )

    # ========================================================
    # LANGUAGE
    # ========================================================

    def extract_languages(
        self,
        text: str
    ) -> list[str]:

        value = self.find_field(
            text,
            [
                r"Language\s*:\s*([^\n]+)",
                r"Languages?\s*:\s*([^\n]+)",
            ]
        )

        if not value:
            return []

        value = clean_text(value)

        # metadata के बाद आने वाली चीजें हटाओ
        value = re.split(
            r"\s+(?:Genre|Quality|Rating|Synopsis)\s*:",
            value,
            flags=re.I
        )[0]

        languages = []

        # Hindi – Original
        # Hindi + English
        # Hindi, Tamil, Telugu
        parts = re.split(
            r"\s*(?:\||,|•|/|\+|&)\s*",
            value
        )

        for part in parts:

            part = re.sub(
                r"\bTrack\s*\d+\s*[:\-]?",
                "",
                part,
                flags=re.I
            )

            part = re.sub(
                r"\bOriginal\b",
                "",
                part,
                flags=re.I
            )

            part = clean_text(part)

            if not part:
                continue

            known = {
                "hindi": "Hindi",
                "english": "English",
                "tamil": "Tamil",
                "telugu": "Telugu",
                "bengali": "Bengali",
                "malayalam": "Malayalam",
                "kannada": "Kannada",
                "marathi": "Marathi",
                "japanese": "Japanese",
                "mandarin": "Mandarin",
                "chinese": "Chinese",
            }

            low = part.lower()

            if low in known:
                part = known[low]

            languages.append(part)

        # अगर page Hindi Dubbed है तो Hindi जरूर
        if (
            "hindi dubbed" in text.lower()
            or "hindi dub" in text.lower()
        ):

            if "Hindi" not in languages:
                languages.insert(
                    0,
                    "Hindi"
                )

        return list(
            dict.fromkeys(languages)
        )

    # ========================================================
    # RELEASE DATE / YEAR
    # ========================================================

    def extract_release_date(
        self,
        text: str
    ) -> str:

        value = self.find_field(
            text,
            [
                r"Indian Release Date\s*:\s*([^\n]+)",
                r"Release Date\s*:\s*([^\n]+)",
                r"Year\s*:\s*([^\n]+)",
            ]
        )

        return clean_text(
            value
        )

    # ========================================================
    # STATUS
    # ========================================================

    def detect_status(
        self,
        text: str
    ) -> str:

        lower = text.lower()

        # Completed सबसे पहले
        if (
            "season finale" in lower
            or "complete all episodes" in lower
            or "completed" in lower
        ):
            return "Completed"

        # ongoing signals
        ongoing_words = [
            "new episode every",
            "new episodes every",
            "stay updated with us for new episodes",
            "next episode",
            "every saturday",
            "every sunday",
            "every monday",
            "every tuesday",
            "every wednesday",
            "every thursday",
            "every friday",
        ]

        if any(
            word in lower
            for word in ongoing_words
        ):
            return "Ongoing"

        return "Unknown"

    # ========================================================
    # SCHEDULE
    # ========================================================
    
    def extract_schedule(
        self,
        text: str
    ) -> str:

        patterns = [

            r"(?:1\s+)?New Episode\s+Every\s+([A-Za-z]+)",

            r"New Episodes?\s+Every\s+([A-Za-z]+)",

            r"Every\s+(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)",

        ]

        for pattern in patterns:

            match = re.search(
                pattern,
                text,
                re.I
            )

            if match:

                day = match.group(1)

                return (
                    "Every "
                    + day.capitalize()
                )

        return ""

    # ========================================================
    # NEXT EPISODE
    # ========================================================
    
    def extract_next_episode(
        self,
        text: str,
        current_episode: int
    ) -> int | None:

        patterns = [

            r"Next\s+Episode\s*[:\-]?\s*(?:Episode\s*)?(\d{1,4})",

            r"Episode\s+(\d{1,4})\s+NEw",

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

        # Ongoing + current episode
        if current_episode:
            return current_episode + 1

        return None

    # ========================================================
    # STUDIO
    # ========================================================

    def extract_studio(
        self,
        text: str
    ) -> str:

        return self.find_field(
            text,
            [
                r"Studio\s*:\s*([^\n]+)",
                r"Studios?\s*:\s*([^\n]+)",
            ]
        )

    # ========================================================
    # DUB BY
    # ========================================================

    def extract_dub_by(
        self,
        text: str
    ) -> str:

        value = self.find_field(
            text,
            [
                r"Dubbed\s+by\s+([^\.\n]+)",
                r"Dub\s+By\s*:\s*([^\n]+)",
                r"Dubbed\s+By\s*:\s*([^\n]+)",
            ]
        )

        return clean_text(
            value
        )

    # ========================================================
    # EPISODE PARSER
    # ========================================================

    def extract_episodes(
        self,
        soup: BeautifulSoup
    ) -> list[Episode]:

        episodes = []

        # अलग-अलग episode headings
        candidates = soup.find_all(
            string=re.compile(
                r"Episode\s+\d+",
                re.I
            )
        )

        seen = set()

        for node in candidates:

            text = clean_text(
                node
            )

            match = re.search(
                r"Episode\s+0*(\d+)",
                text,
                re.I
            )

            if not match:
                continue

            number = int(
                match.group(1)
            )

            if number in seen:
                continue

            seen.add(number)

            # आसपास का parent text
            parent = node.parent

            block = clean_text(
                parent.get_text(
                    " ",
                    strip=True
                )
                if parent
                else text
            )

            languages = []

            language_names = [
                "Hindi",
                "English",
                "Tamil",
                "Telugu",
                "Bengali",
                "Malayalam",
                "Kannada",
                "Japanese",
            ]

            for language in language_names:

                if re.search(
                    rf"\b{language}\b",
                    block,
                    re.I
                ):
                    languages.append(
                        language
                    )

            episodes.append(
                Episode(
                    number=number,
                    title=text,
                    languages=languages
                )
            )

        episodes.sort(
            key=lambda x: (
                x.number
                if x.number is not None
                else 999999
            )
        )

        return episodes

    # ========================================================
    # PARSE PAGE
    # ========================================================

    async def parse_page(
        self,
        url: str
    ) -> AnimeSeason | None:

        html = await self.fetch(
            url
        )

        if not html:
            return None

        soup = BeautifulSoup(
            html,
            "html.parser"
        )

        # पूरा visible text
        text = clean_text(
            soup.get_text(
                " ",
                strip=True
            )
        )

        # title
        title = ""

        og_title = soup.select_one(
            "meta[property='og:title']"
        )

        if og_title:
            title = clean_text(
                og_title.get("content", "")
            )

        if not title:

            h1 = soup.find(
                "h1"
            )

            if h1:
                title = clean_text(
                    h1.get_text(
                        " ",
                        strip=True
                    )
                )

        # remove website suffix
        title = re.sub(
            r"\s*[-|]\s*Rare.*$",
            "",
            title,
            flags=re.I
        )

        season = self.extract_season(
            text,
            title
        )

        episodes = self.extract_episode_count(
            text
        )

        total = self.extract_total_episodes(
            text
        )

        platforms = self.extract_platforms(
            text
        )

        languages = self.extract_languages(
            text
        )

        release_date = self.extract_release_date(
            text
        )

        status = self.detect_status(
            text
        )

        schedule = self.extract_schedule(
            text
        )

        episodes_data = self.extract_episodes(
            soup
        )

        if episodes_data:

            max_episode = max(
                [
                    e.number
                    for e in episodes_data
                    if e.number is not None
                ],
                default=0
            )

            # page parser में count खराब हो तो episode list से
            if max_episode > episodes:
                episodes = max_episode

        poster = self.extract_poster(
            soup
        )

        studio = self.extract_studio(
            text
        )

        dub_by = self.extract_dub_by(
            text
        )

        next_episode = None

        if status == "Ongoing":

            next_episode = (
                self.extract_next_episode(
                    text,
                    episodes
                )
            )

        return AnimeSeason(
            anime_name=title,
            season=season,
            episodes=episodes,
            total_episodes=total,
            languages=languages,
            platforms=platforms,
            release_date=release_date,
            status=status,
            poster=poster,
            url=url,
            schedule=schedule,
            next_episode=next_episode,
            studio=studio,
            dub_by=dub_by,
            episodes_data=episodes_data
        )

    # ========================================================
    # GROUP SEASONS
    # ========================================================

    def merge_unique(
        self,
        old: list[str],
        new: list[str]
    ) -> list[str]:

        result = list(old)

        for item in new:

            item = clean_text(
                item
            )

            if not item:
                continue

            exists = any(
                item.lower()
                == x.lower()
                for x in result
            )

            if not exists:
                result.append(item)

        return result

    def combine_seasons(
        self,
        seasons: list[AnimeSeason]
    ) -> AnimeResult | None:

        if not seasons:
            return None

        # ----------------------------------------------------
        # सबसे अच्छा title
        # ----------------------------------------------------

        title = ""

        for item in seasons:

            name = clean_text(
                item.anime_name
            )

            if not name:
                continue

            # season text हटाकर
            name = re.sub(
                r"\s+Season\s+\d+.*$",
                "",
                name,
                flags=re.I
            )

            if len(name) > len(title):
                title = name

        # ----------------------------------------------------
        # poster
        # ----------------------------------------------------

        poster = ""

        for item in seasons:

            if item.poster:

                poster = item.poster
                break

        # ----------------------------------------------------
        # platform
        # ----------------------------------------------------

        platforms = []

        for item in seasons:

            platforms = self.merge_unique(
                platforms,
                item.platforms
            )

        # ----------------------------------------------------
        # languages
        # ----------------------------------------------------

        languages = []

        for item in seasons:

            languages = self.merge_unique(
                languages,
                item.languages
            )

        # Hindi हमेशा first
        if "Hindi" in languages:

            languages.remove(
                "Hindi"
            )

            languages.insert(
                0,
                "Hindi"
            )

        # ----------------------------------------------------
        # sort seasons
        # ----------------------------------------------------

        seasons.sort(
            key=lambda x: (
                x.season
                if x.season is not None
                else 9999
            )
        )

        # ----------------------------------------------------
        # latest season
        # ----------------------------------------------------

        latest = seasons[-1]

        # ----------------------------------------------------
        # Hindi available
        # ----------------------------------------------------

        hindi_available = any(
            "hindi" in [
                x.lower()
                for x in season.languages
            ]
            or "hindi dub" in season.anime_name.lower()
            or "hindi dubbed" in season.anime_name.lower()
            for season in seasons
        )

        # ----------------------------------------------------
        # status
        # ----------------------------------------------------

        ongoing = [
            s
            for s in seasons
            if s.status.lower()
            == "ongoing"
        ]

        if ongoing:
            status = "Ongoing"
            current = ongoing[-1]
        else:
            status = "Completed"
            current = latest

        # ----------------------------------------------------
        # last episode
        # ----------------------------------------------------

        last_episode = current.episodes

        # ----------------------------------------------------
        # next episode
        # ----------------------------------------------------

        next_episode = current.next_episode

        # ----------------------------------------------------
        # studio
        # ----------------------------------------------------

        studio = ""

        for s in seasons:

            if s.studio:
                studio = s.studio
                break

        # ----------------------------------------------------
        # dub by
        # ----------------------------------------------------

        dub_by = ""

        for s in seasons:

            if s.dub_by:
                dub_by = s.dub_by
                break

        # ----------------------------------------------------
        # release
        # ----------------------------------------------------

        last_release = current.release_date

        # ----------------------------------------------------
        # result
        # ----------------------------------------------------

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
            next_release=current.next_release,
            schedule=current.schedule,
            studio=studio,
            dub_by=dub_by,
            source="DC",
            url=current.url
        )

    # ========================================================
    # MAIN SEARCH
    # ========================================================

    async def get_anime(
        self,
        query: str
    ) -> AnimeResult | None:

        start = asyncio.get_running_loop().time()

        logger.info(
            "Searching: %s",
            query
        )

        urls = await self.search(
            query
        )

        if not urls:

            logger.info(
                "No search results: %s",
                query
            )

            return None

        # ----------------------------------------------------
        # पहले relevant pages
        # ----------------------------------------------------

        pages = urls[
            :MAX_PAGES_TO_CHECK
        ]

        # ----------------------------------------------------
        # PARALLEL FETCH/PARSE
        # ----------------------------------------------------

        tasks = [
            self.parse_page(url)
            for url in pages
        ]

        parsed = await asyncio.gather(
            *tasks,
            return_exceptions=True
        )

        seasons = []

        normalized_query = normalize_title(
            query
        )

        for item in parsed:

            if isinstance(
                item,
                Exception
            ):
                continue

            if not item:
                continue

            # ------------------------------------------------
            # केवल relevant anime
            # ------------------------------------------------

            page_name = normalize_title(
                item.anime_name
            )

            match = False

            if normalized_query in page_name:
                match = True

            # aliases
            for alias in get_search_aliases(
                query
            ):

                alias_normalized = normalize_title(
                    alias
                )

                if (
                    alias_normalized
                    and alias_normalized in page_name
                ):
                    match = True
                    break

            if match:

                seasons.append(
                    item
                )

        if not seasons:

            return None

        # ----------------------------------------------------
        # DUPLICATE SEASON REMOVE
        # ----------------------------------------------------

        unique = {}

        for season in seasons:

            key = (
                season.season,
                normalize_title(
                    season.anime_name
                )
            )

            # same season में ज्यादा detailed page रखो
            if key not in unique:

                unique[key] = season

            else:

                old = unique[key]

                if (
                    len(season.episodes_data)
                    >
                    len(old.episodes_data)
                ):
                    unique[key] = season

        seasons = list(
            unique.values()
        )

        result = self.combine_seasons(
            seasons
        )

        elapsed = (
            asyncio.get_running_loop().time()
            - start
        )

        logger.info(
            "Search finished: %s in %.2fs",
            query,
            elapsed
        )

        return result


# ============================================================
# OUTPUT FORMATTER
# ============================================================

def format_anime_result(
    result: AnimeResult
) -> str:

    if not result:
        return "❌ Anime not found."

    lines = []

    title = result.title or "Unknown"

    lines.append(
        f"🎬 Anime: {title}"
    )

    # Hindi
    if result.hindi_available:

        lines.append(
            "\n🇮🇳 Hindi Dub: ✅ Available"
        )

    else:

        lines.append(
            "\n🇮🇳 Hindi Dub: ❌ Not Available"
        )

    # Platform
    if result.platforms:

        lines.append(
            "📺 Platform: "
            + " • ".join(
                result.platforms
            )
        )

    # --------------------------------------------------------
    # SINGLE SEASON
    # --------------------------------------------------------

    if len(result.seasons) == 1:

        season = result.seasons[0]

        if season.season is not None:

            lines.append(
                f"📀 Season: {season.season}"
            )

        if season.episodes:

            if (
                result.status == "Ongoing"
                and season.total_episodes
                and season.total_episodes
                > season.episodes
            ):

                lines.append(
                    f"🎬 Episodes: "
                    f"{season.episodes} / "
                    f"{season.total_episodes}"
                )

            else:

                lines.append(
                    f"🎬 Episodes: "
                    f"{season.episodes}"
                )

    # --------------------------------------------------------
    # MULTIPLE SEASONS
    # --------------------------------------------------------

    elif len(result.seasons) > 1:

        lines.append(
            "\n📚 Hindi Available Seasons:"
        )

        for season in result.seasons:

            season_number = (
                season.season
                if season.season is not None
                else "?"
            )

            language = (
                " • ".join(
                    season.languages
                )
                if season.languages
                else "Hindi"
            )

            lines.append(
                f"{season.anime_name}"
            )

            lines.append(
                f"• Season {season_number}"
                f" — {language}"
                f" — {season.episodes} episodes"
            )

        total = sum(
            s.episodes
            for s in result.seasons
        )

        lines.append(
            f"\n📊 Total Hindi Episodes: {total}"
        )

    # Languages
    if result.languages:

        lines.append(
            "\n🌐 Languages: "
            + " • ".join(
                result.languages
            )
        )
schedule(
        self,
        text: str
    ) -> str:

        patterns = [

            r"(?:1\s+)?New Episode\s+Every\s+([A-Za-z]+)",

            r"New Episodes?\s+Every\s+([A-Za-z]+)",

            r"Every\s+(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)",

        ]

        for pattern in patterns:

            match = re.search(
                pattern,
                text,
                re.I
            )

            if match:

                day = match.group(1)

                return (
                    "Every "
                    + day.capitalize()
                )

        return ""

    # ========================================================
    # NEXT EPISODE
    # ========================================================

    def extract_next_episode(
        self,
        text: str,
        current_episode: int
    ) -> int | None:

        patterns = [

            r"Next\s+Episode\s*[:\-]?\s*(?:Episode\s*)?(\d{1,4})",

            r"Episode\s+(\d{1,4})\s+NEw",

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

        # Ongoing + current episode
        if current_episode:
            return current_episode + 1

        return None

    # ========================================================
    # STUDIO
    # ========================================================

    def extract_studio(
        self,
        text: str
    ) -> str:

        return self.find_field(
            text,
            [
                r"Studio\s*:\s*([^\n]+)",
                r"Studios?\s*:\s*([^\n]+)",
            ]
        )

    # ========================================================
    # DUB BY
    # ========================================================

    def extract_dub_by(
        self,
        text: str
    ) -> str:

        value = self.find_field(
            text,
            [
                r"Dubbed\s+by\s+([^\.\n]+)",
                r"Dub\s+By\s*:\s*([^\n]+)",
                r"Dubbed\s+By\s*:\s*([^\n]+)",
            ]
        )

        return clean_text(
            value
        )

    # ========================================================
    # EPISODE PARSER
    # ========================================================

    def extract_episodes(
        self,
        soup: BeautifulSoup
    ) -> list[Episode]:

        episodes = []

        # अलग-अलग episode headings
        candidates = soup.find_all(
            string=re.compile(
                r"Episode\s+\d+",
                re.I
            )
        )

        seen = set()

        for node in candidates:

            text = clean_text(
                node
            )

            match = re.search(
                r"Episode\s+0*(\d+)",
                text,
                re.I
            )

            if not match:
                continue

            number = int(
                match.group(1)
            )

            if number in seen:
                continue

            seen.add(number)

            # आसपास का parent text
            parent = node.parent

            block = clean_text(
                parent.get_text(
                    " ",
                    strip=True
                )
                if parent
                else text
            )

            languages = []

            language_names = [
                "Hindi",
                "English",
                "Tamil",
                "Telugu",
                "Bengali",
                "Malayalam",
                "Kannada",
                "Japanese",
            ]

            for language in language_names:

                if re.search(
                    rf"\b{language}\b",
                    block,
                    re.I
                ):
                    languages.append(
                        language
                    )

            episodes.append(
                Episode(
                    number=number,
                    title=text,
                    languages=languages
                )
            )

        episodes.sort(
            key=lambda x: (
                x.number
                if x.number is not None
                else 999999
            )
        )

        return episodes

    # ========================================================
    # PARSE PAGE
    # ========================================================

    async def parse_page(
        self,
        url: str
    ) -> AnimeSeason | None:

        html = await self.fetch(
            url
        )

        if not html:
            return None

        soup = BeautifulSoup(
            html,
            "html.parser"
        )

        # पूरा visible text
        text = clean_text(
            soup.get_text(
                " ",
                strip=True
            )
        )

        # title
        title = ""

        og_title = soup.select_one(
            "meta[property='og:title']"
        )

        if og_title:
            title = clean_text(
                og_title.get("content", "")
            )

        if not title:

            h1 = soup.find(
                "h1"
            )

            if h1:
                title = clean_text(
                    h1.get_text(
                        " ",
                        strip=True
                    )
                )

        # remove website suffix
        title = re.sub(
            r"\s*[-|]\s*Rare.*$",
            "",
            title,
            flags=re.I
        )

        season = self.extract_season(
            text,
            title
        )

        episodes = self.extract_episode_count(
            text
        )

        total = self.extract_total_episodes(
            text
        )

        platforms = self.extract_platforms(
            text
        )

        languages = self.extract_languages(
            text
        )

        release_date = self.extract_release_date(
            text
        )

        status = self.detect_status(
            text
        )

        schedule = self.extract_schedule(
            text
        )

        episodes_data = self.extract_episodes(
            soup
        )

        if episodes_data:

            max_episode = max(
                [
                    e.number
                    for e in episodes_data
                    if e.number is not None
                ],
                default=0
            )

            # page parser में count खराब हो तो episode list से
            if max_episode > episodes:
                episodes = max_episode

        poster = self.extract_poster(
            soup
        )

        studio = self.extract_studio(
            text
        )

        dub_by = self.extract_dub_by(
            text
        )

        next_episode = None

        if status == "Ongoing":

            next_episode = (
                self.extract_next_episode(
                    text,
                    episodes
                )
            )

        return AnimeSeason(
            anime_name=title,
            season=season,
            episodes=episodes,
            total_episodes=total,
            languages=languages,
            platforms=platforms,
            release_date=release_date,
            status=status,
            poster=poster,
            url=url,
            schedule=schedule,
            next_episode=next_episode,
            studio=studio,
            dub_by=dub_by,
            episodes_data=episodes_data
        )

    # ========================================================
    # GROUP SEASONS
    # ========================================================

    def merge_unique(
        self,
        old: list[str],
        new: list[str]
    ) -> list[str]:

        result = list(old)

        for item in new:

            item = clean_text(
                item
            )

            if not item:
                continue

            exists = any(
                item.lower()
                == x.lower()
                for x in result
            )

            if not exists:
                result.append(item)

        return result

    def combine_seasons(
        self,
        seasons: list[AnimeSeason]
    ) -> AnimeResult | None:

        if not seasons:
            return None

        # ----------------------------------------------------
        # सबसे अच्छा title
        # ----------------------------------------------------

        title = ""

        for item in seasons:

            name = clean_text(
                item.anime_name
            )

            if not name:
                continue

            # season text हटाकर
            name = re.sub(
                r"\s+Season\s+\d+.*$",
                "",
                name,
                flags=re.I
            )

            if len(name) > len(title):
                title = name

        # ----------------------------------------------------
        # poster
        # ----------------------------------------------------

        poster = ""

        for item in seasons:

            if item.poster:

                poster = item.poster
                break

        # ----------------------------------------------------
        # platform
        # ----------------------------------------------------

        platforms = []

        for item in seasons:

            platforms = self.merge_unique(
                platforms,
                item.platforms
            )

        # ----------------------------------------------------
        # languages
        # ----------------------------------------------------

        languages = []

        for item in seasons:

            languages = self.merge_unique(
                languages,
                item.languages
            )

        # Hindi हमेशा first
        if "Hindi" in languages:

            languages.remove(
                "Hindi"
            )

            languages.insert(
                0,
                "Hindi"
            )

        # ----------------------------------------------------
        # sort seasons
        # ----------------------------------------------------

        seasons.sort(
            key=lambda x: (
                x.season
                if x.season is not None
                else 9999
            )
        )

        # ----------------------------------------------------
        # latest season
        # ----------------------------------------------------

        latest = seasons[-1]

        # ----------------------------------------------------
        # Hindi available
        # ----------------------------------------------------

        hindi_available = any(
            "hindi" in [
                x.lower()
                for x in season.languages
            ]
            or "hindi dub" in season.anime_name.lower()
            or "hindi dubbed" in season.anime_name.lower()
            for season in seasons
        )

        # ----------------------------------------------------
        # status
        # ----------------------------------------------------

        ongoing = [
            s
            for s in seasons
            if s.status.lower()
            == "ongoing"
        ]

        if ongoing:
            status = "Ongoing"
            current = ongoing[-1]
        else:
            status = "Completed"
            current = latest

        # ----------------------------------------------------
        # last episode
        # ----------------------------------------------------

        last_episode = current.episodes

        # ----------------------------------------------------
        # next episode
        # ----------------------------------------------------

        next_episode = current.next_episode

        # ----------------------------------------------------
        # studio
        # ----------------------------------------------------

        studio = ""

        for s in seasons:

            if s.studio:
                studio = s.studio
                break

        # ----------------------------------------------------
        # dub by
        # ----------------------------------------------------

        dub_by = ""

        for s in seasons:

            if s.dub_by:
                dub_by = s.dub_by
                break

        # ----------------------------------------------------
        # release
        # ----------------------------------------------------

        last_release = current.release_date

        # ----------------------------------------------------
        # result
        # ----------------------------------------------------

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
            next_release=current.next_release,
            schedule=current.schedule,
            studio=studio,
            dub_by=dub_by,
            source="DC",
            url=current.url
        )

    # ========================================================
    # MAIN SEARCH
    # ========================================================

    async def get_anime(
        self,
        query: str
    ) -> AnimeResult | None:

        start = asyncio.get_running_loop().time()

        logger.info(
            "Searching: %s",
            query
        )

        urls = await self.search(
            query
        )

        if not urls:

            logger.info(
                "No search results: %s",
                query
            )

            return None

        # ----------------------------------------------------
        # पहले relevant pages
        # ----------------------------------------------------

        pages = urls[
            :MAX_PAGES_TO_CHECK
        ]

        # ----------------------------------------------------
        # PARALLEL FETCH/PARSE
        # ----------------------------------------------------

        tasks = [
            self.parse_page(url)
            for url in pages
        ]

        parsed = await asyncio.gather(
            *tasks,
            return_exceptions=True
        )

        seasons = []

        normalized_query = normalize_title(
            query
        )

        for item in parsed:

            if isinstance(
                item,
                Exception
            ):
                continue

            if not item:
                continue

            # ------------------------------------------------
            # केवल relevant anime
            # ------------------------------------------------

            page_name = normalize_title(
                item.anime_name
            )

            match = False

            if normalized_query in page_name:
                match = True

            # aliases
            for alias in get_search_aliases(
                query
            ):

                alias_normalized = normalize_title(
                    alias
                )

                if (
                    alias_normalized
                    and alias_normalized in page_name
                ):
                    match = True
                    break

            if match:

                seasons.append(
                    item
                )

        if not seasons:

            return None

        # ----------------------------------------------------
        # DUPLICATE SEASON REMOVE
        # ----------------------------------------------------

        unique = {}

        for season in seasons:

            key = (
                season.season,
                normalize_title(
                    season.anime_name
                )
            )

            # same season में ज्यादा detailed page रखो
            if key not in unique:

                unique[key] = season

            else:

                old = unique[key]

                if (
                    len(season.episodes_data)
                    >
                    len(old.episodes_data)
                ):
                    unique[key] = season

        seasons = list(
            unique.values()
        )

        result = self.combine_seasons(
            seasons
        )

        elapsed = (
            asyncio.get_running_loop().time()
            - start
        )

        logger.info(
            "Search finished: %s in %.2fs",
            query,
            elapsed
        )

        return result


# ============================================================
# OUTPUT FORMATTER
# ============================================================

def format_anime_result(
    result: AnimeResult
) -> str:

    if not result:
        return "❌ Anime not found."

    lines = []

    title = result.title or "Unknown"

    lines.append(
        f"🎬 Anime: {title}"
    )

    # Hindi
    if result.hindi_available:

        lines.append(
            "\n🇮🇳 Hindi Dub: ✅ Available"
        )

    else:

        lines.append(
            "\n🇮🇳 Hindi Dub: ❌ Not Available"
        )

    # Platform
    if result.platforms:

        lines.append(
            "📺 Platform: "
            + " • ".join(
                result.platforms
            )
        )

    # --------------------------------------------------------
    # SINGLE SEASON
    # --------------------------------------------------------

    if len(result.seasons) == 1:

        season = result.seasons[0]

        if season.season is not None:

            lines.append(
                f"📀 Season: {season.season}"
            )

        if season.episodes:

            if (
                result.status == "Ongoing"
                and season.total_episodes
                and season.total_episodes
                > season.episodes
            ):

                lines.append(
                    f"🎬 Episodes: "
                    f"{season.episodes} / "
                    f"{season.total_episodes}"
                )

            else:

                lines.append(
                    f"🎬 Episodes: "
                    f"{season.episodes}"
                )

    # --------------------------------------------------------
    # MULTIPLE SEASONS
    # --------------------------------------------------------

    elif len(result.seasons) > 1:

        lines.append(
            "\n📚 Hindi Available Seasons:"
        )

        for season in result.seasons:

            season_number = (
                season.season
                if season.season is not None
                else "?"
            )

            language = (
                " • ".join(
                    season.languages
                )
                if season.languages
                else "Hindi"
            )

            lines.append(
                f"{season.anime_name}"
            )

            lines.append(
                f"• Season {season_number}"
                f" — {language}"
                f" — {season.episodes} episodes"
            )

        total = sum(
            s.episodes
            for s in result.seasons
        )

        lines.append(
            f"\n📊 Total Hindi Episodes: {total}"
        )

    # Languages
    if result.languages:

        lines.append(
            "\n🌐 Languages: "
            + " • ".join(
                result.languages
            )
        )

    # Status
    if result.status == "Ongoing":

        lines.append(
            "\n📊 Status: 🔴 Ongoing"
        )

    else:

        lines.append(
            "\n📊 Status: ✅ Completed"
        )

    # Last episode
    if result.last_episode:

        lines.append(
            f"\n📅 Last Episode: "
            f"Episode {result.last_episode}"
        )

    # Last release
    if result.last_release:

        lines.append(
            f"🗓 Last Release: "
            f"{result.last_release}"
        )

    # Ongoing info
    if result.status == "Ongoing":

        if result.next_episode:

            lines.append(
                f"\n⏭ Next Episode: "
                f"Episode {result.next_episode}"
            )

        if result.next_release:

            lines.append(
                f"📅 Expected Release: "
                f"{result.next_release}"
            )

        if result.schedule:

            lines.append(
                f"⏰ Schedule: "
                f"{result.schedule}"
            )

    # Studio
    if result.studio:

        lines.append(
            f"\n🏢 Studio: "
            f"{result.studio}"
        )

    else:

        lines.append(
            "\n🏢 Studio: —"
        )

    # Dub By
    if result.dub_by:

        lines.append(
            f"🎙 Dub By: "
            f"{result.dub_by}"
        )

    else:

        lines.append(
            "🎙 Dub By: —"
        )

    # Source
    lines.append(
        "\n🔎 Source: DC"
    )

    return "\n".join(
        lines
    )


# ============================================================
# SIMPLE TEST
# ============================================================

async def test():

    async with RareAnimeScraper() as scraper:

        for query in [
            "naruto",
            "konosuba",
            "black torch",
            "spy x family",
            "re zero",
        ]:

            print(
                "\n"
                + "=" * 60
            )

            result = await scraper.get_anime(
                query
            )

            if result:

                print(
                    format_anime_result(
                        result
                    )
                )

                print(
                    "\nPoster:",
                    result.poster
                )

            else:

                print(
                    "❌ Anime not found:",
                    query
                )


# ============================================================
# RUN DIRECTLY
# ============================================================

if __name__ == "__main__":

    asyncio.run(
        test()
    )

    # ============================================================
# PART 2 - SMART TITLE / SERIES GROUPING
# ============================================================

# Multiple related anime series ko ek family me group karne ke liye

SERIES_GROUPS = {

    "dragon ball": {
        "name": "🐉 Dragon Ball",
        "series": [
            "Dragon Ball",
            "Dragon Ball Z",
            "Dragon Ball GT",
            "Dragon Ball Super",
            "Dragon Ball DAIMA",
            "Dragon Ball Kai",
        ],
    },

    "naruto": {
        "name": "🍥 Naruto",
        "series": [
            "Naruto",
            "Naruto Shippuden",
        ],
    },

}


def normalize_for_group(title: str) -> str:

    title = normalize_title(title)

    title = re.sub(
        r"\bseason\s*\d+\b",
        "",
        title,
        flags=re.I
    )

    return clean_text(title)


def detect_series_group(query: str):

    q = normalize_for_group(query)

    for key, data in SERIES_GROUPS.items():

        if key in q:
            return data

        for series in data["series"]:

            s = normalize_for_group(
                series
            )

            if s and (
                q in s
                or s in q
            ):
                return data

    return None


async def get_series_group(
    scraper,
    query: str
):

    group = detect_series_group(query)

    if not group:
        return None

    all_results = []

    # हर related series को parallel search करो
    tasks = []

    for series_name in group["series"]:

        tasks.append(
            scraper.get_anime(
                series_name
            )
        )

    results = await asyncio.gather(
        *tasks,
        return_exceptions=True
    )

    for result in results:

        if isinstance(
            result,
            Exception
        ):
            continue

        if result:
            all_results.append(
                result
            )

    if not all_results:
        return None

    return merge_series_results(
        group["name"],
        all_results
    )


def merge_series_results(
    group_name: str,
    results: list[AnimeResult]
) -> AnimeResult:

    merged = AnimeResult()

    merged.title = group_name
    merged.source = "DC"

    # --------------------------------------------------------
    # POSTER
    # --------------------------------------------------------

    for result in results:

        if result.poster:
            merged.poster = result.poster
            break

    # --------------------------------------------------------
    # HINDI
    # --------------------------------------------------------

    merged.hindi_available = any(
        r.hindi_available
        for r in results
    )

    # --------------------------------------------------------
    # PLATFORMS
    # --------------------------------------------------------

    for result in results:

        merged.platforms = (
            RareAnimeScraper().merge_unique(
                merged.platforms,
                result.platforms
            )
        )

    # --------------------------------------------------------
    # LANGUAGES
    # --------------------------------------------------------

    for result in results:

        merged.languages = (
            RareAnimeScraper().merge_unique(
                merged.languages,
                result.languages
            )
        )

    # Hindi first
    if "Hindi" in merged.languages:

        merged.languages.remove(
            "Hindi"
        )

        merged.languages.insert(
            0,
            "Hindi"
        )

    # --------------------------------------------------------
    # ALL SEASONS
    # --------------------------------------------------------

    for result in results:

        merged.seasons.extend(
            result.seasons
        )

    # duplicate seasons remove
    unique = {}

    for season in merged.seasons:

        key = (
            normalize_for_group(
                season.anime_name
            ),
            season.season
        )

        if key not in unique:

            unique[key] = season

        else:

            old = unique[key]

            if (
                season.episodes
                > old.episodes
            ):
                unique[key] = season

    merged.seasons = list(
        unique.values()
    )

    # --------------------------------------------------------
    # STATUS
    # --------------------------------------------------------

    if any(
        r.status == "Ongoing"
        for r in results
    ):
        merged.status = "Ongoing"
    else:
        merged.status = "Completed"

    # --------------------------------------------------------
    # TOTAL EPISODES
    # --------------------------------------------------------

    merged.last_episode = sum(
        s.episodes
        for s in merged.seasons
    )

    # --------------------------------------------------------
    # LATEST RELEASE
    # --------------------------------------------------------

    dated = [
        r
        for r in results
        if r.last_release
    ]

    if dated:

        merged.last_release = (
            dated[-1].last_release
        )

    # --------------------------------------------------------
    # NEXT EPISODE
    # --------------------------------------------------------

    for result in results:

        if result.next_episode:

            merged.next_episode = (
                result.next_episode
            )

            merged.next_release = (
                result.next_release
            )

            merged.schedule = (
                result.schedule
            )

            break

    # --------------------------------------------------------
    # STUDIO
    # --------------------------------------------------------

    studios = []

    for result in results:

        if result.studio:

            if result.studio not in studios:

                studios.append(
                    result.studio
                )

    merged.studio = (
        " • ".join(studios)
    )

    # --------------------------------------------------------
    # DUB BY
    # --------------------------------------------------------

    dubbers = []

    for result in results:

        if result.dub_by:

            if result.dub_by not in dubbers:

                dubbers.append(
                    result.dub_by
                )

    merged.dub_by = (
        " • ".join(dubbers)
    )

    return merged


# ============================================================
# SMART SEARCH
# ============================================================

async def smart_anime_search(
    scraper,
    query: str
):

    query = clean_text(query)

    if not query:
        return None

    # --------------------------------------------------------
    # Dragon Ball / Naruto जैसे groups
    # --------------------------------------------------------

    group = detect_series_group(
        query
    )

    if group:

        logger.info(
            "Series group detected: %s",
            group["name"]
        )

        result = await get_series_group(
            scraper,
            query
        )

        if result:
            return result

    # --------------------------------------------------------
    # Normal anime
    # --------------------------------------------------------

    return await scraper.get_anime(
        query
    )


# ============================================================
# EASY FUNCTION FOR TELEGRAM BOT
# ============================================================

async def search_anime(
    query: str
):

    async with RareAnimeScraper() as scraper:

        result = await smart_anime_search(
            scraper,
            query
        )

        if not result:
            return None

        return {
            "data": result,
            "text": format_anime_result(
                result
            ),
            "poster": result.poster,
                    }
        # ============================================================
# PART 3 - EPISODE / ONGOING DETAILS
# ============================================================

def clean_date(value: str) -> str:
    if not value:
        return ""

    value = clean_text(value)

    # Common date formats को readable बनाओ
    formats = [
        "%d %B %Y",
        "%d %b %Y",
        "%B %d, %Y",
        "%b %d, %Y",
    ]

    for fmt in formats:
        try:
            dt = datetime.strptime(
                value,
                fmt
            )
            return dt.strftime(
                "%d %B %Y"
            )
        except ValueError:
            pass

    return value


def find_latest_episode(
    season: AnimeSeason
) -> int:

    numbers = [
        ep.number
        for ep in season.episodes_data
        if ep.number is not None
    ]

    if numbers:
        return max(numbers)

    return season.episodes or 0


def detect_ongoing_from_page(
    text: str
) -> bool:

    text = text.lower()

    completed_signals = [
        "completed",
        "complete",
        "all episodes",
        "season finale",
        "final episode",
    ]

    ongoing_signals = [
        "new episode every",
        "new episodes every",
        "next episode",
        "every monday",
        "every tuesday",
        "every wednesday",
        "every thursday",
        "every friday",
        "every saturday",
        "every sunday",
        "weekly",
    ]

    # अगर site explicitly completed बोल रही है
    if any(
        x in text
        for x in completed_signals
    ):
        return False

    if any(
        x in text
        for x in ongoing_signals
    ):
        return True

    return False


def extract_expected_date(
    text: str
) -> str:

    patterns = [

        r"(?:next\s+episode|expected\s+release)"
        r"\s*[:\-]?\s*"
        r"(\d{1,2}\s+[A-Za-z]+\s+\d{4})",

        r"(\d{1,2}\s+[A-Za-z]+\s+\d{4})",

        r"(\d{1,2}\s+[A-Za-z]{3}\s+\d{4})",

    ]

    for pattern in patterns:

        match = re.search(
            pattern,
            text,
            re.I
        )

        if match:
            return clean_date(
                match.group(1)
            )

    return ""


def extract_episode_languages(
    block: str
) -> list[str]:

    language_map = {
        "hindi": "Hindi",
        "english": "English",
        "tamil": "Tamil",
        "telugu": "Telugu",
        "malayalam": "Malayalam",
        "kannada": "Kannada",
        "bengali": "Bengali",
        "marathi": "Marathi",
        "japanese": "Japanese",
        "mandarin": "Mandarin",
        "chinese": "Chinese",
    }

    result = []

    for key, name in language_map.items():

        if re.search(
            rf"\b{re.escape(key)}\b",
            block,
            re.I
        ):

            result.append(name)

    return result


def update_episode_information(
    season: AnimeSeason,
    page_text: str
) -> AnimeSeason:

    # --------------------------------------------------------
    # Last episode
    # --------------------------------------------------------

    latest = find_latest_episode(
        season
    )

    if latest > season.episodes:
        season.episodes = latest

    # --------------------------------------------------------
    # Status
    # --------------------------------------------------------

    if detect_ongoing_from_page(
        page_text
    ):

        season.status = "Ongoing"

    elif season.status == "Unknown":

        season.status = "Completed"

    # --------------------------------------------------------
    # Schedule
    # --------------------------------------------------------

    if not season.schedule:

        season.schedule = (
            RareAnimeScraper()
            .extract_schedule(
                page_text
            )
        )

    # --------------------------------------------------------
    # Next episode
    # --------------------------------------------------------

    if season.status == "Ongoing":

        if not season.next_episode:

            season.next_episode = (
                latest + 1
                if latest
                else None
            )

        # ----------------------------------------------------
        # Expected release
        # ----------------------------------------------------

        if not season.next_release:

            season.next_release = (
                extract_expected_date(
                    page_text
                )
            )

    # --------------------------------------------------------
    # Episode language fallback
    # --------------------------------------------------------

    if season.episodes_data:

        all_languages = []

        for ep in season.episodes_data:

            if not ep.languages:

                ep.languages = (
                    extract_episode_languages(
                        page_text
                    )
                )

            for language in ep.languages:

                if language not in all_languages:
                    all_languages.append(
                        language
                    )

        if all_languages:

            season.languages = (
                all_languages
            )

    # --------------------------------------------------------
    # Hindi detection
    # --------------------------------------------------------

    if any(
        lang.lower() == "hindi"
        for lang in season.languages
    ):

        return season

    if re.search(
        r"\bhindi\b",
        page_text,
        re.I
    ):

        if "Hindi" not in season.languages:

            season.languages.insert(
                0,
                "Hindi"
            )

    return season


# ============================================================
# BETTER PAGE PARSER
# ============================================================

async def parse_page_complete(
    scraper: RareAnimeScraper,
    url: str
):

    html = await scraper.fetch(
        url
    )

    if not html:
        return None

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

    season = await scraper.parse_page(
        url
    )

    if not season:
        return None

    season = update_episode_information(
        season,
        page_text
    )

    # Better date formatting
    if season.release_date:

        season.release_date = clean_date(
            season.release_date
        )

    # Ongoing में total episodes हो तो
    # current / total सही रहेगा
    if (
        season.status == "Ongoing"
        and season.total_episodes
        and season.episodes
        > season.total_episodes
    ):

        season.total_episodes = None

    return season


# ============================================================
# FINAL SMART SEARCH
# ============================================================

async def final_anime_search(
    query: str
):

    query = clean_text(query)

    if not query:
        return None

    async with RareAnimeScraper() as scraper:

        # पहले grouped anime check
        group = detect_series_group(
            query
        )

        if group:

            result = await get_series_group(
                scraper,
                query
            )

            if result:
                return result

        # normal anime
        urls = await scraper.search(
            query
        )

        if not urls:
            return None

        urls = urls[
            :MAX_PAGES_TO_CHECK
        ]

        tasks = [
            parse_page_complete(
                scraper,
                url
            )
            for url in urls
        ]

        parsed = await asyncio.gather(
            *tasks,
            return_exceptions=True
        )

        seasons = []

        normalized_query = normalize_title(
            query
        )

        for item in parsed:

            if isinstance(
                item,
                Exception
            ):
                continue

            if not item:
                continue

            name = normalize_title(
                item.anime_name
            )

            if (
                normalized_query in name
                or name in normalized_query
            ):

                seasons.append(
                    item
                )

        if not seasons:
            return None

        # duplicate remove
        unique = {}

        for item in seasons:

            key = (
                item.season,
                normalize_title(
                    item.anime_name
                )
            )

            if key not in unique:

                unique[key] = item

            else:

                old = unique[key]

                if (
                    item.episodes
                    > old.episodes
                ):
                    unique[key] = item

        seasons = list(
            unique.values()
        )

        return scraper.combine_seasons(
            seasons
        )


# ============================================================
# BOT-FRIENDLY RESULT
# ============================================================

async def get_anime_info(
    query: str
):

    result = await final_anime_search(
        query
    )

    if not result:

        return {
            "found": False,
            "text": (
                f"❌ Anime not found: "
                f"{query}"
            ),
            "poster": None,
            "data": None,
        }

    return {
        "found": True,
        "text": format_anime_result(
            result
        ),
        "poster": result.poster,
        "data": result,
            }

    # ============================================================
# PART 4 - FINAL TELEGRAM OUTPUT + POSTER
# ============================================================

def build_telegram_text(result: AnimeResult) -> str:

    if not result:
        return "❌ Anime not found."

    lines = [
        f"🎬 Anime: {result.title}",
        "",
        (
            "🇮🇳 Hindi Dub: ✅ Available"
            if result.hindi_available
            else "🇮🇳 Hindi Dub: ❌ Not Available"
        ),
    ]

    if result.platforms:
        lines += [
            "📺 Platform: "
            + " • ".join(result.platforms)
        ]

    # --------------------------------------------------------
    # ONE SEASON
    # --------------------------------------------------------

    if len(result.seasons) == 1:

        s = result.seasons[0]

        if s.season is not None:
            lines.append(
                f"📀 Season: {s.season}"
            )

        if s.episodes:

            if (
                s.status == "Ongoing"
                and s.total_episodes
                and s.total_episodes > s.episodes
            ):
                lines.append(
                    f"🎬 Episodes: "
                    f"{s.episodes} / {s.total_episodes}"
                )
            else:
                lines.append(
                    f"🎬 Episodes: {s.episodes}"
                )

    # --------------------------------------------------------
    # MULTIPLE SEASONS / SERIES
    # --------------------------------------------------------

    elif len(result.seasons) > 1:

        lines += [
            "",
            "📚 Hindi Available Seasons:"
        ]

        current_series = ""

        for s in result.seasons:

            name = re.sub(
                r"\s+Season\s+\d+.*$",
                "",
                s.anime_name,
                flags=re.I
            )

            name = clean_text(name)

            if name != current_series:

                current_series = name

                lines.append("")
                lines.append(name)

            season_no = (
                str(s.season)
                if s.season is not None
                else "?"
            )

            langs = (
                " • ".join(s.languages)
                if s.languages
                else "Hindi"
            )

            lines.append(
                f"• Season {season_no}"
                f" — {langs}"
                f" — {s.episodes} episodes"
            )

        total = sum(
            s.episodes
            for s in result.seasons
        )

        lines += [
            "",
            f"📊 Total Hindi Episodes: {total}"
        ]

    # --------------------------------------------------------
    # LANGUAGES
    # --------------------------------------------------------

    if result.languages:

        lines += [
            "",
            "🌐 Languages: "
            + " • ".join(result.languages)
        ]

    # --------------------------------------------------------
    # STATUS
    # --------------------------------------------------------

    if result.status == "Ongoing":

        lines += [
            "",
            "📊 Status: 🔴 Ongoing"
        ]

    else:

        lines += [
            "",
            "📊 Status: ✅ Completed"
        ]

    # --------------------------------------------------------
    # LAST EPISODE
    # --------------------------------------------------------

    if result.last_episode:

        lines += [
            "",
            f"📅 Last Episode: "
            f"Episode {result.last_episode}"
        ]

    # --------------------------------------------------------
    # LAST RELEASE
    # --------------------------------------------------------

    if result.last_release:

        lines.append(
            f"🗓 Last Release: "
            f"{result.last_release}"
        )

    # --------------------------------------------------------
    # NEXT EPISODE
    # --------------------------------------------------------

    if result.status == "Ongoing":

        if result.next_episode:

            lines += [
                "",
                f"⏭ Next Episode: "
                f"Episode {result.next_episode}"
            ]

        if result.next_release:

            lines.append(
                f"📅 Expected Release: "
                f"{result.next_release}"
            )

        if result.schedule:

            lines.append(
                f"⏰ Schedule: "
                f"{result.schedule}"
            )

    # --------------------------------------------------------
    # STUDIO
    # --------------------------------------------------------

    lines += [
        "",
        "🏢 Studio: "
        + (result.studio or "—")
    ]

    # --------------------------------------------------------
    # DUB BY
    # --------------------------------------------------------

    lines.append(
        "🎙 Dub By: "
        + (result.dub_by or "—")
    )

    # --------------------------------------------------------
    # SOURCE
    # --------------------------------------------------------

    lines += [
        "",
        "🔎 Source: DC"
    ]

    return "\n".join(lines)


# ============================================================
# POSTER + TEXT DATA
# ============================================================

async def get_final_anime_result(
    query: str
):

    result = await final_anime_search(
        query
    )

    if not result:

        return {
            "found": False,
            "text": (
                f"❌ Anime not found: "
                f"{query}"
            ),
            "poster": None,
            "title": None,
            "data": None,
        }

    return {
        "found": True,
        "text": build_telegram_text(
            result
        ),
        "poster": result.poster or None,
        "title": result.title,
        "data": result,
    }


# ============================================================
# QUICK TEST
# ============================================================

async def test_final():

    queries = [
        "naruto",
        "konosuba",
        "black torch",
        "re zero",
        "spy x family",
        "dragon ball",
    ]

    for query in queries:

        print("\n" + "=" * 60)
        print("SEARCH:", query)
        print("=" * 60)

        try:

            data = await get_final_anime_result(
                query
            )

            print(data["text"])

            if data["poster"]:
                print(
                    "\nPOSTER:",
                    data["poster"]
                )

        except Exception as e:

            print(
                "ERROR:",
                e
            )


# ============================================================
# OPTIONAL DIRECT TEST
# ============================================================

if __name__ == "__main__":

    asyncio.run(
        test_final()
    )
             
