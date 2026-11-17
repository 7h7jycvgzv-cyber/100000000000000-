"""Validate business-seed analyses and merge them into the public seeds.json."""

from __future__ import annotations

from datetime import datetime, timezone

from .arxiv import FIELDS

TIME_TO_MARKET = ("now", "1-3y", "3-10y", "10y+")
CONFIDENCE = ("low", "medium", "high")
SCORE_KEYS = ("market_size", "feasibility", "startup_fit")
SEED_TEXT_KEYS = ("title", "customer", "market", "why_now", "moat")
MAX_SEEDS_PER_PAPER = 3

# Paper metadata copied into the public file. The abstract is left out: the page links to arXiv.
PUBLIC_PAPER_KEYS = (
    "arxiv_id", "version", "title", "authors", "primary_category",
    "categories", "published", "abs_url", "pdf_url", "fields",
)


def _check_bilingual(value, path: str, errors: list[str]) -> None:
    if not isinstance(value, dict):
        errors.append(f"{path}: must be an object with 'en' and 'ja'")
        return
    for lang in ("en", "ja"):
        text = value.get(lang)
        if not isinstance(text, str) or not text.strip():
            errors.append(f"{path}.{lang}: must be a non-empty string")


def validate_entry(entry, path: str = "entry") -> list[str]:
    """Return a list of problems with one paper's analysis (empty when valid)."""
    errors: list[str] = []
    if not isinstance(entry, dict):
        return [f"{path}: must be an object"]

    if not isinstance(entry.get("arxiv_id"), str) or not entry.get("arxiv_id"):
        errors.append(f"{path}.arxiv_id: must be a non-empty string")
    if not isinstance(entry.get("version"), int) or isinstance(entry.get("version"), bool) or entry.get("version", 0) < 1:
        errors.append(f"{path}.version: must be an integer >= 1")

    for key in ("technology", "breakthrough", "caveats"):
        _check_bilingual(entry.get(key), f"{path}.{key}", errors)

    seeds = entry.get("business_seeds")
    if not isinstance(seeds, list):
        errors.append(f"{path}.business_seeds: must be a list (empty when there is no seed)")
    else:
        if len(seeds) > MAX_SEEDS_PER_PAPER:
            errors.append(f"{path}.business_seeds: at most {MAX_SEEDS_PER_PAPER} seeds")
        for i, seed in enumerate(seeds):
            seed_path = f"{path}.business_seeds[{i}]"
            if not isinstance(seed, dict):
                errors.append(f"{seed_path}: must be an object")
                continue
            for key in SEED_TEXT_KEYS:
                _check_bilingual(seed.get(key), f"{seed_path}.{key}", errors)
            if seed.get("time_to_market") not in TIME_TO_MARKET:
                errors.append(f"{seed_path}.time_to_market: must be one of {', '.join(TIME_TO_MARKET)}")

    scores = entry.get("scores")
    if not isinstance(scores, dict):
        errors.append(f"{path}.scores: must be an object")
    else:
        for key in SCORE_KEYS:
            value = scores.get(key)
            if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 5:
                errors.append(f"{path}.scores.{key}: must be an integer from 1 to 5")

    if entry.get("confidence") not in CONFIDENCE:
        errors.append(f"{path}.confidence: must be one of {', '.join(CONFIDENCE)}")
    return errors


def validate_analysis(doc) -> list[str]:
    """Validate a whole analysis file: {"analyzed_at", "analyzer", "papers": [...]}."""
    if not isinstance(doc, dict):
        return ["analysis: must be an object"]
    errors: list[str] = []
    if not isinstance(doc.get("analyzed_at"), str) or not doc.get("analyzed_at"):
        errors.append("analysis.analyzed_at: must be a non-empty string")
    if not isinstance(doc.get("analyzer"), str) or not doc.get("analyzer"):
        errors.append("analysis.analyzer: must be a non-empty string")
    papers = doc.get("papers")
    if not isinstance(papers, list) or not papers:
        errors.append("analysis.papers: must be a non-empty list")
        return errors
    seen: set[str] = set()
    for i, entry in enumerate(papers):
        errors.extend(validate_entry(entry, f"papers[{i}]"))
        arxiv_id = entry.get("arxiv_id") if isinstance(entry, dict) else None
        if arxiv_id in seen:
            errors.append(f"papers[{i}].arxiv_id: duplicate {arxiv_id}")
        seen.add(arxiv_id)
    return errors


def pending_papers(papers_doc: dict, seeds_doc: dict | None) -> list[dict]:
    """Papers in papers.json that have no analysis yet for their current version."""
    done = {(p["arxiv_id"], p["version"]) for p in (seeds_doc or {}).get("papers", [])}
    return [p for p in papers_doc.get("papers", []) if (p["arxiv_id"], p["version"]) not in done]


def merge(papers_doc: dict, analysis_doc: dict, seeds_doc: dict | None = None) -> dict:
    """Merge an analysis into seeds.json. A newer analysis of the same paper replaces the old one."""
    errors = validate_analysis(analysis_doc)
    if errors:
        raise ValueError("invalid analysis:\n" + "\n".join(errors))

    papers_by_id = {p["arxiv_id"]: p for p in papers_doc.get("papers", [])}
    merged = {p["arxiv_id"]: p for p in (seeds_doc or {}).get("papers", [])}

    for entry in analysis_doc["papers"]:
        paper = papers_by_id.get(entry["arxiv_id"])
        if paper is None:
            raise ValueError(f"{entry['arxiv_id']}: analyzed paper is not in papers.json")
        if paper["version"] != entry["version"]:
            raise ValueError(
                f"{entry['arxiv_id']}: analysis is for v{entry['version']} but papers.json has v{paper['version']}"
            )
        public = {key: paper[key] for key in PUBLIC_PAPER_KEYS}
        public.update(
            analyzed_at=analysis_doc["analyzed_at"],
            analyzer=analysis_doc["analyzer"],
            technology=entry["technology"],
            breakthrough=entry["breakthrough"],
            business_seeds=entry["business_seeds"],
            scores={key: entry["scores"][key] for key in SCORE_KEYS},
            confidence=entry["confidence"],
            caveats=entry["caveats"],
        )
        merged[entry["arxiv_id"]] = public

    papers = sorted(merged.values(), key=lambda p: (p["published"], p["arxiv_id"]), reverse=True)
    return {
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "arXiv API",
        "fields": {
            key: {"label_en": f.label_en, "label_ja": f.label_ja, "categories": list(f.categories)}
            for key, f in FIELDS.items()
        },
        "count": len(papers),
        "papers": papers,
    }
