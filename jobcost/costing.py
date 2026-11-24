"""The costing engine: roll up by trade, forecast cost at completion, measure drift, flag.

Vocabulary used throughout:

    estimate          cost the job was priced on
    actual            cost booked so far
    forecast          expected cost when the trade (or job) is finished
    estimated margin  (contract - estimate) / contract
    projected margin  (contract - forecast) / contract
    drift             projected margin minus estimated margin, in margin points
    trend             slope of projected margin against job progress across snapshots
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from .config import Settings
from .models import COST_TYPES, ActualEntry, Dataset, EstimateLine, Job, ProgressEntry

ZERO = Decimal("0")
CENT = Decimal("0.01")

BELOW = "BELOW"
TRENDING = "TRENDING"
WATCH = "WATCH"
OK = "OK"
STATUS_ORDER = {BELOW: 0, TRENDING: 1, WATCH: 2, OK: 3}

METHOD_NOTES = {
    "final": "job closed, forecast is actual cost",
    "complete": "trade reported 100% complete, forecast is actual cost",
    "earned-value": "actual cost divided by reported percent complete",
    "early": "too early to extrapolate, remaining work held at estimate",
    "no-progress": "no progress reported, greater of estimate and actual",
}


def cents(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def margin(contract: Decimal, cost: Decimal) -> float:
    return float((contract - cost) / contract)


@dataclass
class TradeCost:
    trade: str
    est_by_type: dict[str, Decimal]
    act_by_type: dict[str, Decimal]
    est_hours: Decimal
    act_hours: Decimal
    pct_complete: float | None
    forecast: Decimal
    method: str

    @property
    def estimate(self) -> Decimal:
        return sum(self.est_by_type.values(), ZERO)

    @property
    def actual(self) -> Decimal:
        return sum(self.act_by_type.values(), ZERO)

    @property
    def variance(self) -> Decimal:
        """Forecast minus estimate. Positive means the trade is running over."""
        return self.forecast - self.estimate

    @property
    def variance_pct(self) -> float | None:
        if self.estimate == 0:
            return None
        return float(self.variance / self.estimate)


@dataclass(frozen=True)
class Snapshot:
    as_of: date
    pct_complete: float
    actual: Decimal
    forecast: Decimal
    proj_margin: float


@dataclass
class JobCost:
    job: Job
    trades: list[TradeCost]
    estimate: Decimal
    actual: Decimal
    forecast: Decimal
    pct_complete: float
    target: float
    final: bool
    last_activity: date | None
    snapshots: list[Snapshot] = field(default_factory=list)
    trend_slope: float | None = None  # margin change per 100% of job progress
    trend_margin: float | None = None  # margin at completion if the trend holds
    status: str = OK
    reasons: list[str] = field(default_factory=list)

    @property
    def est_margin(self) -> float:
        return margin(self.job.contract, self.estimate)

    @property
    def proj_margin(self) -> float:
        return margin(self.job.contract, self.forecast)

    @property
    def drift(self) -> float:
        return self.proj_margin - self.est_margin

    @property
    def flagged(self) -> bool:
        return self.status in (BELOW, TRENDING)


@dataclass
class TradeRollup:
    trade: str
    jobs: int = 0
    estimate: Decimal = ZERO
    actual: Decimal = ZERO
    forecast: Decimal = ZERO

    @property
    def variance(self) -> Decimal:
        return self.forecast - self.estimate

    @property
    def variance_pct(self) -> float | None:
        if self.estimate == 0:
            return None
        return float(self.variance / self.estimate)


@dataclass
class Portfolio:
    jobs: list[JobCost]
    settings: Settings
    as_of: date | None

    def _total(self, name: str) -> Decimal:
        return sum((getattr(j, name) for j in self.jobs), ZERO)

    @property
    def contract(self) -> Decimal:
        return sum((j.job.contract for j in self.jobs), ZERO)

    @property
    def estimate(self) -> Decimal:
        return self._total("estimate")

    @property
    def actual(self) -> Decimal:
        return self._total("actual")

    @property
    def forecast(self) -> Decimal:
        return self._total("forecast")

    @property
    def est_margin(self) -> float:
        return margin(self.contract, self.estimate)

    @property
    def proj_margin(self) -> float:
        return margin(self.contract, self.forecast)

    @property
    def flagged(self) -> list[JobCost]:
        return [j for j in self.jobs if j.flagged]

    def counts(self) -> dict[str, int]:
        result = {status: 0 for status in STATUS_ORDER}
        for job in self.jobs:
            result[job.status] += 1
        return result

    def by_trade(self) -> list[TradeRollup]:
        """Estimate versus forecast for each trade across every job, worst overrun first."""
        rollups: dict[str, TradeRollup] = {}
        for job in self.jobs:
            for trade in job.trades:
                roll = rollups.setdefault(trade.trade, TradeRollup(trade.trade))
                roll.jobs += 1
                roll.estimate += trade.estimate
                roll.actual += trade.actual
                roll.forecast += trade.forecast
        return sorted(rollups.values(), key=lambda r: (-r.variance, r.trade))


# ------------------------------------------------------------------ forecasting


def forecast_trade(
    estimate: Decimal,
    actual: Decimal,
    pct: float | None,
    *,
    final: bool = False,
    min_progress: float = 0.15,
) -> tuple[Decimal, str]:
    """Forecast one trade's cost at completion. Returns (forecast, method)."""
    if final:
        return actual, "final"
    if pct is None:
        return max(estimate, actual), "no-progress"
    if pct >= 1:
        return actual, "complete"
    if pct < min_progress:
        remaining = estimate * Decimal(str(1 - pct))
        return cents(actual + remaining), "early"
    return cents(actual / Decimal(str(pct))), "earned-value"


