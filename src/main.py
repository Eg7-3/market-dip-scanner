from __future__ import annotations

import argparse
import logging
import time
from pathlib import Path

from .alerts.telegram import TelegramAlerter
from .alerts.discord import DiscordAlerter
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


def format_dip_message(ctx) -> str:
    # reason is fully pre-rendered in scanner
    return ctx.reason


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


def run_once(cfg: Config, alerter: TelegramAlerter, provider: YahooProvider, data_dir: Path, backtest_date: str | None = None, simulate: dict | None = None) -> None:
    breadth_line = build_breadth_line(provider)
    scanner = QQQDipScanner(cfg=cfg, provider=provider, state_path=data_dir / "state.json")
    alerts = scanner.scan(breadth_line=breadth_line, backtest_date=backtest_date, simulate=simulate)
    for ctx in alerts:
        msg = format_dip_message(ctx)
        alerter.send(msg)
        if discord_alerter := getattr(run_once, "discord_alerter", None):
            discord_alerter.send(msg)
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
    parser.add_argument("--backtest-date", help="Run a backtest for a specific YYYY-MM-DD date")
    parser.add_argument("--simulate", nargs=5, metavar=("TICKER", "DIP", "RSI", "RELVOL", "DIST200"), help="Simulate a ticker with given metrics (dip pct, rsi, relvol, dist to 200dma pct)")
    parser.add_argument("--log-level", default="INFO", help="Logging level")
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(message)s",
    )

    cfg = Config.from_file(args.config)
    cfg.validate()
    provider = YahooProvider(timeout=cfg.http_timeout)
    data_dir = Path("data")
    data_dir.mkdir(exist_ok=True)
    alerter = TelegramAlerter(cfg.telegram_bot_token, cfg.telegram_chat_id)
    if cfg.use_discord:
        run_once.discord_alerter = DiscordAlerter(cfg.discord_webhook_url, cfg.discord_username)
    else:
        run_once.discord_alerter = None

    if args.test_alert:
        alerter.send("Test alert: QQQ Quality Dip Scanner is connected.")
        return

    simulate = None
    if args.simulate:
        sim_ticker, sim_dip, sim_rsi, sim_relvol, sim_dist = args.simulate
        simulate = {
            "ticker": sim_ticker.upper(),
            "dip": float(sim_dip),
            "rsi": float(sim_rsi),
            "relvol": float(sim_relvol),
            "dist200": float(sim_dist),
        }

    if is_weekend(cfg.market_timezone):
        logging.info("Weekend detected; scanner idle.")
        if args.once:
            return

    while True:
        tz_now = now_tz(cfg.market_timezone)
        if args.backtest_date:
            try:
                run_once(cfg, alerter, provider, data_dir, backtest_date=args.backtest_date, simulate=simulate)
            except Exception as exc:
                logging.exception("Backtest run failed: %s", exc)
            break

        if cfg.market_hours_only and not cfg.after_hours_enabled and not is_market_open(cfg.market_timezone, cfg.cooldown_minutes_after_open):
            logging.info("Outside market hours (%s); sleeping.", tz_now)
        else:
            try:
                run_once(cfg, alerter, provider, data_dir, simulate=simulate)
            except Exception as exc:
                logging.exception("Run failed: %s", exc)
        if args.once:
            break
        time.sleep(cfg.run_interval_seconds)


if __name__ == "__main__":
    main()
