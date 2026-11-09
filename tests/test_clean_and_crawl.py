import httpx
import pytest
import respx

from pe_origination.clean import build_digest, html_to_page, remove_template_lines
from pe_origination.fetch import Fetcher, normalize_url
from pe_origination.models import Page
from pe_origination.sources.website import crawl_site, rank_links
from tests.conftest import fixture_text

BASE = "https://thermo-ouest.fr"


def test_chrome_is_removed_and_structure_kept():
    page = html_to_page(fixture_text("home.html"), BASE)
    assert page.title.startswith("Thermo Services Ouest")
    assert "Maintenance et installation" in page.description
    assert "# Votre partenaire CVC depuis 1987" in page.text
    assert "400 sites tertiaires" in page.text
    for noise in ("cookies", "newsletter", "Tous droits réservés", "Qui sommes-nous", "Mentions"):
        assert noise not in page.text


def test_list_items_become_bullets():
    page = html_to_page(fixture_text("prestations.html"), BASE + "/nos-prestations")
    assert "- Chaufferies et pompes à chaleur" in page.text


def test_template_lines_repeated_across_pages_are_dropped():
    pages = [html_to_page(fixture_text(f), BASE) for f in ("home.html", "prestations.html", "about.html")]
    assert all("Appelez-nous" in p.text for p in pages)
    cleaned = remove_template_lines(pages)
    assert not any("Appelez-nous" in p.text for p in cleaned)
    assert "45 techniciens" in cleaned[2].text  # content survives


def test_digest_respects_budget_and_keeps_every_page():
    pages = [Page(url=f"https://a.fr/{i}", title=f"P{i}", text=("word " * 400).strip()) for i in range(4)]
    d = build_digest(pages, max_chars=2_000)
    assert len(d) <= 2_100
    assert all(f"### P{i}" in d for i in range(4))


def test_rank_links_prefers_business_pages_and_stays_on_site():
    links = rank_links(fixture_text("home.html"), BASE)
    assert links[0] in (BASE + "/qui-sommes-nous", BASE + "/nos-prestations")
    assert BASE + "/mentions-legales" not in links  # legal page excluded
    assert not any("twitter" in u for u in links)  # off-site excluded
    assert not any(u.endswith(".pdf") for u in links)


def test_normalize_url():
    assert normalize_url("thermo-ouest.fr") == "https://thermo-ouest.fr"
    assert normalize_url("http://a.fr/x") == "http://a.fr/x"


def _html(body: str) -> httpx.Response:
    return httpx.Response(200, text=body, headers={"content-type": "text/html; charset=utf-8"})


@respx.mock
def test_crawl_site_end_to_end():
    respx.get(BASE + "/robots.txt").mock(
        return_value=httpx.Response(200, text="User-agent: *\nDisallow: /references")
    )
    respx.route(method="GET", host="thermo-ouest.fr", path="/").mock(
        return_value=_html(fixture_text("home.html"))
    )
    respx.get(BASE + "/qui-sommes-nous").mock(return_value=_html(fixture_text("about.html")))
    respx.get(BASE + "/nos-prestations").mock(return_value=_html(fixture_text("prestations.html")))
    refs = respx.get(BASE + "/references").mock(return_value=_html("<p>should not be fetched</p>"))
    respx.get(BASE + "/contact").mock(return_value=httpx.Response(404))

    fetcher = Fetcher(httpx.Client(), delay_seconds=0, backoff_seconds=0)
    pages = crawl_site(fetcher, "thermo-ouest.fr", max_pages=6)

    assert not refs.called  # robots.txt disallow is respected
    assert [p.url.rsplit("/", 1)[-1] for p in pages][0] == "thermo-ouest.fr"
    assert any("45 techniciens" in p.text for p in pages)


@respx.mock
def test_fetcher_retries_then_succeeds_and_skips_non_html():
    respx.get("https://a.fr/robots.txt").mock(return_value=httpx.Response(404))
    route = respx.get("https://a.fr/page").mock(side_effect=[httpx.Response(503), _html("<p>ok page</p>")])
    respx.get("https://a.fr/file").mock(
        return_value=httpx.Response(200, content=b"%PDF", headers={"content-type": "application/pdf"})
    )
    f = Fetcher(httpx.Client(), delay_seconds=0, backoff_seconds=0)
    assert f.get("https://a.fr/page").text == "<p>ok page</p>"
    assert route.call_count == 2
    assert f.get("https://a.fr/file") is None


@respx.mock
def test_fetcher_cache(tmp_path):
    respx.get("https://a.fr/robots.txt").mock(return_value=httpx.Response(404))
    route = respx.get("https://a.fr/").mock(return_value=_html("<p>cached</p>"))
    f = Fetcher(httpx.Client(), delay_seconds=0, cache_dir=tmp_path)
    f.get("https://a.fr/")
    f.get("https://a.fr/")
    assert route.call_count == 1


@pytest.mark.parametrize("ctype", ["text/html", "text/html; charset=iso-8859-1"])
@respx.mock
def test_html_content_types_accepted(ctype):
    respx.get("https://b.fr/robots.txt").mock(return_value=httpx.Response(404))
    respx.get("https://b.fr/").mock(
        return_value=httpx.Response(200, text="<p>x</p>", headers={"content-type": ctype})
    )
    assert Fetcher(httpx.Client(), delay_seconds=0).get("https://b.fr/") is not None
