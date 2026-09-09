"""
Anime metadata scraper.

Sources:
- AnimeDubHindi:
    Hindi Dub status
    Season
    Episode
    Languages
    Schedule
    Release Date
    Explicit Dub By / platform tags
    Old/completed and ongoing anime posts

- Official streaming / official YouTube pages:
    Platform
    Explicitly listed audio language
    Season information when publicly visible
    Dub By / official channel information when explicitly visible

- Jikan / MyAnimeList:
    Anime title
    Poster
    Animation studio
    Total episode count

Important:
- No anime episodes are downloaded.
- No watch/download links are returned.
- Only metadata is processed.
- Source displayed to users is only "DC".
"""

import html
import re
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional
from urllib.parse import quote, unquote, urljoin

import requests
from bs4 import BeautifulSoup

from utils.logger import logger


# =====================================================================
# URLS
# =====================================================================

SITE_URL = "https://www.animedubhindi.link/"
SCHEDULE_URL = f"{SITE_URL}schedule.php"
ANIME_MIRCHI_URL = "https://animemirchi.com/"
JIKAN_URL = "https://api.jikan.moe/v4/anime"
JIKAN_API_BASE = "https://api.jikan.moe/v4"
ANILIST_URL = "https://graphql.anilist.co"


# =====================================================================
# OFFICIAL SOURCES
# =====================================================================

OFFICIAL_SOURCES = {
    "Crunchyroll": [
        "crunchyroll.com",
    ],
    "Netflix": [
        "netflix.com",
    ],
    "JioHotstar": [
        "hotstar.com",
        "jiohotstar.com",
    ],
    "Amazon Prime Video": [
        "primevideo.com",
        "amazon.com",
    ],
    "Sony YAY! / SonyLIV": [
        "sonyliv.com",
    ],
    "Amazon MX Player": [
        "mxplayer.in",
    ],
    "ZEE5": [
        "zee5.com",
    ],
    "Muse India": [
        "youtube.com",
        "museindia.in",
    ],
    "Ani-One India": [
        "youtube.com",
        "ani-one.com",
    ],
    "Anime Times": [
        "youtube.com",
        "animetimes.co.jp",
    ],
}


# =====================================================================
# PLATFORM TAGS FOUND ON ANIME DUB HINDI
# =====================================================================

PLATFORM_TAGS = {
    "cr dub": "Crunchyroll",
    "crunchyroll dub": "Crunchyroll",
    "crunchyroll": "Crunchyroll",
    "cr": "Crunchyroll",

    "nf dub": "Netflix",
    "netflix dub": "Netflix",
    "netflix": "Netflix",
    "nf": "Netflix",

    "amzn dub": "Amazon Prime Video",
    "amazon prime video": "Amazon Prime Video",
    "prime video": "Amazon Prime Video",
    "amzn": "Amazon Prime Video",

    "hotstar": "JioHotstar",
    "jiohotstar": "JioHotstar",
    "jio hotstar": "JioHotstar",

    "sony yay": "Sony YAY",
    "sony liv": "Sony LIV",

    "mx player": "MX Player",

    "muse dub": "Muse India",
    "muse india": "Muse India",

    "anime times": "Anime Times",
    "anime time": "Anime Times",

    "ani-one": "Ani-One",
    "ani one": "Ani-One",
}


# =====================================================================
# LANGUAGE NAMES
# =====================================================================

LANGUAGES = [
    "Hindi",
    "English",
    "Tamil",
    "Telugu",
    "Japanese",
    "Korean",
    "Chinese",
    "Malayalam",
    "Kannada",
    "Marathi",
    "Bengali",
    "Bangla",
]


