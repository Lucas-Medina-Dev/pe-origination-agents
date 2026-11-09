"""Command line.

pe-origination run config.yaml            # assess new rows, write back to the sheet
pe-origination run config.yaml --limit 5  # first five only
pe-origination evidence config.yaml "Company name"   # show what the agent would see
"""

from __future__ import annotations

import argparse
import logging
import sys

import httpx

from pe_origination.agent.llm import make_client
from pe_origination.config import RunConfig, load_config
from pe_origination.fetch import Fetcher
from pe_origination.pipeline import gather_evidence, run
from pe_origination.sources.linkedin import SheetColumnsProvider
from pe_origination.sources.registry import FrenchRegistry
from pe_origination.sources.sheets import ExcelSheet, GoogleSheet, SheetBackend


def open_sheet(cfg: RunConfig) -> SheetBackend:
    s = cfg.sheet
    if s.type == "excel":
        return ExcelSheet(s.path, cfg.columns, s.worksheet)
    return GoogleSheet(s.spreadsheet_id, cfg.columns, s.worksheet, s.credentials)


def build_sources(cfg: RunConfig):
    client = httpx.Client(timeout=cfg.crawl.timeout_seconds, follow_redirects=True)
    fetcher = Fetcher(
        client,
        user_agent=cfg.crawl.user_agent,
        delay_seconds=cfg.crawl.delay_seconds,
        respect_robots=cfg.crawl.respect_robots,
        cache_dir=cfg.crawl.cache_dir,
    )
    registry = FrenchRegistry(client) if cfg.use_registry else None
    return fetcher, SheetColumnsProvider(cfg.columns.linkedin_fields), registry


def _run(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    sheet = open_sheet(cfg)
    fetcher, linkedin, registry = build_sources(cfg)
    llm = make_client(cfg.llm.provider, cfg.llm.model, cfg.llm.temperature)

    def show(row, result, status):
        if result is None:
            print(f"  ✗ {row.name:<40} {status[:60]}")
        else:
            print(f"  {result.recommendation:<7} {result.fit_score:>5.1f}  {row.name}")

    print(f"Thesis: {cfg.thesis.name} · model {cfg.llm.provider}/{cfg.llm.model}")
    stats = run(
        sheet,
        cfg.thesis,
        llm,
        fetcher,
        linkedin,
        registry,
        max_pages=cfg.crawl.max_pages,
        max_chars=cfg.crawl.max_chars,
        workers=cfg.workers,
        limit=args.limit,
        force=args.force,
        on_result=show,
    )
    print(f"\nAssessed {stats.assessed} · failed {stats.failed} · skipped (already done) {stats.skipped}")
    print("  " + "  ".join(f"{k}: {v}" for k, v in sorted(stats.by_recommendation.items())))
    return 0 if stats.failed == 0 else 1


def _evidence(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    rows = [r for r in open_sheet(cfg).rows() if r.name.lower() == args.company.lower()]
    if not rows:
        print(f"'{args.company}' not found in the sheet", file=sys.stderr)
        return 2
    fetcher, linkedin, registry = build_sources(cfg)
    ev = gather_evidence(
        rows[0], cfg.thesis, fetcher, linkedin, registry, cfg.crawl.max_pages, cfg.crawl.max_chars
    )
    print(f"== registry ==\n{ev.registry or '—'}\n\n== linkedin ==\n{ev.linkedin or '—'}\n")
    print(f"== website ({ev.pages} pages, {len(ev.website):,} chars) ==\n{ev.website or '—'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="pe-origination", description="Screen buy-and-build targets")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="assess companies and write results to the sheet")
    r.add_argument("config")
    r.add_argument("--limit", type=int)
    r.add_argument("--force", action="store_true", help="re-assess rows already scored")
    r.set_defaults(func=_run)
    e = sub.add_parser("evidence", help="print the cleaned evidence for one company (no model call)")
    e.add_argument("config")
    e.add_argument("company")
    e.set_defaults(func=_evidence)
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(message)s")
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
