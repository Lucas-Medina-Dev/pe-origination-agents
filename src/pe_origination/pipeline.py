"""End-to-end run: sheet → evidence → agent → sheet.

Network-bound work (crawling, registry, model calls) runs in a thread pool;
writes to the sheet happen on the main thread only, and the sheet is saved
every ``save_every`` companies so an interrupted run loses little work.
Rows already assessed are skipped unless ``force`` is set.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone

from pe_origination.agent.assess import assess
from pe_origination.agent.llm import LLMClient
from pe_origination.agent.prompt import system_prompt, user_prompt
from pe_origination.agent.schema import Result
from pe_origination.clean import build_digest
from pe_origination.config import Thesis
from pe_origination.fetch import Fetcher
from pe_origination.models import CompanyRow
from pe_origination.sources.linkedin import LinkedInProvider
from pe_origination.sources.registry import FrenchRegistry
from pe_origination.sources.sheets import SheetBackend
from pe_origination.sources.website import crawl_site

log = logging.getLogger("pe_origination")


@dataclass
class Evidence:
    website: str
    linkedin: str
    registry: str
    pages: int
    registry_status: str | None = None


@dataclass
class RunStats:
    assessed: int = 0
    skipped: int = 0
    failed: int = 0
    by_recommendation: dict[str, int] = field(default_factory=dict)


def gather_evidence(
    row: CompanyRow,
    thesis: Thesis,
    fetcher: Fetcher,
    linkedin: LinkedInProvider,
    registry: FrenchRegistry | None,
    max_pages: int,
    max_chars: int,
) -> Evidence:
    pages = crawl_site(fetcher, row.website, max_pages) if row.website else []
    li = linkedin.get(row)
    reg = registry.lookup(siren=row.siren, name=row.name) if registry else None
    return Evidence(
        website=build_digest(pages, max_chars),
        linkedin=li.digest() if li else "",
        registry=reg.digest(thesis.succession_age) if reg else "",
        pages=len(pages),
        registry_status=reg.status if reg else None,
    )


def to_cells(result: Result) -> dict[str, str | float]:
    a = result.assessment
    return {
        "fit score": result.fit_score,
        "recommendation": result.recommendation,
        "summary": a.company_summary,
        "add-on rationale": a.add_on_rationale,
        "criteria": " | ".join(f"{c.criterion} {c.score}/10" for c in a.criteria),
        "red flags": "; ".join(a.red_flags) or "—",
        "evidence": "\n".join(
            f'[{e.source}] "{e.quote}"' + (f" ({e.url})" if e.url else "") for e in a.evidence
        ),
        "confidence": a.confidence,
        "assessed at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "status": "ok",
    }


def run(
    sheet: SheetBackend,
    thesis: Thesis,
    llm: LLMClient,
    fetcher: Fetcher,
    linkedin: LinkedInProvider,
    registry: FrenchRegistry | None = None,
    *,
    max_pages: int = 6,
    max_chars: int = 12_000,
    workers: int = 4,
    limit: int | None = None,
    force: bool = False,
    save_every: int = 5,
    on_result: Callable[[CompanyRow, Result | None, str], None] | None = None,
) -> RunStats:
    stats = RunStats()
    rows = sheet.rows()
    todo = [r for r in rows if force or not r.already_assessed]
    stats.skipped = len(rows) - len(todo)
    if limit is not None:
        todo = todo[:limit]
    system = system_prompt(thesis)

    def work(row: CompanyRow) -> Result:
        ev = gather_evidence(row, thesis, fetcher, linkedin, registry, max_pages, max_chars)
        if not (ev.website or ev.linkedin or ev.registry):
            raise RuntimeError("no evidence found (website unreachable, no registry match)")
        result = assess(llm, system, user_prompt(row, ev.website, ev.linkedin, ev.registry), thesis)
        if ev.registry_status == "C":  # hard rule: a ceased company is never a target
            result = result.model_copy(update={"recommendation": "PASS"})
            result.assessment.red_flags.insert(0, "Company is registered as ceased")
        return result

    done = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(work, r): r for r in todo}
        for fut in as_completed(futures):
            row = futures[fut]
            try:
                result = fut.result()
            except Exception as e:  # one bad company must not stop the run
                log.warning("%s: %s", row.name, e)
                sheet.write(row.row_id, {"status": f"error: {str(e)[:200]}"})
                stats.failed += 1
                if on_result:
                    on_result(row, None, str(e))
            else:
                sheet.write(row.row_id, to_cells(result))
                stats.assessed += 1
                stats.by_recommendation[result.recommendation] = (
                    stats.by_recommendation.get(result.recommendation, 0) + 1
                )
                if on_result:
                    on_result(row, result, "ok")
            done += 1
            if done % save_every == 0:
                sheet.save()
    sheet.save()
    return stats
