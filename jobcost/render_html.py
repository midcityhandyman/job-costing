"""A single self-contained HTML report: inline CSS, inline SVG, no scripts, no network."""

from __future__ import annotations

from html import escape

from .costing import BELOW, METHOD_NOTES, OK, TRENDING, WATCH, JobCost, Portfolio
from .models import Issue
from .render_text import money, pct, points, signed_money

STATUS_LABEL = {BELOW: "Below target", TRENDING: "Trending below", WATCH: "Watch", OK: "On track"}

CSS = """
:root { --bg:#f6f7f9; --card:#fff; --ink:#1c2430; --muted:#5d6878; --line:#dde2ea;
  --est:#9aa7b8; --good:#2f7d5b; --bad:#c2412d; --warn:#b7791f; --watch:#8a6d1a; --accent:#2b5fab; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#12161c; --card:#1b212a; --ink:#e7ebf1; --muted:#9aa5b5; --line:#2e3744;
    --est:#5f6c7e; --good:#5cc198; --bad:#f0806c; --warn:#e3b04f; --watch:#d6bd6a; --accent:#7fa9ea; }
}
* { box-sizing: border-box; }
body { margin:0; background:var(--bg); color:var(--ink);
  font:15px/1.5 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }
main { max-width:1080px; margin:0 auto; padding:24px 16px 48px; }
h1 { font-size:26px; margin:0 0 4px; }
h2 { font-size:19px; margin:32px 0 12px; }
h3 { font-size:17px; margin:0; }
.sub { color:var(--muted); margin:0 0 20px; }
.cards { display:grid; grid-template-columns:repeat(auto-fit, minmax(170px, 1fr)); gap:12px; }
.card { background:var(--card); border:1px solid var(--line); border-radius:8px; padding:14px 16px; }
.card .k { color:var(--muted); font-size:12px; text-transform:uppercase; letter-spacing:.04em; }
.card .v { font-size:22px; font-weight:650; font-variant-numeric:tabular-nums; }
.card .n { color:var(--muted); font-size:13px; }
.wrap { overflow-x:auto; background:var(--card); border:1px solid var(--line); border-radius:8px; }
table { border-collapse:collapse; width:100%; font-variant-numeric:tabular-nums; }
th, td { padding:8px 12px; text-align:right; white-space:nowrap; border-bottom:1px solid var(--line); }
th { font-size:12px; text-transform:uppercase; letter-spacing:.04em; color:var(--muted); }
th:first-child, td:first-child, td.l, th.l { text-align:left; }
tr:last-child td { border-bottom:none; }
tr.total td { font-weight:650; border-top:2px solid var(--line); }
a { color:var(--accent); text-decoration:none; }
a:hover { text-decoration:underline; }
.pill { display:inline-block; padding:1px 9px; border-radius:999px; font-size:12px; font-weight:650;
  border:1px solid currentColor; }
.BELOW { color:var(--bad); } .TRENDING { color:var(--warn); }
.WATCH { color:var(--watch); } .OK { color:var(--good); }
.over { color:var(--bad); } .under { color:var(--good); }
.job { background:var(--card); border:1px solid var(--line); border-radius:8px; padding:16px; margin:16px 0; }
.job .head { display:flex; flex-wrap:wrap; gap:8px 12px; align-items:center; }
.job .meta { color:var(--muted); margin:6px 0 10px; }
.job .wrap { border:none; border-radius:0; }
.grid { display:grid; grid-template-columns:repeat(auto-fit, minmax(300px, 1fr)); gap:16px; margin:12px 0; }
.grid figure { margin:0; }
figcaption { color:var(--muted); font-size:13px; margin-bottom:4px; }
svg { width:100%; height:auto; display:block; }
svg text { fill:var(--muted); font-size:11px; font-family:inherit; }
ul.why { margin:8px 0 0; padding-left:20px; }
.legend span { display:inline-block; width:10px; height:10px; border-radius:2px; margin:0 4px 0 12px; }
footer { color:var(--muted); font-size:13px; margin-top:32px; }
"""


def _pill(status: str) -> str:
    return f'<span class="pill {status}">{escape(STATUS_LABEL[status])}</span>'


def _var_class(value) -> str:
    return "over" if value > 0 else ("under" if value < 0 else "")


def _var_pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:+.1f}%"


def _trade_bars(job: JobCost) -> str:
    """Paired horizontal bars per trade: estimate (grey) over forecast (colored)."""
    trades = job.trades
    top = max([float(max(t.estimate, t.forecast)) for t in trades] + [1.0])
    label_w, width, row_h = 110, 520, 34
    scale = (width - label_w - 70) / top
    parts = [f'<svg viewBox="0 0 {width} {len(trades) * row_h + 4}" role="img" '
             f'aria-label="Estimate versus forecast by trade">']
    for i, t in enumerate(trades):
        y = i * row_h + 4
        est_w = max(float(t.estimate) * scale, 0)
        fc_w = max(float(t.forecast) * scale, 0)
        over = t.estimate > 0 and t.variance_pct > 0.10 or (t.estimate == 0 and t.forecast > 0)
        color = "var(--bad)" if over else "var(--accent)"
        parts.append(f'<text x="0" y="{y + 16}">{escape(t.trade.title()[:16])}</text>')
        parts.append(f'<rect x="{label_w}" y="{y}" width="{est_w:.1f}" height="11" rx="2" fill="var(--est)"/>')
        parts.append(f'<rect x="{label_w}" y="{y + 13}" width="{fc_w:.1f}" height="11" rx="2" fill="{color}"/>')
        parts.append(f'<text x="{label_w + max(est_w, fc_w) + 6:.1f}" y="{y + 16}">{money(t.forecast)}</text>')
    parts.append("</svg>")
    return "".join(parts)


