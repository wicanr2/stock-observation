"""Yahoo Finance 聚合資料（次級來源，只作交叉檢查）。

- institutional_holders：Yahoo 依 13F 彙整的前十大機構，日期為各機構申報期，不是即時。
- major_holders：機構／內部人持股比例，計算口徑由 Yahoo 決定，不能當一手數字。
- short interest：市場總空單，**無法歸戶**到任何具名機構。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

import pandas as pd

from ..config import Stock
from ..httpclient import SourceResult, short_error

TickerFactory = Callable[[str], Any]


def _ticker(symbol: str) -> Any:
    import yfinance as yf

    return yf.Ticker(symbol)


def _num(v: Any) -> Optional[float]:
    try:
        if v is None or (isinstance(v, float) and pd.isna(v)):
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _parse_major_holders(df: Any) -> Dict[str, Optional[float]]:
    out: Dict[str, Optional[float]] = {}
    if not isinstance(df, pd.DataFrame) or df.empty:
        return out
    if "Value" in df.columns:
        for key in ("insidersPercentHeld", "institutionsPercentHeld", "institutionsFloatPercentHeld", "institutionsCount"):
            if key in df.index:
                out[key] = _num(df.loc[key, "Value"])
    return out


def _parse_institutional(df: Any) -> List[dict]:
    rows: List[dict] = []
    if not isinstance(df, pd.DataFrame) or df.empty:
        return rows
    for _, r in df.iterrows():
        reported = r.get("Date Reported")
        rows.append(
            {
                "holder": str(r.get("Holder", "")),
                "date_reported": pd.Timestamp(reported).date() if reported is not None and not pd.isna(reported) else None,
                "shares": _num(r.get("Shares")),
                "pct_held": _num(r.get("pctHeld")),
                "pct_change": _num(r.get("pctChange")),
                "value": _num(r.get("Value")),
            }
        )
    return rows


def _parse_short(info: Dict[str, Any]) -> Dict[str, Any]:
    ts = info.get("dateShortInterest")
    settle = None
    if ts:
        try:
            settle = datetime.fromtimestamp(int(ts), tz=timezone.utc).date()
        except (TypeError, ValueError, OSError):
            settle = None
    return {
        "shares_short": _num(info.get("sharesShort")),
        "shares_short_prior": _num(info.get("sharesShortPriorMonth")),
        "settlement_date": settle,
        "short_pct_float": _num(info.get("shortPercentOfFloat")),
        "short_ratio": _num(info.get("shortRatio")),
    }


def fetch_holders(stock: Stock, factory: Optional[TickerFactory] = None) -> SourceResult:
    url = f"https://finance.yahoo.com/quote/{stock.yahoo}/holders"
    try:
        t = (factory or _ticker)(stock.yahoo)
        institutional = _parse_institutional(getattr(t, "institutional_holders", None))
        major = _parse_major_holders(getattr(t, "major_holders", None))
        info: Dict[str, Any] = {}
        try:
            info = dict(getattr(t, "info", {}) or {})
        except Exception:  # noqa: BLE001 - info 端點常不穩，不影響持股表
            info = {}
        short = _parse_short(info) if stock.is_us else {}
    except Exception as exc:  # noqa: BLE001
        return SourceResult.fail("Yahoo Finance 持股聚合", short_error(exc), url, secondary=True)
    if not institutional and not major:
        return SourceResult.fail("Yahoo Finance 持股聚合", "無機構持股資料", url, secondary=True)
    return SourceResult(
        source="Yahoo Finance 持股聚合",
        ok=True,
        data={"institutional": institutional, "major": major, "short": short},
        url=url,
        secondary=True,
    )


def parse_news_items(items: Any) -> List[dict]:
    """相容 yfinance 新舊兩種 news 結構。"""
    out: List[dict] = []
    for it in items or []:
        content = it.get("content") if isinstance(it, dict) else None
        if isinstance(content, dict):
            title = content.get("title")
            link = (content.get("canonicalUrl") or {}).get("url") or (content.get("clickThroughUrl") or {}).get("url")
            publisher = (content.get("provider") or {}).get("displayName")
            when = content.get("pubDate") or content.get("displayTime")
            published = pd.Timestamp(when).to_pydatetime() if when else None
        elif isinstance(it, dict):
            title = it.get("title")
            link = it.get("link")
            publisher = it.get("publisher")
            ts = it.get("providerPublishTime")
            published = datetime.fromtimestamp(int(ts), tz=timezone.utc) if ts else None
        else:
            continue
        if title and link:
            out.append({"title": str(title), "link": str(link), "source": publisher or "Yahoo Finance", "published": published})
    return out


def fetch_news(stock: Stock, factory: Optional[TickerFactory] = None) -> SourceResult:
    url = f"https://finance.yahoo.com/quote/{stock.yahoo}/news"
    try:
        t = (factory or _ticker)(stock.yahoo)
        items = parse_news_items(getattr(t, "news", None))
    except Exception as exc:  # noqa: BLE001
        return SourceResult.fail("Yahoo Finance 新聞", short_error(exc), url, secondary=True)
    return SourceResult(source="Yahoo Finance 新聞", ok=True, data=items, url=url, secondary=True)
