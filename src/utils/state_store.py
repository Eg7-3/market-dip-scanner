from __future__ import annotations

import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import Dict, Tuple, Any


class StateStore:
    """
    Persists alerts sent per day to avoid duplicates and remember last alert level.
    """

    def __init__(self, path: Path, tz: str | None = None):
        self.path = path
        self.data: Dict[str, Dict[str, Any]] = {}
        self.tz = ZoneInfo(tz) if tz else None
        self._load()

    def _load(self) -> None:
        if self.path.exists():
            try:
                with self.path.open() as f:
                    self.data = json.load(f) or {}
            except Exception:
                self.data = {}
        # Drop stale days to keep file small and I/O fast
        self.reset_if_new_day()

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("w") as f:
            json.dump(self.data, f, indent=2)

    def _today_key(self) -> str:
        now = datetime.now(self.tz) if self.tz else datetime.now()
        return now.strftime("%Y-%m-%d")

    def get_today_entry(self, ticker: str) -> dict | None:
        today = self._today_key()
        entry = self.data.get(today, {}).get(ticker.upper())
        if isinstance(entry, (int, float)):
            return {"low": float(entry), "tier": None}
        return entry

    def should_alert(
        self,
        ticker: str,
        low_change_pct: float,
        realert_delta: float,
        tier: int | None = None,
        price: float | None = None,
        cooldown_minutes: int = 0,
        testing_mode: bool = False,
    ) -> bool:
        today = self._today_key()
        ticker = ticker.upper()
        if today not in self.data:
            self.data[today] = {}

        if testing_mode:
            self.data[today][ticker] = {
                "low": low_change_pct,
                "tier": tier,
                "price": price,
                "ts": datetime.now().isoformat(),
            }
            self._save()
            return True

        last_entry = self.data[today].get(ticker)
        # Backward compatibility: if float stored, wrap into dict
        if isinstance(last_entry, (int, float)):
            last_entry = {"low": float(last_entry), "tier": None}

        if last_entry is None:
            self.data[today][ticker] = {"low": low_change_pct, "tier": tier, "price": price, "ts": datetime.now().isoformat()}
            self._save()
            return True

        last_low = last_entry.get("low")
        last_tier = last_entry.get("tier")
        last_ts = last_entry.get("ts")
        in_cooldown = False
        if last_ts:
            try:
                last_dt = datetime.fromisoformat(last_ts)
                in_cooldown = datetime.now() < last_dt + timedelta(minutes=cooldown_minutes)
            except Exception:
                in_cooldown = False

        # Tier upgrade overrides dedupe
        if tier is not None and last_tier is not None and tier > last_tier:
            self.data[today][ticker] = {
                "low": low_change_pct,
                "tier": tier,
                "price": price,
                "ts": datetime.now().isoformat(),
            }
            self._save()
            return True

        if low_change_pct <= last_low + realert_delta:
            self.data[today][ticker] = {
                "low": low_change_pct,
                "tier": tier or last_tier,
                "price": price,
                "ts": datetime.now().isoformat(),
            }
            self._save()
            return True

        # otherwise respect cooldown
        if in_cooldown:
            return False

        self.data[today][ticker] = {
            "low": low_change_pct,
            "tier": tier,
            "price": price,
            "ts": datetime.now().isoformat(),
        }
        self._save()
        return True

    def reset_if_new_day(self) -> None:
        today = self._today_key()
        if list(self.data.keys()) != [today]:
            self.data = {today: {}}
            self._save()
