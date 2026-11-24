"""CSV import with row-level validation.

A data folder holds four files:

    jobs.csv       job_id, name, contract_amount, [status], [target_margin_pct]
    estimates.csv  job_id, trade, cost_type, [description], [quantity], [unit_cost], [amount]
    actuals.csv    job_id, date, trade, cost_type, [description], [hours], [rate], [amount], [vendor]
    progress.csv   job_id, trade, as_of, percent_complete            (optional file)

Bad rows are collected as issues with their line number instead of stopping the
import, so one typo in a 4,000 line export does not block the whole report.
Pass strict=True to turn any issue into an error.
"""

from __future__ import annotations

import csv
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterator

from .models import (
    COST_TYPES,
    JOB_STATUSES,
    ActualEntry,
    Dataset,
    EstimateLine,
    Issue,
    Job,
    ProgressEntry,
)

ZERO = Decimal("0")
CENT = Decimal("0.01")

REQUIRED = {
    "jobs.csv": ("job_id", "name", "contract_amount"),
    "estimates.csv": ("job_id", "trade", "cost_type"),
    "actuals.csv": ("job_id", "date", "trade", "cost_type"),
    "progress.csv": ("job_id", "trade", "as_of", "percent_complete"),
}

COST_TYPE_ALIASES = {
    "labor": "labor",
    "labour": "labor",
    "material": "material",
    "materials": "material",
    "sub": "sub",
    "subs": "sub",
    "subcontractor": "sub",
    "subcontract": "sub",
}

STATUS_ALIASES = {
    "": "active",
    "active": "active",
    "open": "active",
    "in progress": "active",
    "complete": "complete",
    "completed": "complete",
    "closed": "complete",
}

DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y")


class DataError(Exception):
    """Raised when a file is unusable, or when strict mode finds bad rows."""

    def __init__(self, message: str, issues: list[Issue] | None = None):
        super().__init__(message)
        self.issues = issues or []


# ---------------------------------------------------------------- field parsers


def parse_money(text: str, *, blank: Decimal | None = None) -> Decimal:
    """Parse "$1,234.50", "1234.5" or "(250.00)" (accounting negative)."""
    raw = text.strip()
    if raw == "":
        if blank is None:
            raise ValueError("amount is blank")
        return blank
    negative = raw.startswith("(") and raw.endswith(")")
    cleaned = raw.strip("()").replace("$", "").replace(",", "").strip()
    try:
        value = Decimal(cleaned)
    except InvalidOperation:
        raise ValueError(f"not a number: {text!r}") from None
    if not value.is_finite():
        raise ValueError(f"not a number: {text!r}")
    return -value if negative else value


def parse_percent(text: str) -> float:
    """Parse a value in percent units ("35", "35%", "62.5") into a fraction."""
    cleaned = text.strip().rstrip("%").strip()
    try:
        value = float(cleaned)
    except ValueError:
        raise ValueError(f"not a percentage: {text!r}") from None
    if value != value or value in (float("inf"), float("-inf")):
        raise ValueError(f"not a percentage: {text!r}")
    return value / 100.0


def parse_date(text: str) -> date:
    raw = text.strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"not a date (use YYYY-MM-DD): {text!r}")


def normalize_trade(text: str) -> str:
    trade = " ".join(text.strip().lower().split())
    if not trade:
        raise ValueError("trade is blank")
    return trade


def normalize_cost_type(text: str) -> str:
    key = text.strip().lower()
    if key not in COST_TYPE_ALIASES:
        raise ValueError(f"cost_type must be one of {', '.join(COST_TYPES)}; got {text!r}")
    return COST_TYPE_ALIASES[key]


def _cents(value: Decimal) -> Decimal:
    return value.quantize(CENT)


# ------------------------------------------------------------------ file reader


def _rows(path: Path) -> Iterator[tuple[int, dict[str, str]]]:
    """Yield (line_number, row) with normalized header names. Blank lines are skipped."""
    required = REQUIRED[path.name]
    with open(path, newline="", encoding="utf-8-sig") as handle:
        reader = csv.reader(handle)
        try:
            header = next(reader)
        except StopIteration:
            raise DataError(f"{path.name}: file is empty") from None
        columns = [h.strip().lower().replace(" ", "_") for h in header]
        missing = [c for c in required if c not in columns]
        if missing:
            raise DataError(f"{path.name}: missing column(s): {', '.join(missing)}")
        for row in reader:
            if not any(cell.strip() for cell in row):
                continue
            values = {c: (row[i].strip() if i < len(row) else "") for i, c in enumerate(columns)}
            yield reader.line_num, values


# ------------------------------------------------------------------ row parsers


