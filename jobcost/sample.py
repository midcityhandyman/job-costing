"""Synthetic data generator.

Every job, vendor and number here is invented. Each scenario is scripted so the
demo shows one of each outcome: on track, watch, trending below, below target,
closed above target and closed below target. A seeded random source adds a
little week-to-week noise so the data does not look machine-perfect.
"""

from __future__ import annotations

import csv
import random
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

# Burdened labor cost per hour by trade. Invented round numbers.
LABOR_RATE = {
    "demolition": 46, "framing": 58, "drywall": 54, "tile": 62, "paint": 50,
    "flooring": 56, "cabinets": 60, "roofing": 64, "carpentry": 58, "decking": 57,
    "windows": 59, "cleanup": 42,
}


@dataclass(frozen=True)
class TradePlan:
    trade: str
    hours: float  # estimated labor hours
    material: float  # estimated material cost
    sub: float  # estimated subcontractor cost
    start: int  # week index the trade starts
    weeks: int  # weeks the trade takes to finish
    f0: float = 1.0  # cost factor at the start of the trade (1.0 means on estimate)
    f1: float = 1.0  # cost factor at the end of the trade
    report: bool = True  # False means nobody reports percent complete for this trade


@dataclass(frozen=True)
class JobPlan:
    job_id: str
    name: str
    status: str
    est_margin: float
    start: date
    elapsed: int  # weeks of history to generate
    trades: tuple[TradePlan, ...]
    target_pct: float | None = None
    unplanned: tuple[tuple[str, str, float], ...] = ()  # (trade, description, amount)


T = TradePlan
SCENARIOS = (
    JobPlan("J-101", "Sample Kitchen Remodel", "active", 0.41, date(2026, 8, 3), 5, (
        T("demolition", 40, 300, 0, 0, 1, 0.95, 0.95),
        T("electrical", 0, 0, 4200, 1, 2),
        T("plumbing", 0, 0, 3800, 1, 2, 1.02, 1.02),
        T("drywall", 60, 900, 0, 2, 2, 0.98, 0.98),
        T("cabinets", 70, 9500, 0, 3, 3),
        T("flooring", 36, 2800, 0, 5, 2),
        T("paint", 40, 450, 0, 6, 1),
    )),
    JobPlan("J-102", "Sample Bath Remodel", "active", 0.38, date(2026, 8, 17), 4, (
        T("demolition", 24, 200, 0, 0, 1),
        T("plumbing", 0, 0, 3200, 1, 2, 1.32, 1.40),
        T("electrical", 0, 0, 1400, 1, 1),
        T("tile", 64, 2100, 0, 2, 3, 1.22, 1.28),
        T("paint", 16, 180, 0, 5, 1),
    )),
    JobPlan("J-103", "Sample Basement Finish", "active", 0.40, date(2026, 7, 27), 6, (
        T("framing", 120, 4200, 0, 0, 3, 1.00, 1.10),
        T("electrical", 0, 0, 5200, 1, 4, 1.00, 1.16),
        T("plumbing", 0, 0, 2600, 2, 2, 1.04, 1.10),
        T("drywall", 110, 2600, 0, 3, 4, 1.06, 1.24),
        T("flooring", 60, 5400, 0, 7, 2),
        T("paint", 56, 700, 0, 8, 1),
    )),
    JobPlan("J-104", "Sample Deck Build", "complete", 0.34, date(2026, 6, 15), 3, (
        T("demolition", 16, 150, 0, 0, 1),
        T("decking", 96, 6800, 0, 0, 3, 0.95, 0.97),
        T("electrical", 0, 0, 900, 2, 1),
    ), target_pct=30.0),
    JobPlan("J-105", "Sample Rental Turnover", "complete", 0.37, date(2026, 7, 6), 2, (
        T("paint", 72, 850, 0, 0, 2, 1.18, 1.24),
        T("flooring", 40, 3600, 0, 0, 2, 1.12, 1.16),
        T("cleanup", 20, 120, 0, 1, 1),
    )),
    JobPlan("J-106", "Sample Roof Repair", "active", 0.43, date(2026, 9, 7), 1, (
        T("roofing", 80, 5200, 0, 0, 8),
        T("carpentry", 24, 600, 0, 0, 3, report=False),
    ), unplanned=(("permits", "Permit fee, not in estimate", 185.0),)),
    JobPlan("J-107", "Sample Window Replacement", "active", 0.37, date(2026, 8, 10), 4, (
        T("windows", 64, 11200, 0, 0, 5, 0.99, 1.01),
        T("carpentry", 30, 800, 0, 1, 5),
        T("paint", 20, 240, 0, 5, 1),
    )),
)

