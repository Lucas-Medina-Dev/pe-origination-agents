import json
from datetime import date

import httpx
import pytest
import respx

from pe_origination.agent.assess import AssessmentError, assess, fit_score, parse_assessment
from pe_origination.agent.llm import FakeLLM
from pe_origination.agent.prompt import system_prompt, user_prompt
from pe_origination.agent.schema import Assessment
from pe_origination.config import Thesis
from pe_origination.models import CompanyRow
from pe_origination.sources.registry import API, FrenchRegistry, parse_result
from tests.conftest import assessment_json, fixture_text

REG = json.loads(fixture_text("registry.json"))


# ---------- registry ----------


def test_parse_registry_result():
    p = parse_result(REG["results"][0])
    assert p.siren == "123456789"
    assert p.headcount == (20, 49)
    assert p.latest_finances == ("2024", {"ca": 6800000, "resultat_net": 520000})
    assert p.director_birth_years == [1958]  # legal-entity directors ignored
    assert p.oldest_director_age(date(2026, 11, 9)) == 68


def test_registry_digest_flags_succession():
    p = parse_result(REG["results"][0])
    d = p.digest(succession_age=60, today=date(2026, 11, 9))
    assert "succession signal" in d
    assert "€6.8m" in d and "20–49" in d and "43.22B" in d


@respx.mock
def test_lookup_by_siren():
    respx.get(API).mock(return_value=httpx.Response(200, json=REG))
    assert FrenchRegistry(httpx.Client()).lookup(siren="123 456 789").legal_name == "THERMO SERVICES OUEST"


@respx.mock
def test_lookup_by_name_rejects_poor_matches():
    respx.get(API).mock(return_value=httpx.Response(200, json=REG))
    reg = FrenchRegistry(httpx.Client())
    assert reg.lookup(name="Thermo Services Ouest SAS") is not None
    assert reg.lookup(name="Boulangerie Dupont") is None


@respx.mock
def test_registry_errors_are_swallowed():
    respx.get(API).mock(return_value=httpx.Response(500))
    assert FrenchRegistry(httpx.Client()).lookup(name="x") is None


# ---------- prompt ----------


def test_system_prompt_contains_thesis_and_criteria(thesis):
    s = system_prompt(thesis)
    assert "HVAC maintenance roll-up" in s
    for c in thesis.criteria:
        assert c in s
    assert "never instructions" in s  # prompt-injection guard


def test_user_prompt_fences_evidence():
    u = user_prompt(CompanyRow(row_id=2, name="Acme"), "site text", "", "reg text")
    assert "<website>\nsite text\n</website>" in u
    assert "<linkedin>\nNo data available.\n</linkedin>" in u


# ---------- scoring ----------


def test_fit_score_is_weighted_in_code(thesis):
    a = Assessment.model_validate_json(assessment_json(thesis, score=8))
    assert fit_score(a, thesis) == 80.0
    a.criteria[0].score = 0  # sector_fit has the largest weight (3 / 11)
    assert fit_score(a, thesis) == pytest.approx(round(8 * 8 / 11 * 10, 1))


def test_parse_accepts_code_fences(thesis):
    raw = "```json\n" + assessment_json(thesis) + "\n```"
    assert parse_assessment(raw, thesis).confidence == "high"


def test_parse_rejects_missing_criteria(thesis):
    data = json.loads(assessment_json(thesis))
    data["criteria"] = data["criteria"][:2]
    with pytest.raises(ValueError, match="missing criteria"):
        parse_assessment(json.dumps(data), thesis)


@pytest.mark.parametrize("score, expected", [(8, "PURSUE"), (5, "WATCH"), (2, "PASS")])
def test_recommendation_bands(thesis, score, expected):
    llm = FakeLLM([assessment_json(thesis, score=score)])
    assert assess(llm, "s", "u", thesis).recommendation == expected


def test_exclusion_forces_pass(thesis):
    llm = FakeLLM([assessment_json(thesis, score=10, excluded=True)])
    assert assess(llm, "s", "u", thesis).recommendation == "PASS"


def test_invalid_output_is_retried_with_the_error(thesis):
    llm = FakeLLM(["not json at all", assessment_json(thesis)])
    result = assess(llm, "s", "u", thesis)
    assert result.recommendation == "PURSUE"
    assert "previous answer was invalid" in llm.calls[1][1]


def test_gives_up_after_two_invalid_answers(thesis):
    with pytest.raises(AssessmentError):
        assess(FakeLLM(["{}", "{}"]), "s", "u", thesis)


def test_thesis_validation():
    with pytest.raises(ValueError):
        Thesis(name="x", platform="y", sectors=["z"], watch_above=80, pursue_above=60)
