from datetime import date
from decimal import Decimal

import pytest

from jobcost.config import Settings
from jobcost.costing import (
    BELOW,
    OK,
    TRENDING,
    WATCH,
    Snapshot,
    analyze,
    fit_slope,
    forecast_trade,
    job_to_dict,
    trend,
)
from jobcost.importer import load_dataset

from conftest import SAMPLE, write_csvs

D = Decimal


def only(portfolio):
    assert len(portfolio.jobs) == 1
    return portfolio.jobs[0]


# ------------------------------------------------------------------ forecasting


def test_forecast_methods():
    assert forecast_trade(D(1000), D(600), 0.5) == (D("1200.00"), "earned-value")
    assert forecast_trade(D(1000), D(1100), 1.0) == (D(1100), "complete")
    assert forecast_trade(D(1000), D(400), None) == (D(1000), "no-progress")
    assert forecast_trade(D(1000), D(1300), None) == (D(1300), "no-progress")
    # 10% reported is under the 15% floor: hold the remaining 90% at estimate.
    assert forecast_trade(D(1000), D(250), 0.10) == (D("1150.00"), "early")
    assert forecast_trade(D(1000), D(880), 0.5, final=True) == (D(880), "final")
    assert forecast_trade(D(0), D(185), None) == (D(185), "no-progress")


def test_job_rollup_margin_and_drift(tiny):
    job = only(analyze(load_dataset(tiny)))
    framing, plumbing = job.trades
    # Framing: $1,800 spent at 50% done forecasts $3,600 against a $3,000 estimate.
    assert framing.estimate == D("3000.00") and framing.actual == D("1800.00")
    assert framing.forecast == D("3600.00") and framing.variance == D("600.00")
    assert framing.variance_pct == pytest.approx(0.20)
    assert framing.est_hours == 40 and framing.act_hours == 24
    assert framing.act_by_type == {"labor": D("1200.00"), "material": D("600.00"), "sub": D("0")}
    # Plumbing has not started and has no report, so it stays at estimate.
    assert plumbing.forecast == D("3000.00") and plumbing.method == "no-progress"
    assert job.estimate == D("6000.00") and job.forecast == D("6600.00")
    assert job.est_margin == pytest.approx(0.40)
    assert job.proj_margin == pytest.approx(0.34)
    assert job.drift == pytest.approx(-0.06)
    # Progress is estimate weighted: framing 50% of 3000, plumbing 0% of 3000.
    assert job.pct_complete == pytest.approx(0.25)


def test_status_below_target_names_the_trade(tiny):
    job = only(analyze(load_dataset(tiny)))
    assert job.status == BELOW and job.flagged
    assert "below the 35.0% target" in job.reasons[0]
    assert any(r.startswith("Framing: forecast 20% over estimate (+$600)") for r in job.reasons)


def test_target_precedence_job_over_default(tiny):
    data = load_dataset(tiny)
    assert only(analyze(data, Settings(target_margin=0.30))).status == OK
    write_csvs(tiny, jobs="job_id,name,contract_amount,status,target_margin_pct\nT-1,Sample,10000,active,36\n")
    job = only(analyze(load_dataset(tiny), Settings(target_margin=0.20)))
    assert job.target == pytest.approx(0.36) and job.status == BELOW


def test_watch_band(tiny):
    # Projected 34% against a 32% target: above target but inside the 3 point buffer.
    assert only(analyze(load_dataset(tiny), Settings(target_margin=0.32))).status == WATCH
    assert only(analyze(load_dataset(tiny), Settings(target_margin=0.32, watch_buffer=0.01))).status == OK


def test_as_of_cutoff_ignores_later_entries(tiny):
    job = only(analyze(load_dataset(tiny), as_of=date(2026, 9, 1)))
    assert job.actual == D("1200.00")
    assert job.trades[0].pct_complete is None  # the 9/4 progress report is not known yet
    assert job.last_activity == date(2026, 9, 1)


def test_complete_job_uses_actuals_as_final(tiny):
    write_csvs(tiny, jobs="job_id,name,contract_amount,status\nT-1,Sample,10000,complete\n")
    job = only(analyze(load_dataset(tiny)))
    assert job.final and job.forecast == job.actual == D("1800.00")
    assert job.pct_complete == 1.0 and job.trend_margin is None
    assert job.proj_margin == pytest.approx(0.82) and job.status == OK
    assert job.snapshots[-1].pct_complete == 1.0


def test_unestimated_trade_counts_against_margin(tiny):
    extra = "T-1,2026-09-03,permits,material,Permit,,,400,\n"
    with open(tiny / "actuals.csv", "a", encoding="utf-8") as handle:
        handle.write(extra)
    job = only(analyze(load_dataset(tiny)))
    permits = job.trades[-1]
    assert permits.trade == "permits" and permits.estimate == 0 and permits.forecast == D("400.00")
    assert permits.variance_pct is None
    assert job.forecast == D("7000.00")
    assert any("no estimate line" in r for r in job.reasons)


