from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "data" / "sample"
MESSY = ROOT / "demo" / "messy"


def write_csvs(folder: Path, **files: str) -> Path:
    """Write jobs=..., estimates=..., actuals=..., progress=... as CSV text into folder."""
    for name, text in files.items():
        (folder / f"{name}.csv").write_text(text.strip() + "\n", encoding="utf-8")
    return folder


@pytest.fixture
def tiny(tmp_path):
    """One $10,000 job with two trades, estimated cost $6,000 (40% margin)."""
    return write_csvs(
        tmp_path,
        jobs="job_id,name,contract_amount,status,target_margin_pct\nT-1,Sample Test Job,10000,active,\n",
        estimates=(
            "job_id,trade,cost_type,description,quantity,unit_cost,amount\n"
            "T-1,framing,labor,Framing labor,40,50,\n"
            "T-1,framing,material,Lumber,,,1000\n"
            "T-1,plumbing,sub,Plumbing sub,,,3000\n"
        ),
        actuals=(
            "job_id,date,trade,cost_type,description,hours,rate,amount,vendor\n"
            "T-1,2026-09-01,framing,labor,Crew,24,50,,\n"
            "T-1,2026-09-02,framing,material,Lumber,,,600,Example Building Supply\n"
        ),
        progress="job_id,trade,as_of,percent_complete\nT-1,framing,2026-09-04,50\n",
    )
