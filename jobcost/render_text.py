"""Plain ASCII tables for the terminal."""

from __future__ import annotations

from decimal import Decimal

from .costing import METHOD_NOTES, JobCost, Portfolio
from .models import COST_TYPES, Issue


def money(value: Decimal) -> str:
    rounded = int(value.quantize(Decimal("1")))
    return f"-${-rounded:,}" if rounded < 0 else f"${rounded:,}"


def signed_money(value: Decimal) -> str:
    rounded = int(value.quantize(Decimal("1")))
    if rounded == 0:
        return "$0"
    return f"+${rounded:,}" if rounded > 0 else f"-${-rounded:,}"


def pct(value: float | None, digits: int = 1) -> str:
    return "n/a" if value is None else f"{value * 100:.{digits}f}%"


def points(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:+.1f}"


def table(headers: list[str], rows: list[list[str]], left: int = 1) -> str:
    """Render rows under headers. The first `left` columns are left-aligned, the rest right."""
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))

    def fmt(cells: list[str]) -> str:
        parts = [
            cell.ljust(widths[i]) if i < left else cell.rjust(widths[i])
            for i, cell in enumerate(cells)
        ]
        return "  ".join(parts).rstrip()

    rule = "  ".join("-" * w for w in widths)
    return "\n".join([fmt(headers), rule] + [fmt(r) for r in rows])


def render_summary(portfolio: Portfolio) -> str:
    rows = [
        [
            j.job.job_id,
            j.job.name[:30],
            "closed" if j.final else pct(j.pct_complete, 0),
            money(j.job.contract),
            money(j.estimate),
            money(j.actual),
            money(j.forecast),
            pct(j.est_margin),
            pct(j.proj_margin),
            points(j.drift),
            pct(j.target, 0),
            j.status,
        ]
        for j in portfolio.jobs
    ]
    rows.append(
        [
            "TOTAL",
            f"{len(portfolio.jobs)} jobs",
            "",
            money(portfolio.contract),
            money(portfolio.estimate),
            money(portfolio.actual),
            money(portfolio.forecast),
            pct(portfolio.est_margin),
            pct(portfolio.proj_margin),
            points(portfolio.proj_margin - portfolio.est_margin),
            "",
            "",
        ]
    )
    headers = [
        "Job", "Name", "Done", "Contract", "Estimate", "Actual", "Forecast",
        "Est mgn", "Proj mgn", "Drift", "Target", "Status",
    ]
    counts = portfolio.counts()
    lines = [
        f"Job costing summary as of {portfolio.as_of or 'n/a'} "
        f"(default target margin {pct(portfolio.settings.target_margin, 0)})",
        "",
        table(headers, rows, left=2),
        "",
        "Status counts: " + ", ".join(f"{name} {count}" for name, count in counts.items()),
        "Drift is projected margin minus estimated margin, in margin points.",
    ]
    return "\n".join(lines)


def render_trades(portfolio: Portfolio) -> str:
    rows = [
        [
            r.trade.title(),
            str(r.jobs),
            money(r.estimate),
            money(r.actual),
            money(r.forecast),
            signed_money(r.variance),
            "n/a" if r.variance_pct is None else f"{r.variance_pct * 100:+.1f}%",
        ]
        for r in portfolio.by_trade()
    ]
    headers = ["Trade", "Jobs", "Estimate", "Actual", "Forecast", "Variance", "Var %"]
    return "\n".join(
        [
            "Estimate versus forecast by trade, all jobs (worst overrun first)",
            "",
            table(headers, rows),
        ]
    )


def render_job(job: JobCost) -> str:
    rows = []
    for t in job.trades:
        rows.append(
            [
                t.trade.title(),
                pct(t.pct_complete, 0) if t.pct_complete is not None else "none",
                money(t.estimate),
                money(t.actual),
                money(t.forecast),
                signed_money(t.variance),
                "n/a" if t.variance_pct is None else f"{t.variance_pct * 100:+.1f}%",
                f"{t.est_hours:.0f}/{t.act_hours:.0f}",
                t.method,
            ]
        )
    rows.append(
        [
            "TOTAL",
            pct(job.pct_complete, 0),
            money(job.estimate),
            money(job.actual),
            money(job.forecast),
            signed_money(job.forecast - job.estimate),
            f"{float((job.forecast - job.estimate) / job.estimate) * 100:+.1f}%" if job.estimate else "n/a",
            "",
            "",
        ]
    )
    headers = ["Trade", "Done", "Estimate", "Actual", "Forecast", "Variance", "Var %", "Hrs est/act", "Method"]
    type_rows = []
    for cost_type in COST_TYPES:
        est = sum((t.est_by_type[cost_type] for t in job.trades), Decimal("0"))
        act = sum((t.act_by_type[cost_type] for t in job.trades), Decimal("0"))
        type_rows.append([cost_type, money(est), money(act)])

    lines = [
        f"{job.job.job_id}  {job.job.name}  [{job.status}]",
        f"Contract {money(job.job.contract)} | job status {job.job.status} | "
        f"last activity {job.last_activity or 'n/a'}",
        f"Estimated margin {pct(job.est_margin)} | projected margin {pct(job.proj_margin)} | "
        f"drift {points(job.drift)} pts | target {pct(job.target, 0)}",
    ]
    if job.trend_margin is not None:
        lines.append(
            f"Trend: {job.trend_slope * 10:+.1f} pts per 10% of progress, "
            f"finishing near {pct(job.trend_margin)} if it holds"
        )
    lines += ["", table(headers, rows), "", table(["Cost type", "Estimate", "Actual"], type_rows)]
    if job.snapshots:
        snap_rows = [
            [s.as_of.isoformat(), pct(s.pct_complete, 0), money(s.actual), money(s.forecast), pct(s.proj_margin)]
            for s in job.snapshots
        ]
        lines += ["", "Margin history", table(["As of", "Done", "Actual", "Forecast", "Proj mgn"], snap_rows)]
    if job.reasons:
        lines += ["", "Why:"] + [f"  - {reason}" for reason in job.reasons]
    used = sorted({t.method for t in job.trades})
    lines += ["", "Forecast methods:"] + [f"  {m}: {METHOD_NOTES[m]}" for m in used]
    return "\n".join(lines)


def render_flags(portfolio: Portfolio) -> str:
    flagged = portfolio.flagged
    watch = [j for j in portfolio.jobs if j.status == "WATCH"]
    if not flagged and not watch:
        return f"No jobs flagged. All {len(portfolio.jobs)} jobs are projected at or above target."
    lines = [f"{len(flagged)} job(s) flagged, {len(watch)} on watch (as of {portfolio.as_of or 'n/a'})"]
    for job in flagged + watch:
        lines.append("")
        lines.append(
            f"[{job.status}] {job.job.job_id} {job.job.name}: "
            f"projected {pct(job.proj_margin)} vs target {pct(job.target, 0)} "
            f"(estimated {pct(job.est_margin)}, drift {points(job.drift)} pts)"
        )
        lines += [f"    - {reason}" for reason in job.reasons]
    return "\n".join(lines)


def render_issues(issues: list[Issue]) -> str:
    if not issues:
        return "No import issues."
    return "\n".join([f"{len(issues)} row(s) skipped during import:"] + [f"  {issue}" for issue in issues])
