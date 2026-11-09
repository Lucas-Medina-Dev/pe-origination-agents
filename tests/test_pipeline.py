import json

import httpx
import respx
from openpyxl import Workbook, load_workbook

from pe_origination.agent.llm import FakeLLM
from pe_origination.cli import main
from pe_origination.config import Columns, load_config
from pe_origination.fetch import Fetcher
from pe_origination.pipeline import run
from pe_origination.sources.linkedin import SheetColumnsProvider
from pe_origination.sources.registry import API, FrenchRegistry
from pe_origination.sources.sheets import ExcelSheet
from tests.conftest import assessment_json, fixture_text

BASE = "https://thermo-ouest.fr"


def make_sheet(path):
    wb = Workbook()
    ws = wb.active
    ws.append(["Company", "Website", "LinkedIn", "SIREN", "LinkedIn description", "Owner notes"])
    ws.append(
        [
            "Thermo Services Ouest",
            "thermo-ouest.fr",
            "https://linkedin.com/company/x",
            "123456789",
            "Multi-technical maintenance, 45 technicians",
            "met at trade show",
        ]
    )
    ws.append(["Dead Site SARL", "dead.example", None, None, None, None])
    ws.append([None, None, None, None, None, None])  # empty row ignored
    wb.save(path)


def mock_web():
    html = lambda s: httpx.Response(200, text=s, headers={"content-type": "text/html"})  # noqa: E731
    respx.get(BASE + "/robots.txt").mock(return_value=httpx.Response(404))
    respx.route(method="GET", host="thermo-ouest.fr", path="/").mock(
        return_value=html(fixture_text("home.html"))
    )
    respx.get(BASE + "/qui-sommes-nous").mock(return_value=html(fixture_text("about.html")))
    respx.get(BASE + "/nos-prestations").mock(return_value=html(fixture_text("prestations.html")))
    respx.get(BASE + "/references").mock(return_value=httpx.Response(404))
    respx.get(BASE + "/contact").mock(return_value=httpx.Response(404))
    respx.get(url__startswith="https://dead.example").mock(side_effect=httpx.ConnectError("down"))
    respx.get(API).mock(
        side_effect=lambda req: httpx.Response(
            200,
            json=json.loads(fixture_text("registry.json"))
            if "123456789" in str(req.url)
            else {"results": []},
        )
    )


@respx.mock
def test_pipeline_writes_back_to_the_same_sheet(tmp_path, thesis):
    path = tmp_path / "targets.xlsx"
    make_sheet(path)
    mock_web()
    cols = Columns()
    sheet = ExcelSheet(path, cols)
    llm = FakeLLM(lambda s, u: assessment_json(thesis, score=8))
    client = httpx.Client()
    stats = run(
        sheet,
        thesis,
        llm,
        Fetcher(client, delay_seconds=0, backoff_seconds=0),
        SheetColumnsProvider(cols.linkedin_fields),
        FrenchRegistry(client),
        workers=2,
    )

    assert stats.assessed == 1 and stats.failed == 1
    ws = load_workbook(path).active
    header = [c.value for c in ws[1]]
    row2 = dict(zip(header, [c.value for c in ws[2]], strict=False))
    row3 = dict(zip(header, [c.value for c in ws[3]], strict=False))
    assert row2["AI recommendation"] == "PURSUE" and row2["AI fit score"] == 80.0
    assert row2["AI status"] == "ok"
    assert row2["Owner notes"] == "met at trade show"  # input columns untouched
    assert row3["AI status"].startswith("error")

    # the model saw cleaned website text, the registry and the LinkedIn column
    user = llm.calls[0][1]
    assert "45 techniciens" in user and "succession signal" in user and "45 technicians" in user
    assert "cookies" not in user


@respx.mock
def test_second_run_skips_assessed_rows(tmp_path, thesis):
    path = tmp_path / "targets.xlsx"
    make_sheet(path)
    mock_web()
    client = httpx.Client()
    args = (
        thesis,
        None,
        Fetcher(client, delay_seconds=0, backoff_seconds=0),
        SheetColumnsProvider({}),
        FrenchRegistry(client),
    )
    llm = FakeLLM(lambda s, u: assessment_json(thesis))
    run(ExcelSheet(path, Columns()), args[0], llm, *args[2:])
    calls = len(llm.calls)
    stats = run(ExcelSheet(path, Columns()), args[0], llm, *args[2:])
    assert stats.skipped == 1  # Thermo already scored; the failed row is retried
    assert len(llm.calls) == calls  # the retried row has no evidence, so no model call


@respx.mock
def test_ceased_company_is_always_pass(tmp_path, thesis):
    path = tmp_path / "t.xlsx"
    make_sheet(path)
    mock_web()
    ceased = json.loads(fixture_text("registry.json"))
    ceased["results"][0]["etat_administratif"] = "C"
    respx.get(API).mock(return_value=httpx.Response(200, json=ceased))
    client = httpx.Client()
    run(
        ExcelSheet(path, Columns()),
        thesis,
        FakeLLM(lambda s, u: assessment_json(thesis, score=10)),
        Fetcher(client, delay_seconds=0, backoff_seconds=0),
        SheetColumnsProvider({}),
        FrenchRegistry(client),
        limit=1,
    )
    ws = load_workbook(path).active
    row2 = dict(zip([c.value for c in ws[1]], [c.value for c in ws[2]], strict=False))
    assert row2["AI recommendation"] == "PASS"
    assert "ceased" in row2["AI red flags"]


def test_example_config_loads():
    from pathlib import Path

    cfg = load_config(Path(__file__).resolve().parents[1] / "examples" / "config.yaml")
    assert cfg.thesis.criteria[0] == "sector_fit"
    assert cfg.sheet.path.endswith("targets.xlsx")


@respx.mock
def test_cli_evidence_command(tmp_path, capsys):
    path = tmp_path / "targets.xlsx"
    make_sheet(path)
    mock_web()
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "sheet: {type: excel, path: targets.xlsx}\n"
        "crawl: {delay_seconds: 0, cache_dir: null}\n"
        "thesis: {name: T, platform: P, sectors: [HVAC]}\n",
        encoding="utf-8",
    )
    assert main(["evidence", str(cfg), "Thermo Services Ouest"]) == 0
    out = capsys.readouterr().out
    assert "== registry ==" in out and "45 techniciens" in out
