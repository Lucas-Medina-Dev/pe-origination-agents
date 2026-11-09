"""Focused website crawl: the home page plus the pages that describe the business.

Instead of dumping every page, links are ranked by how likely they are to
answer a screening question (what they do, for whom, how big, where) and
only the best few are fetched. Keywords cover French and English sites.
"""

from __future__ import annotations

import re
from urllib.parse import urldefrag, urljoin, urlparse

from bs4 import BeautifulSoup

from pe_origination.clean import html_to_page, remove_template_lines
from pe_origination.fetch import Fetcher, host_key, normalize_url
from pe_origination.models import Page

KEYWORDS: dict[str, int] = {
    # what they do
    "services": 5,
    "prestations": 5,
    "solutions": 4,
    "expertise": 4,
    "metiers": 4,
    "activites": 4,
    "savoir-faire": 4,
    "offre": 3,
    "produits": 3,
    "products": 3,
    # who they are
    "about": 5,
    "a-propos": 5,
    "qui-sommes-nous": 6,
    "societe": 4,
    "entreprise": 3,
    "histoire": 4,
    "history": 4,
    "company": 3,
    "groupe": 3,
    # scale and clients
    "clients": 4,
    "references": 5,
    "realisations": 4,
    "case": 2,
    "implantations": 4,
    "agences": 4,
    "locations": 3,
    "equipe": 3,
    "team": 3,
    "chiffres": 3,
    "certifications": 2,
    "contact": 1,
}
EXCLUDE = re.compile(
    r"login|connexion|account|compte|cart|panier|privacy|confidentialite|mentions-legales|cgv|cgu|"
    r"legal|cookies|politique|recrutement|careers|jobs|wp-admin|feed|\.(pdf|jpe?g|png|gif|zip|docx?)$",
    re.I,
)


def _slug(text: str) -> str:
    text = text.lower()
    for a, b in (("é", "e"), ("è", "e"), ("ê", "e"), ("à", "a"), ("ç", "c"), ("ô", "o"), ("î", "i")):
        text = text.replace(a, b)
    return re.sub(r"[^a-z0-9]+", "-", text)


def rank_links(html: str, base_url: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    base_host = host_key(base_url)
    scores: dict[str, int] = {}
    for a in soup.find_all("a", href=True):
        url = urldefrag(urljoin(base_url, a["href"]))[0].rstrip("/")
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or host_key(url) != base_host:
            continue
        if EXCLUDE.search(parsed.path) or parsed.path in ("", "/"):
            continue
        haystack = _slug(parsed.path + " " + a.get_text(" "))
        score = sum(w for k, w in KEYWORDS.items() if k in haystack)
        if score:
            scores[url] = max(scores.get(url, 0), score)
    # best score first, shorter (top-level) paths win ties
    return sorted(scores, key=lambda u: (-scores[u], len(urlparse(u).path)))


def crawl_site(fetcher: Fetcher, url: str, max_pages: int = 6) -> list[Page]:
    start = normalize_url(url)
    home = fetcher.get(start)
    if home is None:
        return []
    pages = [html_to_page(home.text, home.url)]
    for link in rank_links(home.text, home.url)[: max_pages - 1]:
        res = fetcher.get(link)
        if res is not None:
            pages.append(html_to_page(res.text, res.url))
    return remove_template_lines(pages)
