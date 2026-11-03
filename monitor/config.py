"""觀察清單與執行設定。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

PACKAGE_DIR = Path(__file__).resolve().parent
DEFAULT_WATCHLIST = PACKAGE_DIR / "watchlist.yaml"


@dataclass
class Predecessor:
    yahoo: str
    until: str
    tw_code: Optional[str] = None
    ratio: float = 1.0
    note: str = ""


@dataclass
class Stock:
    id: str
    name: str
    market: str  # "US" | "TW"
    exchange: str
    yahoo: str
    dsa_code: str
    name_zh: Optional[str] = None
    name_en: Optional[str] = None
    sec_cik: Optional[str] = None
    cusip: Optional[str] = None
    tw_code: Optional[str] = None
    listed_since: Optional[str] = None
    security_note: str = ""
    benchmark: Optional[str] = None
    predecessor: Optional[Predecessor] = None
    news_queries: List[Dict[str, str]] = field(default_factory=list)

    @property
    def display_name(self) -> str:
        if self.market == "TW":
            return f"{self.name}（{self.tw_code or self.id}）"
        return f"{self.id} — {self.name}"

    @property
    def is_us(self) -> bool:
        return self.market == "US"

    @property
    def is_tw(self) -> bool:
        return self.market == "TW"


@dataclass
class Settings:
    timezone: str
    stocks: List[Stock]
    sec: Dict[str, Any]
    news: Dict[str, Any]
    prediction: Dict[str, Any]


def _stock_from_dict(raw: Dict[str, Any]) -> Stock:
    pred_raw = raw.get("predecessor")
    predecessor = None
    if pred_raw:
        predecessor = Predecessor(
            yahoo=str(pred_raw["yahoo"]),
            until=str(pred_raw["until"]),
            tw_code=str(pred_raw.get("tw_code")) if pred_raw.get("tw_code") else None,
            ratio=float(pred_raw.get("ratio", 1.0)),
            note=str(pred_raw.get("note", "")),
        )
    return Stock(
        id=str(raw["id"]),
        name=str(raw["name"]),
        market=str(raw["market"]).upper(),
        exchange=str(raw.get("exchange", "")),
        yahoo=str(raw["yahoo"]),
        dsa_code=str(raw.get("dsa_code") or raw["yahoo"]),
        name_zh=raw.get("name_zh"),
        name_en=raw.get("name_en"),
        sec_cik=str(raw["sec_cik"]) if raw.get("sec_cik") else None,
        cusip=str(raw["cusip"]).upper() if raw.get("cusip") else None,
        tw_code=str(raw["tw_code"]) if raw.get("tw_code") else None,
        listed_since=str(raw["listed_since"]) if raw.get("listed_since") else None,
        security_note=str(raw.get("security_note", "")),
        benchmark=raw.get("benchmark"),
        predecessor=predecessor,
        news_queries=list(raw.get("news_queries") or []),
    )


def load_settings(path: Optional[Path] = None) -> Settings:
    path = Path(path) if path else DEFAULT_WATCHLIST
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    stocks = [_stock_from_dict(s) for s in raw.get("stocks", [])]
    if not stocks:
        raise ValueError(f"觀察清單 {path} 沒有任何股票")
    return Settings(
        timezone=str(raw.get("timezone", "Asia/Taipei")),
        stocks=stocks,
        sec=dict(raw.get("sec") or {}),
        news=dict(raw.get("news") or {}),
        prediction=dict(raw.get("prediction") or {}),
    )
