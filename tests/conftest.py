import json
from pathlib import Path

import pytest

from pe_origination.config import Thesis

FIX = Path(__file__).parent / "fixtures"


def fixture_text(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


@pytest.fixture
def thesis() -> Thesis:
    return Thesis(
        name="HVAC maintenance roll-up, Western France",
        platform="Regional multi-technical maintenance platform",
        sectors=["HVAC maintenance", "multi-technical building maintenance"],
        geography=["Pays de la Loire", "Bretagne"],
        must_have=["recurring maintenance contracts", "B2B clients"],
        exclusions=["pure new-build installation"],
    )


def assessment_json(thesis: Thesis, score: int = 8, **overrides) -> str:
    data = {
        "company_summary": "HVAC maintenance for tertiary and industrial sites in Western France.",
        "criteria": [{"criterion": c, "score": score, "rationale": "supported"} for c in thesis.criteria],
        "add_on_rationale": "Adds three branches in the platform's core region.",
        "red_flags": [],
        "evidence": [{"source": "website", "quote": "plus de 400 sites tertiaires", "url": "https://x.fr"}],
        "data_gaps": [],
        "confidence": "high",
        "excluded": False,
    }
    data.update(overrides)
    return json.dumps(data)
