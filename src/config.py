from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict

import yaml


def _resolve_env(value: Any) -> Any:
    """
    Allow config values in the form ENV:VAR_NAME to be pulled from environment variables.
    """
    if isinstance(value, str) and value.startswith("ENV:"):
        env_key = value.split("ENV:", 1)[1]
        return os.getenv(env_key)
    return value


def load_yaml(path: Path) -> Dict[str, Any]:
    with path.open("r") as f:
        raw = yaml.safe_load(f) or {}
    return {k: _resolve_env(v) for k, v in raw.items()}


@dataclass
class Config:
    max_workers: int
    http_timeout: float
    dip_threshold: float
    market_cap_min: float
    avg_volume_min: float
    realert_delta: float
    min_dollar_volume: float
    rsi_threshold: float
    relative_volume_min: float
    tiered_dips_enabled: bool
    tier1_dip: float
    tier2_dip: float
    tier1_min_confirmations: int
    tier2_min_confirmations: int
    tier1_rsi_max: float
    tier2_rsi_max: float
    tier1_relvol_min: float
    tier2_relvol_min: float
    dma200_tolerance_pct: float
    dma200_green_pct: float
    dma200_red_pct: float
    allow_red_reclaim: bool
    require_rising_dma200_in_yellow: bool
    telegram_bot_token: str | None
    telegram_chat_id: str | None
    run_interval_seconds: int
    market_timezone: str
    market_hours_only: bool
    news_lookback_hours: int
    news_risk_keywords: list[str]
    news_trusted_publishers: list[str]
    news_blocked_publishers: list[str]
    enable_sell_alerts: bool
    take_profit_1: float
    take_profit_2: float
    take_profit_3: float
    cooldown_minutes_after_open: int
    news_keywords: list[str]
    use_intraday_low: bool
    candle_interval: str
    holdings_cache_hours: int
    after_hours_enabled: bool
    require_fast_selloff: bool

    @classmethod
    def from_file(cls, path: str | Path) -> "Config":
        data = load_yaml(Path(path))
        # Defaults if absent
        return cls(
            dip_threshold=float(data.get("dip_threshold", -5.0)),
            market_cap_min=float(data.get("market_cap_min", 20_000_000_000)),
            min_dollar_volume=float(data.get("min_dollar_volume", 1_000_000_000)),
            rsi_threshold=float(data.get("rsi_threshold", 35)),
            relative_volume_min=float(data.get("relative_volume_min", 1.5)),
            avg_volume_min=float(data.get("avg_volume_min", 2_000_000)),
            realert_delta=float(data.get("realert_delta", -2.0)),
            tiered_dips_enabled=bool(data.get("tiered_dips_enabled", False)),
            tier1_dip=float(data.get("tier1_dip", -3.5)),
            tier2_dip=float(data.get("tier2_dip", -5.0)),
            tier1_min_confirmations=int(data.get("tier1_min_confirmations", 3)),
            tier2_min_confirmations=int(data.get("tier2_min_confirmations", 2)),
            tier1_rsi_max=float(data.get("tier1_rsi_max", 35)),
            tier2_rsi_max=float(data.get("tier2_rsi_max", 40)),
            tier1_relvol_min=float(data.get("tier1_relvol_min", 1.5)),
            tier2_relvol_min=float(data.get("tier2_relvol_min", 1.2)),
            dma200_tolerance_pct=float(data.get("dma200_tolerance_pct", 2.0)),
            dma200_green_pct=float(data.get("dma200_green_pct", 2.0)),
            dma200_red_pct=float(data.get("dma200_red_pct", -2.0)),
            allow_red_reclaim=bool(data.get("allow_red_reclaim", False)),
            require_rising_dma200_in_yellow=bool(data.get("require_rising_dma200_in_yellow", True)),
            max_workers=int(data.get("max_workers", 6)),
            http_timeout=float(data.get("http_timeout", 5.0)),
            telegram_bot_token=data.get("telegram_bot_token"),
            telegram_chat_id=data.get("telegram_chat_id"),
            run_interval_seconds=int(data.get("run_interval_seconds", 300)),
            market_timezone=str(data.get("market_timezone", "America/Chicago")),
            market_hours_only=bool(data.get("market_hours_only", True)),
            news_lookback_hours=int(data.get("news_lookback_hours", 48)),
            news_risk_keywords=list(data.get("news_risk_keywords", ["guidance cut", "earnings miss", "fraud", "lawsuit", "fda", "recall", "probe", "downgrade", "sec", "bankruptcy", "restatement"])),
            news_trusted_publishers=list(data.get("news_trusted_publishers", ["reuters", "bloomberg", "wall street journal", "wsj", "ap", "associated press", "cnbc"])),
            news_blocked_publishers=list(data.get("news_blocked_publishers", ["seeking alpha transcript", "motley fool transcript"])),
            enable_sell_alerts=bool(data.get("enable_sell_alerts", False)),
            take_profit_1=float(data.get("take_profit_1", 0.05)),
            take_profit_2=float(data.get("take_profit_2", 0.07)),
            take_profit_3=float(data.get("take_profit_3", 0.10)),
            cooldown_minutes_after_open=int(data.get("cooldown_minutes_after_open", 5)),
            news_keywords=list(data.get("news_keywords", ["bankruptcy", "fraud", "delisting", "halt", "sec", "chapter 11"])),
            use_intraday_low=bool(data.get("use_intraday_low", True)),
            candle_interval=str(data.get("candle_interval", "5m")),
            holdings_cache_hours=int(data.get("holdings_cache_hours", 24)),
            after_hours_enabled=bool(data.get("after_hours_enabled", False)),
            require_fast_selloff=bool(data.get("require_fast_selloff", True)),
        )
