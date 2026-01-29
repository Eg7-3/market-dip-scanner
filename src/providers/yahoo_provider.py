from __future__ import annotations

import json
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Tuple, Dict, Any

import pandas as pd
import requests
import yfinance as yf
import re

from .base import DataProvider, Quote


DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
DATA_DIR.mkdir(exist_ok=True)
QQQ_CACHE_PATH = DATA_DIR / "qqq_tickers.json"


class YahooProvider(DataProvider):
    """
    Data provider using yfinance for quotes and slickcharts.com/StockAnalysis for QQQ holdings.
    """

    def __init__(self, session: requests.Session | None = None, timeout: float = 5.0):
        self.session = session or requests.Session()
        self.timeout = timeout
        self._news_cache: Dict[str, Tuple[float, List[Dict[str, Any]]]] = {}
        # Robust retries for holdings fetch
        from requests.adapters import HTTPAdapter
        from urllib3.util.retry import Retry

        retry = Retry(total=3, backoff_factor=0.3, status_forcelist=[429, 500, 502, 503, 504])
        adapter = HTTPAdapter(max_retries=retry)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)
        self.headers = {"User-Agent": "Mozilla/5.0 (QQQ-dip-scanner)"}

    def _fetch_constituents_online(self) -> List[str]:
        """
        Prefer the StockAnalysis QQQ holdings list (fast, structured).
        Fallback to Slickcharts if needed.
        """
        try:
            url = "https://stockanalysis.com/etf/qqq/holdings/"
            resp = self.session.get(url, timeout=self.timeout, headers=self.headers)
            resp.raise_for_status()
            tables = pd.read_html(resp.text)
            if tables:
                df = tables[0]
                if "Symbol" in df.columns:
                    return df["Symbol"].dropna().astype(str).str.upper().tolist()
        except Exception:
            pass

        # Fallback to slickcharts – sometimes their table trips pandas; fallback to regex extraction.
        url = "https://www.slickcharts.com/nasdaq100"
        try:
            resp = self.session.get(url, timeout=self.timeout, headers=self.headers)
            resp.raise_for_status()
            html = resp.text
            # First try pandas
            try:
                tables = pd.read_html(html)
                if tables:
                    return tables[0]["Symbol"].dropna().astype(str).str.upper().tolist()
            except Exception:
                pass

            # Regex fallback scoped to table body to avoid nav links (e.g., SPY/DIA)
            tbody_start = html.find("<tbody")
            tbody_end = html.find("</tbody>")
            scope = html[tbody_start:tbody_end] if tbody_start != -1 and tbody_end != -1 else html
            symbols = re.findall(r"/symbol/([A-Za-z.\-]+)", scope)
            symbols = [s.upper() for s in symbols]
            # Deduplicate while preserving order
            seen = set()
            uniq = []
            for s in symbols:
                if s not in seen:
                    seen.add(s)
                    uniq.append(s)
            if uniq:
                return uniq
        except Exception:
            pass

        raise RuntimeError("Unable to download QQQ holdings from StockAnalysis or Slickcharts (403 or parse failure).")

    def get_constituents(self, cache_hours: int = 24) -> List[str]:
        # Serve from cache if fresh
        if QQQ_CACHE_PATH.exists():
            mtime = datetime.fromtimestamp(QQQ_CACHE_PATH.stat().st_mtime)
            if mtime > datetime.now() - timedelta(hours=cache_hours):
                with QQQ_CACHE_PATH.open() as f:
                    cached = json.load(f)
                    if isinstance(cached, list) and cached:
                        return cached

        try:
            tickers = self._fetch_constituents_online()
            with QQQ_CACHE_PATH.open("w") as f:
                json.dump(tickers, f, indent=2)
            return tickers
        except Exception as e:
            # If fresh download fails but we have any cached list, return it instead of crashing.
            if QQQ_CACHE_PATH.exists():
                try:
                    with QQQ_CACHE_PATH.open() as f:
                        cached = json.load(f)
                        if cached:
                            return cached
                except Exception:
                    pass
            raise

    def get_quote(self, ticker: str, candle_interval: str = "5m", prepost: bool = False) -> Quote:
        """
        Enrich quote with intraday low, RSI, relative volume, VWAP, 200-DMA and fundamentals.
        """
        ticker_obj = yf.Ticker(ticker)
        info = ticker_obj.fast_info  # lightweight snapshot

        # Basic prices/volumes
        price = float(
            info.get("last_price")
            or info.get("regular_market_price")
            or info.get("regularMarketPrice")
            or 0
        )
        prev_close = float(
            info.get("previous_close")
            or info.get("regular_market_previous_close")
            or info.get("regularMarketPreviousClose")
            or price
            or 0
        )
        volume = int(
            info.get("last_volume", 0)
            or info.get("regular_market_volume", 0)
            or info.get("regularMarketVolume", 0)
            or 0
        )
        avg_volume = int(
            info.get("ten_day_average_volume", 0)
            or info.get("three_month_average_volume", 0)
            or info.get("average_volume", 0)
            or 0
        )
        market_cap = float(info.get("market_cap", 0) or 0)
        change_pct = float(
            info.get("regular_market_change_percent")
            or info.get("regularMarketChangePercent")
            or 0.0
        )

        # Pull richer fundamentals once
        detailed = {}
        try:
            detailed = ticker_obj.get_info()
        except Exception:
            detailed = {}

        if price <= 0:
            price = float(detailed.get("regularMarketPrice") or 0)
        if prev_close <= 0:
            prev_close = float(detailed.get("regularMarketPreviousClose") or price or 0)
        if volume == 0:
            volume = int(detailed.get("regularMarketVolume") or 0)
        if avg_volume == 0:
            avg_volume = int(detailed.get("averageVolume") or 0)
        if market_cap == 0:
            market_cap = float(detailed.get("marketCap") or 0)
        # Final defensive fallback: derive mcap from shares * price to avoid false skips
        if (market_cap is None or market_cap == 0) and price:
            shares_out = (
                info.get("shares_outstanding")
                or detailed.get("sharesOutstanding")
                or detailed.get("floatShares")
            )
            if shares_out:
                try:
                    market_cap = float(shares_out) * float(price)
                except Exception:
                    pass
        if change_pct == 0.0 and prev_close:
            change_pct = (price - prev_close) / prev_close * 100

        # Intraday candles for low and VWAP
        intraday_low = None
        intraday_low_change = None
        vwap_val = None
        try:
            intraday_df = ticker_obj.history(period="1d", interval=candle_interval, prepost=prepost)
            if not intraday_df.empty:
                intraday_low = float(intraday_df["Low"].min())
                if prev_close:
                    intraday_low_change = (intraday_low - prev_close) / prev_close * 100
                from ..utils.indicators import vwap
                vwap_val = vwap(intraday_df)
        except Exception:
            pass

        # Daily history for RSI and 200-DMA
        rsi_val = None
        ma200 = None
        dma200_dist_pct = None
        dma200_slope = None
        dma200_slope_rising = None
        days_since_pullback = None
        try:
            hist = ticker_obj.history(period="200d", interval="1d")
            if not hist.empty:
                from ..utils.indicators import rsi, sma, ma_slope
                ma200 = sma(hist["Close"], 200)
                if ma200:
                    dma200_dist_pct = (price - ma200) / ma200 * 100
                dma200_slope = ma_slope(hist["Close"], window=200, lookback=5)
                if dma200_slope is not None:
                    dma200_slope_rising = dma200_slope > 0
                rsi_val = rsi(hist["Close"], 14)
                # pullback window: look back 3 days for lowest close relative to prev_close
                recent = hist.tail(4)
                if not recent.empty:
                    last_close = recent["Close"].iloc[-1]
                    prior_close = recent["Close"].shift(1).iloc[-1]
                    if prior_close and last_close < prior_close:
                        days_since_pullback = 1
                    else:
                        days_since_pullback = int(
                            (recent["Close"].idxmax() - recent["Close"].idxmin()).days
                            if len(recent) > 1
                            else None
                        )
        except Exception:
            pass

        # Fundamentals: FCF or net income positive
        positive_fcf_or_income = None
        try:
            fcf = detailed.get("freeCashflow")
            net_income = detailed.get("netIncomeToCommon") or detailed.get("netIncome")
            if fcf is not None or net_income is not None:
                positive_fcf_or_income = (fcf or 0) > 0 or (net_income or 0) > 0
        except Exception:
            pass

        analyst_rating = detailed.get("recommendationKey")

        # Dollar volume (using avg)
        dollar_volume = price * avg_volume if price and avg_volume else None
        rel_vol = (volume / avg_volume) if avg_volume else None

        name = detailed.get("shortName") or detailed.get("longName")
        sector = detailed.get("sector")

        return Quote(
            ticker=ticker.upper(),
            price=price,
            prev_close=prev_close,
            change_pct=change_pct,
            volume=volume,
            avg_volume=avg_volume,
            market_cap=market_cap,
            dollar_volume=dollar_volume,
            intraday_low=intraday_low,
            intraday_low_change=intraday_low_change,
            rsi=rsi_val,
            relative_volume=rel_vol,
            vwap=vwap_val,
            ma200=ma200,
            dma200_dist_pct=dma200_dist_pct,
            dma200_slope=dma200_slope,
            dma200_slope_rising=dma200_slope_rising,
            days_since_pullback_start=days_since_pullback,
            name=name,
            sector=sector,
            positive_fcf_or_income=positive_fcf_or_income,
            analyst_rating=analyst_rating,
            timestamp=int(time.time()),
        )

    def get_intraday_low(self, ticker: str, interval: str = "5m", prepost: bool = False) -> Tuple[float | None, float | None]:
        """
        Return (low_price, change_pct_from_prev_close) for today's session using intraday candles.
        """
        t = yf.Ticker(ticker)
        try:
            df = t.history(period="1d", interval=interval, prepost=prepost)
            if df.empty or "Low" not in df:
                return None, None
            low_price = float(df["Low"].min())
            prev_close = float(df["Close"].iloc[0]) if len(df) else None
            if prev_close:
                low_change = (low_price - prev_close) / prev_close * 100
            else:
                # best effort using last close from info
                info = t.fast_info
                pc = info.get("previous_close") or info.get("regularMarketPreviousClose")
                low_change = (low_price - pc) / pc * 100 if pc else None
            return low_price, low_change
        except Exception:
            return None, None

    def get_news_headlines(self, ticker: str, max_items: int = 8, cache_seconds: int = 300) -> list[dict]:
        """
        Return recent headlines as list of dicts: {'title','publisher','published'}.
        Cached for a few minutes to avoid spamming Yahoo.
        """
        now = time.time()
        tk = ticker.upper()
        cached = self._news_cache.get(tk)
        if cached and now - cached[0] < cache_seconds:
            return cached[1]
        try:
            ticker_obj = yf.Ticker(ticker)
            news_items = ticker_obj.get_news() or []
            headlines = []
            for item in news_items[:max_items]:
                title = item.get("title")
                if not title:
                    continue
                pub_ts = item.get("providerPublishTime") or item.get("published_at")
                published = datetime.fromtimestamp(pub_ts) if pub_ts else None
                headlines.append(
                    {
                        "title": title,
                        "publisher": (item.get("publisher") or "").lower(),
                        "published": published,
                    }
                )
            self._news_cache[tk] = (now, headlines)
            return headlines
        except Exception:
            return []
