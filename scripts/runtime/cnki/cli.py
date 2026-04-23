"""Search-only CLI entry point for the portable CNKI review skill."""

from __future__ import annotations

import argparse
import json
from typing import Any

if __package__ in (None, ""):
    import sys
    from pathlib import Path

    sys.path.append(str(Path(__file__).resolve().parent))
    from browser import fail, ok, run_async  # type: ignore
    from search import (  # type: ignore
        advanced_search,
        collect_details,
        navigate_pages,
        parse_results,
        review_expand,
        review_fixed,
        review_workflow,
        search,
        thesis_search,
    )
else:
    from .browser import fail, ok, run_async
    from .search import (
        advanced_search,
        collect_details,
        navigate_pages,
        parse_results,
        review_expand,
        review_fixed,
        review_workflow,
        search,
        thesis_search,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Portable CNKI search automation CLI")
    parser.add_argument("--cdp-url", default="http://127.0.0.1:9222", help="Chrome CDP endpoint to connect to.")
    parser.add_argument("--text", action="store_true", help="Emit a compact text summary instead of JSON.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    search_parser = subparsers.add_parser("search", help="Search CNKI by keyword.")
    search_parser.add_argument("--query", required=True)

    thesis_parser = subparsers.add_parser(
        "thesis-search",
        help="Search CNKI theses and filter by doctoral/master degree.",
    )
    thesis_parser.add_argument("--query", required=True)
    thesis_parser.add_argument("--degree", choices=["both", "doctoral", "master"], default="both")
    thesis_parser.add_argument("--count", type=int, default=20)
    thesis_parser.add_argument("--max-pages", type=int, default=20)

    collect_parser = subparsers.add_parser(
        "collect-details",
        help="Search CNKI and enrich results with abstract and metadata.",
    )
    collect_parser.add_argument("--query", required=True)
    collect_parser.add_argument("--count", type=int, default=10)
    collect_parser.add_argument("--max-pages", type=int, default=20)
    collect_parser.add_argument("--scope", choices=["papers", "theses"], default="papers")
    collect_parser.add_argument("--degree", choices=["both", "doctoral", "master"], default="both")
    collect_parser.add_argument("--concurrency-mode", choices=["serial", "adaptive"], default="adaptive")
    collect_parser.add_argument("--max-concurrency", type=int, default=4)
    collect_parser.add_argument("--min-delay-ms", type=int, default=300)
    collect_parser.add_argument("--max-delay-ms", type=int, default=1200)

    advanced_parser = subparsers.add_parser("advanced-search", help="Run an advanced CNKI search.")
    advanced_parser.add_argument("--query", required=True)
    advanced_parser.add_argument("--field-type", default="SU", choices=["SU", "TI", "KY", "TKA", "AB"])
    advanced_parser.add_argument("--query2")
    advanced_parser.add_argument("--field-type2", default="KY", choices=["SU", "TI", "KY", "TKA", "AB"])
    advanced_parser.add_argument("--row-logic", default="AND", choices=["AND", "OR", "NOT"])
    advanced_parser.add_argument("--source", action="append", choices=["SCI", "EI", "hx", "CSSCI", "CSCD"])
    advanced_parser.add_argument("--start-year")
    advanced_parser.add_argument("--end-year")
    advanced_parser.add_argument("--author")
    advanced_parser.add_argument("--journal")

    review_parser = subparsers.add_parser(
        "review-workflow",
        help="Run advanced search, pull the first pages, enrich with abstracts, and write review files.",
    )
    review_parser.add_argument("--query", required=True)
    review_parser.add_argument("--field-type", default="SU", choices=["SU", "TI", "KY", "TKA", "AB"])
    review_parser.add_argument("--query2")
    review_parser.add_argument("--field-type2", default="KY", choices=["SU", "TI", "KY", "TKA", "AB"])
    review_parser.add_argument("--row-logic", default="AND", choices=["AND", "OR", "NOT"])
    review_parser.add_argument("--source", action="append", choices=["SCI", "EI", "hx", "CSSCI", "CSCD"])
    review_parser.add_argument("--start-year")
    review_parser.add_argument("--end-year")
    review_parser.add_argument("--author")
    review_parser.add_argument("--journal")
    review_parser.add_argument("--pages", type=int, default=2)
    review_parser.add_argument("--sort-by", choices=["relevance", "date", "citations", "downloads", "comprehensive"])
    review_parser.add_argument("--concurrency-mode", choices=["serial", "adaptive"], default="adaptive")
    review_parser.add_argument("--max-concurrency", type=int, default=3)
    review_parser.add_argument("--min-delay-ms", type=int, default=400)
    review_parser.add_argument("--max-delay-ms", type=int, default=1200)
    review_parser.add_argument("--output-dir")

    review_fixed_parser = subparsers.add_parser(
        "review-fixed",
        help="Run the fixed CNKI review workflow with stable defaults and no downloads.",
    )
    review_fixed_parser.add_argument("--query", required=True)
    review_fixed_parser.add_argument("--field-type", default="SU", choices=["SU", "TI", "KY", "TKA", "AB"])
    review_fixed_parser.add_argument("--query2")
    review_fixed_parser.add_argument("--field-type2", default="KY", choices=["SU", "TI", "KY", "TKA", "AB"])
    review_fixed_parser.add_argument("--row-logic", default="AND", choices=["AND", "OR", "NOT"])
    review_fixed_parser.add_argument("--source", action="append", choices=["SCI", "EI", "hx", "CSSCI", "CSCD"])
    review_fixed_parser.add_argument("--start-year")
    review_fixed_parser.add_argument("--end-year")
    review_fixed_parser.add_argument("--author")
    review_fixed_parser.add_argument("--journal")
    review_fixed_parser.add_argument("--sort-by", choices=["relevance", "date", "citations", "downloads", "comprehensive"])
    review_fixed_parser.add_argument("--output-dir")

    review_expand_parser = subparsers.add_parser(
        "review-expand",
        help="Expand an existing fixed review bundle by rerunning the same search with more pages.",
    )
    review_expand_parser.add_argument("--review-file", required=True)
    review_expand_parser.add_argument("--additional-pages", type=int, default=2)
    review_expand_parser.add_argument("--output-dir")

    subparsers.add_parser("parse-results", help="Parse the current CNKI results page.")

    nav_parser = subparsers.add_parser("navigate-pages", help="Navigate or sort CNKI result pages.")
    nav_parser.add_argument("--action", choices=["next", "previous"])
    nav_parser.add_argument("--page", type=int)
    nav_parser.add_argument("--sort-by", choices=["relevance", "date", "citations", "downloads", "comprehensive"])

    return parser


def summarize(result: dict[str, Any]) -> str:
    if result["status"] == "error":
        return f'{result["error"]}: {result["message"]}'
    if result["status"] == "blocked":
        return result["message"]
    if result["status"] == "partial":
        return result["message"]
    data = result.get("data")
    if isinstance(data, dict):
        if "items" in data:
            return f'{result["message"]} {len(data["items"])} item(s).'
    if isinstance(data, list):
        return f'{result["message"]} {len(data)} record(s).'
    return result["message"]


def dispatch(args) -> dict[str, Any]:
    if args.command == "search":
        return run_async(search, args)
    if args.command == "thesis-search":
        return run_async(thesis_search, args)
    if args.command == "collect-details":
        return run_async(collect_details, args)
    if args.command == "advanced-search":
        return run_async(advanced_search, args)
    if args.command == "review-workflow":
        return run_async(review_workflow, args)
    if args.command == "review-fixed":
        return run_async(review_fixed, args)
    if args.command == "review-expand":
        return run_async(review_expand, args)
    if args.command == "parse-results":
        return run_async(parse_results, args)
    if args.command == "navigate-pages":
        return run_async(navigate_pages, args)
    return fail("not_found", f"Unsupported command: {args.command}")


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    result = dispatch(args)
    if args.text:
        print(summarize(result))
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
