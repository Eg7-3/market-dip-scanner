from __future__ import annotations

import abc
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class Quote:
    ticker: str
    price: float
    prev_close: float
    change_pct: float
    volume: int
    avg_volume: int
    market_cap: float
    dollar_volume: float | None = None
    intraday_low: float | None = None
    intraday_low_change: float | None = None
    rsi: float | None = None
    relative_volume: float | None = None
    vwap: float | None = None
    ma200: float | None = None
    dma200_dist_pct: float | None = None
    dma200_slope: float | None = None
    dma200_slope_rising: bool | None = None
    days_since_pullback_start: int | None = None
    name: str | None = None
    sector: str | None = None
    positive_fcf_or_income: bool | None = None
    analyst_rating: str | None = None
    timestamp: Optional[int] = None  # epoch seconds


class DataProvider(abc.ABC):
    @abc.abstractmethod
    def get_constituents(self, cache_hours: int = 24) -> List[str]:
        """
        Return a list of tickers in the Nasdaq-100 / QQQ.
        Should raise a descriptive exception on failure.
        """

    @abc.abstractmethod
    def get_quote(self, ticker: str) -> Quote:
        """
        Return a Quote for the given ticker.
        """

    def get_news_headlines(self, ticker: str) -> list[str]:
        """
        Optional: return a small list of recent headlines for basic risk checks.
        Default implementation returns an empty list.
        """
        return []