def test_credit_reduces_actual_cost(tiny):
    with open(tiny / "actuals.csv", "a", encoding="utf-8") as handle:
        handle.write("T-1,2026-09-03,framing,material,Return,,,(100.00),\n")
    assert only(analyze(load_dataset(tiny))).trades[0].actual == D("1700.00")


# ------------------------------------------------------------------------ trend


def snap(pct, margin_value):
    return Snapshot(date(2026, 9, 1), pct, D(0), D(0), margin_value)


def test_fit_slope():
    assert fit_slope([(0.0, 1.0), (1.0, 3.0), (2.0, 5.0)]) == pytest.approx(2.0)
    assert fit_slope([(1.0, 1.0)]) is None
    assert fit_slope([(0.5, 1.0), (0.5, 2.0)]) is None


def test_trend_needs_three_points_and_real_progress():
    assert trend([snap(0.2, 0.40), snap(0.4, 0.38)], 4) is None
    assert trend([snap(0.20, 0.40), snap(0.22, 0.39), snap(0.25, 0.38)], 4) is None
    slope = trend([snap(0.2, 0.40), snap(0.4, 0.38), snap(0.6, 0.36)], 4)
    assert slope == pytest.approx(-0.10)
    # Only the most recent window counts.
    flat_then_drop = [snap(0.1, 0.40), snap(0.2, 0.40), snap(0.3, 0.40), snap(0.5, 0.38), snap(0.7, 0.36)]
    assert trend(flat_then_drop, 3) == pytest.approx(-0.10)


def trending_dataset(folder):
    """Projected margin slides 40% -> 39% -> 38% -> 37% while progress goes 20% -> 80%."""
    actual_rows, progress_rows = [], []
    spent = 0
    for week, (pct, forecast) in enumerate([(20, 6000), (40, 6100), (60, 6200), (80, 6300)]):
        target_spend = forecast * pct // 100
        day = f"2026-09-{4 + 7 * week:02d}"
        actual_rows.append(f"T-1,{day},framing,material,Week {week + 1},,,{target_spend - spent},")
        progress_rows.append(f"T-1,framing,{day},{pct}")
        spent = target_spend
    return write_csvs(
        folder,
        jobs="job_id,name,contract_amount\nT-1,Sample Trending Job,10000\n",
        estimates="job_id,trade,cost_type,amount\nT-1,framing,material,6000\n",
        actuals="job_id,date,trade,cost_type,description,hours,rate,amount,vendor\n" + "\n".join(actual_rows),
        progress="job_id,trade,as_of,percent_complete\n" + "\n".join(progress_rows),
    )


def test_trending_below_flag(tmp_path):
    data = load_dataset(trending_dataset(tmp_path), strict=True)
    job = only(analyze(data, Settings(target_margin=0.365)))
    assert [round(s.proj_margin, 3) for s in job.snapshots] == [0.40, 0.39, 0.38, 0.37]
    assert job.proj_margin == pytest.approx(0.37)
    assert job.trend_slope == pytest.approx(-0.05)
    # 20% of the job is left: 37% minus 5 points per 100% of progress is 36%.
    assert job.trend_margin == pytest.approx(0.36)
    assert job.status == TRENDING and job.flagged
    assert "finishes near 36.0%" in job.reasons[0]
    # With a lower target the same slide is not a problem.
    assert only(analyze(data, Settings(target_margin=0.30))).status == OK


# -------------------------------------------------------------------- portfolio


def test_sample_portfolio_shows_every_status():
    portfolio = analyze(load_dataset(SAMPLE, strict=True))
    status = {j.job.job_id: j.status for j in portfolio.jobs}
    assert status == {
        "J-101": OK, "J-102": BELOW, "J-103": TRENDING, "J-104": OK,
        "J-105": BELOW, "J-106": OK, "J-107": WATCH,
    }
    # Worst first.
    assert [j.status for j in portfolio.jobs][:3] == [BELOW, BELOW, TRENDING]
    assert portfolio.counts() == {BELOW: 2, TRENDING: 1, WATCH: 1, OK: 3}
    assert {j.job.job_id for j in portfolio.flagged} == {"J-102", "J-103", "J-105"}


def test_portfolio_totals_and_trade_rollup_reconcile():
    portfolio = analyze(load_dataset(SAMPLE))
    assert portfolio.estimate == sum(j.estimate for j in portfolio.jobs)
    rollup = portfolio.by_trade()
    assert sum(r.estimate for r in rollup) == portfolio.estimate
    assert sum(r.actual for r in rollup) == portfolio.actual
    assert sum(r.forecast for r in rollup) == portfolio.forecast
    variances = [r.variance for r in rollup]
    assert variances == sorted(variances, reverse=True)
    paint = next(r for r in rollup if r.trade == "paint")
    assert paint.jobs == 5


def test_job_to_dict_is_json_ready(tiny):
    import json

    payload = job_to_dict(only(analyze(load_dataset(tiny))))
    text = json.dumps(payload)
    assert payload["drift_points"] == pytest.approx(-6.0)
    assert payload["trades"][0]["forecast"] == 3600.0
    assert "snapshots" in payload and "BELOW" in text


def test_settings_validation():
    with pytest.raises(ValueError):
        analyze(load_dataset(SAMPLE), Settings(target_margin=35))
