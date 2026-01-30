# QQQ Quality Dip Scanner

Python 3.11 tool that watches Nasdaq‑100 names for sharp intraday drops and sends Telegram alerts. It avoids auto‑trading; you manually review and act.

## Features (institutional picker)
- Live Nasdaq‑100 holdings pulled and cached for 24h (no hard-coded list).
- Mandatory quality: mcap ≥ $30B, avg dollar vol ≥ $1B, positive FCF or net income, no “sell” rating.
- **200‑DMA zone model:** GREEN (≥ +2% above 200‑DMA) passes; YELLOW (between +2% and −2%) allowed only if 200‑DMA slope is rising with stricter confirmations; RED (≤ −2%) rejected unless reclaim is explicitly enabled. Avoids missing near‑DMA bounces while still blocking broken trends.
- **Tiered dip alerts:** Tier 1 “EARLY FEAR” (e.g., −3.5%) with stricter confirmations; Tier 2 “PANIC” (e.g., −5%) with looser confirmations; automatic tier upgrade and re‑alert on deeper lows.
- Setup: fast 1–3 day pullback, intraday low breach, primarily intraday selling.
- Confirmation: zone- and tier-aware rules (RSI, relative volume, VWAP touch, QQQ backdrop).
- Intraday candles (5m default) compute worst dip, VWAP, relative volume; dedupe with re‑alert only on deeper lows or tier upgrade.
- Telegram alerts include ticker, name, sector, mcap, current % change, worst intraday dip, RSI, relative volume, 200‑DMA distance, zone grade (A/B/C), tier label, reason summary, and news hint (with emojis).

## Pipeline (single ordered path)
1) Universe = live QQQ holdings ∪ custom_watchlist.
2) Fetch metrics (price, intraday low, VWAP, RSI, rel‑vol, MA200 + slope, dollar vol, mcap).
3) Choose dip metric: market open → intraday low; closed → intraday low (or min with extended if after‑hours enabled).
4) Tiering: Tier2 if dip ≤ tier2_dip else Tier1 if dip ≤ tier1_dip else stop.
5) 200‑DMA zones: GREEN/YELLOW/RED; hard reject only if below `hard_reject_below_200dma_pct`; YELLOW optionally requires rising MA200.
6) Confirmation score: RSI / RelVol / $Vol / MktCap / VWAP touch / QQQ backdrop / fast‑selloff. Require tier‑ and zone‑aware minima.
7) Dedupe: per‑ticker state, cooldown, re‑alert on deeper dip or Tier1→Tier2 upgrade; testing_mode bypasses dedupe.
8) Render once for Telegram + Discord with pass/fail reasons and risk hints.

## Quick Start
1) **Install deps**
   ```bash
   pip install -r requirements.txt
   ```
2) **Set secrets (recommended via env)**
   ```bash
   export TELEGRAM_BOT_TOKEN=xxxxxxxx
   export TELEGRAM_CHAT_ID=yyyyyyyy
   ```
3) **Edit `config.yaml`** if you want custom thresholds or timezone.
4) **Run once**
   ```bash
   python -m src.main --once
   ```
5) **Continuous loop (every 5 min default)**
   ```bash
   python -m src.main
   ```

