"""Run configuration: where the sheet is, which columns to use, which model,
and the investment thesis the agent screens against. One YAML file."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, model_validator


class SizeBand(BaseModel):
    min_employees: int | None = None
    max_employees: int | None = None
    min_revenue_eur: float | None = None
    max_revenue_eur: float | None = None


class Thesis(BaseModel):
    """What the fund is looking for. Drives both the prompt and the scoring."""

    name: str
    platform: str = Field(description="The platform company / strategy the add-ons would join")
    sectors: list[str]
    geography: list[str] = Field(default_factory=list)
    size: SizeBand = Field(default_factory=SizeBand)
    must_have: list[str] = Field(default_factory=list)
    nice_to_have: list[str] = Field(default_factory=list)
    exclusions: list[str] = Field(default_factory=list)
    succession_age: int = Field(60, description="Director age from which a succession signal is flagged")
    # criterion -> weight; the LLM scores each criterion 0-10, the code aggregates
    weights: dict[str, float] = Field(
        default_factory=lambda: {
            "sector_fit": 3,
            "size_fit": 2,
            "business_quality": 2,
            "add_on_potential": 2,
            "geography_fit": 1,
            "succession_signal": 1,
        }
    )
    pursue_above: float = 70
    watch_above: float = 45

    @model_validator(mode="after")
    def _check(self) -> Thesis:
        if not self.weights or any(w < 0 for w in self.weights.values()):
            raise ValueError("weights must be non-empty and non-negative")
        if sum(self.weights.values()) <= 0:
            raise ValueError("weights must sum to a positive number")
        if not 0 <= self.watch_above <= self.pursue_above <= 100:
            raise ValueError("need 0 <= watch_above <= pursue_above <= 100")
        return self

    @property
    def criteria(self) -> list[str]:
        return list(self.weights)


class Columns(BaseModel):
    """Sheet column headers. Inputs are read, outputs are created if missing."""

    name: str = "Company"
    website: str = "Website"
    linkedin: str = "LinkedIn"
    siren: str | None = "SIREN"
    # Optional LinkedIn data already in the sheet (manual entry or a licensed provider export)
    linkedin_fields: dict[str, str] = Field(
        default_factory=lambda: {
            "description": "LinkedIn description",
            "industry": "LinkedIn industry",
            "employees": "LinkedIn employees",
        }
    )
    output_prefix: str = "AI "


class SheetConfig(BaseModel):
    type: Literal["excel", "google"] = "excel"
    path: str | None = None
    spreadsheet_id: str | None = None
    worksheet: str | None = None
    credentials: str | None = Field(None, description="Google service-account JSON path")

    @model_validator(mode="after")
    def _check(self) -> SheetConfig:
        if self.type == "excel" and not self.path:
            raise ValueError("sheet.path is required for type 'excel'")
        if self.type == "google" and not self.spreadsheet_id:
            raise ValueError("sheet.spreadsheet_id is required for type 'google'")
        return self


class LLMConfig(BaseModel):
    provider: Literal["openai", "anthropic"] = "openai"
    model: str = "gpt-5"
    temperature: float | None = None


class CrawlConfig(BaseModel):
    max_pages: int = Field(6, ge=1, le=20)
    max_chars: int = Field(12_000, ge=1_000)
    delay_seconds: float = Field(1.0, ge=0)
    timeout_seconds: float = 15.0
    respect_robots: bool = True
    user_agent: str = "pe-origination-agents/0.1 (+https://github.com/Lucas-Medina-Dev/pe-origination-agents)"
    cache_dir: str | None = ".cache/pages"


class RunConfig(BaseModel):
    sheet: SheetConfig
    columns: Columns = Field(default_factory=Columns)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    crawl: CrawlConfig = Field(default_factory=CrawlConfig)
    use_registry: bool = True
    workers: int = Field(4, ge=1, le=16)
    thesis: Thesis


def load_config(path: str | Path) -> RunConfig:
    path = Path(path)
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    cfg = RunConfig.model_validate(raw)
    # relative sheet paths are resolved against the config file
    if cfg.sheet.path and not Path(cfg.sheet.path).is_absolute():
        cfg.sheet.path = str((path.parent / cfg.sheet.path).resolve())
    return cfg
