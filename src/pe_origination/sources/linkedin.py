"""LinkedIn company data — through a provider interface, not a scraper.

Scraping LinkedIn violates its User Agreement, and LinkedIn actively litigates
against scrapers. This project therefore does not scrape it. Two options:

* ``SheetColumnsProvider`` (default): LinkedIn fields already present in the
  sheet — filled by hand by an analyst, or exported from a licensed data
  provider / Sales Navigator workflow you are entitled to use.
* Implement ``LinkedInProvider`` for a licensed API you have a contract with.
"""

from __future__ import annotations

from typing import Protocol

from pe_origination.models import CompanyRow, LinkedInProfile


class LinkedInProvider(Protocol):
    def get(self, row: CompanyRow) -> LinkedInProfile | None: ...


class NullProvider:
    def get(self, row: CompanyRow) -> LinkedInProfile | None:
        return None


class SheetColumnsProvider:
    """Read LinkedIn fields from extra sheet columns (header → field mapping)."""

    def __init__(self, field_columns: dict[str, str]) -> None:
        self.field_columns = field_columns

    def get(self, row: CompanyRow) -> LinkedInProfile | None:
        values = {
            field: (row.extra.get(col) or "").strip() or None for field, col in self.field_columns.items()
        }
        if not any(values.values()):
            return None
        return LinkedInProfile(url=row.linkedin_url, **values)