def _job_progress(trades: list[TradeCost]) -> float:
    """Estimate-weighted percent complete. Trades with no report fall back to cost-to-cost."""
    weight = sum((t.estimate for t in trades), ZERO)
    if weight == 0:
        return 0.0
    earned = 0.0
    for trade in trades:
        if trade.estimate == 0:
            continue
        if trade.pct_complete is not None:
            pct = trade.pct_complete
        else:
            pct = min(max(float(trade.actual / trade.estimate), 0.0), 1.0)
        earned += float(trade.estimate) * pct
    return earned / float(weight)


def _evaluate(
    estimates: list[EstimateLine],
    actuals: list[ActualEntry],
    progress: list[ProgressEntry],
    settings: Settings,
    cutoff: date | None,
    final: bool,
) -> list[TradeCost]:
    order: list[str] = []
    est = defaultdict(lambda: {t: ZERO for t in COST_TYPES})
    act = defaultdict(lambda: {t: ZERO for t in COST_TYPES})
    est_hours: dict[str, Decimal] = defaultdict(lambda: ZERO)
    act_hours: dict[str, Decimal] = defaultdict(lambda: ZERO)
    latest: dict[str, tuple[date, float]] = {}

    def seen(trade: str) -> None:
        if trade not in order:
            order.append(trade)

    for line in estimates:
        seen(line.trade)
        est[line.trade][line.cost_type] += line.amount
        est_hours[line.trade] += line.hours
    for entry in actuals:
        if cutoff and entry.entry_date > cutoff:
            continue
        seen(entry.trade)
        act[entry.trade][entry.cost_type] += entry.amount
        act_hours[entry.trade] += entry.hours
    for report in progress:
        if cutoff and report.as_of > cutoff:
            continue
        seen(report.trade)
        if report.trade not in latest or report.as_of >= latest[report.trade][0]:
            latest[report.trade] = (report.as_of, report.pct)

    trades = []
    for trade in order:
        estimate = sum(est[trade].values(), ZERO)
        actual = sum(act[trade].values(), ZERO)
        pct = latest[trade][1] if trade in latest else None
        forecast, method = forecast_trade(
            estimate, actual, pct, final=final, min_progress=settings.min_progress
        )
        trades.append(
            TradeCost(
                trade=trade,
                est_by_type=dict(est[trade]),
                act_by_type=dict(act[trade]),
                est_hours=est_hours[trade],
                act_hours=act_hours[trade],
                pct_complete=1.0 if final else pct,
                forecast=forecast,
                method=method,
            )
        )
    return trades


# ------------------------------------------------------------------------ trend


