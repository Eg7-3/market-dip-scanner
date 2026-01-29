from __future__ import annotations

import argparse
import logging
import time
from pathlib import Path

from .alerts.telegram import TelegramAlerter
from .config import Config
from .providers.yahoo_provider import YahooProvider
from .scanners.qqq_dip_scanner import QQQDipScanner
from .scanners.sell_alerts import SellAlertEngine
from .utils.time_utils import is_market_open, is_weekend, now_tz


def build_breadth_line(provider: YahooProvider) -> str | None:
    try:
        qqq = provider.get_quote("QQQ")
        return f"QQQ {qqq.change_pct:.2f}% today"
    except Exception:
        return None


def format_dip_message(ctx, breadth_line: str | None) -> str:
    quote = ctx.quote
    def fmt(val, pattern):
        return pattern.format(val) if val is not None else "n/a"

    header = f"{ctx.ticker} — {quote.name or ''}".strip()
    sector = f"{quote.sector}" if quote.sector else ""

    price_line = f"💰 Price ${quote.price:.2f} (cur {quote.change_pct:.2f}%) • Low ${fmt(quote.intraday_low, '{:.2f}')} ({fmt(quote.intraday_low_change, '{:.2f}')}%)"
    strength_line = f"📊 RSI {fmt(quote.rsi, '{:.1f}') } | RelVol {fmt(quote.relative_volume, '{:.2f}')} | VWAP {fmt(quote.vwap, '{:.2f}')}"
    trend_line = f"📈 200-DMA {fmt(quote.ma200, '{:.2f}')} | Dist {fmt(quote.dma200_dist_pct, '{:.2f}')}%"
    size_line = f"🏦 MktCap ${quote.market_cap/1e9:.1f}B | $Vol ${quote.dollar_volume/1e9:.2f}B | Vol {quote.volume:,}/{quote.avg_volume:,}"
    context_line = f"🌐 {breadth_line}" if breadth_line else ""

    parts = [
        f"🚀 *{header}* {f'({sector})' if sector else ''}",
        ctx.reason,
        price_line,
        strength_line,
        trend_line,
        size_line,
    ]
    if context_line:
        parts.append(context_line)
    parts.append(ctx.news_hint)
    return "\n".join(p for p in parts if p)


def format_sell_message(alert) -> str:
    q = alert.quote
    return "\n".join(
        [
            f"*SELL ALERT* {alert.ticker}",
            f"Price ${q.price:.2f} ({q.change_pct:.2f}%)",
            f"Hit {alert.target_hit}",
            f"Entry {alert.entry_price:.2f} on {alert.entry_date}",
            alert.notes or "",
        ]
    )


def run_once(cfg: Config, alerter: TelegramAlerter, provider: YahooProvider, data_dir: Path) -> None:
    breadth_line = build_breadth_line(provider)
    scanner = QQQDipScanner(
        provider=provider,
        state_path=data_dir / "state.json",
        max_workers=cfg.max_workers,
        market_cap_min=cfg.market_cap_min,
        avg_volume_min=cfg.avg_volume_min,
        min_dollar_volume=cfg.min_dollar_volume,
        dip_threshold=cfg.dip_threshold,
        realert_delta=cfg.realert_delta,
        news_keywords=cfg.news_keywords,
        use_intraday_low=cfg.use_intraday_low,
        candle_interval=cfg.candle_interval,
        holdings_cache_hours=cfg.holdings_cache_hours,
        after_hours_enabled=cfg.after_hours_enabled,
        rsi_threshold=cfg.rsi_threshold,
        relative_volume_min=cfg.relative_volume_min,
        tiered_dips_enabled=cfg.tiered_dips_enabled,
        tier1_dip=cfg.tier1_dip,
        tier2_dip=cfg.tier2_dip,
        tier1_min_confirmations=cfg.tier1_min_confirmations,
        tier2_min_confirmations=cfg.tier2_min_confirmations,
        tier1_rsi_max=cfg.tier1_rsi_max,
        tier2_rsi_max=cfg.tier2_rsi_max,
        tier1_relvol_min=cfg.tier1_relvol_min,
        tier2_relvol_min=cfg.tier2_relvol_min,
        dma200_tolerance_pct=cfg.dma200_tolerance_pct,
        dma200_green_pct=cfg.dma200_green_pct,
        dma200_red_pct=cfg.dma200_red_pct,
        allow_red_reclaim=cfg.allow_red_reclaim,
        require_rising_dma200_in_yellow=cfg.require_rising_dma200_in_yellow,
        require_fast_selloff=cfg.require_fast_selloff,
    )
    alerts = scanner.scan()
    for ctx in alerts:
        msg = format_dip_message(ctx, breadth_line)
        alerter.send(msg)
        logging.info("Alert sent for %s", ctx.ticker)

    if cfg.enable_sell_alerts:
        sell_engine = SellAlertEngine(provider, data_dir / "positions.json", [cfg.take_profit_3, cfg.take_profit_2, cfg.take_profit_1])
        for alert in sell_engine.scan():
            alerter.send(format_sell_message(alert))
            logging.info("Sell alert sent for %s", alert.ticker)


def main() -> None:
    parser = argparse.ArgumentParser(description="QQQ Quality Dip Scanner")
    parser.add_argument("--config", default="config.yaml", help="Path to config.yaml")
    parser.add_argument("--once", action="store_true", help="Run only once then exit")
    parser.add_argument("--test-alert", action="store_true", help="Send a test alert to Telegram and exit")
    parser.add_argument("--log-level", default="INFO", help="Logging level")
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(message)s",
    )

    cfg = Config.from_file(args.config)
    provider = YahooProvider(timeout=cfg.http_timeout)
    data_dir = Path("data")
    data_dir.mkdir(exist_ok=True)
    alerter = TelegramAlerter(cfg.telegram_bot_token, cfg.telegram_chat_id)

    if args.test_alert:
        alerter.send("Test alert: QQQ Quality Dip Scanner is connected.")
        return

    if is_weekend(cfg.market_timezone):
        logging.info("Weekend detected; scanner idle.")
        if args.once:
            return

    while True:
        tz_now = now_tz(cfg.market_timezone)
        if cfg.market_hours_only and not cfg.after_hours_enabled and not is_market_open(cfg.market_timezone, cfg.cooldown_minutes_after_open):
            logging.info("Outside market hours (%s); sleeping.", tz_now)
        else:
            try:
                run_once(cfg, alerter, provider, data_dir)
            except Exception as exc:
                logging.exception("Run failed: %s", exc)
        if args.once:
            break
        time.sleep(cfg.run_interval_seconds)


if __name__ == "__main__":
    main()
