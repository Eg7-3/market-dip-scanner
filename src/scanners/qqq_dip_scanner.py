from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd
import yfinance as yf

from ..config import Config
from ..providers.base import DataProvider, Quote
from ..utils.indicators import rsi, sma, ma_slope, vwap
from ..utils.state_store import StateStore
from ..utils.time_utils import is_market_open


@dataclass
class AlertContext:
    ticker: str
    quote: Quote
    reason: str
    news_flag: bool
    news_hint: str
    breadth_line: Optional[str] = None


class QQQDipScanner:
    """
    Single, ordered decision pipeline for dip alerts.
    """

    def __init__(self, cfg: Config, provider: DataProvider, state_path: Path):
        self.cfg = cfg
        self.provider = provider
        self.state = StateStore(state_path, tz=cfg.market_timezone)

    # --- Helpers ---------------------------------------------------------
    def _news_flag(self, ticker: str) -> tuple[bool, str]:
        headlines = self.provider.get_news_headlines(ticker)
        search_url = f"https://news.google.com/search?q={ticker}%20stock&hl=en-US&gl=US&ceid=US:en"
        if not headlines:
            return False, f"News check: Google News search '{ticker} stock'"

        cutoff = datetime.now() - timedelta(hours=self.cfg.news_lookback_hours)
        for item in headlines:
            title = (item.get("title") or "").lower()
            publisher = (item.get("publisher") or "").lower()
            published = item.get("published")
            if published and published < cutoff:
                continue
            if publisher and publisher in {p.lower() for p in self.cfg.news_blocked_publishers}:
                continue
            if any(k in title for k in self.cfg.news_keywords):
                pub_label = f" ({publisher})" if publisher else ""
                return True, f"⚠️ Risky news{pub_label}: {title}"
        return False, f"News check: Google News search '{ticker} stock'"

    def _fetch_quotes(self, tickers: List[str]) -> dict[str, Quote]:
        quotes: dict[str, Quote] = {}
        for t in tickers:
            for attempt in range(4):
                try:
                    quotes[t] = self.provider.get_quote(
                        t,
                        candle_interval=self.cfg.candle_interval,
                        prepost=self.cfg.after_hours_enabled,
                    )
                    break
                except Exception as exc:
                    msg = str(exc).lower()
                    if "too many requests" in msg or "rate limit" in msg:
                        delay = 1.0 + attempt * 0.8
                        logging.debug("Rate limited on %s; retrying in %.2fs (attempt %d)", t, delay, attempt + 1)
                        time.sleep(delay)
                        continue
                    logging.warning("Quote failed for %s: %s", t, exc)
                    break
            time.sleep(0.03)
        return quotes

    def _quote_from_backtest(self, ticker: str, date_str: str) -> Quote | None:
        """
        Build a Quote from historical data for deterministic backtests.
        """
        try:
            date = pd.Timestamp(date_str)
            next_day = (date + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
            # Intraday candles for that session
            intraday = yf.Ticker(ticker).history(
                start=date_str, end=next_day, interval=self.cfg.candle_interval, prepost=self.cfg.after_hours_enabled
            )
            if intraday.empty:
                return None
            low = float(intraday["Low"].min())
            close = float(intraday["Close"].iloc[-1])
            vwap_val = float((intraday["Close"] * intraday["Volume"]).sum() / intraday["Volume"].sum())
            vol_today = int(intraday["Volume"].sum())

            # Daily bars for prev close / averages
            daily = yf.Ticker(ticker).history(start=(date - pd.Timedelta(days=5)).strftime("%Y-%m-%d"), end=next_day, interval="1d")
            if daily.empty or len(daily) < 2:
                return None
            prev_close = float(daily["Close"].shift(1).iloc[-1])
            closes = daily["Close"].values
            volumes = daily["Volume"].values
            avg_vol = int(pd.Series(volumes).rolling(20, min_periods=1).mean().iloc[-1])
            ma200_series = yf.Ticker(ticker).history(period="260d", interval="1d")["Close"]
            ma200_val = float(ma200_series.rolling(200, min_periods=50).mean().iloc[-1])
            dma_slope = ma_slope(ma200_series.tail(220)) if len(ma200_series) >= 20 else 0.0

            change_pct = (close - prev_close) / prev_close * 100 if prev_close else 0.0
            intraday_low_change = (low - prev_close) / prev_close * 100 if prev_close else change_pct
            relvol = vol_today / avg_vol if avg_vol else None
            dollar_vol = close * vol_today

            info = yf.Ticker(ticker).fast_info
            mcap = getattr(info, "market_cap", None) or 50_000_000_000

            return Quote(
                ticker=ticker.upper(),
                price=close,
                prev_close=prev_close,
                change_pct=change_pct,
                volume=vol_today,
                avg_volume=avg_vol,
                market_cap=mcap,
                dollar_volume=dollar_vol,
                intraday_low=low,
                intraday_low_change=intraday_low_change,
                rsi=float(rsi(pd.Series(closes))) if len(closes) >= 15 else None,
                relative_volume=relvol,
                vwap=vwap_val,
                ma200=ma200_val,
                dma200_dist_pct=(close / ma200_val - 1) * 100 if ma200_val else None,
                dma200_slope=dma_slope,
                dma200_slope_rising=dma_slope is not None and dma_slope >= 0,
                name=None,
                sector=None,
                positive_fcf_or_income=True,
                analyst_rating=None,
            )
        except Exception as exc:
            logging.debug("Backtest build failed for %s: %s", ticker, exc)
            return None

    # --- Core pipeline ---------------------------------------------------
    def scan(self, breadth_line: str | None = None, backtest_date: str | None = None, simulate: dict | None = None) -> List[AlertContext]:
        self.state.reset_if_new_day()

        # Universe: QQQ holdings + custom watchlist
        tickers = set(self.provider.get_constituents(cache_hours=self.cfg.holdings_cache_hours))
        tickers.update([t.upper() for t in self.cfg.custom_watchlist])
        if simulate:
            tickers.add(simulate["ticker"].upper())
        tickers = sorted(tickers)

        quotes = {}
        if backtest_date:
            for t in tickers:
                q = self._quote_from_backtest(t, backtest_date)
                if q:
                    quotes[t] = q
        elif simulate:
            q = Quote(
                ticker=simulate["ticker"].upper(),
                price=100.0,
                prev_close=100.0,
                change_pct=simulate.get("dip", 0),
                volume=10_000_000,
                avg_volume=10_000_000,
                market_cap=50_000_000_000,
            )
            q.intraday_low_change = simulate.get("dip")
            q.intraday_low = q.price * (1 + q.intraday_low_change / 100)
            q.rsi = simulate.get("rsi")
            q.relative_volume = simulate.get("relvol")
            q.ma200 = 100.0
            q.dma200_dist_pct = simulate.get("dist200")
            q.dollar_volume = q.price * q.avg_volume
            quotes[q.ticker] = q
        else:
            quotes = self._fetch_quotes(list(tickers))

        # QQQ day change (for context / optional confirmation)
        try:
            if backtest_date:
                qqq_bt = self._quote_from_backtest("QQQ", backtest_date)
                qqq_change = qqq_bt.change_pct if qqq_bt else None
            else:
                qqq_quote = self.provider.get_quote("QQQ", candle_interval=self.cfg.candle_interval, prepost=self.cfg.after_hours_enabled)
                qqq_change = qqq_quote.change_pct
        except Exception:
            qqq_change = None

        alerts: List[AlertContext] = []
        audit = {
            "green_pass": 0,
            "yellow_pass": 0,
            "yellow_fail": 0,
            "red_reject": 0,
            "tier1": 0,
            "tier2": 0,
            "tier_upgrade": 0,
        }

        for ticker in tickers:
            quote = quotes.get(ticker)
            if quote is None:
                continue

            # Metric computation in one place
            price = quote.price
            prev_close = quote.prev_close or price
            pct_from_prev = (price - prev_close) / prev_close * 100 if prev_close else None
            intraday_low_pct = quote.intraday_low_change if quote.intraday_low_change is not None else pct_from_prev
            dist200 = quote.dma200_dist_pct
            if dist200 is not None and quote.ma200:
                dist200 = (price / quote.ma200 - 1) * 100
                quote.dma200_dist_pct = dist200
            relvol = quote.relative_volume
            rsi = quote.rsi

            # Universe-level data quality filters
            if quote.market_cap < self.cfg.market_cap_min:
                logging.debug("%s skipped: market cap %.1fB < min %.1fB", ticker, quote.market_cap / 1e9, self.cfg.market_cap_min / 1e9)
                continue
            if quote.avg_volume < self.cfg.avg_volume_min:
                logging.debug("%s skipped: avg vol %s < min %s", ticker, quote.avg_volume, self.cfg.avg_volume_min)
                continue
            if quote.dollar_volume is None or quote.dollar_volume < self.cfg.min_dollar_volume:
                logging.debug("%s skipped: dollar vol %s < min %s", ticker, quote.dollar_volume, self.cfg.min_dollar_volume)
                continue
            if quote.positive_fcf_or_income is False:
                logging.debug("%s skipped: negative FCF and net income", ticker)
                continue
            if quote.analyst_rating and quote.analyst_rating.lower() == "sell":
                logging.debug("%s skipped: analyst rating sell", ticker)
                continue

            # Dip metric selection
            market_open = True if backtest_date else is_market_open(self.cfg.market_timezone, self.cfg.cooldown_minutes_after_open)
            dip_metric = intraday_low_pct
            metric_used = "intraday_low_pct"
            # After hours: pick a consistent metric and log the choice
            if not market_open:
                if self.cfg.after_hours_enabled and quote.change_pct is not None and quote.intraday_low_change is not None:
                    dip_metric = min(quote.intraday_low_change, quote.change_pct)
                    metric_used = "min_regular_ext"
                else:
                    dip_metric = quote.intraday_low_change or quote.change_pct
                    metric_used = "regular_session_low"
            metric_label = {
                "intraday_low_pct": "intraday low",
                "regular_session_low": "regular session low",
                "min_regular_ext": "min(reg session, extended)",
            }.get(metric_used, metric_used)

            # Tier selection
            tier = None
            tier_min_conf = None
            tier_rsi_max = None
            tier_relvol_min = None
            if self.cfg.tiered_dips_enabled:
                if dip_metric is None:
                    continue
                if dip_metric <= self.cfg.tier2_dip:
                    tier = 2
                    tier_min_conf = self.cfg.tier2_min_confirmations
                    tier_rsi_max = self.cfg.tier2_rsi_max
                    tier_relvol_min = self.cfg.tier2_relvol_min
                    audit["tier2"] += 1
                elif dip_metric <= self.cfg.tier1_dip:
                    tier = 1
                    tier_min_conf = self.cfg.tier1_min_confirmations
                    tier_rsi_max = self.cfg.tier1_rsi_max
                    tier_relvol_min = self.cfg.tier1_relvol_min
                    audit["tier1"] += 1
                else:
                    logging.debug("%s skipped: dip %.2f%% above tier thresholds", ticker, dip_metric)
                    continue
            else:
                if dip_metric is None or dip_metric > self.cfg.dip_threshold:
                    continue
                tier = 2
                tier_min_conf = 2
                tier_rsi_max = self.cfg.rsi_threshold
                tier_relvol_min = self.cfg.relative_volume_min

            # 200-DMA zones
            zone = "UNKNOWN"
            setup_grade = "B"
            if dist200 is not None:
                if dist200 >= self.cfg.dma200_green_pct:
                    zone = "GREEN"
                    setup_grade = "A"
                    audit["green_pass"] += 1
                elif dist200 > self.cfg.dma200_red_pct:
                    zone = "YELLOW"
                    setup_grade = "B"
                    if self.cfg.require_rising_dma200_in_yellow and quote.dma200_slope_rising is False:
                        audit["yellow_fail"] += 1
                        logging.debug("%s skipped: YELLOW but 200-DMA slope not rising", ticker)
                        continue
                    audit["yellow_pass"] += 1
                else:
                    zone = "RED"
                    setup_grade = "B"  # downgrade later if needed
                    if dist200 <= self.cfg.hard_reject_below_200dma_pct:
                        audit["red_reject"] += 1
                        logging.debug("%s rejected: RED hard floor dist %.2f%%", ticker, dist200)
                        continue
                    # downgrade grade for red but allow
                    setup_grade = "C"
            else:
                zone = "UNKNOWN"

            # Confirmations (scored)
            passed = []
            failed = []
            confirmations = 0

            def req(cond, label):
                nonlocal confirmations
                if cond:
                    confirmations += 1
                    passed.append(label)
                else:
                    failed.append(label)

            req(rsi is not None and rsi <= tier_rsi_max, f"RSI<={tier_rsi_max}")
            req(relvol is not None and relvol >= tier_relvol_min, f"RelVol>={tier_relvol_min}")
            req(quote.dollar_volume is not None and quote.dollar_volume >= self.cfg.min_dollar_volume, "$Vol")
            req(quote.market_cap >= self.cfg.market_cap_min, "MktCap")
            req(quote.vwap and price <= quote.vwap, "VWAP touch")
            if qqq_change is not None:
                req(qqq_change > -2.5, "QQQ>-2.5%")
            if self.cfg.require_fast_selloff and quote.days_since_pullback_start is not None:
                req(quote.days_since_pullback_start <= 3, "Fast selloff")

            min_conf = tier_min_conf
            if zone == "YELLOW":
                min_conf = max(min_conf, 3)
            if zone == "RED":
                min_conf = max(min_conf, 3)

            if confirmations < min_conf:
                logging.debug("%s skipped: %d confirmations (need %d) zone=%s tier=%s", ticker, confirmations, min_conf, zone, tier)
                continue

            # Dedup logic
            if not self.state.should_alert(
                ticker,
                low_change_pct=dip_metric,
                realert_delta=self.cfg.realert_delta,
                tier=tier,
                price=price,
                cooldown_minutes=self.cfg.dedupe_cooldown_minutes,
                testing_mode=self.cfg.testing_mode or bool(backtest_date) or bool(simulate),
            ):
                logging.debug("%s deduped", ticker)
                continue

            flagged, hint = self._news_flag(ticker)

            # Grade adjustments for zone
            emoji_grade = {"A": "🟢", "B": "🟡", "C": "🔴"}.get(setup_grade, "⚪️")
            tier_label = "Tier 2 (PANIC)" if tier == 2 else "Tier 1 (EARLY FEAR)"
            tier_emoji = "🚀" if tier == 2 else "⚡️"

            # Message render (single place)
            def fmt(val, pattern):
                return pattern.format(val) if val is not None else "n/a"

            why_pass = " • ".join(passed)
            why_fail = " • ".join(failed[:3])
            header = f"{tier_emoji} {emoji_grade} **{ticker} — {quote.name or ''} ({quote.sector or 'N/A'})**"
            line2 = (
                f"{tier_label} · Grade {setup_grade} · Dip {dip_metric:.2f}% ({metric_label}) · Dist200 {fmt(dist200, '{:.2f}')}% · RSI {fmt(rsi, '{:.1f}')} · RelVol {fmt(relvol, '{:.2f}')} · $Vol {fmt(quote.dollar_volume/1e9 if quote.dollar_volume else None, '{:.2f}')}B · QQQ {fmt(qqq_change, '{:.2f}%') if qqq_change is not None else 'n/a'}"
            )
            line3 = f"Px {price:.2f} | Prev {fmt(prev_close, '{:.2f}')} | Intraday Low {fmt(quote.intraday_low, '{:.2f}')} | VWAP {fmt(quote.vwap, '{:.2f}')} | MA200 {fmt(quote.ma200, '{:.2f}')}"
            why_line = "Why ✅ " + why_pass if why_pass else ""
            fail_line = "Why ❌ " + why_fail if why_fail else ""
            extra_risks = []
            if zone == "RED":
                extra_risks.append("Below 200DMA")
            risk_text = " | ".join(extra_risks) if extra_risks else ""
            risk_line = hint if not flagged else f"⚠️ Risk flag · {hint}"
            if risk_text:
                risk_line = f"{risk_line} · {risk_text}"
            context = f"Context: {breadth_line}" if breadth_line else ""

            reason = "\n".join(x for x in [header, line2, line3, why_line, fail_line, risk_line, context] if x)

            alerts.append(
                AlertContext(
                    ticker=ticker,
                    quote=quote,
                    reason=reason,
                    news_flag=flagged,
                    news_hint=hint,
                    breadth_line=breadth_line,
                )
            )

        logging.debug("AUDIT 200DMA zones: %s", audit)
        return alerts
