from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import List

from ..providers.base import DataProvider, Quote


@dataclass
class Position:
    ticker: str
    entry_price: float
    entry_date: str
    shares: float | None = None
    notes: str | None = None


@dataclass
class SellAlert:
    ticker: str
    quote: Quote
    target_hit: str
    entry_price: float
    entry_date: str
    notes: str | None = None


class SellAlertEngine:
    def __init__(self, provider: DataProvider, positions_path: Path, tp_levels: list[float]):
        self.provider = provider
        self.positions_path = positions_path
        self.tp_levels = tp_levels

    def _load_positions(self) -> List[Position]:
        if not self.positions_path.exists():
            return []
        with self.positions_path.open() as f:
            raw = json.load(f) or []
        positions = []
        for item in raw:
            try:
                positions.append(
                    Position(
                        ticker=item["ticker"].upper(),
                        entry_price=float(item["entry_price"]),
                        entry_date=item.get("entry_date", ""),
                        shares=item.get("shares"),
                        notes=item.get("notes"),
                    )
                )
            except Exception:
                continue
        return positions

    def scan(self) -> List[SellAlert]:
        alerts: List[SellAlert] = []
        positions = self._load_positions()
        for pos in positions:
            try:
                quote = self.provider.get_quote(pos.ticker)
            except Exception:
                continue
            gain = (quote.price - pos.entry_price) / pos.entry_price
            for level in self.tp_levels:
                if gain >= level:
                    alerts.append(
                        SellAlert(
                            ticker=pos.ticker,
                            quote=quote,
                            target_hit=f"{level*100:.1f}% above entry",
                            entry_price=pos.entry_price,
                            entry_date=pos.entry_date,
                            notes=pos.notes,
                        )
                    )
                    break
        return alerts
