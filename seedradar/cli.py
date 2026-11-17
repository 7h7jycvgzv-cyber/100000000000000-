"""Command line entry point: python -m seedradar <command>"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import seeds
from .arxiv import FIELDS, ArxivError, fetch_all

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
        "source": "arXiv API",
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
        papers = fetch_all(field_keys, max_results=args.max)
    except (ArxivError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    _write_json(args.out, build_payload(papers, field_keys))

    for key in field_keys:
        matched = [p for p in papers if key in p.fields]
        print(f"[{key}] {FIELDS[key].label_ja} / {FIELDS[key].label_en}: {len(matched)}")
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="seedradar", description="arXiv papers -> business seeds")
    sub = parser.add_subparsers(dest="command", required=True)

    fetch = sub.add_parser("fetch", help="fetch the latest papers from arXiv")
    fetch.add_argument("--fields", nargs="+", choices=list(FIELDS), help="default: all fields")
    fetch.add_argument("--max", type=int, default=DEFAULT_PER_FIELD,
                       help=f"papers per field (1-200, default {DEFAULT_PER_FIELD})")
    fetch.add_argument("--out", default=DEFAULT_PAPERS, help="output JSON path")
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

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
