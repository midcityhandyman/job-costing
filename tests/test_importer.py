from datetime import date
from decimal import Decimal

import pytest

from jobcost.importer import (
    DataError,
    load_dataset,
    normalize_cost_type,
    parse_date,
    parse_money,
    parse_percent,
)

from conftest import MESSY, SAMPLE, write_csvs


@pytest.mark.parametrize(
    "text,expected",
    [("1234.5", "1234.5"), ("$1,234.50", "1234.50"), ("(85.00)", "-85.00"), (" 40 ", "40"), ("-12", "-12")],
)
def test_parse_money(text, expected):
    assert parse_money(text) == Decimal(expected)


@pytest.mark.parametrize("text", ["", "abc", "12..5", "NaN"])
def test_parse_money_rejects(text):
    with pytest.raises(ValueError):
        parse_money(text)


def test_parse_money_blank_default():
    assert parse_money("", blank=Decimal("0")) == 0


def test_parse_percent_is_always_percent_units():
    assert parse_percent("35") == pytest.approx(0.35)
    assert parse_percent("62.5%") == pytest.approx(0.625)
    assert parse_percent("1") == pytest.approx(0.01)
    with pytest.raises(ValueError):
        parse_percent("half")


def test_parse_date_formats():
    assert parse_date("2026-09-02") == date(2026, 9, 2)
    assert parse_date("09/02/2026") == date(2026, 9, 2)
    with pytest.raises(ValueError):
        parse_date("2026-13-40")


def test_cost_type_aliases():
    assert normalize_cost_type("Labour") == "labor"
    assert normalize_cost_type("materials") == "material"
    assert normalize_cost_type("Subcontractor") == "sub"
    with pytest.raises(ValueError):
        normalize_cost_type("equipment")


def test_sample_data_imports_clean():
    data = load_dataset(SAMPLE, strict=True)
    assert len(data.jobs) == 7
    assert data.issues == []
    assert data.estimates and data.actuals and data.progress


def test_tiny_dataset_values(tiny):
    data = load_dataset(tiny)
    job = data.jobs["T-1"]
    assert job.contract == Decimal("10000.00")
    assert job.status == "active" and job.target_margin is None
    labor = data.estimates[0]
    assert labor.amount == Decimal("2000.00") and labor.hours == Decimal("40")
    # Labor actual with no amount is hours times rate.
    assert data.actuals[0].amount == Decimal("1200.00")
    assert data.actuals[1].hours == 0
    assert data.progress[0].pct == pytest.approx(0.5)


def test_messy_rows_are_skipped_with_line_numbers():
    data = load_dataset(MESSY)
    found = {(i.file, i.line) for i in data.issues}
    assert found == {
        ("jobs.csv", 3), ("jobs.csv", 4),
        ("estimates.csv", 4), ("estimates.csv", 5),
        ("actuals.csv", 5), ("actuals.csv", 6), ("actuals.csv", 7),
    }
    # Header names, currency text, aliases and accounting negatives all normalize.
    job = data.jobs["M-1"]
    assert job.contract == Decimal("8400.00") and job.target_margin == pytest.approx(0.35)
    assert {e.cost_type for e in data.estimates} == {"labor", "material"}
    assert sum(a.amount for a in data.actuals) == Decimal("928.00") + Decimal("1920.40") - Decimal("85.00")


def test_strict_mode_raises_with_issues():
    with pytest.raises(DataError) as info:
        load_dataset(MESSY, strict=True)
    assert len(info.value.issues) == 7


def test_missing_folder_file_and_column(tmp_path, tiny):
    with pytest.raises(DataError, match="not found"):
        load_dataset(tmp_path / "nope")
    (tiny / "actuals.csv").unlink()
    with pytest.raises(DataError, match="missing required file"):
        load_dataset(tiny)
    write_csvs(tiny, actuals="job_id,trade,cost_type,amount\nT-1,framing,labor,10\n")
    with pytest.raises(DataError, match="missing column"):
        load_dataset(tiny)


def test_progress_file_is_optional_and_validated(tiny):
    write_csvs(tiny, progress="job_id,trade,as_of,percent_complete\nT-1,framing,2026-09-04,140\n")
    data = load_dataset(tiny)
    assert data.progress == [] and data.issues[0].file == "progress.csv"
    (tiny / "progress.csv").unlink()
    assert load_dataset(tiny).progress == []


def test_no_usable_jobs_is_an_error(tiny):
    write_csvs(tiny, jobs="job_id,name,contract_amount\nT-1,Bad,-5\n")
    with pytest.raises(DataError, match="no usable rows"):
        load_dataset(tiny)
