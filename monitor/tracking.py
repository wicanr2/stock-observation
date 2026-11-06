"""預測紀錄與事後命中率追蹤。

每次執行把「統計基準模型」與「上游 LLM（daily_stock_analysis）」的方向判斷寫入
``monitor/data/predictions.csv``；到期（N 個交易日後）再用實際收盤檢驗。
每日紀錄的預測期間互相重疊，命中次數彼此相關，不能當獨立樣本做顯著性檢定。
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

COLUMNS = ["run_date", "stock_id", "model", "asof_date", "horizon", "prob_up", "direction", "close_asof", "beats_baseline", "label"]


def load(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=COLUMNS)
    df = pd.read_csv(path, dtype={"stock_id": str, "asof_date": str, "run_date": str})
    for c in COLUMNS:
        if c not in df.columns:
            df[c] = None
    return df[COLUMNS]


def append(path: Path, rows: List[dict]) -> pd.DataFrame:
    df = load(path)
    if rows:
        df = pd.concat([df, pd.DataFrame(rows, columns=COLUMNS)], ignore_index=True)
        df = df.drop_duplicates(["stock_id", "model", "asof_date"], keep="last")
        df = df.sort_values(["asof_date", "stock_id", "model"]).reset_index(drop=True)
        path.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(path, index=False)
    return df


def evaluate(log: pd.DataFrame, histories: Dict[str, pd.DataFrame]) -> Dict[tuple, dict]:
    """回傳 {(stock_id, model): {...}}；只計 direction ≠ 0 且已到期的預測。"""
    out: Dict[tuple, dict] = {}
    if log is None or log.empty:
        return out
    for (sid, model), grp in log.groupby(["stock_id", "model"]):
        hist = histories.get(str(sid))
        stats = {"logged": int(len(grp)), "directional": 0, "matured": 0, "hits": 0, "up_windows": 0}
        if hist is not None and not hist.empty:
            close = hist["close"]
            dates = list(close.index)
            for _, r in grp.iterrows():
                d = int(r["direction"]) if pd.notna(r["direction"]) else 0
                if d == 0:
                    continue
                stats["directional"] += 1
                asof = pd.Timestamp(r["asof_date"])
                if asof not in close.index:
                    continue
                pos = dates.index(asof)
                h = int(r["horizon"])
                if pos + h >= len(dates):
                    continue
                realized = close.iloc[pos + h] / close.iloc[pos] - 1
                stats["matured"] += 1
                stats["up_windows"] += int(realized > 0)
                stats["hits"] += int((realized > 0 and d > 0) or (realized < 0 and d < 0))
        stats["hit_rate"] = stats["hits"] / stats["matured"] if stats["matured"] else None
        stats["up_rate"] = stats["up_windows"] / stats["matured"] if stats["matured"] else None
        out[(str(sid), str(model))] = stats
    return out


def llm_direction(trend: Optional[str]) -> int:
    if not trend:
        return 0
    if "看多" in trend or "看漲" in trend:
        return 1
    if "看空" in trend or "看跌" in trend:
        return -1
    return 0