### Telegram setup
1. Talk to [@BotFather](https://t.me/BotFather) → `/newbot` → grab the bot token.
2. Add the bot to a private channel or DM it; send any message.
3. Get chat id (easy way): visit `https://api.telegram.org/bot<token>/getUpdates` after sending a message; copy `chat.id`. Save both as env vars.

### Config reference (`config.yaml`)

### Additional commands
- Live loop: `python3 -m src.main`
- Single run: `python3 -m src.main --once`
- Test alert: `python3 -m src.main --test-alert`
- Backtest a date: `python3 -m src.main --once --backtest-date YYYY-MM-DD`
- Simulate (demo): `python3 -m src.main --once --simulate TICKER DIP RSI RELVOL DIST200`
- Testing mode (no dedupe): set `testing_mode: true` in config.
- Dip logic:
  - `tiered_dips_enabled`: switch between legacy single threshold and tiered system.
  - `tier1_dip`, `tier2_dip`: EARLY FEAR and PANIC intraday low triggers.
  - `tier1_min_confirmations`, `tier2_min_confirmations`: required confirmations per tier.
  - `tier1_rsi_max`, `tier2_rsi_max`; `tier1_relvol_min`, `tier2_relvol_min`: tier‑specific signal gates.
  - `realert_delta`: allow new alert if worst low worsens by this many pct points; tier upgrades always re‑alert.
- Quality: `market_cap_min`, `avg_volume_min`, `min_dollar_volume`, positive FCF/net income, no “sell”.
- 200‑DMA zone knobs:
  - `dma200_tolerance_pct`: YELLOW decision zone below 200‑DMA.
  - `dma200_green_pct`: ≥ this above 200‑DMA → GREEN (Grade A).
  - `dma200_red_pct`: ≤ this below 200‑DMA → RED (reject unless reclaim allowed).
  - `allow_red_reclaim`: enable reclaim logic for deep dips.
  - `require_rising_dma200_in_yellow`: if true, YELLOW only passes when the 200‑DMA slope is rising.
  - `hard_reject_below_200dma_pct`: absolute floor; below this distance alerts are blocked.
- Dedupe / testing:
  - `dedupe_cooldown_minutes`: minimum minutes between alerts for same ticker (unless deeper dip or tier upgrade).
  - `testing_mode`: bypasses dedupe for demos/backtests.
- `market_timezone`: e.g., `America/Chicago`; `market_hours_only` gates scans.
- `cooldown_minutes_after_open`: skip first few minutes after open.
- `run_interval_seconds`: loop sleep.
- `telegram_*`: can be `ENV:VAR` placeholders.
- `enable_sell_alerts` + `take_profit_[1-3]`: thresholds for optional sell pings.

### Tiered Dip Alerts (how it works)
- Compute intraday low vs yesterday’s close.
- If low ≤ `tier2_dip` → Tier 2 “PANIC”; needs ≥ `tier2_min_confirmations` and tier2 RSI/rel‑vol gate (RSI<=tier2_rsi_max OR relvol>=tier2_relvol_min).
- Else if low ≤ `tier1_dip` → Tier 1 “EARLY FEAR”; stricter: needs ≥ `tier1_min_confirmations` AND RSI<=tier1_rsi_max AND relvol>=tier1_relvol_min.
- Zone filter: GREEN/YELLOW/RED per 200‑DMA distance; YELLOW requires rising 200‑DMA and stricter confirmations.
- Dedupe: one alert per day unless low deepens by `realert_delta` or upgrades from Tier 1→Tier 2.

### Data & state
- `data/qqq_tickers.json`: fallback list; refreshed weekly from slickcharts.
- `data/state.json`: per‑day alert memory (avoid spam).
- `data/positions.json`: optional positions for sell alerts. Example included.

### GitHub Actions (free scheduler)
1. Put `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` in repo secrets.
2. Push this repo with `.github/workflows/scanner.yml`.
3. Adjust cron in the workflow if desired (defaults to market hours CDT).

### Troubleshooting
- **No alerts?** Run with `--log-level DEBUG` and `--once` to see filters.
- **Bot not sending?** Verify env vars and chat id; try `--test-alert`.
- **Rate limits/data gaps:** yfinance is best‑effort; retries are built‑in via quick sessions. Consider swapping provider via `providers/base.py` interface if you have Polygon/IEX, etc.
- **Timezones:** All scheduling uses `market_timezone` from config; default CDT.

### Test alert
```bash
python -m src.main --test-alert
```

### Notes
- The scanner never auto‑trades.
- Minimal dependencies; modular provider design to swap data sources later.
