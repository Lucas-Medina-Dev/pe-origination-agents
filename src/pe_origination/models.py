from __future__ import annotations

from pydantic import BaseModel, Field


class CompanyRow(BaseModel):
    """One company from the input sheet."""

    row_id: int  # sheet row number (1-based, header is row 1)
    name: str
    website: str | None = None
    linkedin_url: str | None = None
    siren: str | None = None
    extra: dict[str, str] = Field(default_factory=dict)
    already_assessed: bool = False


class Page(BaseModel):
    url: str
    title: str = ""
    description: str = ""
    text: str = ""


class LinkedInProfile(BaseModel):
    url: str | None = None
    description: str | None = None
    industry: str | None = None
    employees: str | None = None

    def digest(self) -> str:
        parts = [
            f"Industry: {self.industry}" if self.industry else "",
            f"Employees (LinkedIn): {self.employees}" if self.employees else "",
            f"Description: {self.description}" if self.description else "",
        ]
        return "\n".join(p for p in parts if p)
