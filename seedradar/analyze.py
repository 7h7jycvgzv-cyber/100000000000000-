"""Analyze papers with the Claude API and return entries in the analysis-file format.

Used by the daily automation. Coding agents that analyze papers themselves follow
seedradar/prompts/update.md instead; both use the same criteria (prompts/criteria.md).
"""

from __future__ import annotations

import json
from pathlib import Path

from . import seeds

DEFAULT_MODEL = "claude-opus-5-5"
DEFAULT_EFFORT = "medium"
MAX_TOKENS = 16000
MAX_ATTEMPTS = 2
FALLBACK_BETA = "server-side-fallback-2026-07-01"
ANALYZER_NAME = "Seed Radar daily (Claude API, title and abstract only)"

CRITERIA_PATH = Path(__file__).parent / "prompts" / "criteria.md"

_BILINGUAL = {
    "type": "object",
    "properties": {"en": {"type": "string"}, "ja": {"type": "string"}},
    "required": ["en", "ja"],
    "additionalProperties": False,
}
_SCORE = {"type": "integer", "enum": [1, 2, 3, 4, 5]}

# Structured-output schema for one paper. arxiv_id and version are filled in by code.
# Limits the API schema cannot express (at most 3 seeds, non-empty text) are checked
# afterwards by seeds.validate_entry.
OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "technology": _BILINGUAL,
        "breakthrough": _BILINGUAL,
        "business_seeds": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": _BILINGUAL,
                    "customer": _BILINGUAL,
                    "market": _BILINGUAL,
                    "why_now": _BILINGUAL,
                    "time_to_market": {"type": "string", "enum": list(seeds.TIME_TO_MARKET)},
                    "moat": _BILINGUAL,
                },
                "required": ["title", "customer", "market", "why_now", "time_to_market", "moat"],
                "additionalProperties": False,
            },
        },
        "scores": {
            "type": "object",
            "properties": {key: _SCORE for key in seeds.SCORE_KEYS},
            "required": list(seeds.SCORE_KEYS),
            "additionalProperties": False,
        },
        "confidence": {"type": "string", "enum": list(seeds.CONFIDENCE)},
        "caveats": _BILINGUAL,
    },
    "required": ["technology", "breakthrough", "business_seeds", "scores", "confidence", "caveats"],
    "additionalProperties": False,
}


class AnalysisError(RuntimeError):
    """One paper could not be analyzed."""


def system_prompt() -> str:
    criteria = CRITERIA_PATH.read_text(encoding="utf-8")
    return (
        "You analyze arXiv papers for Seed Radar, a public bilingual (English and Japanese) "
        "dashboard read by founders and investors. For each paper you are given only its title "
        "and abstract. Decide what businesses or startups the technology could seed, following "
        "the criteria below. Every text field has an English (`en`) and a Japanese (`ja`) "
        "version with the same content. Give at most 3 business seeds.\n\n"
        + criteria
    )


def paper_message(paper: dict) -> str:
    return (
        f"arXiv ID: {paper['arxiv_id']}v{paper['version']}\n"
        f"Categories: {', '.join(paper['categories'])}\n"
        f"Title: {paper['title']}\n\n"
        f"Abstract:\n{paper['summary']}"
    )


def make_client():
    """Create the Anthropic client. Imported here so the rest of the package needs no SDK."""
    import anthropic

    # The SDK retries 408/409/429/5xx and connection errors with backoff.
    return anthropic.Anthropic(max_retries=4)


def _request(client, paper: dict, model: str, effort: str, use_fallbacks: bool):
    params = dict(
        model=model,
        max_tokens=MAX_TOKENS,
        system=system_prompt(),
        messages=[{"role": "user", "content": paper_message(paper)}],
        output_config={"effort": effort, "format": {"type": "json_schema", "schema": OUTPUT_SCHEMA}},
    )
    if use_fallbacks:
        # On a safety decline, the API re-runs the request on Anthropic's recommended fallback model.
        return client.beta.messages.create(betas=[FALLBACK_BETA], fallbacks="default", **params)
    return client.messages.create(**params)


def analyze_paper(client, paper: dict, model: str = DEFAULT_MODEL, effort: str = DEFAULT_EFFORT,
                  use_fallbacks: bool = True) -> dict:
    """Return one validated analysis entry, or raise AnalysisError."""
    problems = ""
    for _ in range(MAX_ATTEMPTS):
        response = _request(client, paper, model, effort, use_fallbacks)
        if response.stop_reason == "refusal":
            raise AnalysisError("the model declined to analyze this paper")
        if response.stop_reason == "max_tokens":
            problems = "the answer was cut off at max_tokens"
            continue
        text = next((b.text for b in response.content if b.type == "text"), "")
        try:
            body = json.loads(text)
        except json.JSONDecodeError as e:
            problems = f"the answer was not valid JSON: {e}"
            continue
        entry = {"arxiv_id": paper["arxiv_id"], "version": paper["version"], **body}
        if isinstance(entry.get("business_seeds"), list) and not entry["business_seeds"]:
            # The criteria say a paper without a seed scores 1 everywhere; the published data relies on it.
            entry["scores"] = {key: 1 for key in seeds.SCORE_KEYS}
        errors = seeds.validate_entry(entry, paper["arxiv_id"])
        if not errors:
            return entry
        problems = "; ".join(errors)
    raise AnalysisError(f"no valid analysis after {MAX_ATTEMPTS} attempts: {problems}")


def analyze_papers(papers: list[dict], analyzed_at: str, client=None, model: str = DEFAULT_MODEL,
                   effort: str = DEFAULT_EFFORT, use_fallbacks: bool = True, log=print) -> tuple[dict, list[str]]:
    """Analyze each paper. Returns (analysis document, list of failures). Failed papers are left out,
    so they stay pending and are retried on the next run."""
    client = client or make_client()
    entries, failures = [], []
    for i, paper in enumerate(papers, 1):
        log(f"[{i}/{len(papers)}] {paper['arxiv_id']} {paper['title'][:70]}")
        try:
            entries.append(analyze_paper(client, paper, model, effort, use_fallbacks))
        except AnalysisError as e:
            failures.append(f"{paper['arxiv_id']}: {e}")
            log(f"  skipped: {e}")
    doc = {"analyzed_at": analyzed_at, "analyzer": ANALYZER_NAME, "papers": entries}
    return doc, failures

