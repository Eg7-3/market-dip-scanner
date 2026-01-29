from __future__ import annotations

import numpy as np
import pandas as pd


def rsi(series: pd.Series, period: int = 14) -> float | None:
    """Compute classic Wilder RSI. Returns last value."""
    if len(series) < period + 1:
        return None
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = -delta.clip(upper=0).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi_series = 100 - (100 / (1 + rs))
    return float(rsi_series.iloc[-1]) if not rsi_series.empty else None


def sma(series: pd.Series, period: int) -> float | None:
    if len(series) < period:
        return None
    return float(series.tail(period).mean())


def vwap(df: pd.DataFrame) -> float | None:
    """VWAP from intraday dataframe with columns 'Close','Volume' (optionally High/Low)."""
    if df.empty or "Close" not in df or "Volume" not in df:
        return None
    price = df["Close"]
    volume = df["Volume"]
    denom = volume.sum()
    if denom == 0:
        return None
    return float((price * volume).sum() / denom)


def relative_volume(latest_vol: float, avg_vol: float) -> float | None:
    if avg_vol == 0:
        return None
    return latest_vol / avg_vol


def ma_slope(series: pd.Series, window: int = 200, lookback: int = 5) -> float | None:
    """
    Approximate slope of a moving average over a short lookback.
    Positive => MA rising. Returns change per day.
    """
    ma = series.rolling(window).mean().dropna()
    if len(ma) < lookback + 1:
        return None
    latest = ma.iloc[-1]
    prior = ma.iloc[-1 - lookback]
    return float((latest - prior) / lookback)
