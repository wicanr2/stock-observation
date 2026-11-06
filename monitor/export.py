"""把每日監控結果輸出成 JSON，供 web 儀表板讀取。

`monitor/data/dashboard.json` 由每日 workflow commit；網頁只讀這份檔案
（以及可選的即時報價 `quotes.json`），不在瀏覽器端呼叫任何第三方 API。
"""

from __future__ import annotations

import dataclasses
import json
import math
import os
from datetime import date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

import numpy as np
import pandas as pd

from .sources import sec_edgar

if TYPE_CHECKING:  # pragma: no cover
    from .runner import RunContext, StockBundle

SCHEMA_VERSION = 1
PRICE_ROWS = 400  # 約 1.5 年日線；網頁端計算均線需要區間前的資料


def clean(obj: Any) -> Any:
    """遞迴轉成 JSON 可序列化型別；NaN／inf → None。"""
    if obj is None or isinstance(obj, (bool, str)):
        return obj
    if isinstance(obj, (int, np.integer)):
        return int(obj)
    if isinstance(obj, (float, np.floating)):
        f = float(obj)
        return None if math.isnan(f) or math.isinf(f) else f
    if isinstance(obj, (pd.Timestamp, datetime)):
        return obj.isoformat()
    if isinstance(obj, date):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {str(k): clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [clean(v) for v in obj]
    if isinstance(obj, pd.DataFrame):
        return [clean(r) for r in obj.to_dict(orient="records")]
    if dataclasses.is_dataclass(obj):
        return clean(dataclasses.asdict(obj))
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    return str(obj)


def _source(res) -> Optional[dict]:
    if res is None:
        return None
    return {
        "source": res.source,
        "ok": res.ok,
        "error": res.error,
        "note": res.note,
        "url": res.url,
        "secondary": res.secondary,
    }


def _legs(legs: Optional[sec_edgar.Legs]) -> Optional[dict]:
    if legs is None:
        return None
    return {
        "shares": legs.shares,
        "call": legs.call,
        "put": legs.put,
        "value_shares": legs.value_shares,
        "value_call": legs.value_call,
        "value_put": legs.value_put,
        "prn_rows": legs.prn_rows,
    }


def _thirteen_f(res) -> Optional[dict]:
    if res is None:
        return None
    out = _source(res)
    if res.ok:
        data = res.data
        out["window"] = [data["start"], data["end"]]
        out["truncated"] = data["truncated"]
        out["positions"] = [
            {
                "filer": p.hit.filer,
                "cik": p.hit.cik,
                "form": p.hit.form,
                "period": p.hit.period,
                "file_date": p.hit.file_date,
                "url": p.url,
                "prior_url": p.prior_url,
                "current": _legs(p.current),
                "prior": _legs(p.prior),
                "prior_note": p.prior_note,
                "notes": p.notes,
                "class": sec_edgar.classify(p),
            }
            for p in data["positions"]
        ]
    return out


def _price(b: "StockBundle") -> dict:
    out = _source(b.prices)
    if b.prices.ok:
        hist: pd.DataFrame = b.prices.data["history"].tail(PRICE_ROWS)
        out["series"] = [{"d": f"{i:%Y-%m-%d}", "c": round(float(c), 4)} for i, c in hist["close"].items()]
        out["spliced_at"] = b.prices.data.get("spliced_at")
    out["describe"] = b.desc
    return out


def bundle_to_dict(b: "StockBundle") -> dict:
    st = b.stock
    filings = b.filings.data if b.filings is not None and b.filings.ok else []
    d: Dict[str, Any] = {
        "id": st.id,
        "name": st.name,
        "name_zh": st.name_zh,
        "name_en": st.name_en,
        "display_name": st.display_name,
        "market": st.market,
        "exchange": st.exchange,
        "yahoo": st.yahoo,
        "currency": "TWD" if st.is_tw else "USD",
        "security_note": st.security_note,
        "listed_since": st.listed_since,
        "cusip": st.cusip,
        "price": _price(b),
        "prediction": dataclasses.asdict(b.prediction),
        "dsa": dataclasses.asdict(b.dsa) if b.dsa else None,
        "track": b.track,
        "news": b.news,
        "news_sources": [_source(s) for s in b.news_sources],
        "filings": {
            "status": _source(b.filings),
            "company": [f for f in filings if not f["is_holder_filing"]],
            "holders": [f for f in filings if f["is_holder_filing"]],
        },
    }
    if st.is_us:
        d["us"] = {
            "thirteen_f": _thirteen_f(b.thirteen_f),
            "yahoo": dict(_source(b.yahoo_holders) or {}, **({"data": b.yahoo_holders.data} if b.yahoo_holders and b.yahoo_holders.ok else {})),
        }
    if st.is_tw:
        d["tw"] = {
            "latest": dict(_source(b.tw_latest) or {}, data=b.tw_latest.data if b.tw_latest and b.tw_latest.ok else None),
            "crosscheck": b.tw_crosscheck,
            "history": b.tw_history if b.tw_history is not None else [],
            "shareholding": dict(_source(b.tw_shareholding) or {}, data=b.tw_shareholding.data if b.tw_shareholding and b.tw_shareholding.ok else []),
            "margin": dict(_source(b.tw_margin) or {}, data=b.tw_margin.data if b.tw_margin and b.tw_margin.ok else []),
            "tdcc": dict(_source(b.tdcc) or {}, data=b.tdcc.data if b.tdcc and b.tdcc.ok else None),
            "tdcc_history": b.tdcc_history if b.tdcc_history is not None else [],
        }
    return d


def build_dashboard(ctx: "RunContext", bundles: List["StockBundle"], report_path: Path) -> dict:
    try:
        rel_report = report_path.resolve().relative_to(Path(__file__).resolve().parent.parent).as_posix()
    except ValueError:
        rel_report = report_path.name
    return clean(
        {
            "schema": SCHEMA_VERSION,
            "generated_at": ctx.generated_at,
            "report_date": ctx.today,
            "timezone": ctx.settings.timezone,
            "repo": os.environ.get("GITHUB_REPOSITORY", "wicanr2/stock-observation"),
            "dsa_version": ctx.dsa_version,
            "report_path": rel_report,
            "llm_report_path": (Path(rel_report).parent / Path(ctx.dsa_report_rel).name).as_posix() if ctx.dsa_report_rel else None,
            "horizon_days": int(ctx.settings.prediction.get("horizon_days", 5)),
            "stocks": [bundle_to_dict(b) for b in bundles],
            "sources": [_source(e) for e in ctx.log.entries],
        }
    )


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(clean(payload), ensure_ascii=False, indent=1, allow_nan=False), encoding="utf-8")