JOB_COLS = ["job_id", "name", "contract_amount", "status", "target_margin_pct"]
EST_COLS = ["job_id", "trade", "cost_type", "description", "quantity", "unit_cost", "amount"]
ACT_COLS = ["job_id", "date", "trade", "cost_type", "description", "hours", "rate", "amount", "vendor"]
PROG_COLS = ["job_id", "trade", "as_of", "percent_complete"]


def _done(plan: TradePlan, week: int) -> float:
    """Fraction of the trade finished by the end of the given week index."""
    return min(max((week - plan.start + 1) / plan.weeks, 0.0), 1.0)


def _estimate_cost(plan: TradePlan) -> float:
    return plan.hours * LABOR_RATE.get(plan.trade, 55) + plan.material + plan.sub


def generate(seed: int = 7) -> dict[str, list[list]]:
    """Return rows for each CSV file, keyed by file name (header row first)."""
    rng = random.Random(seed)
    jobs, estimates, actuals, progress = [JOB_COLS], [EST_COLS], [ACT_COLS], [PROG_COLS]

    for job in SCENARIOS:
        cost = sum(_estimate_cost(t) for t in job.trades)
        contract = round(cost / (1 - job.est_margin) / 50) * 50
        jobs.append([job.job_id, job.name, f"{contract:.2f}", job.status,
                     "" if job.target_pct is None else f"{job.target_pct:g}"])

        for plan in job.trades:
            rate = LABOR_RATE.get(plan.trade, 55)
            title = plan.trade.title()
            if plan.hours:
                estimates.append([job.job_id, plan.trade, "labor", f"{title} labor",
                                  f"{plan.hours:g}", f"{rate:.2f}", ""])
            if plan.material:
                estimates.append([job.job_id, plan.trade, "material", f"{title} materials",
                                  "", "", f"{plan.material:.2f}"])
            if plan.sub:
                estimates.append([job.job_id, plan.trade, "sub", f"{title} subcontract",
                                  "", "", f"{plan.sub:.2f}"])

        for week in range(job.elapsed):
            monday = job.start + timedelta(weeks=week)
            friday = monday + timedelta(days=4)
            for plan in job.trades:
                before, after = _done(plan, week - 1), _done(plan, week)
                step = after - before
                if step <= 0:
                    continue
                midpoint = (before + after) / 2
                factor = plan.f0 + (plan.f1 - plan.f0) * midpoint
                rate = LABOR_RATE.get(plan.trade, 55)
                title = plan.trade.title()
                if plan.hours:
                    hours = round(plan.hours * step * factor * rng.uniform(0.98, 1.02), 1)
                    actuals.append([job.job_id, (monday + timedelta(days=3)).isoformat(), plan.trade,
                                    "labor", f"{title} crew, week {week + 1}",
                                    f"{hours:g}", f"{rate:.2f}", "", ""])
                if plan.material:
                    amount = plan.material * step * factor * rng.uniform(0.98, 1.02)
                    actuals.append([job.job_id, (monday + timedelta(days=1)).isoformat(), plan.trade,
                                    "material", f"{title} materials, week {week + 1}",
                                    "", "", f"{amount:.2f}", "Example Building Supply"])
                if plan.sub:
                    amount = plan.sub * step * factor * rng.uniform(0.98, 1.02)
                    actuals.append([job.job_id, friday.isoformat(), plan.trade,
                                    "sub", f"{title} progress invoice {week + 1}",
                                    "", "", f"{amount:.2f}", f"Sample {title} Subcontractor"])
                if plan.report:
                    progress.append([job.job_id, plan.trade, friday.isoformat(), f"{after * 100:.0f}"])

        for trade, description, amount in job.unplanned:
            actuals.append([job.job_id, (job.start + timedelta(days=2)).isoformat(), trade,
                            "material", description, "", "", f"{amount:.2f}", "Example Permit Office"])

    return {"jobs.csv": jobs, "estimates.csv": estimates, "actuals.csv": actuals, "progress.csv": progress}


def write_sample(out_dir: str | Path, seed: int = 7) -> dict[str, int]:
    """Write the four CSV files. Returns the data row count per file."""
    folder = Path(out_dir)
    folder.mkdir(parents=True, exist_ok=True)
    counts = {}
    for name, rows in generate(seed).items():
        with open(folder / name, "w", newline="", encoding="utf-8") as handle:
            csv.writer(handle, lineterminator="\n").writerows(rows)
        counts[name] = len(rows) - 1
    return counts
