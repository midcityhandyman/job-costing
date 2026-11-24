"""Plain data records shared by the importer, the costing engine and the renderers."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

COST_TYPES = ("labor", "material", "sub")
JOB_STATUSES = ("active", "complete")


@dataclass(frozen=True)
class Job:
    job_id: str
    name: str
    contract: Decimal
    status: str = "active"
    # Per-job target margin as a fraction (0.35 means 35%). None means "use the default".
    target_margin: float | None = None


@dataclass(frozen=True)
class EstimateLine:
    job_id: str
    trade: str
    cost_type: str
    description: str
    hours: Decimal
    amount: Decimal


@dataclass(frozen=True)
class ActualEntry:
    job_id: str
    entry_date: date
    trade: str
    cost_type: str
    description: str
    hours: Decimal
    amount: Decimal
    vendor: str = ""


@dataclass(frozen=True)
class ProgressEntry:
    """Field-reported percent complete for one trade on one job, as a fraction 0..1."""

    job_id: str
    trade: str
    as_of: date
    pct: float


@dataclass(frozen=True)
class Issue:
    """A row that could not be imported. Line numbers are 1-based and include the header."""

    file: str
    line: int
    message: str

    def __str__(self) -> str:
        return f"{self.file}:{self.line}: {self.message}"


@dataclass
class Dataset:
    jobs: dict[str, Job] = field(default_factory=dict)
    estimates: list[EstimateLine] = field(default_factory=list)
    actuals: list[ActualEntry] = field(default_factory=list)
    progress: list[ProgressEntry] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)
