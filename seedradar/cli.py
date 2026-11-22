"""Command line entry point: python -m seedradar <command>"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import seeds
from .arxiv import FIELDS, SOURCES, ArxivError, fetch_all

DEFAULT_PAPERS = "data/papers.json"
DEFAULT_SEEDS = "data/seeds.json"
DEFAULT_PER_FIELD = 5


def _read_json(path: str | Path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _write_json(path: str | Path, payload) -> None:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def build_payload(papers, field_keys: list[str]) -> dict:
    return {
        "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "arXiv RSS" if any(p.source == "rss" for p in papers) else "arXiv API",
        "fields": {
            key: {
                "label_en": FIELDS[key].label_en,
                "label_ja": FIELDS[key].label_ja,
                "categories": list(FIELDS[key].categories),
                "keywords": list(FIELDS[key].keywords),
            }
            for key in field_keys
        },
        "count": len(papers),
        "papers": [p.to_dict() for p in papers],
    }


def cmd_fetch(args: argparse.Namespace) -> int:
    field_keys = args.fields or list(FIELDS)
    try:
        papers = fetch_all(field_keys, max_results=args.max, source=args.source)
    except (ArxivError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    if not papers:
        # Never replace a good papers.json with an empty one (e.g. an empty RSS feed).
        print(f"error: no papers found; {args.out} was not changed", file=sys.stderr)
        return 1

    _write_json(args.out, build_payload(papers, field_keys))

    for key in field_keys:
        matched = [p for p in papers if key in p.fields]
        via = "" if not matched else " (via RSS)" if any(p.source == "rss" for p in matched) else " (via API)"
        print(f"[{key}] {FIELDS[key].label_ja} / {FIELDS[key].label_en}: {len(matched)}{via}")
        for p in matched[: args.show]:
            print(f"  {p.published[:10]}  {p.arxiv_id}  {p.title[:90]}")
    print(f"saved {len(papers)} papers -> {args.out}")
    return 0


def cmd_pending(args: argparse.Namespace) -> int:
    """Print the papers that still need an analysis, as JSON."""
    papers_doc = _read_json(args.papers)
    seeds_doc = _read_json(args.seeds) if Path(args.seeds).exists() else None
    pending = seeds.pending_papers(papers_doc, seeds_doc)
    print(json.dumps({"count": len(pending), "papers": pending}, ensure_ascii=False, indent=2))
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    errors = seeds.validate_analysis(_read_json(args.analysis))
    if errors:
        for error in errors:
            print(f"error: {error}", file=sys.stderr)
        return 1
    print(f"ok: {args.analysis}")
    return 0


def cmd_merge(args: argparse.Namespace) -> int:
    seeds_doc = _read_json(args.seeds) if Path(args.seeds).exists() else None
    try:
        merged = seeds.merge(_read_json(args.papers), _read_json(args.analysis), seeds_doc)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    _write_json(args.seeds, merged)
    with_seeds = sum(1 for p in merged["papers"] if p["business_seeds"])
    print(f"merged -> {args.seeds}: {merged['count']} papers, {with_seeds} with business seeds")
    return 0


def cmd_remove(args: argparse.Namespace) -> int:
    try:
        result = seeds.remove(_read_json(args.seeds), args.arxiv_ids)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    _write_json(args.seeds, result)
    print(f"removed {len(args.arxiv_ids)} -> {args.seeds}: {result['count']} papers left")
    return 0


def _analysis_path(directory: Path, day: str) -> Path:
    """data/analysis/DAY.json, or DAY-2.json, DAY-3.json ... when that day already has a file."""
    path = directory / f"{day}.json"
    n = 2
    while path.exists():
        path = directory / f"{day}-{n}.json"
        n += 1
    return path


def _set_output(name: str, value: str) -> None:
    """Expose a value to later GitHub Actions steps (no-op outside Actions)."""
    target = os.environ.get("GITHUB_OUTPUT")
    if target:
        with open(target, "a", encoding="utf-8") as f:
            f.write(f"{name}={value}\n")


def build_summary(day: str, papers: list[dict], analysis: dict, failures: list[str], merged: dict | None) -> str:
    analyzed = {p["arxiv_id"]: p for p in analysis["papers"]}
    lines = [f"## Seed Radar daily update {day}", ""]
    lines.append(f"- New papers: {len(papers)}")
    lines.append(f"- Analyzed: {len(analyzed)} (with business seeds: "
                 f"{sum(1 for p in analyzed.values() if p['business_seeds'])})")
    if merged is not None:
        lines.append(f"- Papers on the dashboard: {merged['count']}")
    if analyzed:
        lines += ["", "| Total | Field | Paper | Top seed |", "|---|---|---|---|"]
        rows = []
        for paper in papers:
            entry = analyzed.get(paper["arxiv_id"])
            if entry is None:
                continue
            total = sum(entry["scores"].values())
            seed = entry["business_seeds"][0]["title"]["en"] if entry["business_seeds"] else "(no seed)"
            title = paper["title"].replace("|", "/")[:80]
            rows.append((total, f"| {total} | {', '.join(paper['fields'])} | "
                                f"[{title}]({paper['abs_url']}) | {seed.replace('|', '/')} |"))
        lines += [row for _, row in sorted(rows, key=lambda r: -r[0])]
    if failures:
        lines += ["", "### Not analyzed (retried on the next run)", ""] + [f"- {f}" for f in failures]
    lines += ["", "Analyses are model estimates from titles and abstracts only. "
              "Review before merging if anything looks off."]
    return "\n".join(lines) + "\n"


def cmd_daily(args: argparse.Namespace) -> int:
    """fetch -> pending -> analyze (Claude API) -> validate -> merge. Writes only data/ files."""
    from . import analyze

    _set_output("changed", "false")
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    try:
        fetched = fetch_all(list(FIELDS), max_results=args.per_field, source=args.source)
    except (ArxivError, ValueError) as e:
        print(f"error: fetch failed: {e}", file=sys.stderr)
        return 1
    if not fetched:
        print("No papers were fetched; nothing to do.")
        return 0
    papers_doc = build_payload(fetched, list(FIELDS))
    _write_json(args.papers, papers_doc)
    print(f"fetched {len(fetched)} papers ({papers_doc['source']}) -> {args.papers}")

    seeds_doc = _read_json(args.seeds) if Path(args.seeds).exists() else None
    pending = seeds.pending_papers(papers_doc, seeds_doc)[: args.limit]
    if not pending:
        print("No new papers to analyze.")
        if args.summary:
            Path(args.summary).write_text(f"## Seed Radar daily update {day}\n\nNo new papers.\n", encoding="utf-8")
        return 0

    print(f"analyzing {len(pending)} papers with {args.model} (effort {args.effort})")
    analysis, failures = analyze.analyze_papers(
        pending, day, model=args.model, effort=args.effort, use_fallbacks=not args.no_fallbacks
    )
    merged = None
    if analysis["papers"]:
        path = _analysis_path(Path(args.analysis_dir), day)
        _write_json(path, analysis)
        merged = seeds.merge(papers_doc, analysis, seeds_doc)
        _write_json(args.seeds, merged)
        print(f"wrote {path}; {args.seeds} now has {merged['count']} papers")
        _set_output("changed", "true")

    summary = build_summary(day, pending, analysis, failures, merged)
    if args.summary:
        Path(args.summary).write_text(summary, encoding="utf-8")
    print(summary)
    if not analysis["papers"]:
        print("error: no paper could be analyzed", file=sys.stderr)
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="seedradar", description="arXiv papers -> business seeds")
    sub = parser.add_subparsers(dest="command", required=True)

    fetch = sub.add_parser("fetch", help="fetch the latest papers from arXiv")
    fetch.add_argument("--fields", nargs="+", choices=list(FIELDS), help="default: all fields")
    fetch.add_argument("--max", type=int, default=DEFAULT_PER_FIELD,
                       help=f"papers per field (1-200, default {DEFAULT_PER_FIELD})")
    fetch.add_argument("--out", default=DEFAULT_PAPERS, help="output JSON path")
    fetch.add_argument("--source", choices=SOURCES, default="auto",
                       help="auto: arXiv API, then the RSS feed if the API is unavailable (default)")
    fetch.add_argument("--show", type=int, default=3, help="titles to print per field")
    fetch.set_defaults(func=cmd_fetch)

    pending = sub.add_parser("pending", help="print papers that have no analysis yet")
    pending.add_argument("--papers", default=DEFAULT_PAPERS)
    pending.add_argument("--seeds", default=DEFAULT_SEEDS)
    pending.set_defaults(func=cmd_pending)

    validate = sub.add_parser("validate", help="check an analysis file against the schema")
    validate.add_argument("analysis")
    validate.set_defaults(func=cmd_validate)

    merge = sub.add_parser("merge", help="merge an analysis file into the public seeds.json")
    merge.add_argument("analysis")
    merge.add_argument("--papers", default=DEFAULT_PAPERS)
    merge.add_argument("--seeds", default=DEFAULT_SEEDS)
    merge.set_defaults(func=cmd_merge)

    remove = sub.add_parser("remove", help="remove papers from the public seeds.json")
    remove.add_argument("arxiv_ids", nargs="+")
    remove.add_argument("--seeds", default=DEFAULT_SEEDS)
    remove.set_defaults(func=cmd_remove)

    daily = sub.add_parser("daily", help="fetch, analyze new papers with the Claude API, and update seeds.json")
    daily.add_argument("--per-field", type=int, default=DEFAULT_PER_FIELD, help="papers per field to fetch")
    daily.add_argument("--limit", type=int, default=15, help="at most this many papers are analyzed per run")
    daily.add_argument("--source", choices=SOURCES, default="auto")
    daily.add_argument("--model", default=os.environ.get("SEEDRADAR_MODEL") or "claude-opus-5-5")
    daily.add_argument("--effort", default=os.environ.get("SEEDRADAR_EFFORT") or "medium",
                       choices=["low", "medium", "high", "xhigh", "max"])
    daily.add_argument("--no-fallbacks", action="store_true", help="do not use server-side refusal fallbacks")
    daily.add_argument("--papers", default=DEFAULT_PAPERS)
    daily.add_argument("--seeds", default=DEFAULT_SEEDS)
    daily.add_argument("--analysis-dir", default="data/analysis")
    daily.add_argument("--summary", help="also write a Markdown summary to this file")
    daily.set_defaults(func=cmd_daily)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
