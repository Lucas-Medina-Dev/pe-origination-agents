"""Read the company list and write results back to the *same* sheet.

Two backends with one interface: a local Excel file (openpyxl) and Google
Sheets (gspread, optional extra). Output columns are created at the right
of the sheet if they do not exist yet; input columns are never modified.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Protocol

from openpyxl import load_workbook

from pe_origination.config import Columns
from pe_origination.models import CompanyRow

OUTPUT_FIELDS = [
    "fit score",
    "recommendation",
    "summary",
    "add-on rationale",
    "criteria",
    "red flags",
    "evidence",
    "confidence",
    "assessed at",
    "status",
]


def output_headers(columns: Columns) -> dict[str, str]:
    return {f: f"{columns.output_prefix}{f}" for f in OUTPUT_FIELDS}


class SheetBackend(Protocol):
    def rows(self) -> list[CompanyRow]: ...
    def write(self, row_id: int, values: dict[str, str | float]) -> None: ...
    def save(self) -> None: ...


def _rows_from_records(
    header: list[str], records: Iterable[tuple[int, list]], columns: Columns
) -> list[CompanyRow]:
    idx = {h: i for i, h in enumerate(header) if h}
    if columns.name not in idx:
        raise KeyError(f"column '{columns.name}' not found in sheet header {header}")
    done_col = idx.get(output_headers(columns)["fit score"])
    out = []
    for row_id, values in records:

        def cell(col: str | None, values=values) -> str | None:
            if col is None or col not in idx or idx[col] >= len(values):
                return None
            v = values[idx[col]]
            return None if v is None or str(v).strip() == "" else str(v).strip()

        name = cell(columns.name)
        if not name:
            continue
        extra = {h: str(values[i]) for h, i in idx.items() if i < len(values) and values[i] not in (None, "")}
        out.append(
            CompanyRow(
                row_id=row_id,
                name=name,
                website=cell(columns.website),
                linkedin_url=cell(columns.linkedin),
                siren=cell(columns.siren),
                extra=extra,
                already_assessed=done_col is not None and cell(header[done_col]) is not None,
            )
        )
    return out


class ExcelSheet:
    def __init__(self, path: str | Path, columns: Columns, worksheet: str | None = None) -> None:
        self.path = Path(path)
        self.columns = columns
        self.wb = load_workbook(self.path)
        self.ws = self.wb[worksheet] if worksheet else self.wb.active
        self._ensure_output_columns()

    def _header(self) -> list[str]:
        return [str(c.value) if c.value is not None else "" for c in self.ws[1]]

    def _ensure_output_columns(self) -> None:
        header = self._header()
        while header and header[-1] == "":
            header.pop()
        for h in output_headers(self.columns).values():
            if h not in header:
                header.append(h)
                self.ws.cell(row=1, column=len(header), value=h)
        self._col = {h: i + 1 for i, h in enumerate(header)}

    def rows(self) -> list[CompanyRow]:
        records = ((i, [c.value for c in row]) for i, row in enumerate(self.ws.iter_rows(min_row=2), start=2))
        return _rows_from_records(self._header(), records, self.columns)

    def write(self, row_id: int, values: dict[str, str | float]) -> None:
        headers = output_headers(self.columns)
        for field, v in values.items():
            self.ws.cell(row=row_id, column=self._col[headers[field]], value=v)

    def save(self) -> None:
        self.wb.save(self.path)


class GoogleSheet:  # pragma: no cover - needs network and credentials
    def __init__(
        self,
        spreadsheet_id: str,
        columns: Columns,
        worksheet: str | None = None,
        credentials: str | None = None,
    ) -> None:
        try:
            import gspread
        except ImportError as e:
            raise ImportError(
                "Google Sheets support needs: pip install 'pe-origination-agents[sheets]'"
            ) from e
        gc = gspread.service_account(filename=credentials) if credentials else gspread.service_account()
        sh = gc.open_by_key(spreadsheet_id)
        self.ws = sh.worksheet(worksheet) if worksheet else sh.sheet1
        self.columns = columns
        self._pending: list[dict] = []
        header = self.ws.row_values(1)
        for h in output_headers(columns).values():
            if h not in header:
                header.append(h)
        self.ws.update(values=[header], range_name="A1")
        self._header = header
        self._col = {h: i + 1 for i, h in enumerate(header)}

    def rows(self) -> list[CompanyRow]:
        values = self.ws.get_all_values()
        return _rows_from_records(
            values[0], ((i, v) for i, v in enumerate(values[1:], start=2)), self.columns
        )

    def write(self, row_id: int, values: dict[str, str | float]) -> None:
        import gspread.utils as u

        headers = output_headers(self.columns)
        for field, v in values.items():
            a1 = u.rowcol_to_a1(row_id, self._col[headers[field]])
            self._pending.append({"range": a1, "values": [[v]]})

    def save(self) -> None:
        if self._pending:
            self.ws.batch_update(self._pending)  # one API call instead of one per cell
            self._pending = []