def _margin_line(job: JobCost) -> str:
    """Projected margin at each progress snapshot, with the target as a dashed line."""
    snaps = job.snapshots
    if len(snaps) < 2:
        return '<p class="sub">Not enough progress reports for a margin history yet.</p>'
    values = [s.proj_margin for s in snaps] + [job.target, job.est_margin]
    low, high = min(values) - 0.02, max(values) + 0.02
    width, height, left, right, pad = 520, 170, 44, 12, 14

    def x(i: int) -> float:
        return left + i * (width - left - right) / (len(snaps) - 1)

    def y(value: float) -> float:
        return pad + (high - value) / (high - low) * (height - 2 * pad - 14)

    points_attr = " ".join(f"{x(i):.1f},{y(s.proj_margin):.1f}" for i, s in enumerate(snaps))
    color = "var(--bad)" if job.proj_margin < job.target else "var(--good)"
    parts = [
        f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="Projected margin over time">',
        f'<line x1="{left}" x2="{width - right}" y1="{y(job.target):.1f}" y2="{y(job.target):.1f}" '
        f'stroke="var(--bad)" stroke-dasharray="5 4" stroke-width="1"/>',
        f'<text x="0" y="{y(job.target) + 4:.1f}">{pct(job.target, 0)} tgt</text>',
        f'<line x1="{left}" x2="{width - right}" y1="{y(job.est_margin):.1f}" y2="{y(job.est_margin):.1f}" '
        f'stroke="var(--est)" stroke-dasharray="2 4" stroke-width="1"/>',
        f'<text x="0" y="{y(job.est_margin) + 4:.1f}">{pct(job.est_margin, 0)} est</text>',
        f'<polyline points="{points_attr}" fill="none" stroke="{color}" stroke-width="2"/>',
    ]
    for i, s in enumerate(snaps):
        parts.append(f'<circle cx="{x(i):.1f}" cy="{y(s.proj_margin):.1f}" r="3" fill="{color}">'
                     f"<title>{s.as_of.isoformat()}: {pct(s.proj_margin)}</title></circle>")
    parts.append(f'<text x="{left}" y="{height - 2}">{snaps[0].as_of.isoformat()}</text>')
    parts.append(f'<text x="{width - right}" y="{height - 2}" text-anchor="end">'
                 f"{snaps[-1].as_of.isoformat()} ({pct(snaps[-1].proj_margin)})</text>")
    parts.append("</svg>")
    return "".join(parts)


def _job_section(job: JobCost) -> str:
    rows = []
    for t in job.trades:
        done = "none" if t.pct_complete is None else pct(t.pct_complete, 0)
        rows.append(
            f"<tr><td>{escape(t.trade.title())}</td><td>{done}</td><td>{money(t.estimate)}</td>"
            f"<td>{money(t.actual)}</td><td>{money(t.forecast)}</td>"
            f'<td class="{_var_class(t.variance)}">{signed_money(t.variance)}</td>'
            f'<td class="{_var_class(t.variance)}">{_var_pct(t.variance_pct)}</td>'
            f"<td>{t.est_hours:.0f} / {t.act_hours:.0f}</td>"
            f'<td class="l" title="{escape(METHOD_NOTES[t.method])}">{escape(t.method)}</td></tr>'
        )
    total_var = job.forecast - job.estimate
    rows.append(
        f'<tr class="total"><td>Total</td><td>{pct(job.pct_complete, 0)}</td><td>{money(job.estimate)}</td>'
        f"<td>{money(job.actual)}</td><td>{money(job.forecast)}</td>"
        f'<td class="{_var_class(total_var)}">{signed_money(total_var)}</td><td></td><td></td><td></td></tr>'
    )
    trend = ""
    if job.trend_margin is not None:
        trend = (f" Trend {job.trend_slope * 10:+.1f} pts per 10% of progress, "
                 f"finishing near {pct(job.trend_margin)}.")
    why = ""
    if job.reasons:
        why = '<ul class="why">' + "".join(f"<li>{escape(r)}</li>" for r in job.reasons) + "</ul>"
    return f"""
<section class="job" id="{escape(job.job.job_id)}">
  <div class="head"><h3>{escape(job.job.job_id)} {escape(job.job.name)}</h3>{_pill(job.status)}</div>
  <p class="meta">Contract {money(job.job.contract)}. Estimated margin {pct(job.est_margin)},
    {"final" if job.final else "projected"} margin {pct(job.proj_margin)}
    ({points(job.drift)} pts drift), target {pct(job.target, 0)}.{escape(trend)}</p>
  <div class="grid">
    <figure><figcaption>Estimate versus forecast by trade
      <span class="legend"><span style="background:var(--est)"></span>estimate
      <span style="background:var(--accent)"></span>forecast
      <span style="background:var(--bad)"></span>over 10%</span></figcaption>{_trade_bars(job)}</figure>
    <figure><figcaption>Projected margin at each progress report</figcaption>{_margin_line(job)}</figure>
  </div>
  <div class="wrap"><table>
    <tr><th>Trade</th><th>Done</th><th>Estimate</th><th>Actual</th><th>Forecast</th><th>Variance</th>
      <th>Var %</th><th>Hours est / act</th><th class="l">Method</th></tr>
    {"".join(rows)}
  </table></div>
  {why}
</section>"""


