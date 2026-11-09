"""French company registry via the official open API (recherche-entreprises.api.gouv.fr).

Free, keyless, maintained by the French State (INSEE Sirene + INPI RNE). For
buy-and-build screening it gives what a website rarely states clearly:
activity code (NAF), headcount bracket, age of the company, published
revenue / net income, legal status (active or ceased) — and the birth year
of the directors, which is the classic *succession* signal for founder-owned
SMEs.
"""

from __future__ import annotations

import difflib
import re
from datetime import date

import httpx
from pydantic import BaseModel, Field

API = "https://recherche-entreprises.api.gouv.fr/search"

# INSEE "tranche d'effectif salarié" codes
HEADCOUNT_BRACKETS = {
    "00": (0, 0),
    "01": (1, 2),
    "02": (3, 5),
    "03": (6, 9),
    "11": (10, 19),
    "12": (20, 49),
    "21": (50, 99),
    "22": (100, 199),
    "31": (200, 249),
    "32": (250, 499),
    "41": (500, 999),
    "42": (1000, 1999),
    "51": (2000, 4999),
    "52": (5000, 9999),
    "53": (10000, None),
}


class RegistryProfile(BaseModel):
    siren: str
    legal_name: str
    naf_code: str | None = None
    headcount_code: str | None = None
    created: str | None = None
    status: str | None = None  # A = active, C = ceased
    category: str | None = None  # PME / ETI / GE
    establishments: int | None = None
    city: str | None = None
    postcode: str | None = None
    finances: dict[str, dict[str, float | None]] = Field(default_factory=dict)
    director_birth_years: list[int] = Field(default_factory=list)
    match_score: float = 1.0  # name similarity when matched by name

    @property
    def headcount(self) -> tuple[int, int | None] | None:
        return HEADCOUNT_BRACKETS.get(self.headcount_code or "")

    @property
    def latest_finances(self) -> tuple[str, dict[str, float | None]] | None:
        if not self.finances:
            return None
        year = max(self.finances)
        return year, self.finances[year]

    def oldest_director_age(self, today: date | None = None) -> int | None:
        if not self.director_birth_years:
            return None
        return (today or date.today()).year - min(self.director_birth_years)

    def digest(self, succession_age: int = 60, today: date | None = None) -> str:
        lines = [f"Legal name: {self.legal_name} (SIREN {self.siren})"]
        if self.status:
            lines.append(f"Status: {'active' if self.status == 'A' else 'CEASED'}")
        if self.naf_code:
            lines.append(f"Activity code (NAF): {self.naf_code}")
        if self.headcount:
            lo, hi = self.headcount
            lines.append(f"Employees (registry bracket): {lo}–{hi if hi is not None else '+'}")
        if self.category:
            lines.append(f"Size category: {self.category}")
        if self.created:
            lines.append(f"Incorporated: {self.created}")
        if self.establishments:
            lines.append(f"Establishments: {self.establishments}")
        if self.city:
            lines.append(f"Head office: {self.postcode or ''} {self.city}".strip())
        lf = self.latest_finances
        if lf:
            year, f = lf
            ca, rn = f.get("ca"), f.get("resultat_net")
            lines.append(f"Published accounts {year}: revenue {_eur(ca)}, net income {_eur(rn)}")
        age = self.oldest_director_age(today)
        if age is not None:
            flag = " — succession signal" if age >= succession_age else ""
            lines.append(f"Oldest individual director: ~{age} years old{flag}")
        if self.match_score < 0.9:
            lines.append(
                f"(Matched by name, similarity {self.match_score:.2f}: verify it is the right entity)"
            )
        return "\n".join(lines)


def _eur(v: float | None) -> str:
    if v is None:
        return "n/a"
    return f"€{v / 1e6:,.1f}m" if abs(v) >= 1e6 else f"€{v:,.0f}"


def _norm(name: str) -> str:
    name = re.sub(r"\b(sas|sasu|sarl|sa|eurl|sci|groupe|group)\b", " ", name.lower())
    return re.sub(r"[^a-z0-9]+", " ", name).strip()


def parse_result(item: dict, query_name: str | None = None) -> RegistryProfile:
    siege = item.get("siege") or {}
    years = []
    for d in item.get("dirigeants") or []:
        if (d.get("type_dirigeant") or "personne physique").startswith("personne physique"):
            y = d.get("annee_de_naissance") or d.get("annee_naissance")
            if y and str(y).isdigit():
                years.append(int(y))
    finances = {
        str(year): {"ca": v.get("ca"), "resultat_net": v.get("resultat_net")}
        for year, v in (item.get("finances") or {}).items()
        if isinstance(v, dict)
    }
    legal = item.get("nom_complet") or item.get("nom_raison_sociale") or ""
    score = 1.0
    if query_name:
        score = difflib.SequenceMatcher(None, _norm(query_name), _norm(legal)).ratio()
    return RegistryProfile(
        siren=str(item.get("siren", "")),
        legal_name=legal,
        naf_code=item.get("activite_principale"),
        headcount_code=item.get("tranche_effectif_salarie"),
        created=item.get("date_creation"),
        status=item.get("etat_administratif"),
        category=item.get("categorie_entreprise"),
        establishments=item.get("nombre_etablissements_ouverts") or item.get("nombre_etablissements"),
        city=siege.get("libelle_commune"),
        postcode=siege.get("code_postal"),
        finances=finances,
        director_birth_years=years,
        match_score=round(score, 3),
    )


class FrenchRegistry:
    def __init__(self, client: httpx.Client | None = None, min_match: float = 0.6) -> None:
        self.client = client or httpx.Client(timeout=15.0)
        self.min_match = min_match

    def lookup(self, siren: str | None = None, name: str | None = None) -> RegistryProfile | None:
        siren = re.sub(r"\D", "", siren or "")
        if len(siren) == 9:
            items = self._search(siren)
            for it in items:
                if str(it.get("siren")) == siren:
                    return parse_result(it)
            return None
        if not name:
            return None
        items = self._search(name)
        if not items:
            return None
        best = max((parse_result(it, name) for it in items[:5]), key=lambda p: p.match_score)
        return best if best.match_score >= self.min_match else None

    def _search(self, q: str) -> list[dict]:
        try:
            r = self.client.get(API, params={"q": q, "per_page": 5, "page": 1})
            r.raise_for_status()
            return r.json().get("results") or []
        except (httpx.HTTPError, ValueError):
            return []
