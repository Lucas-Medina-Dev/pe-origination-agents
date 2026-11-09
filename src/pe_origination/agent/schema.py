"""Structured output of the screening agent.

The model scores each thesis criterion and must back claims with verbatim
quotes from the evidence. The overall fit score and the recommendation are
*not* asked from the model: they are computed in code from the criterion
scores and the thesis weights, so they are reproducible and auditable.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class CriterionScore(BaseModel):
    criterion: str
    score: int = Field(ge=0, le=10, description="0 = clearly fails, 10 = ideal; 5 when evidence is missing")
    rationale: str = Field(max_length=600)


class Evidence(BaseModel):
    source: Literal["website", "linkedin", "registry"]
    quote: str = Field(max_length=400, description="Verbatim excerpt from the evidence")
    url: str | None = None


class Assessment(BaseModel):
    company_summary: str = Field(max_length=800, description="What the company does, for whom, where")
    criteria: list[CriterionScore]
    add_on_rationale: str = Field(max_length=800, description="Why / why not it fits the platform")
    red_flags: list[str] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list, max_length=8)
    data_gaps: list[str] = Field(default_factory=list)
    confidence: Literal["low", "medium", "high"]
    excluded: bool = Field(False, description="True if a thesis exclusion clearly applies")


class Result(BaseModel):
    assessment: Assessment
    fit_score: float
    recommendation: Literal["PURSUE", "WATCH", "PASS"]
