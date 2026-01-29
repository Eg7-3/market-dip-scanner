from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from ..providers.base import DataProvider, Quote
from ..utils.state_store import StateStore


@dataclass
class AlertContext:
    ticker: str
    quote: Quote
    reason: str
    news_flag: bool
    news_hint: str
    breadth_line: Optional[str] = None


class QQQDipScanner:
    def __init__(
        self,
        provider: DataProvider,
        state_path: Path,
        max_workers: int,
        market_cap_min: float,
        avg_volume_min: float,
        min_dollar_volume: float,
        dip_threshold: float,
        realert_delta: float,
        news_keywords: List[str],
        use_intraday_low: bool,
        candle_interval: str,
        holdings_cache_hours: int,
        after_hours_enabled: bool,
        rsi_threshold: float,
        relative_volume_min: float,
        tiered_dips_enabled: bool,
        tier1_dip: float,
        tier2_dip: float,
        tier1_min_confirmations: int,
        tier2_min_confirmations: int,
        tier1_rsi_max: float,
        tier2_rsi_max: float,
        tier1_relvol_min: float,
        tier2_relvol_min: float,
        dma200_tolerance_pct: float,
        dma200_green_pct: float,
        dma200_red_pct: float,
        allow_red_reclaim: bool,
        require_rising_dma200_in_yellow: bool,
        require_fast_selloff: bool,
    ):
        self.provider = provider
        self.state = StateStore(state_path)
        self.max_workers = max(1, max_workers)
        self.market_cap_min = market_cap_min
        self.avg_volume_min = avg_volume_min
        self.min_dollar_volume = min_dollar_volume
        self.dip_threshold = dip_threshold
        self.realert_delta = realert_delta
        self.news_keywords = [k.lower() for k in news_keywords]
        self.use_intraday_low = use_intraday_low
        self.candle_interval = candle_interval
        self.holdings_cache_hours = holdings_cache_hours
        self.after_hours_enabled = after_hours_enabled
        self.rsi_threshold = rsi_threshold
        self.relative_volume_min = relative_volume_min
        self.tiered_dips_enabled = tiered_dips_enabled
        self.tier1_dip = tier1_dip
        self.tier2_dip = tier2_dip
        self.tier1_min_confirmations = tier1_min_confirmations
        self.tier2_min_confirmations = tier2_min_confirmations
        self.tier1_rsi_max = tier1_rsi_max
        self.tier2_rsi_max = tier2_rsi_max
        self.tier1_relvol_min = tier1_relvol_min
        self.tier2_relvol_min = tier2_relvol_min
        self.dma200_tolerance_pct = dma200_tolerance_pct
        self.dma200_green_pct = dma200_green_pct
        self.dma200_red_pct = dma200_red_pct
        self.allow_red_reclaim = allow_red_reclaim
        self.require_rising_dma200_in_yellow = require_rising_dma200_in_yellow
        self.require_fast_selloff = require_fast_selloff

    def _news_flag(self, ticker: str) -> tuple[bool, str]:
        headlines = self.provider.get_news_headlines(ticker)
        search_url = f"https://news.google.com/search?q={ticker}%20stock&hl=en-US&gl=US&ceid=US:en"
        if not headlines:
            return False, f"News check: [Google News]({search_url})"

        from datetime import datetime, timedelta

        cutoff = datetime.now() - timedelta(hours=getattr(self, "news_lookback_hours", 48))

        for item in headlines:
            title = item.get("title", "").lower()
            publisher = item.get("publisher", "").lower()
            published = item.get("published")

            if cutoff and published and published < cutoff:
                continue
            if publisher and any(bp in publisher for bp in ["seeking alpha transcript", "motley fool transcript"]):
                continue
            if any(k in title for k in self.news_keywords):
                pub_label = f" ({publisher})" if publisher else ""
                return True, f"⚠️ HIGH RISK – NEWS MATCH{pub_label}: {title} | [News]({search_url})"

        return False, f"News check: [Google News]({search_url})"

    def _fetch_quotes_parallel(self, tickers: List[str]) -> dict[str, Quote]:
        """
        Fetch quotes sequentially with backoff to avoid Yahoo/yfinance rate limits.
        Still preserves ordering and reliability; speed is acceptable for ~100 tickers.
        """
        quotes: dict[str, Quote] = {}

        for t in tickers:
            for attempt in range(4):
                try:
                    quotes[t] = self.provider.get_quote(
                        t,
                        candle_interval=self.candle_interval,
                        prepost=self.after_hours_enabled,
                    )
                    break
                except Exception as exc:
                    msg = str(exc).lower()
                    if "too many requests" in msg or "rate limit" in msg:
                        delay = 1.0 + attempt * 0.75
                        logging.debug("Rate limited on %s; retrying in %.2fs (attempt %d)", t, delay, attempt + 1)
                        time.sleep(delay)
                        continue
                    logging.warning("Quote failed for %s: %s", t, exc)
                    break
            else:
                logging.warning("Quote failed for %s after retries", t)

            # small stagger to be gentle with API
            time.sleep(0.05)
        return quotes

    def scan(self) -> List[AlertContext]:
        self.state.reset_if_new_day()
        tickers = self.provider.get_constituents(cache_hours=self.holdings_cache_hours)
        alerts: List[AlertContext] = []
        audit = {"green_pass": 0, "yellow_pass": 0, "yellow_fail": 0, "red_reject": 0, "tier1": 0, "tier2": 0, "tier_upgrade": 0}
        try:
            qqq_quote = self.provider.get_quote("QQQ", candle_interval=self.candle_interval, prepost=self.after_hours_enabled)
            qqq_change = qqq_quote.change_pct
        except Exception:
            qqq_change = None

        quotes = self._fetch_quotes_parallel(tickers)

        for ticker in tickers:
            quote = quotes.get(ticker)
            if quote is None:
                continue

            if quote.market_cap < self.market_cap_min:
                logging.debug("%s skipped: market cap %.1fB < min %.1fB", ticker, quote.market_cap / 1e9, self.market_cap_min / 1e9)
                continue
            if quote.avg_volume < self.avg_volume_min:
                logging.debug("%s skipped: avg vol %s < min %s", ticker, quote.avg_volume, self.avg_volume_min)
                continue
            if quote.dollar_volume is None or quote.dollar_volume < self.min_dollar_volume:
                logging.debug("%s skipped: dollar vol %s < min %s", ticker, quote.dollar_volume, self.min_dollar_volume)
                continue
            if quote.positive_fcf_or_income is False:
                logging.debug("%s skipped: negative FCF and net income", ticker)
                continue
            if quote.analyst_rating and quote.analyst_rating.lower() == "sell":
                logging.debug("%s skipped: analyst rating sell", ticker)
                continue
            intraday_low_change = quote.change_pct
            intraday_low_price = quote.price
            if self.use_intraday_low:
                if quote.intraday_low_change is not None:
                    intraday_low_change = quote.intraday_low_change
                if quote.intraday_low is not None:
                    intraday_low_price = quote.intraday_low

            quote.intraday_low = intraday_low_price
            quote.intraday_low_change = intraday_low_change

            # Tiered dip detection
            tier = None  # 1 or 2
            tier_label = None
            tier_min_conf = None
            tier_rsi_max = self.rsi_threshold
            tier_relvol_min = self.relative_volume_min
            if self.tiered_dips_enabled:
                if intraday_low_change <= self.tier2_dip:
                    tier = 2
                    tier_label = "Tier 2 (PANIC)"
                    tier_min_conf = self.tier2_min_confirmations
                    tier_rsi_max = self.tier2_rsi_max
                    tier_relvol_min = self.tier2_relvol_min
                    audit["tier2"] += 1
                elif intraday_low_change <= self.tier1_dip:
                    tier = 1
                    tier_label = "Tier 1 (EARLY FEAR)"
                    tier_min_conf = self.tier1_min_confirmations
                    tier_rsi_max = self.tier1_rsi_max
                    tier_relvol_min = self.tier1_relvol_min
                    audit["tier1"] += 1
                else:
                    logging.debug("%s skipped: intraday low %.2f%% above tier thresholds", ticker, intraday_low_change)
                    continue
            else:
                # legacy single threshold
                if intraday_low_change > self.dip_threshold:
                    logging.debug(
                        "%s skipped: intraday low change %.2f%% above threshold %.2f%%",
                        ticker,
                        intraday_low_change,
                        self.dip_threshold,
                    )
                    continue
                tier = 2
                tier_label = "Tier 2 (PANIC)"
                tier_min_conf = 2

            # 200-DMA zone logic
            dist = quote.dma200_dist_pct
            zone = "RED"
            setup_grade = "C"
            if dist is not None and quote.ma200 is not None:
                if dist >= self.dma200_green_pct:
                    zone = "GREEN"
                    setup_grade = "A"
                elif dist > self.dma200_red_pct:
                    zone = "YELLOW"
                    setup_grade = "B"
                else:
                    zone = "RED"

            if zone == "RED":
                audit["red_reject"] += 1
                if not self.allow_red_reclaim or quote.price <= (quote.ma200 or 0):
                    logging.debug("%s rejected: RED zone (dist %.2f%%)", ticker, dist if dist is not None else float("nan"))
                    continue
                setup_grade = "C"
            elif zone == "YELLOW":
                if self.require_rising_dma200_in_yellow and (quote.dma200_slope_rising is False):
                    audit["yellow_fail"] += 1
                    logging.debug("%s skipped: YELLOW zone but 200DMA slope not rising", ticker)
                    continue
                audit["yellow_pass"] += 1
            else:
                audit["green_pass"] += 1

            if self.require_fast_selloff and (quote.days_since_pullback_start is not None and quote.days_since_pullback_start > 3):
                logging.debug("%s skipped: pullback older than 3 days", ticker)
                continue
            # Pre-compute RSI / rel-vol vs tier-specific thresholds
            rsi_ok = quote.rsi is not None and quote.rsi <= tier_rsi_max
            relvol_ok = quote.relative_volume and quote.relative_volume >= tier_relvol_min

            # Tier-specific quality gates (RSI / relative volume strictness)
            if tier == 1 and not (rsi_ok and relvol_ok):
                logging.debug(
                    "%s skipped: Tier1 requires RSI<=%.1f and relvol>=%.2f (got rsi_ok=%s relvol_ok=%s)",
                    ticker,
                    tier_rsi_max,
                    tier_relvol_min,
                    rsi_ok,
                    relvol_ok,
                )
                continue
            if tier == 2 and not (rsi_ok or relvol_ok):
                logging.debug(
                    "%s skipped: Tier2 needs RSI<=%.1f OR relvol>=%.2f (got rsi_ok=%s relvol_ok=%s)",
                    ticker,
                    tier_rsi_max,
                    tier_relvol_min,
                    rsi_ok,
                    relvol_ok,
                )
                continue

            # Confirmation filters with tier/zone-specific thresholds
            confirmations = 0
            met_signals = []
            if relvol_ok:
                confirmations += 1
                met_signals.append("relvol")
            if rsi_ok:
                confirmations += 1
                met_signals.append("rsi")
            if quote.vwap and quote.price <= quote.vwap:
                confirmations += 1
                met_signals.append("vwap_touch ✋")
            if qqq_change is not None and qqq_change > -2.5:
                confirmations += 1
                met_signals.append("qqq_ok 📊")

            # zone-specific requirements
            if zone == "GREEN":
                min_conf = tier_min_conf
            elif zone == "YELLOW":
                min_conf = max(tier_min_conf, 3)
            else:  # RED reclaim (if allowed)
                min_conf = max(tier_min_conf, 3)

            if confirmations < min_conf:
                logging.debug("%s skipped: only %d confirmations (need %d) tier=%s zone=%s", ticker, confirmations, min_conf, tier_label, zone)
                if zone == "YELLOW":
                    audit["yellow_fail"] += 1
                continue

            prev_entry = self.state.get_today_entry(ticker)
            prev_tier = None
            if prev_entry:
                prev_tier = prev_entry.get("tier")

            if not self.state.should_alert(ticker, intraday_low_change, self.realert_delta, tier=tier):
                logging.debug(
                    "%s deduped: low change %.2f%%, need %.2f%% more for re-alert",
                    ticker,
                    intraday_low_change,
                    self.realert_delta,
                )
                continue
            if prev_tier is not None and tier is not None and tier > prev_tier:
                audit["tier_upgrade"] += 1

            flagged, hint = self._news_flag(ticker)
            def fmt(val, fmtstr):
                return fmtstr.format(val) if val is not None else "n/a"

            emoji = {"A": "🟢", "B": "🟡", "C": "🔴"}.get(setup_grade, "⚪️")
            tier_emoji = "🚀" if tier_label.startswith("Tier 2") else "⚡️"
            reason = (
                f"{tier_emoji} {emoji} Grade {setup_grade} | "
                f"{tier_label} | "
                f"Dip {intraday_low_change:.2f}% (current {quote.change_pct:.2f}%), "
                f"RSI {fmt(quote.rsi, '{:.1f}')}, "
                f"rel vol {fmt(quote.relative_volume, '{:.2f}')}, "
                f"MA200 {fmt(quote.ma200, '{:.2f}')}, "
                f"dist {fmt(dist, '{:.2f}')}% (slope {'rising' if quote.dma200_slope_rising else 'flat/declining'}), "
                f"QQQ {fmt(qqq_change, '{:.2f}')}"
            )
            alerts.append(
                AlertContext(
                    ticker=ticker,
                    quote=quote,
                    reason=reason,
                    news_flag=flagged,
                    news_hint=hint,
                    breadth_line=None,
                )
            )
        logging.debug("AUDIT 200DMA zones: %s", audit)
        return alerts