def fit_slope(points: list[tuple[float, float]]) -> float | None:
    """Least-squares slope of y on x. None when x does not vary."""
    n = len(points)
    if n < 2:
        return None
    mean_x = sum(x for x, _ in points) / n
    mean_y = sum(y for _, y in points) / n
    spread = sum((x - mean_x) ** 2 for x, _ in points)
    if spread < 1e-12:
        return None
    return sum((x - mean_x) * (y - mean_y) for x, y in points) / spread


def trend(snapshots: list[Snapshot], window: int) -> float | None:
    """Margin change per 100% of job progress over the most recent snapshots.

    Needs at least three snapshots covering at least ten points of progress,
    otherwise one noisy week would read as a trend.
    """
    recent = snapshots[-window:]
    if len(recent) < 3:
        return None
    if recent[-1].pct_complete - recent[0].pct_complete < 0.10:
        return None
    return fit_slope([(s.pct_complete, s.proj_margin) for s in recent])


# ------------------------------------------------------------------------ flags


def _pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def trade_overruns(job: JobCost, settings: Settings) -> list[TradeCost]:
    """Trades whose forecast is over estimate by both the percent and dollar thresholds."""
    hits = []
    for trade in job.trades:
        if trade.variance < settings.trade_overrun_min:
            continue
        if trade.estimate > 0 and trade.variance_pct < settings.trade_overrun_pct:
            continue
        hits.append(trade)
    return sorted(hits, key=lambda t: -t.variance)


def _classify(job: JobCost, settings: Settings) -> None:
    target, projected = job.target, job.proj_margin
    label = "Final" if job.final else "Projected"
    reasons = []
    if projected < target:
        job.status = BELOW
        reasons.append(
            f"{label} margin {_pct(projected)} is below the {_pct(target)} target "
            f"by {(target - projected) * 100:.1f} points."
        )
    elif job.trend_margin is not None and job.trend_margin < target:
        job.status = TRENDING
        reasons.append(
            f"Projected margin {_pct(projected)} is still above the {_pct(target)} target but is "
            f"moving {job.trend_slope * 10:+.1f} points per 10% of progress; "
            f"on that trend the job finishes near {_pct(job.trend_margin)}."
        )
    elif not job.final and projected - target < settings.watch_buffer:
        job.status = WATCH
        reasons.append(
            f"Projected margin {_pct(projected)} is within "
            f"{settings.watch_buffer * 100:.0f} points of the {_pct(target)} target."
        )
    else:
        job.status = OK
    for trade in trade_overruns(job, settings)[:3]:
        if trade.estimate == 0:
            detail = f"${trade.variance:,.0f} of cost with no estimate line"
        else:
            detail = f"forecast {trade.variance_pct * 100:.0f}% over estimate (+${trade.variance:,.0f})"
        reasons.append(f"{trade.trade.title()}: {detail}.")
    job.reasons = reasons


# ------------------------------------------------------------------ entry points


def _summarize(job: Job, trades: list[TradeCost]) -> tuple[Decimal, Decimal, Decimal, float]:
    estimate = sum((t.estimate for t in trades), ZERO)
    actual = sum((t.actual for t in trades), ZERO)
    forecast = sum((t.forecast for t in trades), ZERO)
    return estimate, actual, forecast, _job_progress(trades)


def analyze_job(
    job: Job,
    estimates: list[EstimateLine],
    actuals: list[ActualEntry],
    progress: list[ProgressEntry],
    settings: Settings,
    as_of: date | None = None,
) -> JobCost:
    final = job.status == "complete"
    trades = _evaluate(estimates, actuals, progress, settings, as_of, final)
    estimate, actual, forecast, pct = _summarize(job, trades)
    dated = [a.entry_date for a in actuals if not as_of or a.entry_date <= as_of]
    dated += [p.as_of for p in progress if not as_of or p.as_of <= as_of]
    result = JobCost(
        job=job,
        trades=trades,
        estimate=estimate,
        actual=actual,
        forecast=forecast,
        pct_complete=1.0 if final else pct,
        target=job.target_margin if job.target_margin is not None else settings.target_margin,
        final=final,
        last_activity=max(dated) if dated else None,
    )

    # Replay history: one snapshot per progress report date, using only data known by then.
    for day in sorted({p.as_of for p in progress if not as_of or p.as_of <= as_of}):
        past = _evaluate(estimates, actuals, progress, settings, day, False)
        _, past_actual, past_forecast, past_pct = _summarize(job, past)
        result.snapshots.append(
            Snapshot(day, past_pct, past_actual, past_forecast, margin(job.contract, past_forecast))
        )
    if final and result.last_activity:
        closing = Snapshot(result.last_activity, 1.0, actual, forecast, result.proj_margin)
        if result.snapshots and result.snapshots[-1].as_of == closing.as_of:
            result.snapshots[-1] = closing
        else:
            result.snapshots.append(closing)

    if not final:
        slope = trend(result.snapshots, settings.trend_window)
        if slope is not None:
            result.trend_slope = slope
            result.trend_margin = result.proj_margin + slope * (1 - result.pct_complete)
    _classify(result, settings)
    return result


