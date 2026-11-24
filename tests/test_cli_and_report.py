import json

import pytest

from jobcost.cli import main
from jobcost.config import Settings, read_env_file, settings_from_env
from jobcost.costing import analyze
from jobcost.importer import load_dataset
from jobcost.render_html import render_html
from jobcost.render_text import money, signed_money, table
from jobcost.sample import generate, write_sample

from conftest import MESSY, SAMPLE, write_csvs

from decimal import Decimal


def run(capsys, *argv):
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_summary_table(capsys):
    code, out, _ = run(capsys, "summary", "--data", str(SAMPLE))
    assert code == 0
    assert "J-103" in out and "TRENDING" in out and "TOTAL" in out
    assert "Status counts: BELOW 2, TRENDING 1, WATCH 1, OK 3" in out


def test_summary_json(capsys):
    code, out, _ = run(capsys, "summary", "--data", str(SAMPLE), "--json")
    payload = json.loads(out)
    assert code == 0 and len(payload["jobs"]) == 7
    assert payload["status_counts"]["BELOW"] == 2


def test_target_flag_changes_outcome(capsys):
    _, out, _ = run(capsys, "summary", "--data", str(SAMPLE), "--target", "20", "--json")
    assert json.loads(out)["status_counts"]["BELOW"] == 0
    with pytest.raises(SystemExit):
        main(["summary", "--data", str(SAMPLE), "--target", "0"])


def test_job_detail_and_unknown_job(capsys):
    code, out, _ = run(capsys, "job", "j-102", "--data", str(SAMPLE))
    assert code == 0 and "Sample Bath Remodel" in out and "Plumbing" in out and "Why:" in out
    code, _, err = run(capsys, "job", "J-999", "--data", str(SAMPLE))
    assert code == 1 and "Known jobs" in err


def test_flags_exit_code(capsys):
    code, out, _ = run(capsys, "flags", "--data", str(SAMPLE))
    assert code == 0 and "[BELOW] J-102" in out and "[TRENDING] J-103" in out and "[WATCH] J-107" in out
    code, _, _ = run(capsys, "flags", "--data", str(SAMPLE), "--fail-on-flag")
    assert code == 2
    code, out, _ = run(capsys, "flags", "--data", str(SAMPLE), "--fail-on-flag", "--target", "5")
    assert code == 0 and "No jobs flagged" in out


def test_as_of_flag(capsys):
    _, out, _ = run(capsys, "summary", "--data", str(SAMPLE), "--as-of", "2026-08-14", "--json")
    assert json.loads(out)["as_of"] == "2026-08-14"


def test_trades_view(capsys):
    code, out, _ = run(capsys, "trades", "--data", str(SAMPLE))
    assert code == 0 and out.index("Tile") < out.index("Decking")


def test_validate_and_strict(capsys):
    code, out, _ = run(capsys, "validate", "--data", str(SAMPLE))
    assert code == 0 and "No import issues." in out
    code, out, _ = run(capsys, "validate", "--data", str(MESSY))
    assert code == 1 and "actuals.csv:5" in out
    code, _, err = run(capsys, "summary", "--data", str(MESSY), "--strict")
    assert code == 1 and "failed validation" in err
    code, _, err = run(capsys, "summary", "--data", str(MESSY))
    assert code == 0 and "skipped" in err


def test_missing_data_folder(capsys, tmp_path):
    code, _, err = run(capsys, "summary", "--data", str(tmp_path / "missing"))
    assert code == 1 and "not found" in err


def test_report_file(capsys, tmp_path):
    out_file = tmp_path / "out" / "report.html"
    code, out, _ = run(capsys, "report", "--data", str(SAMPLE), "--out", str(out_file))
    html = out_file.read_text(encoding="utf-8")
    assert code == 0 and "3 flagged" in out
    assert html.startswith("<!doctype html>") and html.rstrip().endswith("</html>")
    for job_id in ("J-101", "J-102", "J-103", "J-104", "J-105", "J-106", "J-107"):
        assert f'id="{job_id}"' in html
    assert "Trending below" in html and "<svg" in html
    # Self contained: no scripts and nothing fetched from the network.
    assert "<script" not in html and "http://" not in html and "https://" not in html


def test_report_escapes_names_and_lists_issues(tmp_path):
    write_csvs(
        tmp_path,
        jobs='job_id,name,contract_amount\nX-1,"Sample <b>Job</b> & Co",1000\n',
        estimates="job_id,trade,cost_type,amount\nX-1,paint,material,500\nX-1,paint,labor,\n",
        actuals="job_id,date,trade,cost_type,amount\nX-1,2026-09-01,paint,material,100\n",
    )
    data = load_dataset(tmp_path)
    html = render_html(analyze(data), data.issues)
    assert "Sample &lt;b&gt;Job&lt;/b&gt; &amp; Co" in html and "<b>Job</b>" not in html
    assert "Import issues" in html and "estimates.csv:3" in html


def test_sample_generator_is_deterministic(tmp_path):
    assert generate(7) == generate(7)
    assert generate(7) != generate(8)
    counts = write_sample(tmp_path / "s", seed=7)
    assert counts["jobs.csv"] == 7
    assert load_dataset(tmp_path / "s", strict=True).issues == []
    for name in ("jobs.csv", "estimates.csv", "actuals.csv", "progress.csv"):
        fresh = (tmp_path / "s" / name).read_text(encoding="utf-8")
        assert fresh == (SAMPLE / name).read_text(encoding="utf-8"), f"{name} is stale; rerun jobcost sample"


def test_sample_cli(capsys, tmp_path):
    code, out, _ = run(capsys, "sample", "--out", str(tmp_path / "gen"), "--seed", "3")
    assert code == 0 and "jobs.csv: 7 rows" in out


def test_settings_from_env(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("# comment\nJOBCOST_TARGET_MARGIN=0.30\nJOBCOST_TREND_WINDOW=5\n", encoding="utf-8")
    assert read_env_file(env_file)["JOBCOST_TARGET_MARGIN"] == "0.30"
    settings = settings_from_env(env={}, env_file=env_file)
    assert settings.target_margin == 0.30 and settings.trend_window == 5
    # The process environment wins over the file.
    assert settings_from_env(env={"JOBCOST_TARGET_MARGIN": "0.4"}, env_file=env_file).target_margin == 0.4
    assert settings_from_env(env={}, env_file=tmp_path / "none") == Settings()
    with pytest.raises(ValueError):
        settings_from_env(env={"JOBCOST_TARGET_MARGIN": "lots"}, env_file=tmp_path / "none")
    with pytest.raises(ValueError):
        settings_from_env(env={"JOBCOST_TARGET_MARGIN": "35"}, env_file=tmp_path / "none")


def test_text_helpers():
    assert money(Decimal("1234.56")) == "$1,235" and money(Decimal("-20")) == "-$20"
    assert signed_money(Decimal("0.2")) == "$0" and signed_money(Decimal("-449")) == "-$449"
    assert table(["A", "B"], [["x", "1"], ["long", "22"]]).splitlines() == ["A      B", "----  --", "x      1", "long  22"]
