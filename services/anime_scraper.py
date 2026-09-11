from __future__ import annotations

import json
import re
import time
import unicodedata

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional
from urllib.parse import (
    quote,
    urljoin,
    urlparse,
    parse_qs,
)

import requests
from bs4 import BeautifulSoup


# ============================================================
# CONFIG
# ============================================================

BASE_URL = "https://www.rareanimes.mov"

SEARCH_URL = BASE_URL + "/?s={query}"

SOURCE_NAME = "DC"

REQUEST_TIMEOUT = 8
SEARCH_TIMEOUT = 5

MAX_SEARCH_RESULTS = 15
MAX_SEASON_PAGES = 8

DISPLAY_LANGUAGES = [
    "Hindi",
    "English",
    "Tamil",
    "Telugu",
    "Japanese",
]


# ============================================================
# KNOWN ALIASES
# ============================================================

ALIASES = {
    "naruto": [
        "naruto",
        "naruto season 1",
    ],
    "naruto shippuden": [
        "naruto shippuden",
        "naruto shipuden",
        "naruto shippuden series",
    ],
    "re zero": [
        "re zero",
        "re:zero",
        "rezero",
        "re zero starting life in another world",
    ],
    "solo leveling": [
        "solo leveling",
        "solo-leveling",
    ],
    "mushoku tensei": [
        "mushoku tensei",
        "jobless reincarnation",
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
# DATA MODEL
# ============================================================

@dataclass
class AnimeInfo:
    name: str = ""
    original_name: str = ""

    poster: Optional[str] = None

    hindi_dub: bool = False
    hindi_status_known: bool = False

    platform: List[str] = field(
        default_factory=list
    )

    season: Optional[int] = None
    seasons: List[int] = field(
        default_factory=list
    )

    episodes: Optional[int] = None
    total_episodes: Optional[int] = None

    languages: List[str] = field(
        default_factory=list
    )

    status: str = "Unknown"

    last_episode: Optional[int] = None
    last_release: Optional[str] = None

    next_episode: Optional[int] = None
    expected_release: Optional[str] = None
    schedule: Optional[str] = None

    studio: Optional[str] = None
    dub_by: Optional[str] = None

    source: str = SOURCE_NAME
    source_url: Optional[str] = None

    matched_title: Optional[str] = None
    confidence: float = 0.0

    season_pages: List[str] = field(
        default_factory=list
    )


# ============================================================
# HTTP SESSION
# ============================================================

session = requests.Session()

session.headers.update(
    {
        "User-Agent": (
            "Mozilla/5.0 "
            "(Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 "
            "(KHTML, like Gecko) "
            "Chrome/131.0 Safari/537.36"
        ),
        "Accept-Language": "en-US,en;q=0.9",
        "Accept": "text/html,application/xhtml+xml",
    }
)


# ============================================================
# TEXT HELPERS
# ============================================================

def clean_text(value: Any) -> str:
    if value is None:
        return ""

    value = str(value)

    value = re.sub(
        r"\s+",
        " ",
        value,
    )

    return value.strip()


def normalize_text(value: str) -> str:
    """
    Generic anime title normalization.

    Examples:
        Re Zero
        re:zero
        REZERO

    become comparable strings.
    """

    if not value:
        return ""

    value = unicodedata.normalize(
        "NFKD",
        value,
    )

    value = value.lower()

    value = value.replace(":", " ")
    value = value.replace("-", " ")
    value = value.replace("_", " ")
    value = value.replace("–", " ")
    value = value.replace("—", " ")
    value = value.replace(".", " ")

    value = re.sub(
        r"\bseason\s+\d+\b",
        " ",
        value,
        flags=re.IGNORECASE,
    )

    value = re.sub(
        r"\bpart\s+\d+\b",
        " ",
        value,
        flags=re.IGNORECASE,
    )

    value = re.sub(
        r"\b(hindi|dubbed|dub|episodes?|download|hd|subbed|"
        r"english|tamil|telugu|japanese)\b",
        " ",
        value,
        flags=re.IGNORECASE,
    )

    value = re.sub(
        r"[^a-z0-9]+",
        " ",
        value,
    )

    value = re.sub(
        r"\s+",
        " ",
        value,
    )

    return value.strip()


def compact_normalize(value: str) -> str:
    return normalize_text(value).replace(
        " ",
        "",
    )


def tokenize(value: str) -> List[str]:
    return [
        token
        for token in normalize_text(value).split()
        if token
    ]


def slug_normalize(value: str) -> str:
    """Normalize URL slug/title into comparable text."""

    if not value:
        return ""

    value = value.lower()

    value = re.sub(
        r"[^a-z0-9]+",
        " ",
        value,
    )

    value = re.sub(
        r"\b(season|part)\s*\d+\b",
        " ",
        value,
    )

    value = re.sub(
        r"\b(hindi|dubbed|dub|episodes?|download|hd|subbed|"
        r"english|tamil|telugu|japanese)\b",
        " ",
        value,
    )

    return re.sub(
        r"\s+",
        " ",
        value,
    ).strip()


def slug_from_url(url: str) -> str:
    try:
        path = urlparse(url).path.rstrip("/")

        slug = path.rsplit(
            "/",
            1,
        )[-1]

        return slug_normalize(slug)

    except Exception:
        return ""


def compact_tokens(value: str) -> List[str]:
    return [
        x
        for x in normalize_text(value).split()
        if x
    ]


# ============================================================
# FUZZY MATCHING
# ============================================================

def similarity(
    a: str,
    b: str,
) -> float:
    """Generic fuzzy matching for short/common anime names."""

    na = normalize_text(a)
    nb = normalize_text(b)

    if not na or not nb:
        return 0.0

    ca = compact_normalize(a)
    cb = compact_normalize(b)

    if na == nb:
        return 1.0

    if ca == cb:
        return 0.99

    seq = SequenceMatcher(
        None,
        na,
        nb,
    ).ratio()

    ta = set(
        compact_tokens(a)
    )

    tb = set(
        compact_tokens(b)
    )

    if ta and tb:
        overlap = (
            len(ta & tb)
            / max(
                len(ta),
                len(tb),
            )
        )
    else:
        overlap = 0.0

    containment = 0.0

    if na in nb or nb in na:
        shorter = min(
            len(na),
            len(nb),
        )

        longer = max(
            len(na),
            len(nb),
        )

        if longer:
            containment = (
                shorter / longer
            )

    compact_seq = SequenceMatcher(
        None,
        ca,
        cb,
    ).ratio()

    return max(
        seq * 0.55
        + overlap * 0.25
        + compact_seq * 0.20,

        containment * 0.94,

        compact_seq * 0.88,
    )


def query_variants(
    query: str,
) -> List[str]:
    """
    Generate several search variants.

    Works for arbitrary anime names.
    """

    query = clean_text(query)

    variants: List[str] = []

    def add(value: str) -> None:
        value = clean_text(value)

        if value and value not in variants:
            variants.append(value)

    add(query)

    normalized = normalize_text(
        query
    )

    add(normalized)

    compact = compact_normalize(
        query
    )

    add(compact)

    # Generic punctuation variants.
    add(
        query.replace(
            ":",
            " ",
        )
    )

    add(
        query.replace(
            "-",
            " ",
        )
    )

    # Known aliases are an additional boost,
    # not the main matching system.
    qnorm = normalize_text(query)

    for canonical, aliases in ALIASES.items():

        all_names = [
            canonical
        ] + aliases

        for alias in all_names:

            if (
                qnorm
                == normalize_text(alias)
                or compact
                == compact_normalize(alias)
            ):
                for item in all_names:
                    add(item)

    return variants[:10]


def canonical_alias(
    query: str,
) -> Optional[str]:
    """Return canonical alias if known."""

    q = normalize_text(query)

    best = None
    best_score = 0.0

    for canonical, aliases in ALIASES.items():

        candidates = [
            canonical
        ] + aliases

        for candidate in candidates:

            score = similarity(
                q,
                candidate,
            )

            if score > best_score:
                best_score = score
                best = canonical

    if best_score >= 0.78:
        return best

    return None


# ============================================================
# HTTP
# ============================================================

def fetch(
    url: str,
    timeout: int = REQUEST_TIMEOUT,
) -> Optional[str]:
    """
    Fetch HTML safely.
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

        if (
            "text/html"
            not in content_type
        ):
            return None

        return response.text

    except requests.RequestException:
        return None

    except Exception:
        return None


# ============================================================
# URL VALIDATION
# ============================================================

def is_our_domain(
    url: str,
) -> bool:

    try:

        host = urlparse(
            url
        ).netloc.lower()

        return (
            host == "rareanimes.mov"
            or host.endswith(
                ".rareanimes.mov"
            )
        )

    except Exception:
        return False


def safe_page_url(
    url: str,
) -> Optional[str]:
    """
    Only allow RareAnimes pages.
    """

    if not url:
        return None

    url = url.strip()

    if not url.startswith(
        (
            "http://",
            "https://",
        )
    ):
        url = urljoin(
            BASE_URL,
            url,
        )

    if not is_our_domain(
        url
    ):
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

    results: List[
        Dict[str, Any]
    ] = []

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

        href = anchor.get(
            "href"
        )

        if not href:
            continue

        href = urljoin(
            BASE_URL,
            href,
        )

        href = safe_page_url(
            href
        )

        if not href:
            continue

        title = clean_text(
            anchor.get_text(
                " ",
                strip=True,
            )
        )

        if not title:
            continue

        key = href.rstrip("/")

        if key in seen:
            continue

        seen.add(key)

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

        slug_score = similarity(
            query,
            slug_from_url(href),
        )

        score = max(
            score,
            slug_score * 0.97,
        )

        results.append(
            {
                "title": title,
                "url": href,
                "score": score,
            }
        )

    # Generic fallback for different WordPress themes.
    if not results:

        for anchor in soup.find_all(
            "a"
        ):

            href = anchor.get(
                "href"
            )

            if not href:
                continue

            href = urljoin(
                BASE_URL,
                href,
            )

            href = safe_page_url(
                href
            )

            if not href:
                continue

            title = clean_text(
                anchor.get_text(
                    " ",
                    strip=True,
                )
            )

            if not title:
                continue

            score = max(
                similarity(
                    query,
                    title,
                ),
                similarity(
                    query,
                    slug_from_url(
                        href
                    ),
                ) * 0.97,
            )

            if score < 0.24:
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
        key=lambda item: item.get(
            "score",
            0,
        ),
        reverse=True,
    )

    return results[
        :MAX_SEARCH_RESULTS
        ]
    def search_web_fallback(
    query: str,
) -> List[Dict[str, Any]]:
    """
    Last-resort discovery.

    Only RareAnimes URLs are accepted.
    """

    variants = query_variants(
        query
    )

    results: Dict[
        str,
        Dict[str, Any]
    ] = {}

    def worker(
        search_query: str,
    ):

        try:

            ddg_url = (
                "https://html.duckduckgo.com/html/?q="
                + quote(
                    "site:rareanimes.mov "
                    + search_query
                )
            )

            page = fetch(
                ddg_url,
                timeout=4,
            )

            if not page:
                return []

            soup = BeautifulSoup(
                page,
                "html.parser",
            )

            found = []

            for a in soup.select(
                "a.result__a, "
                "a[data-testid='result-title-a']"
            ):

                href = a.get(
                    "href"
                )

                title = clean_text(
                    a.get_text(
                        " ",
                        strip=True,
                    )
                )

                if not href or not title:
                    continue

                if "uddg=" in href:

                    parsed = parse_qs(
                        urlparse(
                            href
                        ).query
                    )

                    href = parsed.get(
                        "uddg",
                        [href],
                    )[0]

                href = safe_page_url(
                    href
                )

                if not href:
                    continue

                score = max(
                    similarity(
                        search_query,
                        title,
                    ),
                    similarity(
                        search_query,
                        slug_from_url(
                            href
                        ),
                    ) * 0.98,
                )

                found.append(
                    {
                        "title": title,
                        "url": href,
                        "score": score,
                    }
                )

            return found

        except Exception:
            return []

    with ThreadPoolExecutor(
        max_workers=min(
            3,
            len(variants),
        )
    ) as executor:

        futures = [
            executor.submit(
                worker,
                x,
            )
            for x in variants
        ]

        for future in as_completed(
            futures
        ):

            try:

                for item in future.result():

                    key = item[
                        "url"
                    ].rstrip("/")

                    old = results.get(
                        key
                    )

                    if (
                        old is None
                        or item["score"]
                        > old.get(
                            "score",
                            0,
                        )
                    ):
                        results[key] = item

            except Exception:
                continue

    return sorted(
        results.values(),
        key=lambda x: x.get(
            "score",
            0,
        ),
        reverse=True,
    )[:MAX_SEARCH_RESULTS]


def search_rareanimes(
    query: str,
) -> List[Dict[str, Any]]:
    """
    Search RareAnimes using several variants.
    """

    variants = query_variants(
        query
    )

    all_results: Dict[
        str,
        Dict[str, Any]
    ] = {}

    def worker(
        search_query: str,
    ):

        url = SEARCH_URL.format(
            query=quote(
                search_query
            )
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
        max_workers=min(
            5,
            max(
                1,
                len(variants),
            ),
        )
    ) as executor:

        futures = [
            executor.submit(
                worker,
                variant,
            )
            for variant in variants
        ]

        for future in as_completed(
            futures
        ):

            try:
                items = future.result()

            except Exception:
                continue

            for item in items:

                url = item[
                    "url"
                ]

                old = all_results.get(
                    url
                )

                if old is None:

                    all_results[
                        url
                    ] = item

                elif (
                    item["score"]
                    > old["score"]
                ):

                    all_results[
                        url
                    ] = item

    final = list(
        all_results.values()
    )

    final.sort(
        key=lambda x: x.get(
            "score",
            0,
        ),
        reverse=True,
    )

    # If RareAnimes search misses a short/common name,
    # use search-engine indexing to discover the RareAnimes page.
    if (
        not final
        or final[0].get(
            "score",
            0,
        ) < 0.32
    ):

        fallback_results = (
            search_web_fallback(
                query
            )
        )

        for item in fallback_results:

            url = item[
                "url"
            ]

            old = all_results.get(
                url
            )

            if (
                old is None
                or item["score"]
                > old.get(
                    "score",
                    0,
                )
            ):

                all_results[
                    url
                ] = item

        final = list(
            all_results.values()
        )

        final.sort(
            key=lambda x: x.get(
                "score",
                0,
            ),
            reverse=True,
        )

    return final[
        :MAX_SEARCH_RESULTS
    ]


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
            attrs={
                "name": name
            },
        )

    if (
        tag is None
        and prop
    ):

        tag = soup.find(
            "meta",
            attrs={
                "property": prop
            },
        )

    if tag is None:
        return None

    content = tag.get(
        "content"
    )

    return clean_text(
        content
    )


def extract_poster(
    soup: BeautifulSoup,
) -> Optional[str]:

    image = get_meta(
        soup,
        prop="og:image",
    )

    if image:
        return image

    image = get_meta(
        soup,
        name="twitter:image",
    )

    if image:
        return image

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
            or img.get(
                "data-lazy-src"
            )
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

    h1 = soup.find(
        "h1"
    )

    if h1:

        return clean_text(
            h1.get_text(
                " ",
                strip=True,
            )
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

    for tag in soup(
        [
            "script",
            "style",
            "noscript",
        ]
    ):
        tag.decompose()

    main = (
        soup.select_one(
            "article"
        )
        or soup.select_one(
            ".entry-content"
        )
        or soup.select_one(
            "main"
        )
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


def extract_season(
    text: str,
) -> Optional[int]:

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
                return int(
                    match.group(1)
                )

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

                if (
                    0
                    < number
                    <= 5000
                ):
                    numbers.append(
                        number
                    )

            except ValueError:
                continue

    return sorted(
        set(numbers)
    )


def extract_total_episodes(
    text: str,
) -> Optional[int]:

    patterns = [
        r"\b(\d{1,4})\s*episodes?\b",
        r"\btotal\s*episodes?\s*[:\-]?\s*(\d{1,4})\b",
        r"\bepisodes?\s*[:\-]?\s*(\d{1,4})\b",
    ]

    values = []

    for pattern in patterns:

        for match in re.finditer(
            pattern,
            text,
            re.IGNORECASE,
        ):

            try:

                value = int(
                    match.group(1)
                )

                if (
                    0
                    < value
                    <= 5000
                ):
                    values.append(
                        value
                    )

            except ValueError:
                continue

    if not values:
        return None

    return max(values)


def extract_languages(
    text: str,
) -> List[str]:

    found = []

    for language in DISPLAY_LANGUAGES:

        if re.search(
            rf"\b{re.escape(language)}\b",
            text,
            re.IGNORECASE,
        ):

            found.append(
                language
            )

    return found


# ============================================================
# PLATFORM DETECTION
# ============================================================

PLATFORM_PATTERNS = {
    "Crunchyroll": [
        r"\bcrunchyroll\b",
    ],
    "Netflix": [
        r"\bnetflix\b",
    ],
    "Sony YAY!": [
        r"\bsony\s*yay\b",
        r"\bsony\s*yay!\b",
    ],
    "SonyLIV": [
        r"\bsonyliv\b",
    ],
    "JioHotstar": [
        r"\bjiohotstar\b",
        r"\bhotstar\b",
    ],
    "Amazon MX Player": [
        r"\bmx\s*player\b",
        r"\bamazon\s*mx\s*player\b",
    ],
    "Prime Video": [
        r"\bprime\s*video\b",
    ],
    "Anime Times": [
        r"\banime\s*times\b",
    ],
    "ZEE5": [
        r"\bzee5\b",
    ],
    "Muse India": [
        r"\bmuse\s*india\b",
    ],
    "Ani-One India": [
        r"\bani[\-\s]?one\s*india\b",
        r"\banione\s*india\b",
    ],
}


def extract_platforms(
    text: str,
) -> List[str]:

    found = []

    for platform, patterns in (
        PLATFORM_PATTERNS.items()
    ):

        for pattern in patterns:

            if re.search(
                pattern,
                text,
                re.IGNORECASE,
            ):

                found.append(
                    platform
                )

                break

    return found

# ============================================================
# HINDI DUB DETECTION
# ============================================================

def detect_hindi_dub(
    text: str,
) -> tuple[bool, bool]:

    lower = text.lower()

    positive_patterns = [
        r"\bhindi\s*dub\b",
        r"\bhindi\s*dubbed\b",
        r"\bhindi\s*audio\b",
        r"\bhindi\s*language\b",
        r"\blanguage\s*[:\-]?\s*hindi\b",
        r"\bhindi\s*version\b",
    ]

    negative_patterns = [
        r"\bnot\s*available\s*in\s*hindi\b",
        r"\bhindi\s*not\s*available\b",
        r"\bno\s*hindi\s*dub\b",
    ]

    for pattern in negative_patterns:

        if re.search(
            pattern,
            lower,
            re.IGNORECASE,
        ):
            return False, True

    for pattern in positive_patterns:

        if re.search(
            pattern,
            lower,
            re.IGNORECASE,
        ):
            return True, True

    # A standalone Hindi language mention is weaker,
    # but useful on RareAnimes metadata pages.
    if re.search(
        r"\bhindi\b",
        lower,
        re.IGNORECASE,
    ):
        return True, True

    return False, False


# ============================================================
# CURRENT AIRING SCHEDULE FALLBACKS
# ============================================================

CURRENT_AIRING_SCHEDULES = {
    "black torch": {
        "total_episodes": 12,
        "start_date": "2026-07-04",
        "weekday": "Saturday",
        "schedule": "Every Saturday",
    },
}


def _schedule_key(
    value: str,
) -> str:

    return compact_normalize(
        value
    )


def apply_current_schedule(
    info: AnimeInfo,
) -> AnimeInfo:
    """
    Apply known current airing data.

    This prevents a Hindi upload count from being
    incorrectly treated as the total series episode count.
    """

    key = _schedule_key(
        info.name
    )

    data = CURRENT_AIRING_SCHEDULES.get(
        key
    )

    if not data:
        return info

    try:

        start = datetime.strptime(
            data["start_date"],
            "%Y-%m-%d",
        ).date()

        today = datetime.now().date()

        elapsed_days = (
            today - start
        ).days

        if elapsed_days < 0:
            current_episode = 0
        else:
            current_episode = (
                elapsed_days // 7
            ) + 1

        total = int(
            data[
                "total_episodes"
            ]
        )

        current_episode = max(
            1,
            min(
                current_episode,
                total,
            ),
        )

        last_date = (
            start
            + timedelta(
                weeks=current_episode - 1
            )
        )

        info.total_episodes = total
        info.last_episode = (
            current_episode
        )

        info.last_release = (
            last_date.strftime(
                "%d %B %Y"
            )
        )

        info.schedule = data[
            "schedule"
        ]

        if current_episode < total:

            info.status = "Ongoing"

            info.next_episode = (
                current_episode + 1
            )

            next_date = (
                last_date
                + timedelta(
                    weeks=1
                )
            )

            info.expected_release = (
                next_date.strftime(
                    "%d %B %Y"
                )
            )

        else:

            info.status = "Completed"
            info.next_episode = None
            info.expected_release = None

    except Exception:
        pass

    return info


# ============================================================
# STATUS DETECTION
# ============================================================

def detect_status(
    text: str,
    current_episode: Optional[int],
    total_episode: Optional[int],
) -> str:

    lower = text.lower()

    ongoing_patterns = [
        r"\bongoing\b",
        r"\bcurrently\s*airing\b",
        r"\bairing\b",
        r"\bstill\s*airing\b",
        r"\bweekly\b",
    ]

    completed_patterns = [
        r"\bcompleted\b",
        r"\bcomplete\b",
        r"\bfinished\b",
    ]

    for pattern in ongoing_patterns:

        if re.search(
            pattern,
            lower,
            re.IGNORECASE,
        ):
            return "Ongoing"

    for pattern in completed_patterns:

        if re.search(
            pattern,
            lower,
            re.IGNORECASE,
        ):
            return "Completed"

    if (
        current_episode is not None
        and total_episode is not None
    ):

        if current_episode < total_episode:
            return "Ongoing"

        if current_episode >= total_episode:
            return "Completed"

    return "Unknown"


# ============================================================
# DATE EXTRACTION
# ============================================================

MONTHS = (
    "January|February|March|April|May|June|"
    "July|August|September|October|November|December"
)


def extract_dates(
    text: str,
) -> List[str]:

    patterns = [
        rf"\b\d{{1,2}}\s+(?:{MONTHS})\s+\d{{4}}\b",
        rf"\b(?:{MONTHS})\s+\d{{1,2}},?\s+\d{{4}}\b",
        r"\b\d{1,2}[/-]\d{1,2}[/-]\d{4}\b",
    ]

    found = []

    for pattern in patterns:

        for match in re.finditer(
            pattern,
            text,
            re.IGNORECASE,
        ):

            value = clean_text(
                match.group(0)
            )

            if value not in found:
                found.append(
                    value
                )

    return found


def extract_last_episode(
    text: str,
) -> Optional[int]:

    patterns = [
        r"\blast\s+episode\s*[:\-]?\s*(\d{1,4})\b",
        r"\blatest\s+episode\s*[:\-]?\s*(\d{1,4})\b",
        r"\bepisode\s*(\d{1,4})\s*(?:released|aired)\b",
    ]

    for pattern in patterns:

        match = re.search(
            pattern,
            text,
            re.IGNORECASE,
        )

        if match:

            try:
                return int(
                    match.group(1)
                )
            except ValueError:
                pass

    numbers = extract_episode_numbers(
        text
    )

    if numbers:
        return max(
            numbers
        )

    return None


def extract_next_episode(
    text: str,
) -> Optional[int]:

    patterns = [
        r"\bnext\s+episode\s*[:\-]?\s*(\d{1,4})\b",
        r"\bupcoming\s+episode\s*[:\-]?\s*(\d{1,4})\b",
        r"\bepisode\s*(\d{1,4})\s*(?:next|upcoming)\b",
    ]

    for pattern in patterns:

        match = re.search(
            pattern,
            text,
            re.IGNORECASE,
        )

        if match:

            try:
                return int(
                    match.group(1)
                )
            except ValueError:
                pass

    return None


def extract_last_release(
    text: str,
) -> Optional[str]:

    patterns = [
        rf"(?:last|latest|released|release|aired|airdate)"
        rf"[^.{{0,100}}]{{0,100}}"
        rf"(\d{{1,2}}\s+(?:{MONTHS})\s+\d{{4}})",

        rf"(\d{{1,2}}\s+(?:{MONTHS})\s+\d{{4}})",
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
            )

    dates = extract_dates(
        text
    )

    if dates:
        return dates[-1]

    return None


def extract_expected_release(
    text: str,
) -> Optional[str]:

    patterns = [
        rf"(?:expected|next|upcoming)"
        rf"[^.{{0,100}}]{{0,100}}"
        rf"(\d{{1,2}}\s+(?:{MONTHS})\s+\d{{4}})",

        rf"(?:next\s+episode|episode\s+\d+)"
        rf"[^.{{0,100}}]{{0,100}}"
        rf"(\d{{1,2}}\s+(?:{MONTHS})\s+\d{{4}})",
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
            )

    return None


# ============================================================
# SCHEDULE
# ============================================================

def extract_schedule(
    text: str,
) -> Optional[str]:

    patterns = [
        (
            r"\bevery\s+"
            r"(monday|tuesday|wednesday|thursday|friday|"
            r"saturday|sunday)\b",
            lambda m:
                "Every "
                + m.group(1).capitalize(),
        ),

        (
            r"\bweekly\b",
            lambda m: "Weekly",
        ),

        (
            r"\b(monday|tuesday|wednesday|thursday|friday|"
            r"saturday|sunday)\s+release\b",
            lambda m:
                "Every "
                + m.group(1).capitalize(),
        ),
    ]

    for pattern, formatter in patterns:

        match = re.search(
            pattern,
            text,
            re.IGNORECASE,
        )

        if match:
            return formatter(
                match
            )

    return None


# ============================================================
# STUDIO / DUB BY
# ============================================================

def extract_studio(
    text: str,
) -> Optional[str]:

    patterns = [
        r"\bstudio\s*[:\-]\s*([^|•]+)",
        r"\bstudios?\s*[:\-]\s*([^|•]+)",
    ]

    for pattern in patterns:

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
                return value[:120]

    return None


def extract_dub_by(
    text: str,
) -> Optional[str]:

    patterns = [
        r"\bdub(?:bed)?\s*by\s*[:\-]?\s*([^|•]+)",
        r"\bdub\s*by\s*[:\-]\s*([^|•]+)",
    ]

    for pattern in patterns:

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
                return value[:120]

    return None


# ============================================================
# SEASON LINK DISCOVERY
# ============================================================

def discover_season_links(
    soup: BeautifulSoup,
) -> List[str]:

    links = []

    season_pattern = re.compile(
        r"\bseason\s*\d+\b"
        r"|\bs\d{1,2}\b",
        re.IGNORECASE,
    )

    for anchor in soup.find_all(
        "a"
    ):

        href = anchor.get(
            "href"
        )

        title = clean_text(
            anchor.get_text(
                " ",
                strip=True,
            )
        )

        if not href:
            continue

        href = urljoin(
            BASE_URL,
            href,
        )

        href = safe_page_url(
            href
        )

        if not href:
            continue

        if season_pattern.search(
            title
        ):

            if href not in links:
                links.append(
                    href
                )

    return links

# ============================================================
# ANIME PAGE PARSER
# ============================================================

def parse_anime_page(
    url: str,
    fallback_name: str = "",
) -> Optional[AnimeInfo]:

    url = safe_page_url(
        url
    )

    if not url:
        return None

    html = fetch(
        url,
        timeout=REQUEST_TIMEOUT,
    )

    if not html:
        return None

    soup = BeautifulSoup(
        html,
        "html.parser",
    )

    title = page_title(
        soup
    )

    if not title:
        title = fallback_name

    text = body_text(
        soup
    )

    if not text:
        return None

    season = extract_season(
        title + " " + text
    )

    episode_numbers = (
        extract_episode_numbers(
            text
        )
    )

    total_episodes = (
        extract_total_episodes(
            text
        )
    )

    if episode_numbers:

        current_episode = max(
            episode_numbers
        )

    else:

        current_episode = None

    languages = extract_languages(
        text
    )

    platforms = extract_platforms(
        text
    )

    hindi_dub, hindi_known = (
        detect_hindi_dub(
            title + " " + text
        )
    )

    status = detect_status(
        title + " " + text,
        current_episode,
        total_episodes,
    )

    last_episode = (
        extract_last_episode(
            text
        )
    )

    if last_episode is None:
        last_episode = current_episode

    last_release = (
        extract_last_release(
            text
        )
    )

    next_episode = (
        extract_next_episode(
            text
        )
    )

    expected_release = (
        extract_expected_release(
            text
        )
    )

    schedule = extract_schedule(
        text
    )

    studio = extract_studio(
        text
    )

    dub_by = extract_dub_by(
        text
    )

    poster = extract_poster(
        soup
    )

    season_pages = (
        discover_season_links(
            soup
        )
    )

    info = AnimeInfo(
        name=title,
        original_name=title,
        poster=poster,
        hindi_dub=hindi_dub,
        hindi_status_known=hindi_known,
        platform=platforms,
        season=season,
        seasons=(
            [season]
            if season is not None
            else []
        ),
        episodes=current_episode,
        total_episodes=total_episodes,
        languages=languages,
        status=status,
        last_episode=last_episode,
        last_release=last_release,
        next_episode=next_episode,
        expected_release=expected_release,
        schedule=schedule,
        studio=studio,
        dub_by=dub_by,
        source=SOURCE_NAME,
        source_url=url,
        matched_title=title,
        confidence=similarity(
            fallback_name,
            title,
        )
        if fallback_name
        else 1.0,
        season_pages=season_pages,
    )

    return info


# ============================================================
# BEST SEARCH RESULT
# ============================================================

def choose_best_result(
    query: str,
    results: List[
        Dict[str, Any]
    ],
) -> Optional[
    Dict[str, Any]
]:

    if not results:
        return None

    canonical = canonical_alias(
        query
    )

    best_item = None
    best_score = -1.0

    qlower = query.lower()

    for item in results:

        title = item.get(
            "title",
            "",
        )

        url = item.get(
            "url",
            "",
        )

        score = similarity(
            query,
            title,
        )

        # URL slug gets a strong weight for pages such as:
        # konosuba-season-1-hindi-dubbed...
        slug_score = similarity(
            query,
            slug_from_url(
                url
            ),
        )

        score = max(
            score,
            slug_score * 0.98,
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

        tlower = title.lower()

        # Don't select a movie when user asked for normal anime.
        if (
            "movie" in tlower
            and "movie" not in qlower
        ):
            score -= 0.12

        # Don't select obvious irrelevant content.
        bad_terms = [
            "request",
            "privacy policy",
            "contact us",
            "telegram",
            "advertisement",
        ]

        if any(
            term in tlower
            for term in bad_terms
        ):
            score -= 0.30

        if score > best_score:

            best_score = score

            best_item = dict(
                item
            )

            best_item[
                "score"
            ] = score

    if not best_item:
        return None

    # Exact/compact/containment matches are accepted.
    qnorm = normalize_text(
        query
    )

    qcompact = compact_normalize(
        query
    )

    tnorm = normalize_text(
        best_item.get(
            "title",
            "",
        )
    )

    tcompact = compact_normalize(
        best_item.get(
            "title",
            "",
        )
    )

    slug = compact_normalize(
        slug_from_url(
            best_item.get(
                "url",
                "",
            )
        )
    )

    exactish = (
        qnorm == tnorm
        or qcompact == tcompact
        or (
            qcompact
            and qcompact in tcompact
        )
        or (
            tcompact
            and tcompact in qcompact
        )
        or (
            qcompact
            and qcompact in slug
        )
    )

    # Short names can score lower because the page title
    # contains season/dub/upload metadata.
    if len(qcompact) <= 12:
        threshold = 0.30
    else:
        threshold = 0.36

    if (
        not exactish
        and best_item[
            "score"
        ] < threshold
    ):
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
                + [
                    season_info.season
                ]
            )
        )

    main_info.languages = sorted(
        set(
            main_info.languages
            + season_info.languages
        ),
        key=lambda x:
            DISPLAY_LANGUAGES.index(x)
            if x in DISPLAY_LANGUAGES
            else 99,
    )

    if season_info.hindi_status_known:
        main_info.hindi_status_known = True

    if season_info.hindi_dub:
        main_info.hindi_dub = True

    main_info.platform = sorted(
        set(
            main_info.platform
            + season_info.platform
        )
    )

    if not main_info.studio:
        main_info.studio = (
            season_info.studio
        )

    if not main_info.dub_by:
        main_info.dub_by = (
            season_info.dub_by
        )


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

    urls = urls[
        :MAX_SEASON_PAGES
    ]

    def worker(
        url: str,
    ):

        return parse_anime_page(
            url,
            info.name,
        )

    with ThreadPoolExecutor(
        max_workers=min(
            6,
            len(urls),
        )
    ) as executor:

        futures = [
            executor.submit(
                worker,
                url,
            )
            for url in urls
        ]

        for future in as_completed(
            futures
        ):

            try:

                season_info = (
                    future.result()
                )

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

    info.platform = list(
        dict.fromkeys(
            info.platform
        )
    )

    filtered_languages = []

    for language in DISPLAY_LANGUAGES:

        if language in info.languages:
            filtered_languages.append(
                language
            )

    info.languages = (
        filtered_languages
    )

    if info.hindi_dub:

        if (
            "Hindi"
            not in info.languages
        ):

            info.languages.insert(
                0,
                "Hindi",
            )

    if (
        info.episodes is not None
        and info.total_episodes is None
        and info.status == "Completed"
    ):

        info.total_episodes = (
            info.episodes
        )

    if info.last_episode is None:

        info.last_episode = (
            info.episodes
        )

    if info.status == "Ongoing":

        info.episodes = (
            info.last_episode
        )

    if (
        info.status == "Completed"
        and info.last_episode is not None
    ):

        info.episodes = (
            info.last_episode
        )

        if info.total_episodes is None:

            info.total_episodes = (
                info.last_episode
            )

    return info


# ============================================================
# CACHE
# ============================================================

_INFO_CACHE: Dict[
    str,
    tuple[
        float,
        AnimeInfo,
    ]
] = {}

_INFO_CACHE_TTL = 600


def _cache_key(
    query: str,
) -> str:

    return compact_normalize(
        query
    )


def _get_cached_info(
    query: str,
) -> Optional[AnimeInfo]:

    key = _cache_key(
        query
    )

    item = _INFO_CACHE.get(
        key
    )

    if not item:
        return None

    timestamp, info = item

    if (
        time.time()
        - timestamp
        > _INFO_CACHE_TTL
    ):

        _INFO_CACHE.pop(
            key,
            None,
        )

        return None

    return info


def _set_cached_info(
    query: str,
    info: AnimeInfo,
) -> None:

    key = _cache_key(
        query
    )

    _INFO_CACHE[
        key
    ] = (
        time.time(),
        info,
    )

# ============================================================
# MAIN API
# ============================================================

def get_anime_info(
    query: str,
    load_seasons: bool = False,
) -> Optional[AnimeInfo]:
    """
    Main anime lookup.

    Fast by default:
        load_seasons=False

    Use load_seasons=True only when full season
    aggregation is required.
    """

    query = clean_text(
        query
    )

    if not query:
        return None

    cached = _get_cached_info(
        query
    )

    if cached is not None:
        return cached

    # Search RareAnimes.
    results = search_rareanimes(
        query
    )

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

    # Keep the search result confidence.
    info.confidence = float(
        best.get(
            "score",
            info.confidence,
        )
    )

    info.matched_title = (
        best.get(
            "title"
        )
        or info.matched_title
    )

    # Prefer clean anime title when possible.
    # Remove common RareAnimes upload suffixes.
    display_name = clean_anime_title(
        info.name
    )

    if display_name:
        info.name = display_name

    # Load additional seasons only when explicitly requested.
    if load_seasons:
        info = load_additional_seasons(
            info
        )

    info = finalize_info(
        info
    )

    # Apply current schedule fallback after
    # normal parsing so it can override incorrect
    # upload-count based episode totals.
    info = apply_current_schedule(
        info
    )

    info = finalize_info(
        info
    )

    _set_cached_info(
        query,
        info,
    )

    return info


# ============================================================
# TITLE CLEANUP
# ============================================================

def clean_anime_title(
    title: str,
) -> str:

    if not title:
        return ""

    value = clean_text(
        title
    )

    # Remove common upload-page suffixes.
    patterns = [
        r"\s+Hindi\s+Dubbed\s+Episodes?.*$",
        r"\s+Hindi\s+Dubbed.*$",
        r"\s+Episodes?\s+Download.*$",
        r"\s+Download\s+HD.*$",
        r"\s+All\s+Episodes?.*$",
        r"\s+Season\s+\d+\s+Hindi.*$",
    ]

    for pattern in patterns:

        value = re.sub(
            pattern,
            "",
            value,
            flags=re.IGNORECASE,
        )

    return clean_text(
        value
    )


# ============================================================
# DICT API
# ============================================================

def get_anime_info_dict(
    query: str,
    load_seasons: bool = False,
) -> Optional[
    Dict[str, Any]
]:

    info = get_anime_info(
        query,
        load_seasons=load_seasons,
    )

    if info is None:
        return None

    data = asdict(
        info
    )

    return data


# ============================================================
# FORMATTER
# ============================================================

def format_anime_info(
    info: AnimeInfo,
) -> str:

    lines = []

    name = (
        info.name
        or info.original_name
        or "Unknown"
    )

    lines.append(
        f"🎬 Anime: {name}"
    )

    lines.append("")

    if info.hindi_status_known:

        if info.hindi_dub:
            hindi_text = (
                "🇮🇳 Hindi Dub: "
                "✅ Available"
            )
        else:
            hindi_text = (
                "🇮🇳 Hindi Dub: "
                "❌ Not Available"
            )

        lines.append(
            hindi_text
        )

    else:

        lines.append(
            "🇮🇳 Hindi Dub: "
            "❓ Unknown"
        )

    if info.platform:

        lines.append(
            "📺 Platform: "
            + " • ".join(
                info.platform
            )
        )

    if info.season is not None:

        lines.append(
            f"📀 Season: "
            f"{info.season}"
        )

    if (
        info.status == "Ongoing"
        and info.total_episodes is not None
        and info.last_episode is not None
    ):

        lines.append(
            "🎬 Episodes: "
            f"{info.last_episode} / "
            f"{info.total_episodes}"
        )

    elif info.total_episodes is not None:

        lines.append(
            "🎬 Episodes: "
            f"{info.total_episodes}"
        )

    elif info.episodes is not None:

        lines.append(
            "🎬 Episodes: "
            f"{info.episodes}"
        )

    if info.languages:

        lines.append("")

        lines.append(
            "🌐 Languages: "
            + " • ".join(
                info.languages
            )
        )

    lines.append("")

    if info.status == "Ongoing":

        status_text = (
            "📊 Status: 🔴 Ongoing"
        )

    elif info.status == "Completed":

        status_text = (
            "📊 Status: ✅ Completed"
        )

    else:

        status_text = (
            "📊 Status: ❓ Unknown"
        )

    lines.append(
        status_text
    )

    if info.last_episode is not None:

        lines.append("")

        lines.append(
            "📅 Last Episode: "
            f"Episode "
            f"{info.last_episode}"
        )

    if info.last_release:

        lines.append(
            "🗓 Last Release: "
            f"{info.last_release}"
        )

    if info.status == "Ongoing":

        if info.next_episode is not None:

            lines.append("")

            lines.append(
                "⏭ Next Episode: "
                f"Episode "
                f"{info.next_episode}"
            )

        if info.expected_release:

            lines.append(
                "📅 Expected Release: "
                f"{info.expected_release}"
            )

        if info.schedule:

            lines.append(
                "⏰ Schedule: "
                f"{info.schedule}"
            )

    if info.studio:

        lines.append("")

        lines.append(
            "🏢 Studio: "
            f"{info.studio}"
        )

    if info.dub_by:

        lines.append(
            "🎙 Dub By: "
            f"{info.dub_by}"
        )

    lines.append("")

    lines.append(
        "🔎 Source: "
        f"{SOURCE_NAME}"
    )

    return "\n".join(
        lines
    )


# ============================================================
# SIMPLE SEARCH API
# ============================================================

def search_anime(
    query: str,
    load_seasons: bool = False,
) -> Optional[str]:

    info = get_anime_info(
        query,
        load_seasons=load_seasons,
    )

    if info is None:

        return (
            "😕 Anime not found: "
            f"{clean_text(query)}\n\n"
            "Try:\n"
            "• Another spelling\n"
            "• English title\n"
            "• Short/common title\n"
            "• Add Movie if it is a movie"
        )

    return format_anime_info(
        info
    )


# ============================================================
# JSON API
# ============================================================

def anime_to_json(
    query: str,
    load_seasons: bool = False,
) -> str:

    data = get_anime_info_dict(
        query,
        load_seasons=load_seasons,
    )

    if data is None:

        return json.dumps(
            {
                "success": False,
                "query": query,
                "error": "Anime not found",
            },
            ensure_ascii=False,
            indent=2,
        )

    return json.dumps(
        {
            "success": True,
            "data": data,
        },
        ensure_ascii=False,
        indent=2,
    )


# ============================================================
# CLI
# ============================================================

if __name__ == "__main__":

    import sys

    query = " ".join(
        sys.argv[1:]
    ).strip()

    if not query:

        print(
            "Usage: "
            "python anime_scraper.py "
            "\"anime name\""
        )

        raise SystemExit(0)

    result = search_anime(
        query
    )

    print(
        result
    )
    # ============================================================
# PART 6/7 — EXTRA API + CACHE HELPERS
# ============================================================

def clear_anime_cache() -> None:
    """Clear the in-memory anime cache."""
    _INFO_CACHE.clear()


def get_cache_size() -> int:
    """Return number of cached anime queries."""
    return len(_INFO_CACHE)


def refresh_anime_info(
    query: str,
    load_seasons: bool = False,
) -> Optional[AnimeInfo]:
    """
    Force a fresh lookup by removing the old cached result.
    """

    key = _cache_key(query)

    _INFO_CACHE.pop(
        key,
        None,
    )

    return get_anime_info(
        query,
        load_seasons=load_seasons,
    )


# ============================================================
# SEASON DETAILS API
# ============================================================

def get_anime_seasons(
    query: str,
) -> List[int]:

    info = get_anime_info(
        query,
        load_seasons=True,
    )

    if not info:
        return []

    seasons = list(
        info.seasons
    )

    if (
        info.season is not None
        and info.season not in seasons
    ):
        seasons.append(
            info.season
        )

    return sorted(
        set(seasons)
    )


# ============================================================
# POSTER API
# ============================================================

def get_anime_poster(
    query: str,
) -> Optional[str]:

    info = get_anime_info(
        query,
        load_seasons=False,
    )

    if not info:
        return None

    return info.poster


# ============================================================
# NORMALIZED SEARCH NAME
# ============================================================

def get_matched_title(
    query: str,
) -> Optional[str]:

    info = get_anime_info(
        query,
        load_seasons=False,
    )

    if not info:
        return None

    return (
        info.name
        or info.original_name
        or info.matched_title
    )


# ============================================================
# SAFE SEARCH RESULT
# ============================================================

def is_valid_anime_info(
    info: Optional[AnimeInfo],
) -> bool:

    if not info:
        return False

    if not (
        info.name
        or info.original_name
        or info.matched_title
    ):
        return False

    if (
        info.source_url
        and not is_our_domain(
            info.source_url
        )
    ):
        return False

    return True


# ============================================================
# BULK SEARCH
# ============================================================

def search_multiple_anime(
    queries: List[str],
) -> Dict[
    str,
    Optional[AnimeInfo]
]:

    output = {}

    cleaned = []

    for query in queries:

        query = clean_text(
            query
        )

        if (
            query
            and query not in cleaned
        ):
            cleaned.append(
                query
            )

    if not cleaned:
        return output

    def worker(
        query: str,
    ):

        try:

            return (
                query,
                get_anime_info(
                    query
                ),
            )

        except Exception:

            return (
                query,
                None,
            )

    with ThreadPoolExecutor(
        max_workers=min(
            6,
            len(cleaned),
        )
    ) as executor:

        futures = [
            executor.submit(
                worker,
                query,
            )
            for query in cleaned
        ]

        for future in as_completed(
            futures
        ):

            try:

                query, info = (
                    future.result()
                )

                output[
                    query
                ] = info

            except Exception:
                continue

    return output


# ============================================================
# DEBUG INFORMATION
# ============================================================

def debug_anime_search(
    query: str,
) -> Dict[str, Any]:

    variants = query_variants(
        query
    )

    results = search_rareanimes(
        query
    )

    best = choose_best_result(
        query,
        results,
    )

    return {
        "query": query,
        "variants": variants,
        "results": results,
        "best": best,
    }


# ============================================================
# ERROR-SAFE SEARCH
# ============================================================

def safe_get_anime_info(
    query: str,
    load_seasons: bool = False,
) -> Optional[AnimeInfo]:

    try:

        return get_anime_info(
            query,
            load_seasons=load_seasons,
        )

    except Exception:
        return None


# ============================================================
# FORMAT DICT
# ============================================================

def format_anime_dict(
    data: Optional[
        Dict[str, Any]
    ],
) -> str:

    if not data:
        return (
            "😕 Anime not found."
        )

    try:

        info = AnimeInfo(
            **{
                key: value
                for key, value in data.items()
                if key in AnimeInfo.__dataclass_fields__
            }
        )

        return format_anime_info(
            info
        )

    except Exception:

        name = data.get(
            "name",
            "Unknown",
        )

        return (
            f"🎬 Anime: {name}"
        )


# ============================================================
# HEALTH CHECK
# ============================================================

def scraper_health_check() -> bool:

    try:

        page = fetch(
            BASE_URL,
            timeout=5,
        )

        return bool(
            page
        )

    except Exception:
        return False
        # ============================================================
# PART 7/7 — FINAL PUBLIC FUNCTIONS
# ============================================================

def lookup_anime(
    query: str,
) -> Optional[AnimeInfo]:
    """
    Simple public lookup function.

    Use this from bot.py / commands.py.
    """

    return safe_get_anime_info(
        query,
        load_seasons=False,
    )


def lookup_anime_full(
    query: str,
) -> Optional[AnimeInfo]:
    """
    Full lookup including season pages.
    """

    return safe_get_anime_info(
        query,
        load_seasons=True,
    )


def lookup_anime_dict(
    query: str,
) -> Optional[Dict[str, Any]]:

    info = lookup_anime(
        query
    )

    if not info:
        return None

    return asdict(
        info
    )


def lookup_anime_text(
    query: str,
) -> str:

    info = lookup_anime(
        query
    )

    if not info:

        return (
            "😕 Anime not found: "
            f"{clean_text(query)}\n\n"
            "Try:\n"
            "• Another spelling\n"
            "• English title\n"
            "• Short/common title\n"
            "• Add Movie if it is a movie"
        )

    return format_anime_info(
        info
    )


# ============================================================
# COMPATIBILITY ALIASES
# ============================================================

# Older bot code can use these names.

get_anime = lookup_anime

get_anime_details = lookup_anime

get_anime_data = lookup_anime_dict

get_anime_text = lookup_anime_text


# ============================================================
# MODULE EXPORTS
# ============================================================

__all__ = [
    "AnimeInfo",
    "get_anime_info",
    "get_anime_info_dict",
    "format_anime_info",
    "search_anime",
    "anime_to_json",
    "lookup_anime",
    "lookup_anime_full",
    "lookup_anime_dict",
    "lookup_anime_text",
    "get_anime",
    "get_anime_details",
    "get_anime_data",
    "get_anime_text",
    "get_anime_seasons",
    "get_anime_poster",
    "get_matched_title",
    "search_multiple_anime",
    "refresh_anime_info",
    "clear_anime_cache",
    "get_cache_size",
    "scraper_health_check",
]


# ============================================================
# COMMAND LINE TEST
# ============================================================

def main() -> None:

    import sys

    args = sys.argv[1:]

    if not args:

        print(
            "Usage:"
        )

        print(
            "python anime_scraper.py "
            "\"anime name\""
        )

        print()

        print(
            "Examples:"
        )

        print(
            "python anime_scraper.py bleach"
        )

        print(
            "python anime_scraper.py konosuba"
        )

        print(
            "python anime_scraper.py "
            "\"re zero\""
        )

        return

    query = " ".join(
        args
    ).strip()

    started = time.perf_counter()

    info = lookup_anime(
        query
    )

    elapsed = (
        time.perf_counter()
        - started
    )

    if not info:

        print(
            "😕 Anime not found: "
            f"{query}"
        )

        print(
            f"\nSearch time: "
            f"{elapsed:.2f}s"
        )

        return

    print(
        format_anime_info(
            info
        )
    )

    print(
        f"\nSearch time: "
        f"{elapsed:.2f}s"
    )


if __name__ == "__main__":
    main()