def analyze(data: Dataset, settings: Settings | None = None, as_of: date | None = None) -> Portfolio:
    """Cost every job in the dataset. Jobs come back worst status first, then by job id."""
    settings = (settings or Settings()).validate()
    estimates = defaultdict(list)
    actuals = defaultdict(list)
    progress = defaultdict(list)
    for line in data.estimates:
        estimates[line.job_id].append(line)
    for entry in data.actuals:
        actuals[entry.job_id].append(entry)
    for report in data.progress:
        progress[report.job_id].append(report)
    jobs = [
        analyze_job(job, estimates[job_id], actuals[job_id], progress[job_id], settings, as_of)
        for job_id, job in data.jobs.items()
    ]
    jobs.sort(key=lambda j: (STATUS_ORDER[j.status], j.job.job_id))
    dates = [j.last_activity for j in jobs if j.last_activity]
    return Portfolio(jobs=jobs, settings=settings, as_of=as_of or (max(dates) if dates else None))


# ------------------------------------------------------------------------- JSON


def trade_to_dict(trade: TradeCost) -> dict:
    return {
        "trade": trade.trade,
        "estimate": float(trade.estimate),
        "actual": float(trade.actual),
        "forecast": float(trade.forecast),
        "variance": float(trade.variance),
        "variance_pct": trade.variance_pct,
        "percent_complete": trade.pct_complete,
        "method": trade.method,
        "estimate_by_type": {k: float(v) for k, v in trade.est_by_type.items()},
        "actual_by_type": {k: float(v) for k, v in trade.act_by_type.items()},
        "estimate_hours": float(trade.est_hours),
        "actual_hours": float(trade.act_hours),
    }


def job_to_dict(job: JobCost, *, detail: bool = True) -> dict:
    result = {
        "job_id": job.job.job_id,
        "name": job.job.name,
        "job_status": job.job.status,
        "contract": float(job.job.contract),
        "estimate": float(job.estimate),
        "actual": float(job.actual),
        "forecast": float(job.forecast),
        "percent_complete": round(job.pct_complete, 4),
        "estimated_margin": round(job.est_margin, 4),
        "projected_margin": round(job.proj_margin, 4),
        "drift_points": round(job.drift * 100, 2),
        "target_margin": job.target,
        "trend_margin": None if job.trend_margin is None else round(job.trend_margin, 4),
        "status": job.status,
        "reasons": job.reasons,
    }
    if detail:
        result["trades"] = [trade_to_dict(t) for t in job.trades]
        result["snapshots"] = [
            {
                "as_of": s.as_of.isoformat(),
                "percent_complete": round(s.pct_complete, 4),
                "actual": float(s.actual),
                "forecast": float(s.forecast),
                "projected_margin": round(s.proj_margin, 4),
            }
            for s in job.snapshots
        ]
    return result


def portfolio_to_dict(portfolio: Portfolio, *, detail: bool = False) -> dict:
    return {
        "as_of": portfolio.as_of.isoformat() if portfolio.as_of else None,
        "default_target_margin": portfolio.settings.target_margin,
        "contract": float(portfolio.contract),
        "estimate": float(portfolio.estimate),
        "actual": float(portfolio.actual),
        "forecast": float(portfolio.forecast),
        "estimated_margin": round(portfolio.est_margin, 4),
        "projected_margin": round(portfolio.proj_margin, 4),
        "status_counts": portfolio.counts(),
        "jobs": [job_to_dict(j, detail=detail) for j in portfolio.jobs],
    }