def render_html(portfolio: Portfolio, issues: list[Issue] | None = None, title: str = "Job costing report") -> str:
    counts = portfolio.counts()
    drift = portfolio.proj_margin - portfolio.est_margin
    cards = [
        ("Contract value", money(portfolio.contract), f"{len(portfolio.jobs)} jobs"),
        ("Estimated margin", pct(portfolio.est_margin), f"on {money(portfolio.estimate)} estimated cost"),
        ("Projected margin", pct(portfolio.proj_margin), f"{points(drift)} pts versus estimate"),
        ("Flagged", str(counts[BELOW] + counts[TRENDING]),
         f"{counts[BELOW]} below, {counts[TRENDING]} trending, {counts[WATCH]} watch"),
    ]
    card_html = "".join(
        f'<div class="card"><div class="k">{escape(k)}</div><div class="v">{escape(v)}</div>'
        f'<div class="n">{escape(n)}</div></div>'
        for k, v, n in cards
    )
    job_rows = "".join(
        f'<tr><td><a href="#{escape(j.job.job_id)}">{escape(j.job.job_id)}</a></td>'
        f'<td class="l">{escape(j.job.name)}</td>'
        f"<td>{'closed' if j.final else pct(j.pct_complete, 0)}</td><td>{money(j.job.contract)}</td>"
        f"<td>{money(j.estimate)}</td><td>{money(j.forecast)}</td><td>{pct(j.est_margin)}</td>"
        f"<td>{pct(j.proj_margin)}</td>"
        f'<td class="{_var_class(-j.drift)}">{points(j.drift)}</td><td>{pct(j.target, 0)}</td>'
        f'<td class="l">{_pill(j.status)}</td></tr>'
        for j in portfolio.jobs
    )
    trade_rows = "".join(
        f"<tr><td>{escape(r.trade.title())}</td><td>{r.jobs}</td><td>{money(r.estimate)}</td>"
        f"<td>{money(r.actual)}</td><td>{money(r.forecast)}</td>"
        f'<td class="{_var_class(r.variance)}">{signed_money(r.variance)}</td>'
        f'<td class="{_var_class(r.variance)}">{_var_pct(r.variance_pct)}</td></tr>'
        for r in portfolio.by_trade()
    )
    issue_html = ""
    if issues:
        items = "".join(f"<li>{escape(str(i))}</li>" for i in issues)
        issue_html = f'<h2>Import issues</h2><div class="card"><ul class="why">{items}</ul></div>'
    methods = "".join(f"<li><b>{escape(k)}</b>: {escape(v)}</li>" for k, v in METHOD_NOTES.items())
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(title)}</title>
<style>{CSS}</style>
</head>
<body>
<main>
<h1>{escape(title)}</h1>
<p class="sub">Data through {portfolio.as_of or "n/a"}. Default target margin
  {pct(portfolio.settings.target_margin, 0)}. Synthetic demo data.</p>
<div class="cards">{card_html}</div>

<h2>Jobs</h2>
<div class="wrap"><table>
<tr><th>Job</th><th class="l">Name</th><th>Done</th><th>Contract</th><th>Estimate</th><th>Forecast</th>
  <th>Est margin</th><th>Proj margin</th><th>Drift pts</th><th>Target</th><th class="l">Status</th></tr>
{job_rows}
</table></div>

<h2>By trade, all jobs</h2>
<div class="wrap"><table>
<tr><th>Trade</th><th>Jobs</th><th>Estimate</th><th>Actual</th><th>Forecast</th><th>Variance</th><th>Var %</th></tr>
{trade_rows}
</table></div>

<h2>Job detail</h2>
{"".join(_job_section(j) for j in portfolio.jobs)}
{issue_html}
<footer>
<p>Forecast methods:</p><ul>{methods}</ul>
<p>Drift is projected margin minus estimated margin. A job is flagged "Below target" when its projected
margin is under target, and "Trending below" when the slope of projected margin across recent progress
reports would carry it under target by completion.</p>
</footer>
</main>
</body>
</html>
"""
