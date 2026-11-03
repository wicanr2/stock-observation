"""盤中報價（Yahoo Finance，透過 yfinance；次級來源，可能延遲 15 分鐘以上）。

只用於儀表板的「最新報價」區塊；不寫入預測紀錄，也不取代每日收盤資料。
"""

from __future__ import annotations

from datetime import timezone
from typing import Any, Callable, Optional

import pandas as pd

from ..config import Stock
from ..httpclient import SourceResult, short_error

TickerFactory = Callable[[str], Any]


def _ticker(symbol: str) -> Any:
    import yfinance as yf

    return yf.Ticker(symbol)


def _prev_close(t: Any) -> Optional[float]:
    try:
        fi = t.fast_info
        for key in ("previousClose", "regularMarketPreviousClose", "previous_close"):
            try:
                v = fi[key] if hasattr(fi, "__getitem__") else getattr(fi, key)
            except (KeyError, AttributeError, TypeError):
                continue
            if v is not None and not pd.isna(v):
                return float(v)
    except Exception:  # noqa: BLE001
        return None
    return None


def parse_intraday(df: pd.DataFrame) -> list:
    if df is None or df.empty or "Close" not in df:
        return []
    idx = pd.DatetimeIndex(df.index)
    if idx.tz is None:
        idx = idx.tz_localize(timezone.utc)
    days = idx.normalize()  # 以交易所時區切日
    mask = days == days[-1]
    out = []
    for ts, close in zip(idx[mask], pd.to_numeric(df["Close"], errors="coerce")[mask]):
        if pd.isna(close):
            continue
        out.append({"t": ts.isoformat(), "p": round(float(close), 4)})
    return out


def fetch_quote(stock: Stock, factory: Optional[TickerFactory] = None) -> SourceResult:
    url = f"https://finance.yahoo.com/quote/{stock.yahoo}"
    try:
        t = (factory or _ticker)(stock.yahoo)
        bars = parse_intraday(t.history(period="1d", interval="5m"))
        if not bars:
            bars = parse_intraday(t.history(period="5d", interval="30m"))
        prev = _prev_close(t)
    except Exception as exc:  # noqa: BLE001
        return SourceResult.fail("Yahoo Finance 盤中報價", short_error(exc), url, secondary=True)
    if not bars:
        return SourceResult.fail("Yahoo Finance 盤中報價", "無盤中資料", url, secondary=True)
    last = bars[-1]
    change = (last["p"] / prev - 1) if prev else None
    return SourceResult(
        source="Yahoo Finance 盤中報價",
        ok=True,
        data={"price": last["p"], "as_of": last["t"], "prev_close": prev, "change_pct": change, "intraday": bars},
        url=url,
        secondary=True,
    )
