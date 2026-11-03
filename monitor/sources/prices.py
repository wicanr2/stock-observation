"""價格歷史：Yahoo Finance（透過 yfinance）。

Yahoo 為聚合來源；收盤價僅用於描述走勢與統計模型，不作為法定資料。
"""

from __future__ import annotations

from typing import Callable, Optional

import pandas as pd

from ..config import Stock
from ..httpclient import SourceResult, short_error

Downloader = Callable[[str, str], pd.DataFrame]


def _yf_download(symbol: str, period: str) -> pd.DataFrame:
    import yfinance as yf

    df = yf.Ticker(symbol).history(period=period, auto_adjust=True)
    return df


def _normalize(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=["close", "volume"])
    out = pd.DataFrame(
        {
            "close": pd.to_numeric(df["Close"], errors="coerce"),
            "volume": pd.to_numeric(df.get("Volume", pd.Series(index=df.index, dtype=float)), errors="coerce"),
        },
        index=pd.DatetimeIndex(df.index),
    )
    if out.index.tz is not None:
        out.index = out.index.tz_localize(None)
    out.index = out.index.normalize()
    out = out[~out.index.duplicated(keep="last")].sort_index()
    return out.dropna(subset=["close"])


def fetch_history(stock: Stock, period: str = "5y", downloader: Optional[Downloader] = None) -> SourceResult:
    """抓取日線；若設定 predecessor，於上市日前拼接舊代碼歷史。"""
    dl = downloader or _yf_download
    url = f"https://finance.yahoo.com/quote/{stock.yahoo}/history"
    try:
        current = _normalize(dl(stock.yahoo, period))
    except Exception as exc:  # noqa: BLE001 - fail-open
        return SourceResult.fail("Yahoo Finance 日線", short_error(exc), url, secondary=True)
    if current.empty:
        return SourceResult.fail("Yahoo Finance 日線", "回傳空資料", url, secondary=True)

    note = None
    spliced_at = None
    if stock.predecessor:
        pred = stock.predecessor
        try:
            old = _normalize(dl(pred.yahoo, period))
        except Exception as exc:  # noqa: BLE001
            old = pd.DataFrame()
            note = f"舊代碼 {pred.yahoo} 歷史未取得（{short_error(exc)}），僅用新代碼資料"
        if not old.empty:
            cutoff = pd.Timestamp(pred.until)
            old = old[old.index <= cutoff].copy()
            if pred.ratio and pred.ratio != 1.0:
                old["close"] = old["close"] / pred.ratio
                old["volume"] = old["volume"] * pred.ratio
            first_new = current.index.min()
            old = old[old.index < first_new]
            if not old.empty:
                spliced_at = first_new
                current = pd.concat([old, current]).sort_index()
                note = (
                    f"{old.index.min():%Y-%m-%d}～{old.index.max():%Y-%m-%d} 使用 {pred.yahoo}，"
                    f"{first_new:%Y-%m-%d} 起使用 {stock.yahoo}（{pred.note}）"
                )
        elif note is None:
            note = f"舊代碼 {pred.yahoo} 在 Yahoo 無資料，僅用新代碼資料"

    return SourceResult(
        source="Yahoo Finance 日線",
        ok=True,
        data={"history": current, "spliced_at": spliced_at},
        url=url,
        note=note,
        secondary=True,
    )


def describe(history: pd.DataFrame) -> dict:
    """描述性統計（不是預測）：最近收盤、報酬、均線相對位置、RSI。"""
    close = history["close"]
    last = close.iloc[-1]

    def ret(n: int) -> Optional[float]:
        if len(close) <= n:
            return None
        return float(last / close.iloc[-1 - n] - 1)

    def ma_gap(n: int) -> Optional[float]:
        if len(close) < n:
            return None
        return float(last / close.iloc[-n:].mean() - 1)

    rsi = None
    if len(close) >= 15:
        delta = close.diff()
        gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
        loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
        rs = gain.iloc[-1] / loss.iloc[-1] if loss.iloc[-1] > 0 else float("inf")
        rsi = float(100 - 100 / (1 + rs))

    return {
        "last_date": history.index[-1],
        "last_close": float(last),
        "ret_1d": ret(1),
        "ret_5d": ret(5),
        "ret_20d": ret(20),
        "ma20_gap": ma_gap(20),
        "ma60_gap": ma_gap(60),
        "rsi14": rsi,
        "rows": int(len(close)),
    }
