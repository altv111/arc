from __future__ import annotations

import csv
import hashlib
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field


@dataclass(frozen=True)
class Dataset:
    rows: tuple[Any, ...]
    content_hash: str


class CompletenessSummaryRow(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    cobdate: date
    ubr_level_8: str = Field(validation_alias="UBR Level 8 Name")
    ubr_level_8_id: str = Field(validation_alias="UBR Level 8 ID")
    ubr_level_9: str = Field(validation_alias="UBR Level 9 Name")
    vcs_run_qualifier: str = Field(validation_alias="VCS Run Qualifier")
    vcs_calc_type: str = Field(validation_alias="VCS Calc Type")
    portfolio: str = Field(validation_alias="Portfolio Name")
    book_id: str = Field(validation_alias="Trader Book Name")
    frequency: str = Field(validation_alias="Frequency")
    trade_count_expected: int = Field(validation_alias="Trade Count Expected")
    trade_count_error_partial: int = Field(validation_alias="Trade Count Error Partial")
    trade_count_error_full: int = Field(validation_alias="Trade Count Error Full")
    trade_count_match: int = Field(validation_alias="Trade Count Match")
    trade_count_not_expected: int = Field(validation_alias="Trade Count Not Expected")
    trade_count_not_received: int = Field(validation_alias="Trade Count Not Received")
    trade_count_received: int = Field(validation_alias="Trade Count Received")


class BookCompletenessRow(BaseModel):
    book_id: str
    ubr_level_8: str
    ubr_level_9: str
    portfolio: str


class MVarRow(BaseModel):
    book_id: str
    ubr_level_8: str
    ubr_level_9: str
    portfolio: str
    mvar: float | None = None
    parent_var: float | None = None


class DodVarExtractRow(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    var_type: str = Field(validation_alias="VARType")
    ubr_level_8: str = Field(validation_alias="UBR Level 8 Name")
    ubr_level_9: str = Field(validation_alias="UBR Level 9 Name")
    portfolio: str = Field(validation_alias="Portfolio Name")
    book_id: str = Field(validation_alias="Trader Book Name")
    curr_1d_var: float = Field(validation_alias="Curr1DVaR")
    prev_1d_var: float = Field(validation_alias="Prev1DVAR")
    diff_1d_var_pct: float = Field(validation_alias="Diff1DVARPct")
    curr_10d_var: float = Field(validation_alias="Curr10DVaR")
    prev_10d_var: float = Field(validation_alias="Prev10DVAR")
    diff_10d_var_pct: float = Field(validation_alias="Diff10DVARPct")
    curr_10d_svar: float = Field(validation_alias="Curr1DSVaR")
    prev_10d_svar: float = Field(validation_alias="Prev1DSVAR")
    diff_10d_svar_pct: float = Field(validation_alias="Diff1DSVARPct")


class SensiRow(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    cobdate: date
    methodology: str
    portfolio: str = Field(validation_alias="portfolio_name")
    ubr_level_8: str = Field(validation_alias="UBR Name Level 08")
    ubr_level_9: str = Field(validation_alias="UBR Name Level 09")
    book_id: str = Field(validation_alias="Trade Book Name")
    risk_type: str = Field(validation_alias="Risk Type")
    sensitivity_type: str = Field(validation_alias="Sensitivity Type")
    trade_book_id: str = Field(validation_alias="Trade Book ID")
    sensitivity_amount_eur: float = Field(validation_alias="Sensitivity Amount Eur")


@runtime_checkable
class ReportingClient(Protocol):
    def get_completeness_summary(self, ba: str, business_date: date, **params: Any) -> Dataset:
        ...

    def get_mvar(self, ba: str, business_date: date, **params: Any) -> Dataset:
        ...

    def get_dod_var_extract(self, ba: str, business_date: date, **params: Any) -> Dataset:
        ...

    def get_rf_sensi(self, ba: str, business_date: date, **params: Any) -> Dataset:
        ...

    def get_kannon_sensi(self, ba: str, business_date: date, **params: Any) -> Dataset:
        ...


class CSVReportingClient:
    """Fixture-backed reporting client for local development."""

    def __init__(self, fixtures_root: Path) -> None:
        self.fixtures_root = Path(fixtures_root)

    def _read_csv(self, filename: str, model: type[BaseModel]) -> Dataset:
        path = self.fixtures_root / filename
        raw = path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        with path.open(newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            rows = tuple(model.model_validate(dict(row)) for row in reader)
        return Dataset(rows=rows, content_hash=digest)

    def get_completeness_summary(self, ba: str, business_date: date, **params: Any) -> Dataset:
        return self._read_csv("completeness_summary.csv", CompletenessSummaryRow)

    def get_mvar(self, ba: str, business_date: date, **params: Any) -> Dataset:
        return self._read_csv("mvar.csv", MVarRow)

    def get_dod_var_extract(self, ba: str, business_date: date, **params: Any) -> Dataset:
        grain = params.get("grain") or "portfolio"
        return self._read_csv(f"dod_var_extract__{_safe_grain(grain)}.csv", DodVarExtractRow)

    def get_rf_sensi(self, ba: str, business_date: date, **params: Any) -> Dataset:
        return self._read_csv("rf_sensi.csv", SensiRow)

    def get_kannon_sensi(self, ba: str, business_date: date, **params: Any) -> Dataset:
        return self._read_csv("kannon_sensi.csv", SensiRow)


def _safe_grain(grain: Any) -> str:
    return str(grain).lower().replace(" ", "_")
