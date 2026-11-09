"""Call the model, validate its answer, and turn it into a decision."""

from __future__ import annotations

import json
import re

from pydantic import ValidationError

from pe_origination.agent.llm import LLMClient
from pe_origination.agent.schema import Assessment, Result
from pe_origination.config import Thesis

FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.I)


class AssessmentError(RuntimeError):
    pass


def parse_assessment(raw: str, thesis: Thesis) -> Assessment:
    text = FENCE.sub("", raw.strip())
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("no JSON object in model output")
    a = Assessment.model_validate(json.loads(text[start : end + 1]))
    got = {c.criterion for c in a.criteria}
    missing = set(thesis.criteria) - got
    if missing:
        raise ValueError(f"missing criteria: {sorted(missing)}")
    return a


def fit_score(a: Assessment, thesis: Thesis) -> float:
    """Weighted average of criterion scores, on 0-100. Unknown criteria are ignored."""
    scores = {c.criterion: c.score for c in a.criteria}
    total_w = sum(thesis.weights.values())
    return round(sum(w * scores.get(k, 5) for k, w in thesis.weights.items()) / total_w * 10, 1)


def decide(a: Assessment, thesis: Thesis) -> Result:
    score = fit_score(a, thesis)
    if a.excluded:
        rec = "PASS"
    elif score >= thesis.pursue_above:
        rec = "PURSUE"
    elif score >= thesis.watch_above:
        rec = "WATCH"
    else:
        rec = "PASS"
    return Result(assessment=a, fit_score=score, recommendation=rec)


def assess(llm: LLMClient, system: str, user: str, thesis: Thesis, max_attempts: int = 2) -> Result:
    """Ask the model; on invalid output, retry once with the validation error."""
    prompt, last_err = user, None
    for _ in range(max_attempts):
        raw = llm.complete_json(system, prompt)
        try:
            return decide(parse_assessment(raw, thesis), thesis)
        except (ValueError, ValidationError, json.JSONDecodeError) as e:
            last_err = e
            prompt = (
                f"{user}\n\nYour previous answer was invalid: {str(e)[:500]}\n"
                "Return ONLY a JSON object that matches the schema, with every criterion."
            )
    raise AssessmentError(f"invalid model output after {max_attempts} attempts: {last_err}")