def _job(row: dict[str, str], seen: dict[str, Job]) -> Job:
    job_id = row["job_id"]
    if not job_id:
        raise ValueError("job_id is blank")
    if job_id in seen:
        raise ValueError(f"duplicate job_id {job_id!r}")
    contract = parse_money(row["contract_amount"])
    if contract <= 0:
        raise ValueError("contract_amount must be greater than zero")
    status_key = row.get("status", "").lower()
    if status_key not in STATUS_ALIASES:
        raise ValueError(f"status must be one of {', '.join(JOB_STATUSES)}; got {row['status']!r}")
    target = None
    if row.get("target_margin_pct", ""):
        target = parse_percent(row["target_margin_pct"])
        if not 0 < target < 1:
            raise ValueError("target_margin_pct must be between 0 and 100")
    return Job(
        job_id=job_id,
        name=row["name"] or job_id,
        contract=_cents(contract),
        status=STATUS_ALIASES[status_key],
        target_margin=target,
    )


def _known_job(row: dict[str, str], jobs: dict[str, Job]) -> str:
    job_id = row["job_id"]
    if job_id not in jobs:
        raise ValueError(f"unknown job_id {job_id!r}")
    return job_id


def _estimate(row: dict[str, str], jobs: dict[str, Job]) -> EstimateLine:
    job_id = _known_job(row, jobs)
    trade = normalize_trade(row["trade"])
    cost_type = normalize_cost_type(row["cost_type"])
    quantity = parse_money(row.get("quantity", ""), blank=ZERO)
    unit_cost = parse_money(row.get("unit_cost", ""), blank=ZERO)
    if row.get("amount", ""):
        amount = parse_money(row["amount"])
    elif row.get("quantity", "") and row.get("unit_cost", ""):
        amount = quantity * unit_cost
    else:
        raise ValueError("give either amount, or both quantity and unit_cost")
    if amount < 0:
        raise ValueError("estimate amount cannot be negative")
    return EstimateLine(
        job_id=job_id,
        trade=trade,
        cost_type=cost_type,
        description=row.get("description", ""),
        hours=quantity if cost_type == "labor" else ZERO,
        amount=_cents(amount),
    )


def _actual(row: dict[str, str], jobs: dict[str, Job]) -> ActualEntry:
    job_id = _known_job(row, jobs)
    entry_date = parse_date(row["date"])
    trade = normalize_trade(row["trade"])
    cost_type = normalize_cost_type(row["cost_type"])
    hours = parse_money(row.get("hours", ""), blank=ZERO)
    if row.get("amount", ""):
        # Negative amounts are allowed: returns and vendor credits reduce cost.
        amount = parse_money(row["amount"])
    elif cost_type == "labor" and row.get("hours", "") and row.get("rate", ""):
        amount = hours * parse_money(row["rate"])
    else:
        raise ValueError("give amount, or hours and rate for a labor entry")
    return ActualEntry(
        job_id=job_id,
        entry_date=entry_date,
        trade=trade,
        cost_type=cost_type,
        description=row.get("description", ""),
        hours=hours if cost_type == "labor" else ZERO,
        amount=_cents(amount),
        vendor=row.get("vendor", ""),
    )


def _progress(row: dict[str, str], jobs: dict[str, Job]) -> ProgressEntry:
    job_id = _known_job(row, jobs)
    pct = parse_percent(row["percent_complete"])
    if not 0 <= pct <= 1:
        raise ValueError("percent_complete must be between 0 and 100")
    return ProgressEntry(
        job_id=job_id,
        trade=normalize_trade(row["trade"]),
        as_of=parse_date(row["as_of"]),
        pct=pct,
    )


# -------------------------------------------------------------------- entry point


def load_dataset(data_dir: str | Path, *, strict: bool = False) -> Dataset:
    folder = Path(data_dir)
    if not folder.is_dir():
        raise DataError(f"data folder not found: {folder}")
    for name in ("jobs.csv", "estimates.csv", "actuals.csv"):
        if not (folder / name).is_file():
            raise DataError(f"missing required file: {folder / name}")

    data = Dataset()

    def collect(name: str, parse, sink) -> None:
        for line, row in _rows(folder / name):
            try:
                sink(parse(row))
            except ValueError as exc:
                data.issues.append(Issue(name, line, str(exc)))

    def add_job(job: Job) -> None:
        data.jobs[job.job_id] = job

    collect("jobs.csv", lambda row: _job(row, data.jobs), add_job)
    collect("estimates.csv", lambda row: _estimate(row, data.jobs), data.estimates.append)
    collect("actuals.csv", lambda row: _actual(row, data.jobs), data.actuals.append)
    if (folder / "progress.csv").is_file():
        collect("progress.csv", lambda row: _progress(row, data.jobs), data.progress.append)

    if not data.jobs:
        raise DataError("jobs.csv has no usable rows", data.issues)
    if strict and data.issues:
        raise DataError(f"{len(data.issues)} row(s) failed validation", data.issues)
    return data