class AnimeScraper:
    """Live anime information lookup service."""

    # =================================================================
    # INIT
    # =================================================================

    def __init__(self) -> None:

        self.session = requests.Session()

        self.session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Linux; Android 14) "
                    "AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/131.0 "
                    "Mobile Safari/537.36"
                ),
                "Accept-Language": (
                    "en-IN,en;q=0.9"
                ),
            }
        )

    # =================================================================
    # MAIN SEARCH
    # =================================================================

    def search_anime(
        self,
        anime_name: str,
    ) -> Optional[Dict]:
        """
        Resolve an anime from a short/friendly name, then verify Hindi
        dub/platform information from official sources in parallel.

        Official sources are authoritative for platform/audio claims.
        AnimeDubHindi and AnimeMirchi are fallback cross-check sources
        only when the official checks cannot establish the information.
        """

        anime_name = (anime_name or "").strip()
        if not anime_name:
            return None

        query = self._normalize(anime_name)
        logger.info("Anime search started: %s", anime_name)

        # Resolve the title and fetch fallback sources concurrently.
        # MAL/Jikan is also what prevents a short query such as
        # "re zero" from requiring the complete official title.
        def fetch_mal():
            return AnimeScraper()._get_mal_info(anime_name)

        def fetch_anilist():
            return AnimeScraper()._get_anilist_info(anime_name)

        def fetch_animedubhindi():
            return AnimeScraper()._find_animedubhindi(anime_name, query)

        def fetch_mirchi():
            return AnimeScraper()._search_anime_mirchi(anime_name, query)

        mal = None
        anilist = None
        fallback_dh = None
        mirchi = None

        # Use several independent metadata sources at the same time.  A
        # temporary 429/403 from one service must not make a valid anime
        # look "not found".
        with ThreadPoolExecutor(max_workers=4, thread_name_prefix="anime-base") as executor:
            futures = {
                executor.submit(fetch_mal): "mal",
                executor.submit(fetch_anilist): "anilist",
                executor.submit(fetch_animedubhindi): "animedubhindi",
                executor.submit(fetch_mirchi): "animemirchi",
            }
            for future in as_completed(futures):
                name = futures[future]
                try:
                    value = future.result()
                    if name == "mal":
                        mal = value
                    elif name == "anilist":
                        anilist = value
                    elif name == "animedubhindi":
                        fallback_dh = value
                    else:
                        mirchi = value
                except Exception as exc:
                    logger.debug("%s lookup failed: %s", name, exc)

        # The anime itself is considered found if MAL/Jikan or either
        # fallback metadata source identified a matching title.
        if not mal and not anilist and not fallback_dh and not mirchi:
            logger.info("Anime not found in metadata sources: %s", anime_name)
            return None

        result = {
            "name": (
                mal.get("name") if mal else
                anilist.get("name") if anilist else
                fallback_dh.get("name") if fallback_dh else
                mirchi.get("name") if mirchi else anime_name
            ),
            "hindi_dub": "Not Verified",
            "platform": None,
            "platform_entries": [],
            "dub_by": None,
            "studio": (mal.get("studio") if mal else (anilist.get("studio") if anilist else None)),
            "hindi_details": None,
            "season": fallback_dh.get("season") if fallback_dh else None,
            "episodes": (fallback_dh.get("episodes") if fallback_dh else (anilist.get("episodes") if anilist else None)),
            "languages": fallback_dh.get("languages") if fallback_dh else None,
            "schedule": fallback_dh.get("schedule") if fallback_dh else None,
            "release_date": fallback_dh.get("release_date") if fallback_dh else None,
            "status": (mal.get("status") if mal else (anilist.get("status") if anilist else None)),
            "airing": (mal.get("airing") if mal else (anilist.get("airing") if anilist else None)),
            "total_episodes": (mal.get("total_episodes") if mal else (anilist.get("total_episodes") if anilist else None)),
            "aired_episodes": (mal.get("aired_episodes") if mal else (anilist.get("aired_episodes") if anilist else None)),
            "last_episode": (mal.get("last_episode") if mal else (anilist.get("last_episode") if anilist else None)),
            "last_episode_date": (mal.get("last_episode_date") if mal else (anilist.get("last_episode_date") if anilist else None)),
            "next_episode": (mal.get("next_episode") if mal else (anilist.get("next_episode") if anilist else None)),
            "next_episode_date": (mal.get("next_episode_date") if mal else (anilist.get("next_episode_date") if anilist else None)),
            "broadcast": (mal.get("broadcast") if mal else (anilist.get("broadcast") if anilist else None)),
            "poster_url": (mal.get("poster_url") if mal else (anilist.get("poster_url") if anilist else None)),
            "mal_url": mal.get("mal_url") if mal else None,
            "source": "DC",
            "source_link": None,
            "verification_level": "unverified",
        }

        # Keep richer fallback metadata available, but never let it claim
        # that a platform is official before the official verification step.
        if fallback_dh:
            for key in (
                "season", "episodes", "languages", "schedule",
                "release_date", "studio", "hindi_details",
            ):
                if not result.get(key) and fallback_dh.get(key):
                    result[key] = fallback_dh[key]

        # AniList is a metadata-only emergency fallback when Jikan/MAL is
        # rate-limited.  It never replaces official platform verification.
        if anilist:
            for key in (
                "name", "episodes", "total_episodes", "aired_episodes",
                "last_episode", "last_episode_date", "next_episode",
                "next_episode_date", "broadcast", "poster_url", "studio",
                "status", "airing",
            ):
                if not result.get(key) and anilist.get(key) is not None:
                    result[key] = anilist[key]

        if mirchi:
            result["fallback_mirchi"] = mirchi

        # IMPORTANT: all official platforms are checked concurrently.
        official = self._check_official_sources(anime_name)
        official_entries = official.get("platform_entries", [])

        if official_entries:
            result["platform_entries"] = self._dedupe_platform_entries(official_entries)
            result["platform"] = " • ".join(dict.fromkeys(
                entry.get("platform")
                for entry in result["platform_entries"]
                if entry.get("platform")
            )) or None
            verified_languages = []
            for entry in result["platform_entries"]:
                for language in entry.get("languages", []):
                    if language not in verified_languages:
                        verified_languages.append(language)
            result["languages"] = self._languages_string(
                self._ordered_languages(verified_languages)
            ) or None
            result["hindi_dub"] = "Available" if any(
                "Hindi" in entry.get("languages", [])
                for entry in result["platform_entries"]
            ) else "Not Verified"
            result["dub_by"] = official.get("dub_by")
            result["verification_level"] = "official"
        else:
            # No clear official evidence: use the two requested fallback
            # databases as cross-checks. Their data is useful, but it is
            # explicitly kept separate internally from official evidence.
            fallback_entries = []
            fallback_dub_by = []

            if fallback_dh:
                fallback_entries.extend(fallback_dh.get("platform_entries", []))
                if fallback_dh.get("dub_by"):
                    fallback_dub_by.append(fallback_dh["dub_by"])

            if mirchi:
                fallback_entries.extend(mirchi.get("platform_entries", []))
                if mirchi.get("dub_by"):
                    fallback_dub_by.append(mirchi["dub_by"])

            fallback_entries = self._dedupe_platform_entries(fallback_entries)
            if fallback_entries:
                result["platform_entries"] = fallback_entries
                result["platform"] = " • ".join(dict.fromkeys(
                    entry.get("platform")
                    for entry in fallback_entries
                    if entry.get("platform")
                )) or None

                fallback_languages = []
                for entry in fallback_entries:
                    for language in entry.get("languages", []):
                        if language not in fallback_languages:
                            fallback_languages.append(language)
                result["languages"] = self._languages_string(
                    self._ordered_languages(fallback_languages)
                ) or result.get("languages")

                hindi_found = any(
                    "Hindi" in entry.get("languages", [])
                    for entry in fallback_entries
                )
                if hindi_found or (mirchi and mirchi.get("hindi_dub")) or (fallback_dh and fallback_dh.get("hindi_dub") == "Available"):
                    result["hindi_dub"] = "Available"

                if fallback_dub_by:
                    result["dub_by"] = " • ".join(dict.fromkeys(fallback_dub_by))

                result["verification_level"] = "fallback"

        # Always expose DC to the bot user, regardless of the internal
        # verification path.
        result["source"] = "DC"
        result["source_link"] = None

        # Movies must not inherit a series episode/season count.
        combined_name = str(result.get("name") or anime_name)
        if self._is_movie_result(combined_name, result.get("hindi_details")):
            result["season"] = None
            result["episodes"] = None
            result["total_episodes"] = None
            result["aired_episodes"] = None
            result["last_episode"] = None
            result["next_episode"] = None

        return result

    # =================================================================
    # ANIMEDUBHINDI SEARCH
    # =================================================================

    def _find_animedubhindi(
        self,
        anime_name: str,
        query: str,
    ) -> Optional[Dict]:

        # -------------------------------------------------------------
        # Current schedule
        # -------------------------------------------------------------

        schedule_result = self._search_schedule(
            query
        )

        if schedule_result:
            return schedule_result

        # -------------------------------------------------------------
        # Site search
        # -------------------------------------------------------------

        search_result = self._search_site_search(
            anime_name,
            query
        )

        if search_result:
            return search_result

        # -------------------------------------------------------------
        # Search engine fallback restricted to the site.
        # This helps with old/completed posts.
        # -------------------------------------------------------------

        engine_result = self._search_engine_for_site(
            anime_name,
            query
        )

        if engine_result:
            return engine_result

        return None

    # =================================================================
    # SCHEDULE
    # =================================================================

    def _search_schedule(
        self,
        query: str,
    ) -> Optional[Dict]:

        try:

            response = self.session.get(
                SCHEDULE_URL,
                timeout=20
            )

            response.raise_for_status()

            soup = BeautifulSoup(
                response.text,
                "html.parser"
        )
                
            return self._parse_matching_page(
                soup,
                query,
                SCHEDULE_URL
            )

        except Exception as exc:

            logger.warning(
                "Schedule search failed: %s",
                exc
            )

            return None

    # =================================================================
    # WORDPRESS SITE SEARCH
    # =================================================================

    def _search_site_search(
        self,
        anime_name: str,
        query: str,
    ) -> Optional[Dict]:

        search_url = (
            SITE_URL
            + "?s="
            + quote(anime_name)
        )

        try:

            response = self.session.get(
                search_url,
                timeout=20,
                allow_redirects=True
            )

            if response.status_code != 200:
                return None

            soup = BeautifulSoup(
                response.text,
                "html.parser"
            )

            # ---------------------------------------------------------
            # DO NOT treat page title like:
            # "Search Results for: Naruto"
            # as the anime itself.
            # ---------------------------------------------------------

            candidates = []

            for link in soup.find_all(
                "a",
                href=True
            ):

                title = link.get_text(
                    " ",
                    strip=True
                )

                href = link.get(
                    "href"
                )

                if not title or not href:
                    continue

                if len(title) > 300:
                    continue

                if self._is_generic_title(
                    title
                ):
                    continue

                if not self._title_matches(
                    title,
                    query
                ):
                    continue

                full_url = urljoin(
                    SITE_URL,
                    href
                )

                if not full_url.startswith(
                    SITE_URL
                ):
                    continue

                candidates.append(
                    (
                        title,
                        full_url
                    )
                )

            # ---------------------------------------------------------
            # Try article pages.
            # ---------------------------------------------------------

            seen = set()

            for fallback_title, article_url in (
                candidates
            ):

                if article_url in seen:
                    continue

                seen.add(
                    article_url
                )

                result = self._parse_detail_page(
                    article_url,
                    fallback_title,
                    query
                )

                if result:
                    return result

        except Exception as exc:

            logger.warning(
                "AnimeDubHindi search failed: %s",
                exc
            )

        return None

    # =================================================================
    # SEARCH ENGINE FALLBACK
    # =================================================================

    def _search_engine_for_site(
        self,
        anime_name: str,
        query: str,
    ) -> Optional[Dict]:

        search_url = (
            "https://html.duckduckgo.com/html/?q="
            + quote(
                f"site:animedubhindi.link "
                f'"{anime_name}"'
            )
        )

        try:

            response = self.session.get(
                search_url,
                timeout=15
            )

            response.raise_for_status()

            soup = BeautifulSoup(
                response.text,
                "html.parser"
            )

            for result in soup.select(
                ".result"
            ):

                anchor = result.select_one(
                    ".result__a"
                )

                if not anchor:
                    continue

                href = anchor.get(
                    "href"
                )

                if not href:
                    continue

                href = unquote(
                    href
                )

                if not href.startswith(
                    "http"
                ):
                    continue

                if "animedubhindi.link" not in (
                    href.lower()
                ):
                    continue

                title = anchor.get_text(
                    " ",
                    strip=True
                )

                if not self._title_matches(
                    title,
                    query
                ):
                    # Look at result snippet too.
                    snippet_tag = (
                        result.select_one(
                            ".result__snippet"
                        )
                    )

                    snippet = (
                        snippet_tag.get_text(
                            " ",
                            strip=True
                        )
                        if snippet_tag
                        else ""
                    )

                    if not self._title_matches(
                        snippet,
                        query
                    ):
                        continue

                parsed = self._parse_detail_page(
                    href,
                    title,
                    query
                )

                if parsed:
                    return parsed

        except Exception as exc:

            logger.debug(
                "Search engine fallback failed: %s",
                exc
            )

        return None

    # =================================================================
    # PARSE MATCHING PAGE
    # =================================================================

    def _parse_matching_page(
        self,
        soup: BeautifulSoup,
        query: str,
        page_url: str,
    ) -> Optional[Dict]:

        elements = soup.find_all(
            [
                "h1",
                "h2",
                "h3",
                "h4",
                "h5",
                "a",
            ]
        )

        best = None
        best_score = -1

        for element in elements:

            title = element.get_text(
                " ",
                strip=True
            )

            if not title:
                continue

            if len(title) > 300:
                continue

            if self._is_generic_title(
                title
            ):
                continue

            if not self._title_matches(
                title,
                query
            ):
                continue

            container = element
            text = title

            for _ in range(8):

                if container.parent is None:
                    break

                container = container.parent

                candidate = (
                    container.get_text(
                        " ",
                        strip=True
                    )
                )

                if (
                    20
                    <= len(candidate)
                    <= 5000
                ):
                    text = candidate

                if re.search(
                    r"\bHindi\b",
                    candidate,
                    re.I
                ):
                    break

            score = 0

            if re.search(
                r"\bHindi\b",
                text,
                re.I
            ):
                score += 50

            if self._extract_season(
                text
            ):
                score += 20

            if self._extract_episode(
                text
            ):
                score += 20

            if self._extract_schedule(
                text
            ):
                score += 10

            if (
                self._extract_platform_tag(
                    text
                )
            ):
                score += 15

            if score > best_score:

                best_score = score

                best = (
                    element,
                    title,
                    text
                )

        if not best:
            return None

        element, title, text = best

        href = element.get(
            "href"
        )

        detail_url = None

        if href:

            full = urljoin(
                SITE_URL,
                href
            )

            if full.startswith(
                SITE_URL
            ):
                detail_url = full

        return self._build_result(
            title,
            text,
            detail_url
        )

    # =================================================================
    # PARSE DETAIL PAGE
    # =================================================================

    def _parse_detail_page(
        self,
        url: str,
        fallback_title: str,
        query: str,
    ) -> Optional[Dict]:

        try:

            response = self.session.get(
                url,
                timeout=20
            )

            if response.status_code != 200:
                return None

            soup = BeautifulSoup(
                response.text,
                "html.parser"
            )

            for tag in soup.find_all(
                [
                    "script",
                    "style",
                    "noscript",
                ]
            ):
                tag.decompose()

            title = self._pick_title(
                soup,
                fallback_title
            )

            page_text = soup.get_text(
                " ",
                strip=True
            )

            # Search page titles and generic headings are never
            # accepted as anime names.
            if self._is_generic_title(
                title
            ):
                title = fallback_title

            if (
                not self._title_matches(
                    title,
                    query
                )
                and not self._title_matches(
                    page_text[:12000],
                    query
                )
            ):
                return None

            result = self._build_result(
                title,
                page_text,
                url
            )

            # Use source page's OG image only as temporary fallback.
            if not result.get(
                "poster_url"
            ):
                result["poster_url"] = (
                    self._extract_og_image(
                        soup
                                )
                )

            return result

        except Exception as exc:

            logger.debug(
                "Detail page parsing failed: %s",
                exc
            )

            return None

    # =================================================================
    # BUILD RESULT
    # =================================================================

    def _build_result(
        self,
        title: str,
        text: str,
        detail_url: Optional[str],
    ) -> Dict:

        languages = (
            self._extract_languages(
                text
            )
        )

        return {
            "name": self._clean_title(
                title
            ),

            "hindi_dub": (
                "Available"
                if "Hindi" in languages
                else "Not Mentioned"
            ),

            "platform": (
                self._extract_platform_tag(
                    text
                )
            ),

            "platform_entries": (
                self._entries_from_source(
                    text,
                    languages
                )
            ),

            "dub_by": (
                self._extract_dub_by(
                    text
                )
            ),

            "studio": (
                self._extract_studio(text)
            ),

            "hindi_details": None,

            "season": (
                self._extract_season(
                    text
                )
            ),

            "episodes": (
                self._extract_episode(
                    text
                )
            ),

            "languages": (
                self._languages_string(
                    languages
                )
            ),

            "schedule": (
                self._extract_schedule(
                    text
                )
            ),

            "release_date": (
                self._extract_date(
                    text
                )
            ),

            "poster_url": None,

            "mal_url": None,

            "source": "DC",

            "source_link": None,

            "_detail_url": detail_url,
        }
    # =================================================================
    # SOURCE PLATFORM TAG
    # =================================================================

    @staticmethod
    def _extract_platform_tag(
        text: str,
    ) -> Optional[str]:

        low = (
            text or ""
        ).lower()

        found = []

        for alias, platform in sorted(
            PLATFORM_TAGS.items(),
            key=lambda item: len(
                item[0]
            ),
            reverse=True
        ):

            if re.search(
                rf"\b{re.escape(alias)}\b",
                low,
                re.I
            ):

                found.append(
                    platform
                )

        if not found:
            return None

        return " • ".join(
            dict.fromkeys(
                found
            )
        )

    # =================================================================
    # SOURCE ENTRIES
    # =================================================================

    def _entries_from_source(
        self,
        text: str,
        languages: List[str],
    ) -> List[Dict]:

        platform = (
            self._extract_platform_tag(
                text
            )
        )

        if not platform:
            return []

        seasons = (
            self._season_numbers(
                text
            )
        )

        if not seasons:
            seasons = ["all"]

        platforms = [
            item.strip()
            for item in platform.split(
                " • "
            )
        ]

        return [
            {
                "platform": name,
                "seasons": seasons,
                "languages": (
                    self._ordered_languages(
                        languages
                    )
                ),
                "verified": True,
                "source": "AnimeDubHindi",
            }
            for name in platforms
        ]

    # =================================================================
    # ANIME MIRCHI PLATFORM / DUB BY SEARCH
    # =================================================================

    def _search_anime_mirchi(
        self,
        anime_name: str,
        query: str,
    ) -> Optional[Dict]:
        """
        Anime Mirchi is the preferred source for platform and explicit
        Dub By information.  It is intentionally independent from the
        official OTT checks below, which are retained for compatibility
        but are no longer needed for normal searches.
        """

        search_url = (
            ANIME_MIRCHI_URL
            + "?s="
            + quote(anime_name)
        )

        try:
            response = self.session.get(
                search_url,
                timeout=6,
                allow_redirects=True
            )

            if response.status_code != 200:
                logger.debug(
                    "Anime Mirchi search returned %s",
                    response.status_code
                )
                return None

            soup = BeautifulSoup(
                response.text,
                "html.parser"
            )

            candidates = []
            seen = set()

            for link in soup.find_all(
                "a",
                href=True
            ):
                title = link.get_text(
                    " ",
                    strip=True
                )
                href = link.get("href")

                if not title or not href:
                    continue

                if len(title) > 300:
                    continue

                if self._is_generic_title(title):
                    continue

                full_url = urljoin(
                    ANIME_MIRCHI_URL,
                    href
                )

                if not full_url.startswith(
                    ANIME_MIRCHI_URL
                ):
                    continue

                if full_url in seen:
                    continue

                if not self._title_matches(
                    title,
                    query
                ):
                    continue

                seen.add(full_url)

                score = 0

                normalized_title = self._normalize(title)
                normalized_query = self._normalize(anime_name)

                if normalized_title == normalized_query:
                    score += 100
                elif normalized_query in normalized_title:
                    score += 50

                if re.search(
                    r"\b(guide|hindi|dub|india|season|episodes?)\b",
                    title,
                    re.I
                ):
                    score += 10

                candidates.append(
                    (score, title, full_url)
                )

            candidates.sort(
                key=lambda item: item[0],
                reverse=True
            )

            for _score, title, article_url in candidates[:8]:
                parsed = self._parse_anime_mirchi_page(
                    article_url,
                    title,
                    query
                )

                if parsed:
                    return parsed

            # Anime Mirchi can occasionally return an incomplete WordPress
            # search page.  Use a site-restricted search-engine lookup as
            # a fallback, while still fetching/parsing only Anime Mirchi
            # pages for the actual metadata.
            engine_url = (
                "https://html.duckduckgo.com/html/?q="
                + quote(
                    f'site:animemirchi.com "{anime_name}"'
                )
            )

            engine_response = self.session.get(
                engine_url,
                timeout=6
            )

            if engine_response.status_code == 200:
                engine_soup = BeautifulSoup(
                    engine_response.text,
                    "html.parser"
                )

                engine_candidates = []

                for item in engine_soup.select(".result")[:10]:
                    anchor = item.select_one(".result__a")

                    if not anchor:
                        continue

                    href = unquote(
                        anchor.get("href", "")
                    )

                    if "animemirchi.com" not in href.lower():
                        continue

                    title_text = anchor.get_text(
                        " ",
                        strip=True
                    )

                    if not self._title_matches(
                        title_text,
                        query
                    ):
                        continue

                    engine_candidates.append(
                        (title_text, href)
                    )

                for title_text, article_url in engine_candidates:
                    parsed = self._parse_anime_mirchi_page(
                        article_url,
                        title_text,
                        query
                    )

                    if parsed:
                        return parsed

        except Exception as exc:
            logger.debug(
                "Anime Mirchi search failed: %s",
                exc
            )

        return None

    def _parse_anime_mirchi_page(
        self,
        url: str,
        fallback_title: str,
        query: str,
    ) -> Optional[Dict]:

        try:
            response = self.session.get(
                url,
                timeout=6,
                allow_redirects=True
            )

            if response.status_code != 200:
                return None

            soup = BeautifulSoup(
                response.text,
                "html.parser"
            )

            for tag in soup.find_all(
                ["script", "style", "noscript"]
            ):
                tag.decompose()

            title = self._pick_title(
                soup,
                fallback_title
            )

            text = soup.get_text(
                " ",
                strip=True
            )

            # Preserve table row structure in a compact metadata block so
            # labels such as Platform (India), Dub Platform(s), and Studio
            # remain associated with their values after HTML flattening.
            table_parts = []
            for table in soup.find_all("table"):
                for row in table.find_all("tr"):
                    cells = [
                        cell.get_text(" ", strip=True)
                        for cell in row.find_all(["th", "td"])
                    ]
                    if cells:
                        table_parts.append(" | ".join(cells))

            if table_parts:
                text += " " + " ".join(table_parts)

            if self._is_generic_title(title):
                title = fallback_title

            if not self._title_matches(
                title,
                query
            ) and not self._query_present(
                query,
                text[:30000]
            ):
                return None

            platforms = self._extract_mirchi_platforms(text)
            dub_by = self._extract_mirchi_dub_by(text)

            # A page with no platform/dub information is not useful as
            # the platform source, so continue to the next candidate.
            if not platforms and not dub_by:
                return None

            languages = self._extract_languages(text)
            seasons = self._season_numbers(text)

            entries = []

            for platform in platforms:
                entries.append(
                    {
                        "platform": platform,
                        "seasons": seasons or ["all"],
                        "languages": self._ordered_languages(
                            languages
                        ),
                        "verified": True,
                        "source": "Anime Mirchi",
                    }
            )
        
            return {
                "name": self._clean_mirchi_title(title),
                "platform": " • ".join(platforms) if platforms else None,
                "platform_entries": self._dedupe_platform_entries(entries),
                "dub_by": dub_by,
                "hindi_dub": (
                    "Available"
                    if re.search(r"\bHindi\b", text, re.I)
                    else None
                ),
                "source_link": url,
            }

        except Exception as exc:
            logger.debug(
                "Anime Mirchi page parsing failed: %s",
                exc
            )

        return None

    @staticmethod
    def _clean_mirchi_title(
        title: str,
    ) -> str:

        value = re.sub(
            r"\s+[-|–]\s+Anime Mirchi.*$",
            "",
            title or "",
            flags=re.I
        )

        return value.strip()

    @staticmethod
    def _extract_mirchi_platforms(
        text: str,
    ) -> List[str]:
        """Extract platform names from Anime Mirchi article text."""

        found = []

        platform_names = [
            "Crunchyroll",
            "Netflix",
            "JioHotstar",
            "Jio Hotstar",
            "Amazon Prime Video",
            "Prime Video",
            "Sony YAY",
            "Sony LIV",
            "MX Player",
            "Muse India",
            "Anime Times",
            "Ani-One",
            "YouTube",
        ]

        # Labelled text sections.
        patterns = [
            r"Indian Platforms\s*[:|]?\s*(.{0,400})",
            r"Dub Platform\(s\)\s*[:|]?\s*(.{0,400})",
            r"Platform \(India\)\s*[:|]?\s*(.{0,400})",
            r"Where to Watch(?: Solo Leveling)?\s*(.{0,500})",
        ]

        chunks = []

        for pattern in patterns:
            chunks.extend(
                re.findall(
                    pattern,
                    text or "",
                    re.I
                )
            )

        # Also inspect table-like flattened text.  Anime Mirchi commonly
        # uses rows such as `Platform (India) | Crunchyroll`.
        chunks.extend(
            re.findall(
                r"(?:Platform|Indian Platforms|Dub Platform\(s\))\s*[^.]{0,500}",
                text or "",
                re.I
            )
        )

        for chunk in chunks:
            for platform in platform_names:
                if re.search(
                    rf"\b{re.escape(platform)}\b",
                    chunk,
                    re.I
                ):
                    canonical = {
                        "Jio Hotstar": "JioHotstar",
                        "Prime Video": "Amazon Prime Video",
                    }.get(
                        platform,
                        platform
                    )

                    if canonical not in found:
                        found.append(canonical)

        return found

    @staticmethod
    def _extract_mirchi_dub_by(
        text: str,
    ) -> Optional[str]:
        patterns = [
            r"Dubbed\s+By\s*[:|]\s*([^|.]{1,120})",
            r"Dub\s+By\s*[:|]\s*([^|.]{1,120})",
            r"Dubbing\s+(?:By|Studio)\s*[:|]\s*([^|.]{1,120})",
            r"dubbing\s+(?:was|is)\s+(?:done|produced)\s+by\s+([^|.]{1,120})",
        ]

        for pattern in patterns:
            match = re.search(
                pattern,
                text or "",
                re.I
            )
            if match:
                value = re.sub(
                    r"\s+",
                    " ",
                    match.group(1)
                ).strip(" :-|•,")

                if value and len(value) <= 120:
                    return value

        return None

    @staticmethod
    def _is_movie_result(
        title: str,
        details: Optional[str],
    ) -> bool:
        value = f"{title or ''} {details or ''}"
        return bool(
            re.search(
                r"\b(movie|film|theatrical|ova film)\b",
                value,
                re.I
            )
        )

    # =================================================================
    # OFFICIAL PLATFORM CHECK
    # =================================================================

    def _check_official_sources(
        self,
        anime_name: str,
    ) -> Dict:
        """
        Check all configured official domains concurrently.

        A result is accepted only after a page from the official domain is
        fetched and the page itself contains the anime query plus an audio
        language signal. Search-engine results are discovery only; they are
        never treated as proof on their own.
        """

        tasks = []
        for platform, domains in OFFICIAL_SOURCES.items():
            for domain in domains:
                tasks.append((platform, domain))

        def check_one(platform: str, domain: str):
            local_entries = []
            local_dub_by = []

            try:
                results = self._search_engine(anime_name, domain)
                for item in results[:5]:
                    page = self._fetch_page(item.get("url", ""))
                    if not page:
                        continue

                    page_text, _soup = page
                    combined = (
                        f"{item.get('title', '')} "
                        f"{item.get('snippet', '')} "
                        f"{page_text[:50000]}"
                    )

                    if not self._query_present(anime_name, combined):
                        continue

                    languages = self._extract_verified_audio_languages(combined)
                    # A generic word such as "Hindi" somewhere on a page is
                    # NOT proof of Hindi audio. Accept only audio/dub context.
                    if "Hindi" not in languages:
                        continue

                    seasons = self._season_numbers(combined) or ["all"]

                    # Official YouTube channels are displayed by their
                    # channel/platform name, while the actual platform is
                    # still YouTube internally.
                    channel_platforms = {
                        "Muse India", "Ani-One India", "Anime Times"
                    }
                    display_platform = (
                        "YouTube" if platform in channel_platforms else platform
                    )

                    local_entries.append({
                        "platform": display_platform,
                        "channel": platform if display_platform == "YouTube" else None,
                        "seasons": seasons,
                        "languages": self._ordered_languages(languages),
                        "verified": True,
                        "source": platform,
                    })

                    explicit = self._extract_dub_by(combined)
                    if explicit:
                        local_dub_by.append(explicit)
                    break

            except Exception as exc:
                logger.debug(
                    "Official source check failed %s (%s): %s",
                    platform, domain, exc
                )

            return local_entries, local_dub_by

        entries = []
        dub_by_names = []

        # 10+ official checks can run at the same time instead of waiting
        # for every service one after another.
        with ThreadPoolExecutor(
            max_workers=min(12, max(1, len(tasks))),
            thread_name_prefix="official-source",
        ) as executor:
            futures = {
                executor.submit(check_one, platform, domain): (platform, domain)
                for platform, domain in tasks
            }
            for future in as_completed(futures):
                try:
                    found_entries, found_dub_by = future.result()
                    entries.extend(found_entries)
                    dub_by_names.extend(found_dub_by)
                except Exception as exc:
                    logger.debug("Official worker failed: %s", exc)

        unique_dub_by = list(dict.fromkeys(
            value for value in dub_by_names if value
        ))

        return {
            "platform_entries": self._dedupe_platform_entries(entries),
            "dub_by": " • ".join(unique_dub_by) if unique_dub_by else None,
        }

    # =================================================================
    # WEB SEARCH
    # =================================================================

    def _search_engine(
        self,
        anime_name: str,
        domain: str,
    ) -> List[Dict]:

        query = (
            f"site:{domain} "
            f'"{anime_name}" '
            f'(Hindi OR dubbed OR audio OR language) anime'
        )

        url = (
            "https://html.duckduckgo.com/html/?q="
            + quote(query)
        )

        try:

            response = self.session.get(
                url,
                timeout=15
            )

            response.raise_for_status()

            soup = BeautifulSoup(
                response.text,
                "html.parser"
            )

            results = []

            for result in soup.select(
                ".result"
            )[:10]:

                anchor = result.select_one(
                    ".result__a"
                )

                if not anchor:
                    continue

                href = anchor.get(
                    "href"
                )

                if not href:
                    continue

                href = unquote(
                    href
                )

                if domain.lower() not in (
                    href.lower()
                ):
                    continue

                snippet_tag = (
                    result.select_one(
                        ".result__snippet"
                    )
                )

                results.append(
                    {
                        "url": href,
                        "title": anchor.get_text(
                            " ",
                            strip=True
                        ),
                        "snippet": (
                            snippet_tag.get_text(
                                " ",
                                strip=True
                            )
                            if snippet_tag
                            else ""
                        ),
                    }
                )

            return results

        except Exception as exc:

            logger.debug(
                "Official source search failed %s: %s",
                domain,
                exc
            )

            return []
    # =================================================================
    # FETCH PAGE
    # =================================================================
    def _fetch_page(
        self,
        url: str,
    ) -> Optional[tuple]:

        try:

            response = self.session.get(
                url,
                timeout=15,
                allow_redirects=True
            )

            if response.status_code != 200:
                return None

            soup = BeautifulSoup(
                response.text,
                "html.parser"
            )

            for tag in soup.find_all(
                [
                    "script",
                    "style",
                    "noscript",
                ]
            ):
                tag.decompose()

            return (
                soup.get_text(
                    " ",
                    strip=True
                ),
                soup,
            )

        except Exception:
            return None

    # =================================================================
    # ANILIST METADATA FALLBACK
    # =================================================================

    def _get_anilist_info(
        self,
        anime_name: str,
    ) -> Optional[Dict]:
        """
        Independent metadata fallback for times when Jikan/MAL is
        rate-limited.  AniList is used only for anime identity and metadata;
        it is never used as proof of Hindi dubbing or platform availability.
        """
        query = (anime_name or "").strip()
        if not query:
            return None

        graphql = """
        query ($search: String) {
          Page(page: 1, perPage: 8) {
            media(search: $search, type: ANIME) {
              id
              type
              title {
                romaji
                english
                native
                userPreferred
              }
              episodes
              status
              isAdult
              isLicensed
              coverImage {
                large
                extraLarge
              }
              studios(isMain: true) {
                nodes {
                  name
                }
              }
              nextAiringEpisode {
                airingAt
                episode
              }
              airingSchedule(notYetAired: false, perPage: 1) {
                nodes {
                  airingAt
                  episode
                }
              }
              startDate { year month day }
              endDate { year month day }
            }
          }
        }
        """

        try:
            response = self.session.post(
                ANILIST_URL,
                json={"query": graphql, "variables": {"search": query}},
                timeout=15,
            )
            if response.status_code != 200:
                logger.debug(
                    "AniList returned HTTP %s for %s",
                    response.status_code,
                    anime_name,
    )
                                return None

            payload = response.json()
            media = (
                payload.get("data", {})
                .get("Page", {})
                .get("media", [])
            )

            if not media:
                return None

            normalized_query = self._normalize(query)

            def score(item):
                titles = []
                title_obj = item.get("title") or {}
                for key in ("userPreferred", "romaji", "english", "native"):
                    value = title_obj.get(key)
                    if value:
                        titles.append(value)

                best = 0
                for title in titles:
                    normalized_title = self._normalize(title)
                    if normalized_title == normalized_query:
                        best = max(best, 100)
                    elif normalized_query in normalized_title:
                        best = max(best, 80)
                    elif set(normalized_query.split()).issubset(
                        set(normalized_title.split())
                    ):
                        best = max(best, 70)

                # Strongly prefer non-adult results for normal anime queries.
                if item.get("isAdult"):
                    best -= 100

                return best

            selected = max(media, key=score)
            if score(selected) < 70:
                return None

            title_obj = selected.get("title") or {}
            name = (
                title_obj.get("userPreferred")
                or title_obj.get("english")
                or title_obj.get("romaji")
                or query
            )

            studios = []
            for node in (selected.get("studios") or {}).get("nodes", []):
                if isinstance(node, dict) and node.get("name"):
                    studios.append(node["name"])

            studio = " • ".join(dict.fromkeys(studios)) if studios else None

            episodes = selected.get("episodes")
            next_air = selected.get("nextAiringEpisode") or {}
            next_episode = next_air.get("episode")
            next_timestamp = next_air.get("airingAt")

            next_date = None
            if next_timestamp:
                try:
                    next_date = datetime.fromtimestamp(
                        int(next_timestamp),
                        tz=timezone.utc,
                    ).isoformat()
                except Exception:
                    next_date = None

            airing = bool(next_air)
            status = selected.get("status")
            if status == "FINISHED":
                airing = False

            aired_count = None
            last_episode = None
            last_episode_date = None

            # AniList's airingSchedule can provide an already-aired episode
            # when available.  Keep this best-effort because the endpoint can
            # change independently of the rest of the metadata.
            try:
                nodes = (
                    (selected.get("airingSchedule") or {}).get("nodes")
                    or []
                )
                if nodes:
                    latest = max(
                        nodes,
                        key=lambda item: int(item.get("airingAt") or 0),
                    )
                    last_episode = latest.get("episode")
                    if latest.get("airingAt"):
                        last_episode_date = datetime.fromtimestamp(
                            int(latest["airingAt"]),
                            tz=timezone.utc,
                        ).isoformat()
                    aired_count = last_episode
            except Exception:
                pass

            if not airing and episodes is not None:
                aired_count = episodes
                last_episode = episodes

            broadcast = None

            return {
                "name": name,
                "poster_url": (
                    (selected.get("coverImage") or {}).get("extraLarge")
                    or (selected.get("coverImage") or {}).get("large")
                ),
                "studio": studio,
                "mal_url": None,
                "episodes": str(episodes) if episodes is not None else None,
                "total_episodes": (
                    int(episodes) if episodes is not None else None
                ),
                "aired_episodes": (
                    int(aired_count) if aired_count is not None else None
                ),
                "last_episode": (
                    str(last_episode) if last_episode is not None else None
                ),
                "last_episode_date": last_episode_date,
                "next_episode": (
                    str(next_episode) if next_episode is not None else None
                ),
                "next_episode_date": next_date,
                "status": status,
                "airing": airing,
                "broadcast": broadcast,
            }

        except Exception as exc:
            logger.debug("AniList lookup failed for %s: %s", anime_name, exc)
            return None

    # =================================================================
    # JIKAN / MAL
    # =================================================================

    def _get_mal_info(
        self,
        anime_name: str,
    ) -> Optional[Dict]:
        """
        Get authoritative anime metadata from Jikan/MAL.

        Jikan is used for:
        - title / MAL URL
        - poster
        - studio
        - status
        - total episode count
        - broadcast schedule
        - aired episode count
        - latest aired episode/date
        - expected next episode/date

        Important: Jikan's broadcast schedule is an EXPECTED schedule.
        A delayed/cancelled episode can therefore differ from it.
        """

        # Jikan can temporarily return 429/5xx or fail from a transient
        # network error. Never treat that as "anime not found" on the
        # first attempt. Try the query a few times before giving up.
        data = []
        last_error = None
        for attempt in range(3):
            try:
                response = self.session.get(
                    JIKAN_URL,
                    params={
                        "q": anime_name,
                        "limit": 15,
                        "sfw": "true",
                    },
                    timeout=15,
                )
                if response.status_code == 200:
                    data = response.json().get("data", [])
                    if data:
                        break
                else:
                    last_error = f"HTTP {response.status_code}"
            except Exception as exc:
                last_error = str(exc)

            if attempt < 2:
                import time
                time.sleep(0.8 * (attempt + 1))

        if not data:
            logger.warning(
                "Jikan returned no data for %s after retries: %s",
                anime_name,
                last_error,
            )
            return None

        query = self._normalize(anime_name)
        selected = None

        # Exact title first.
        for anime in data:
            if any(
                self._normalize(title) == query
                for title in self._get_titles(anime)
            ):
                selected = anime
                break

        # Then partial/alternative title.
        if selected is None:
            for anime in data:
                if any(
                    self._title_matches(title, query)
                    for title in self._get_titles(anime)
                ):
                    selected = anime
                    break

        if selected is None:
            # Avoid returning an unrelated MAL result.
            return None

            mal_id = selected.get("mal_id")
            if not mal_id:
                return None

            # Fetch /full because the search result can omit broadcast,
            # aired dates and other useful fields.
            full = selected
            try:
                full_response = self.session.get(
                    f"{JIKAN_API_BASE}/anime/{mal_id}/full",
                    timeout=10,
                )
                if full_response.ok:
                    full = full_response.json().get("data") or selected
            except Exception as exc:
                logger.debug("Jikan full lookup failed: %s", exc)

            jpg = (
                full.get("images", {})
                .get("jpg", {})
            )

            poster = (
                jpg.get("large_image_url")
                or jpg.get("image_url")
                or jpg.get("small_image_url")
            )

            studios = []
            for item in full.get("studios", []):
                if isinstance(item, dict) and item.get("name"):
                    studios.append(item["name"])

            studio = (
                " • ".join(dict.fromkeys(studios))
                if studios
                else None
            )

            total_episodes = full.get("episodes")
            status = full.get("status")
            airing = bool(full.get("airing"))

            aired_count = None
            last_episode = None
            last_episode_date = None

            # Jikan's episode endpoint gives the number of episodes that
            # have actually aired and the latest episode/date. The
            # pagination total is used instead of assuming total episodes
            # have already released.
            try:
                ep_response = self.session.get(
                    f"{JIKAN_API_BASE}/anime/{mal_id}/episodes",
                    params={"page": 1},
                    timeout=10,
                )

                if ep_response.ok:
                    ep_data = ep_response.json()
                    pagination = ep_data.get("pagination", {})
                    items = ep_data.get("data", [])

                    total_items = (
                        pagination.get("items", {})
                        if isinstance(pagination.get("items"), dict)
                        else {}
                    ).get("total")

                    if total_items is not None:
                        aired_count = int(total_items)

                    dated_items = [
                        item for item in items
                        if item.get("aired")
                    ]

                    if dated_items:
                        latest = max(
                            dated_items,
                            key=lambda item: item.get("aired") or ""
                        )
                        last_episode = latest.get("mal_id")
                        last_episode_date = latest.get("aired")
            except Exception as exc:
                logger.debug(
                    "Jikan episode lookup failed for %s: %s",
                    anime_name,
                    exc
                )

            # Completed anime: all episodes are already released.
            if not airing and total_episodes is not None:
                aired_count = int(total_episodes)

            broadcast = full.get("broadcast") or {}
            broadcast_text = broadcast.get("string")
            next_episode_date = None
            next_episode = None

            # For an airing series, calculate the next EXPECTED broadcast
            # from MAL/Jikan's weekday/time/timezone. We never present this
            # as a guaranteed release when the source only provides a
            # schedule.
            if airing:
                next_dt = self._next_broadcast_datetime(
                    broadcast
                )
                if next_dt:
                    next_episode_date = next_dt.isoformat()
                    if aired_count is not None:
                        next_episode = aired_count + 1

            # Fallback for a currently airing show where Jikan's episode
            # list is temporarily unavailable.
            if airing and next_episode is None and aired_count is not None:
                next_episode = aired_count + 1

            return {
                "name": (
                    full.get("title")
                    or full.get("title_english")
                    or anime_name
                ),
                "poster_url": poster,
                "studio": studio,
                "mal_url": full.get("url"),
                "mal_id": mal_id,

                "episodes": (
                    str(total_episodes)
                    if total_episodes is not None
                    else None
                ),
                "total_episodes": (
                    int(total_episodes)
                    if total_episodes is not None
                    else None
                ),
                "aired_episodes": aired_count,
                "last_episode": (
                    str(last_episode)
                    if last_episode is not None
                    else None
                ),
                "last_episode_date": (
                    last_episode_date
                ),
                "next_episode": (
                    str(next_episode)
                    if next_episode is not None
                    else None
                ),
                "next_episode_date": next_episode_date,

                "status": status,
                "airing": airing,
                "broadcast": broadcast_text,

                "aired_from": (
                    (full.get("aired") or {}).get("from")
                ),
                "aired_to": (
                    (full.get("aired") or {}).get("to")
                ),
            }

    @staticmethod
    def _next_broadcast_datetime(
        broadcast: Dict,
    ) -> Optional[datetime]:
        """Return the next expected broadcast datetime in UTC."""

        if not isinstance(broadcast, dict):
            return None

        day = broadcast.get("day")
        time_text = broadcast.get("time")
        timezone_name = broadcast.get("timezone") or "Asia/Tokyo"

        if not day or not time_text:
            return None

        weekdays = {
            "Monday": 0,
            "Tuesday": 1,
            "Wednesday": 2,
            "Thursday": 3,
            "Friday": 4,
            "Saturday": 5,
            "Sunday": 6,
        }

        target_weekday = weekdays.get(str(day))
        if target_weekday is None:
            return None

        # The datetime module's zoneinfo is part of Python 3.9+.
        try:
            from zoneinfo import ZoneInfo
            source_tz = ZoneInfo(timezone_name)
        except Exception:
            source_tz = timezone.utc

        try:
            hour, minute = [
                int(part)
                for part in str(time_text).split(":")[:2]
            ]
        except Exception:
            return None

        now = datetime.now(source_tz)
        days_ahead = (target_weekday - now.weekday()) % 7

        candidate = (
            now.replace(
                hour=hour,
                minute=minute,
                second=0,
                microsecond=0,
            )
            + timedelta(days=days_ahead)
        )

        # If today's scheduled time has already passed, use next week.
        if candidate <= now:
            candidate += timedelta(days=7)

        return candidate.astimezone(timezone.utc)

    # =================================================================
    # TITLE HELPERS
    # =================================================================

    @staticmethod
    def _get_titles(
        anime: Dict,
    ) -> List[str]:

        titles = []

        for key in (
            "title",
            "title_english",
            "title_japanese",
                   ):

            value = anime.get(
                key
            )

            if value:
                titles.append(
                    value
                )

        for item in anime.get(
            "titles",
            []
        ):

            if isinstance(
                item,
                dict
            ):

                value = item.get(
                    "title"
                )

                if value:
                    titles.append(
                        value
                    )

        return list(
            dict.fromkeys(
                titles
            )
        )

    @staticmethod
    def _pick_title(
        soup: BeautifulSoup,
        fallback: str,
    ) -> str:

        h1 = soup.find(
            "h1"
        )

        if h1:

            value = h1.get_text(
                " ",
                strip=True
            )

            if value:
                return value

        og_title = soup.find(
                        "meta",
            attrs={
                "property": "og:title"
            }
        )

        if og_title:

            value = og_title.get(
                "content"
                )
       
            if value:
                return value

        title_tag = soup.find(
            "title"
        )

        if title_tag:

            value = title_tag.get_text(
                " ",
                strip=True
            )

            if value:
                return value

        return fallback

    @staticmethod
    def _extract_og_image(
        soup: BeautifulSoup,
    ) -> Optional[str]:

        tag = soup.find(
            "meta",
            attrs={
                "property": "og:image"
            }
        )

        if tag:

            value = tag.get(
                "content"
            )

            if value:
                return value.strip()

        return None

    # =================================================================
    # NORMALIZATION / MATCHING
    # =================================================================

    @staticmethod
    def _normalize(
        text: str,
    ) -> str:

        text = (
            text or ""
        ).lower()

        text = text.replace(
            "-",
            " "
        )

        text = re.sub(
            r"[^a-z0-9 ]+",
            " ",
            text
        )

        return " ".join(
            text.split()
        )

    @staticmethod
    def _title_matches(
        title: str,
        query: str,
    ) -> bool:

        a = AnimeScraper._normalize(
            title
        )

        b = AnimeScraper._normalize(
            query
        )

        if not b:
            return False

        if a == b:
            return True

        if b in a:
            return True

        return set(
            b.split()
        ).issubset(
            set(
                a.split()
            )
        )

    @staticmethod
    def _query_present(
        anime_name: str,
        text: str,
    ) -> bool:

        query = AnimeScraper._normalize(
            anime_name
        )

        normalized = AnimeScraper._normalize(
            text
        )

        return (
            query in normalized
            or set(
                query.split()
            ).issubset(
                set(
                    normalized.split()
                )
            )
        )

    @staticmethod
    def _is_generic_title(
        title: Optional[str],
    ) -> bool:

        if not title:
            return True

        value = title.strip().lower()

        return (
            value.startswith(
                "search results for:"
            )
            or value.startswith(
                "results for:"
            )
            or value in {
                "search",
                "anime",
                "anime schedule",
                "search results",
            }
        )

    @staticmethod
    def _clean_title(
        title: str,
    ) -> str:

        title = re.sub(
            r"^search\s+results?\s+for:\s*",
            "",
            title or "",
            flags=re.I
        )

        title = re.sub(
            r"\s+[-|–]\s+AnimeDubHindi.*$",
            "",
            title,
            flags=re.I
        )

        title = re.sub(
            r"\s+Hindi\s+Dub.*$",
            "",
            title,
            flags=re.I
        )

        return title.strip()      
        
    # =================================================================
    # STUDIO
    # =================================================================

    @staticmethod
    def _extract_studio(
        text: str,
    ) -> Optional[str]:

        patterns = [
            r"(?:animation\s+)?studio\s*[:\-]\s*([^|\n]{1,120})",
            r"production\s+studio\s*[:\-]\s*([^|\n]{1,120})",
            r"produced\s+by\s*[:\-]\s*([^|\n]{1,120})",
        ]

        for pattern in patterns:
            match = re.search(
                pattern,
                text or "",
                re.I
            )

            if match:
                value = re.sub(
                    r"\s+",
                    " ",
                    match.group(1)
                ).strip(" :-|•,")

                if value and len(value) <= 120:
                    return value

        return None

    # =================================================================
    # DUB BY
    # =================================================================

    @staticmethod
    def _extract_dub_by(
        text: str,
    ) -> Optional[str]:

        patterns = [
            (
                r"official\s+dub(?:bed)?\s+by"
                r"\s*[:\-]?\s*"
                r"(.{1,120}?)"
                r"(?=\s+(?:encoder|quality|"
                r"subtitle|audio|genres|total|"
                r"episode|$))"
            ),
            (
                r"dub(?:bed)?\s+by"
                r"\s*[:\-]?\s*"
                r"(.{1,120}?)"
                r"(?=\s+(?:encoder|quality|"
                r"subtitle|audio|genres|total|"
                r"episode|$))"
            ),
            (
                r"dubbing\s+(?:studio|by)"
                r"\s*[:\-]?\s*"
                r"(.{1,120}?)"
                r"(?=\s+(?:encoder|quality|"
                r"subtitle|audio|genres|total|"
                r"episode|$))"
            ),
        ]

        for pattern in patterns:

            match = re.search(
                pattern,
                text or "",
                re.I
            )

            if match:

                value = re.sub(
                    r"\s+",
                    " ",
                    match.group(1)
                ).strip(
                    " :-|•,"
                )

                if (
                    value
                    and len(value) <= 120
                ):
                    return value

        return None


    # =================================================================
    # SEASON
    # =================================================================

    @staticmethod
    def _season_numbers(
        text: str
    ) -> List[str]:

        values = set()

        for value in re.findall(
            r"\bSeason\s*([0-9]{1,3})\b",
            text or "",
            re.I
        ):

            values.add(
                int(value)
            )

        for value in re.findall(
            r"\bS\s*([0-9]{1,3})\b",
            text or "",
            re.I
        ):

            values.add(
                int(value)
            )

        return [
            str(value)
            for value in sorted(
                values
            )
        ]

    @staticmethod
    def _extract_season(
        text: str
    ) -> Optional[str]:

        seasons = (
            AnimeScraper._season_numbers(
                text
            )
        )

        if not seasons:
            return None

        if len(seasons) > 5:
            return (
                f"{len(seasons)} Seasons"
            )

        return (
            "Season "
            + ", ".join(
                seasons
            )
        )


    # =================================================================
    # EPISODE
    # =================================================================

    @staticmethod
    def _extract_episode(
        text: str
    ) -> Optional[str]:

        patterns = [
            r"\bEP\s*([0-9]+(?:-[0-9]+)?)\b",
            r"\bEpisode\s*([0-9]+(?:-[0-9]+)?)\b",
            r"\bEp\.\s*([0-9]+(?:-[0-9]+)?)\b",
        ]

        for pattern in patterns:

            match = re.search(
                pattern,
                text or "",
                re.I
            )

            if match:
                return match.group(1)

        return None

    # =================================================================
    # LANGUAGES
    # =================================================================

    @staticmethod
    def _extract_verified_audio_languages(
        text: str
    ) -> List[str]:
        """Extract languages only when they appear near audio/dub labels."""
        value = text or ""
        found = []

        # Keep a reasonably tight context window around explicit audio/dub
        # wording so unrelated page text does not create fake languages.
        contexts = []
        for match in re.finditer(
            r'(?:audio|audios|language|languages|dub|dubbed|dubbing|"audio")',
            value,
            re.I,
        ):
            start = max(0, match.start() - 180)
            end = min(len(value), match.end() + 260)
            contexts.append(value[start:end])

        context_text = " ".join(contexts)
        for language in LANGUAGES:
            if re.search(rf"\b{re.escape(language)}\b", context_text, re.I):
                found.append(language)

        return AnimeScraper._ordered_languages(found)

    @staticmethod
    def _extract_languages(
        text: str
    ) -> List[str]:

        found = []

        for language in LANGUAGES:

            if re.search(
                    rf"\b{re.escape(language)}\b",
                text or "",
                re.I
            ):

                found.append(
                    language
                )

        return list(
            dict.fromkeys(
                found
            )
        )

    @staticmethod
    def _ordered_languages(
        languages: List[str]
    ) -> List[str]:

        order = [
            "Hindi",
            "English",
            "Tamil",
            "Telugu",
            "Japanese",
            "Korean",
            "Chinese",
            "Malayalam",
            "Kannada",
            "Marathi",
            "Bengali",
            "Bangla",
            ]
            
        return [
            language
            for language in order
            if language in languages
        ]

    @staticmethod
    def _languages_string(
        languages: List[str]
    ) -> Optional[str]:

        ordered = (
            AnimeScraper._ordered_languages(
                languages
            )
        )

        return (
            " • ".join(
                ordered
            )
            if ordered
            else None
    )

    # =================================================================
    # SCHEDULE
    # =================================================================

    @staticmethod
    def _extract_schedule(
        text: str
    ) -> Optional[str]:

        days = [
            "Monday",
            "Tuesday",
            "Wednesday",
            "Thursday",
            "Friday",
            "Saturday",
            "Sunday",
            "Daily",
        ]

        for day in days:

            match = re.search(
                rf"\b{day}\b.*?"
                rf"([0-9]{{1,2}}:"
                rf"[0-9]{{2}}\s*[AP]M)",
                text or "",
                re.I
            )

            if match:

                return (
                    f"{day} "
                    f"{match.group(1)}"
                )

        return None

    # =================================================================
    # DATE
    # =================================================================

    @staticmethod
    def _extract_date(
        text: str
    ) -> Optional[str]:

        patterns = [
            (
                r"\b\d{1,2}\s+"
                r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|"
                r"Sep|Oct|Nov|Dec)"
                r"\s+\d{4}\b"
            ),
            (
                r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|"
                r"Sep|Oct|Nov|Dec)"
                r"\s+\d{1,2},\s+\d{4}\b"
            ),
            r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b",
        ]

        for pattern in patterns:

            match = re.search(
                pattern,
                text or "",
                re.I
            )

            if match:
                return match.group(0)

        return None

    # =================================================================
    # PLATFORM SUMMARY
    # =================================================================

    @staticmethod
    def _dedupe_platform_entries(
        entries: List[Dict]
    ) -> List[Dict]:

        output = []
        seen = set()

        for entry in entries:

            key = (
                entry.get(
                    "platform"
                ),
                entry.get(
                    "channel"
                ),
                tuple(
                    entry.get(
                        "seasons",
                        []
                    )
                ),
                tuple(
                    entry.get(
                        "languages",
                        []
                    )
                ),
            )

            if key in seen:
                continue

            seen.add(key)

            output.append(
                entry
            )

        return output

    @staticmethod
    def _platform_summary(
        entries: List[Dict]
    ) -> Optional[str]:

        if not entries:
            return None

        grouped = {}

        for entry in entries:

            platform = entry.get(
                "platform"
            )

            if not platform:
                continue

            label = platform

            if entry.get(
                "channel"
            ):
                label = (
                    f"{platform} "
                    f"({entry['channel']})"
                )

            if label not in grouped:

                grouped[label] = {
                    "seasons": set(),
                    "languages": set(),
                }

            for season in entry.get(
                "seasons",
                []
            ):
                grouped[label]["seasons"].add(
                    str(season)
                )

            for language in entry.get(
                "languages",
                []
            ):
                grouped[label]["languages"].add(
                    str(language)
                )

        lines = []

        def sort_key(item):

            label, data = item

            return (
                0
                if "Hindi"
                in data["languages"]
                else 1,
                label.lower(),
            )

        for label, data in sorted(
            grouped.items(),
            key=sort_key
        ):

            seasons = sorted(
                {
                    int(value)
                    for value
                    in data["seasons"]
                    if value.isdigit()
                }
            )

            if len(seasons) > 5:

                season_text = (
                    f"{len(seasons)} Seasons"
                )

            elif seasons:

                season_text = (
                    "Season "
                    + ", ".join(
                        str(value)
                        for value in seasons
                    )
                )

            else:

                season_text = (
                    "All Seasons"
                )

            languages = (
                AnimeScraper._ordered_languages(
                    list(
                        data["languages"]
                    )
                )
            )

            language_text = (
                " • ".join(
                    languages
                )
                if languages
                else "Verified"
            )

            lines.append(
                f"• {label} — "
                f"{season_text} — "
                f"{language_text}"
            )

        return (
            "\n".join(lines)
            if lines
            else None
            )

    # =================================================================
    # RESULT NORMALIZATION FOR BOT/UI
    # =================================================================

    @staticmethod
    def _status_label(result: Dict) -> str:
        if result.get("airing") is True:
            return "🔴 Ongoing"
        status = str(result.get("status") or "").lower()
        if "finished" in status or "complete" in status:
            return "✅ Completed"
        return "ℹ️ " + (result.get("status") or "Unknown")

    @staticmethod
    def _episode_display(result: Dict) -> Optional[str]:
        total = result.get("total_episodes")
        aired = result.get("aired_episodes")

        if result.get("airing") is True:
            if aired is not None and total is not None:
                return f"{aired} / {total}"
            if aired is not None:
                return f"{aired} released"
            if total is not None:
                return f"0 / {total}"
            return None

        if total is not None:
            return f"{total} / {total}"

        if aired is not None:
            return str(aired)

        return result.get("episodes")

    @staticmethod
    def format_bot_result(result: Dict) -> str:
        """
        Ready-to-send Telegram text. The scraper still returns the full
        dict, while this method keeps presentation separate from scraping.
        """

        if not result:
            return ""

        lines = [
            f"🎬 Anime: {result.get('name') or 'Unknown'}",
            "",
            "🇮🇳 Hindi Dub: "
            + str(result.get("hindi_dub") or "Not Verified"),
            "📺 Platform: "
            + str(result.get("platform") or "Not Verified"),
            "📀 Season: "
            + str(result.get("season") or "Not Mentioned"),
            "🎬 Episodes: "
            + str(
                AnimeScraper._episode_display(result)
                or "Not Available"
            ),
            "🌐 Languages: "
            + str(result.get("languages") or "Not Mentioned"),
            "",
            "📊 Status: "
            + AnimeScraper._status_label(result),
        ]

        if result.get("last_episode"):
            lines.append(
                "📅 Last Episode: "
                f"Episode {result['last_episode']}"
            )

        if result.get("last_episode_date"):
            date_value = AnimeScraper._format_iso_date(
                result["last_episode_date"]
            )
            if date_value:
                lines.append(
                    "🗓 Last Release: " + date_value
                )

        if result.get("airing") is True:
            if result.get("next_episode"):
                lines.append(
                    "⏭ Next Episode: "
                    f"Episode {result['next_episode']}"
                )

            if result.get("next_episode_date"):
                date_value = AnimeScraper._format_iso_date(
                    result["next_episode_date"]
                )
                if date_value:
                    lines.append(
                        "📅 Expected Release: " + date_value
                    )

            if result.get("broadcast"):
                lines.append(
                    "⏰ Schedule: "
                    + str(result["broadcast"])
                )

        if result.get("studio"):
            lines.append(
                "🏢 Studio: " + str(result["studio"])
            )

        if result.get("dub_by"):
            lines.append(
                "🎙 Dub By: " + str(result["dub_by"])
            )

        lines.extend([
            "",
            "🔎 Source: DC",
        ])

        return "\n".join(lines)

    @staticmethod
    def _format_iso_date(value: str) -> Optional[str]:
        try:
            dt = datetime.fromisoformat(
                str(value).replace("Z", "+00:00")
            )
            return dt.strftime("%d %b %Y, %I:%M %p UTC")
        except Exception:
            return str(value) if value else None


# =====================================================================
# SINGLE INSTANCE
# =====================================================================

anime_scraper = AnimeScraper()


# =====================================================================
# PUBLIC FUNCTION
# =====================================================================

def get_anime_info(
    anime_name: str,
) -> Optional[Dict]:
    """Public function used by commands.py."""

    return anime_scraper.search_anime(
        anime_name
    )


def format_anime_info(
    anime_name: str,
) -> Optional[str]:
    """Return ready-to-send bot text for an anime search."""

    result = get_anime_info(anime_name)

    if not result:
        return None

    return anime_scraper.format_bot_result(result)
    


