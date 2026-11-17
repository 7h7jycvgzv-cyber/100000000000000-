"""Command line entry point: python -m seedradar fetch"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from .arxiv import FIELDS, ArxivError, fetch_all


def build_payload(papers, field_keys: list[str]) -> dict:
    return {
        "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "arXiv API",
        "fields": {
            key: {
                "label_en": FIELDS[key].label_en,
                "label_ja": FIELDS[key].label_ja,
                "categories": list(FIELDS[key].categories),
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

    payload = build_payload(papers, field_keys)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    for key in field_keys:
        matched = [p for p in papers if key in p.fields]
        print(f"[{key}] {FIELDS[key].label_ja} / {FIELDS[key].label_en}: {len(matched)}")
        for p in matched[: args.show]:
            print(f"  {p.published[:10]}  {p.arxiv_id}  {p.title[:90]}")
    print(f"saved {len(papers)} papers -> {out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="seedradar", description="arXiv papers -> business seeds")
    sub = parser.add_subparsers(dest="command", required=True)

    fetch = sub.add_parser("fetch", help="fetch the latest papers from arXiv")
    fetch.add_argument("--fields", nargs="+", choices=list(FIELDS), help="default: all fields")
    fetch.add_argument("--max", type=int, default=10, help="papers per field (1-200, default 10)")
    fetch.add_argument("--out", default="data/papers.json", help="output JSON path")
    fetch.add_argument("--show", type=int, default=3, help="titles to print per field")
    fetch.set_defaults(func=cmd_fetch)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
