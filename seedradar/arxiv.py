"""arXiv API client: fetch the latest papers per field and normalize them."""

from __future__ import annotations

import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field

API_URL = "https://export.arxiv.org/api/query"
USER_AGENT = "seedradar/0.1"
# arXiv asks API clients to wait 3 seconds between requests.
REQUEST_INTERVAL_SEC = 3.0
TIMEOUT_SEC = 30
MAX_ATTEMPTS = 3
MAX_RESULTS_LIMIT = 200

NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "arxiv": "http://arxiv.org/schemas/atom",
}


@dataclass(frozen=True)
class Field:
    key: str
    label_en: str
    label_ja: str
    categories: tuple[str, ...]


FIELDS: dict[str, Field] = {
    f.key: f
    for f in (
        Field(
            "space",
            "Space & Astrophysics",
            "宇宙開発",
            ("astro-ph.EP", "astro-ph.IM", "physics.space-ph"),
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

    def to_dict(self) -> dict:
        return asdict(self)


class ArxivError(RuntimeError):
    """Raised when the arXiv API cannot be reached or returns bad data."""


def build_query_url(categories: tuple[str, ...] | list[str], max_results: int) -> str:
    if not categories:
        raise ValueError("categories must not be empty")
    if not 1 <= max_results <= MAX_RESULTS_LIMIT:
        raise ValueError(f"max_results must be between 1 and {MAX_RESULTS_LIMIT}")
    query = " OR ".join(f"cat:{c}" for c in categories)
    params = {
        "search_query": query,
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
                raise ArxivError(f"arXiv API returned HTTP {e.code}") from e
            last_error = e
        except (urllib.error.URLError, TimeoutError) as e:
            last_error = e
        if attempt < MAX_ATTEMPTS:
            time.sleep(REQUEST_INTERVAL_SEC * attempt)
    raise ArxivError(f"arXiv API request failed after {MAX_ATTEMPTS} attempts: {last_error}")


def fetch_field(field_key: str, max_results: int = 10, get=http_get) -> list[Paper]:
    if field_key not in FIELDS:
        raise ValueError(f"unknown field: {field_key} (choose from {', '.join(FIELDS)})")
    url = build_query_url(FIELDS[field_key].categories, max_results)
    return parse_feed(get(url))


def fetch_all(
    field_keys: list[str] | None = None,
    max_results: int = 10,
    get=http_get,
    sleep=time.sleep,
) -> list[Paper]:
    """Fetch every field, merging papers that appear in more than one."""
    keys = field_keys or list(FIELDS)
    by_id: dict[str, Paper] = {}
    for i, key in enumerate(keys):
        if i > 0:
            sleep(REQUEST_INTERVAL_SEC)
        for paper in fetch_field(key, max_results, get=get):
            existing = by_id.setdefault(paper.arxiv_id, paper)
            if key not in existing.fields:
                existing.fields.append(key)
    return sorted(by_id.values(), key=lambda p: p.published, reverse=True)
