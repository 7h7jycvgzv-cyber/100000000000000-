"""arXiv API client: fetch the latest papers per field and normalize them."""

from __future__ import annotations

import re
import time
import urllib.error
import urllib.parse
import urllib.request
from email.utils import parsedate_to_datetime
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from datetime import timezone

API_URL = "https://export.arxiv.org/api/query"
RSS_URL = "https://rss.arxiv.org/rss/"
SOURCES = ("auto", "api", "rss")
USER_AGENT = "seedradar/0.1"
# arXiv asks API clients to wait 3 seconds between requests.
REQUEST_INTERVAL_SEC = 3.0
TIMEOUT_SEC = 60
MAX_ATTEMPTS = 3
MAX_RESULTS_LIMIT = 200

NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "arxiv": "http://arxiv.org/schemas/atom",
    "dc": "http://purl.org/dc/elements/1.1/",
}


@dataclass(frozen=True)
class Field:
    key: str
    label_en: str
    label_ja: str
    categories: tuple[str, ...]
    # When set, a paper must also mention at least one of these in its abstract.
    keywords: tuple[str, ...] = ()


FIELDS: dict[str, Field] = {
    f.key: f
    for f in (
        # Space is matched by keyword inside engineering-leaning categories, so that
        # satellite and spacecraft technology comes first and basic astronomy drops out.
        Field(
            "space",
            "Space Technology",
            "宇宙開発",
            (
                "astro-ph.IM", "physics.space-ph", "eess.SP", "eess.SY",
                "eess.IV", "cs.RO", "cs.NI", "cs.CV",
            ),
            (
                "spacecraft", "cubesat", "satellite constellation", "earth observation",
                "remote sensing", "on-orbit", "in-orbit", "space debris", "low earth orbit",
                "inter-satellite", "satellite communication", "satellite imagery",
            ),
        ),
        Field("quantum", "Quantum Computing", "量子計算", ("quant-ph",)),
        Field("ai", "Artificial Intelligence", "AI", ("cs.AI", "cs.LG", "cs.CL")),
    )
}


@dataclass
class Paper:
    arxiv_id: str
    version: int
    title: str
    summary: str
    authors: list[str]
    primary_category: str
    categories: list[str]
    published: str
    updated: str
    abs_url: str
    pdf_url: str
    fields: list[str] = field(default_factory=list)
    # "api" (arXiv API) or "rss" (arXiv RSS feed, used when the API is unavailable).
    source: str = "api"

    def to_dict(self) -> dict:
        return asdict(self)


class ArxivError(RuntimeError):
    """Raised when the arXiv API cannot be reached or returns bad data."""


class ArxivUnavailable(ArxivError):
    """The request may be fine but the service is not answering (rate limit, 5xx, network)."""


def build_search_query(categories: tuple[str, ...] | list[str], keywords: tuple[str, ...] | list[str] = ()) -> str:
    if not categories:
        raise ValueError("categories must not be empty")
    query = " OR ".join(f"cat:{c}" for c in categories)
    if not keywords:
        return query
    for keyword in keywords:
        if '"' in keyword:
            raise ValueError(f"keyword must not contain a double quote: {keyword}")
    terms = " OR ".join(f'abs:"{k}"' if " " in k else f"abs:{k}" for k in keywords)
    return f"({query}) AND ({terms})"


def build_query_url(
    categories: tuple[str, ...] | list[str],
    max_results: int,
    keywords: tuple[str, ...] | list[str] = (),
) -> str:
    if not 1 <= max_results <= MAX_RESULTS_LIMIT:
        raise ValueError(f"max_results must be between 1 and {MAX_RESULTS_LIMIT}")
    params = {
        "search_query": build_search_query(categories, keywords),
        "sortBy": "submittedDate",
        "sortOrder": "descending",
        "start": 0,
        "max_results": max_results,
    }
    return f"{API_URL}?{urllib.parse.urlencode(params)}"


