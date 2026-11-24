"""Thresholds for forecasting and flagging, with optional overrides from the environment."""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from decimal import Decimal
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    # Default target gross margin as a fraction of contract value.
    target_margin: float = 0.35
    # A job projected above target but within this many points of it is put on WATCH.
    watch_buffer: float = 0.03
    # Below this reported progress, earned-value forecasting is too noisy to trust.
    min_progress: float = 0.15
    # A trade is called out when its forecast runs over estimate by this share...
    trade_overrun_pct: float = 0.10
    # ...and by at least this many dollars.
    trade_overrun_min: Decimal = Decimal("250")
    # How many recent progress snapshots feed the drift trend.
    trend_window: int = 4

    def validate(self) -> "Settings":
        if not 0 < self.target_margin < 1:
            raise ValueError("target margin must be between 0 and 1 (0.35 means 35%)")
        if not 0 <= self.watch_buffer < 1:
            raise ValueError("watch buffer must be between 0 and 1")
        if not 0 <= self.min_progress < 1:
            raise ValueError("min progress must be between 0 and 1")
        if self.trend_window < 3:
            raise ValueError("trend window must be at least 3")
        return self


ENV_FIELDS = {
    "JOBCOST_TARGET_MARGIN": ("target_margin", float),
    "JOBCOST_WATCH_BUFFER": ("watch_buffer", float),
    "JOBCOST_MIN_PROGRESS": ("min_progress", float),
    "JOBCOST_TRADE_OVERRUN_PCT": ("trade_overrun_pct", float),
    "JOBCOST_TRADE_OVERRUN_MIN": ("trade_overrun_min", Decimal),
    "JOBCOST_TREND_WINDOW": ("trend_window", int),
}


def read_env_file(path: str | Path) -> dict[str, str]:
    """Read KEY=VALUE lines from a .env file. Missing file means no values."""
    values: dict[str, str] = {}
    file = Path(path)
    if not file.is_file():
        return values
    for line in file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def settings_from_env(env: dict[str, str] | None = None, env_file: str | Path = ".env") -> Settings:
    """Build settings from a .env file, then the process environment (which wins)."""
    merged = read_env_file(env_file)
    merged.update(os.environ if env is None else env)
    changes = {}
    for key, (name, cast) in ENV_FIELDS.items():
        raw = merged.get(key, "").strip()
        if not raw:
            continue
        try:
            changes[name] = cast(raw)
        except Exception:
            raise ValueError(f"{key} has an invalid value: {raw!r}") from None
    return replace(Settings(), **changes).validate()