def _clean(text: str | None) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _split_id(id_url: str) -> tuple[str, int]:
    # "http://arxiv.org/abs/2611.20756v1" -> ("2611.20756", 1)
    raw = id_url.rsplit("/abs/", 1)[-1]
    match = re.fullmatch(r"(.+?)v(\d+)", raw)
    if match:
        return match.group(1), int(match.group(2))
    return raw, 1


def parse_feed(xml_text: str | bytes) -> list[Paper]:
    """Parse an arXiv Atom feed into papers. Entries without an id are skipped."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        raise ArxivError(f"arXiv returned invalid XML: {e}") from e

    papers: list[Paper] = []
    for entry in root.findall("atom:entry", NS):
        id_url = _clean(entry.findtext("atom:id", default="", namespaces=NS))
        if "/abs/" not in id_url:
            # arXiv reports query errors as an entry whose id is an error URL.
            if "/api/errors" in id_url:
                raise ArxivError(_clean(entry.findtext("atom:summary", default="", namespaces=NS)))
            continue
        arxiv_id, version = _split_id(id_url)

        pdf_url = ""
        for link in entry.findall("atom:link", NS):
            if link.get("title") == "pdf":
                pdf_url = link.get("href", "")
        categories = [c.get("term", "") for c in entry.findall("atom:category", NS) if c.get("term")]
        primary = entry.find("arxiv:primary_category", NS)

        papers.append(
            Paper(
                arxiv_id=arxiv_id,
                version=version,
                title=_clean(entry.findtext("atom:title", default="", namespaces=NS)),
                summary=_clean(entry.findtext("atom:summary", default="", namespaces=NS)),
                authors=[
                    _clean(a.findtext("atom:name", default="", namespaces=NS))
                    for a in entry.findall("atom:author", NS)
                ],
                primary_category=primary.get("term", "") if primary is not None else (categories[0] if categories else ""),
                categories=categories,
                published=_clean(entry.findtext("atom:published", default="", namespaces=NS)),
                updated=_clean(entry.findtext("atom:updated", default="", namespaces=NS)),
                abs_url=f"https://arxiv.org/abs/{arxiv_id}",
                pdf_url=pdf_url or f"https://arxiv.org/pdf/{arxiv_id}",
            )
        )
    return papers


def http_get(url: str) -> bytes:
    """GET with retries on transient failures (network errors, 429, 5xx)."""
    last_error: Exception | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_SEC) as response:
                return response.read()
        except urllib.error.HTTPError as e:
            if e.code != 429 and e.code < 500:
                raise ArxivError(f"HTTP {e.code} from {urllib.parse.urlparse(url).netloc}") from e
            last_error = e
        except (urllib.error.URLError, TimeoutError) as e:
            last_error = e
        if attempt < MAX_ATTEMPTS:
            time.sleep(REQUEST_INTERVAL_SEC * attempt)
    raise ArxivUnavailable(f"request failed after {MAX_ATTEMPTS} attempts: {last_error}")


def build_rss_url(categories: tuple[str, ...] | list[str]) -> str:
    if not categories:
        raise ValueError("categories must not be empty")
    return RSS_URL + "+".join(categories)


def matches_keywords(text: str, keywords: tuple[str, ...] | list[str]) -> bool:
    """Same rule as the API query: the abstract must mention at least one keyword."""
    if not keywords:
        return True
    haystack = text.lower()
    return any(re.search(r"(?<![a-z0-9])" + re.escape(k.lower()) + r"(?![a-z0-9])", haystack) for k in keywords)


def parse_rss(xml_text: str | bytes) -> list[Paper]:
    """Parse an arXiv RSS feed (the day's announcements). Replacements of old papers are skipped."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        raise ArxivError(f"arXiv RSS returned invalid XML: {e}") from e

    papers: list[Paper] = []
    for item in root.iter("item"):
        announce = _clean(item.findtext("arxiv:announce_type", default="new", namespaces=NS))
        if announce not in ("new", "cross"):
            continue
        guid = _clean(item.findtext("guid", default=""))
        raw_id = guid.rsplit("arXiv.org:", 1)[-1] if "arXiv.org:" in guid else ""
        if not raw_id:
            link = _clean(item.findtext("link", default=""))
            raw_id = link.rsplit("/abs/", 1)[-1] if "/abs/" in link else ""
        if not raw_id:
            continue
        arxiv_id, version = _split_id("/abs/" + raw_id)

        description = item.findtext("description", default="") or ""
        summary = _clean(description.split("Abstract:", 1)[1] if "Abstract:" in description else description)
        categories = [_clean(c.text) for c in item.findall("category") if _clean(c.text)]
        published = ""
        pub_date = _clean(item.findtext("pubDate", default=""))
        if pub_date:
            try:
                published = parsedate_to_datetime(pub_date).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            except (TypeError, ValueError):
                published = ""
        creators = _clean(item.findtext("dc:creator", default="", namespaces=NS))

        papers.append(
            Paper(
                arxiv_id=arxiv_id,
                version=version,
                title=_clean(item.findtext("title", default="")),
                summary=summary,
                authors=[a.strip() for a in creators.split(",") if a.strip()],
                primary_category=categories[0] if categories else "",
                categories=categories,
                # The feed carries the announcement date, not the submission date.
                published=published,
                updated=published,
                abs_url=f"https://arxiv.org/abs/{arxiv_id}",
                pdf_url=f"https://arxiv.org/pdf/{arxiv_id}v{version}",
                source="rss",
            )
        )
    return papers


def fetch_field_api(field_key: str, max_results: int = 10, get=http_get) -> list[Paper]:
    spec = FIELDS[field_key]
    url = build_query_url(spec.categories, max_results, spec.keywords)
    return parse_feed(get(url))


def fetch_field_rss(field_key: str, max_results: int = 10, get=http_get) -> list[Paper]:
    """Today's announcements in the field's categories, filtered by the field's keywords."""
    spec = FIELDS[field_key]
    papers = parse_rss(get(build_rss_url(spec.categories)))
    matched = [p for p in papers if matches_keywords(p.summary, spec.keywords)]
    return matched[:max_results]


def fetch_field(field_key: str, max_results: int = 10, get=http_get, source: str = "auto") -> list[Paper]:
    """Fetch one field. With source "auto", fall back to RSS when the API is unavailable."""
    if field_key not in FIELDS:
        raise ValueError(f"unknown field: {field_key} (choose from {', '.join(FIELDS)})")
    if source not in SOURCES:
        raise ValueError(f"unknown source: {source} (choose from {', '.join(SOURCES)})")
    if not 1 <= max_results <= MAX_RESULTS_LIMIT:
        raise ValueError(f"max_results must be between 1 and {MAX_RESULTS_LIMIT}")
    if source == "rss":
        return fetch_field_rss(field_key, max_results, get=get)
    try:
        return fetch_field_api(field_key, max_results, get=get)
    except ArxivUnavailable as api_error:
        if source == "api":
            raise
        try:
            papers = fetch_field_rss(field_key, max_results, get=get)
        except ArxivError as rss_error:
            raise ArxivUnavailable(f"arXiv API: {api_error}; RSS fallback: {rss_error}") from rss_error
        if not papers:
            raise ArxivUnavailable(
                f"arXiv API: {api_error}; RSS fallback: no matching papers in today's feed for '{field_key}'"
            ) from api_error
        return papers


def fetch_all(
    field_keys: list[str] | None = None,
    max_results: int = 10,
    get=http_get,
    sleep=time.sleep,
    source: str = "auto",
) -> list[Paper]:
    """Fetch every field, merging papers that appear in more than one.

    With source "auto", once the API fails for one field the rest of the run uses RSS,
    so a rate-limited API is not hit again.
    """
    keys = field_keys or list(FIELDS)
    by_id: dict[str, Paper] = {}
    for i, key in enumerate(keys):
        if i > 0:
            sleep(REQUEST_INTERVAL_SEC)
        papers = fetch_field(key, max_results, get=get, source=source)
        if source == "auto" and any(p.source == "rss" for p in papers):
            source = "rss"
        for paper in papers:
            existing = by_id.setdefault(paper.arxiv_id, paper)
            if key not in existing.fields:
                existing.fields.append(key)
    return sorted(by_id.values(), key=lambda p: p.published, reverse=True)
