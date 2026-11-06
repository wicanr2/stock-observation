"""台股籌碼：三大法人買賣超、外資持股、融資融券、集保股權分散。

來源優先順序（AGENTS.md：一手優先、聚合只作交叉檢查）：
1. 櫃買中心 OpenAPI ``tpex_3insti_daily_trading``（一手；只提供最新交易日）。
2. 集保結算所開放資料 ``getOD.ashx?id=1-5``（一手；每週一份，只保留當週）。
3. FinMind（開源專案，彙整 TWSE/TPEx 公開資料；次級來源，用於補歷史與交叉檢查）。

由於一手端點多半只提供最新一期，monitor 每次執行都會把結果寫入
``monitor/data/tw/``，逐日累積成可回溯的歷史。
"""

from __future__ import annotations

import csv
import io
import os
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

from ..httpclient import HttpClient, SourceResult, short_error

TPEX_3INSTI_URL = "https://www.tpex.org.tw/openapi/v1/tpex_3insti_daily_trading"
TDCC_URL = "https://smart.tdcc.com.tw/opendata/getOD.ashx?id=1-5"
FINMIND_URL = "https://api.finmindtrade.com/api/v4/data"

# 欄位名稱沿用 daily_stock_analysis 已線上驗證的 TPEx 欄位（含官方不一致的空白）。
_TPEX_FOREIGN = "Foreign Investors include Mainland Area Investors (Foreign Dealers excluded)-Difference"
_TPEX_TRUST = "SecuritiesInvestmentTrustCompanies-Difference"
_TPEX_DEALER = "Dealers-Difference"
_TPEX_TOTAL = "TotalDifference"

INSTI_COLUMNS = ["date", "foreign_net", "trust_net", "dealer_net", "total_net", "source"]
TDCC_COLUMNS = ["date", "holders_total", "big1000_ratio", "big1000_holders", "big400_ratio", "small50_ratio", "source"]


def _to_int(v) -> Optional[int]:
    t = str(v).replace(",", "").replace(" ", "").strip()
    if t in ("", "-", "--", "—", "None", "nan"):
        return None
    try:
        return int(float(t))
    except ValueError:
        return None


def _to_float(v) -> Optional[float]:
    t = str(v).replace(",", "").replace("%", "").strip()
    if t in ("", "-", "--", "None", "nan"):
        return None
    try:
        return float(t)
    except ValueError:
        return None


def minguo_to_iso(text: str) -> Optional[str]:
    t = str(text).strip().replace("/", "")
    if not (t.isdigit() and len(t) == 7):
        return None
    return f"{int(t[:3]) + 1911:04d}-{t[3:5]}-{t[5:7]}"


# --------------------------------------------------------------------------- TPEx 三大法人


def parse_tpex_3insti(payload, code: str) -> Optional[dict]:
    if not isinstance(payload, list):
        return None
    for raw in payload:
        if not isinstance(raw, dict) or str(raw.get("SecuritiesCompanyCode", "")).strip() != code:
            continue
        iso = minguo_to_iso(raw.get("Date", ""))
        foreign, trust, dealer = (_to_int(raw.get(k)) for k in (_TPEX_FOREIGN, _TPEX_TRUST, _TPEX_DEALER))
        if iso is None or foreign is None or trust is None or dealer is None:
            return None  # 欄位缺漏 → fail-open，不補 0
        total = _to_int(raw.get(_TPEX_TOTAL))
        return {
            "date": iso,
            "foreign_net": foreign,
            "trust_net": trust,
            "dealer_net": dealer,
            "total_net": total if total is not None else foreign + trust + dealer,
            "source": "TPEx OpenAPI",
        }
    return None


def fetch_tpex_3insti(client: HttpClient, code: str) -> SourceResult:
    try:
        payload = client.get_json(TPEX_3INSTI_URL, headers={"Accept": "application/json"})
    except Exception as exc:  # noqa: BLE001
        return SourceResult.fail("櫃買中心 三大法人", short_error(exc), TPEX_3INSTI_URL)
    rec = parse_tpex_3insti(payload, code)
    if rec is None:
        return SourceResult.fail("櫃買中心 三大法人", f"最新交易日資料中找不到 {code} 或欄位不符", TPEX_3INSTI_URL)
    return SourceResult(source="櫃買中心 三大法人", ok=True, data=rec, url=TPEX_3INSTI_URL)


# --------------------------------------------------------------------------- FinMind（次級）


def _finmind(client: HttpClient, dataset: str, code: str, start: date) -> List[dict]:
    params = {"dataset": dataset, "data_id": code, "start_date": start.isoformat()}
    headers = {}
    token = os.environ.get("FINMIND_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    payload = client.get_json(FINMIND_URL, params=params, headers=headers)
    if not isinstance(payload, dict) or payload.get("status") not in (200, "200"):
        raise RuntimeError(f"FinMind 回應異常：{str(payload)[:120]}")
    return list(payload.get("data") or [])


def parse_finmind_institutional(rows: List[dict]) -> List[dict]:
    """FinMind 以 name 分列；彙整成與 TPEx 相同口徑（外資不含外資自營商）。"""
    by_date: Dict[str, Dict[str, int]] = {}
    for r in rows:
        d = str(r.get("date", ""))[:10]
        name = str(r.get("name", ""))
        buy, sell = _to_int(r.get("buy")), _to_int(r.get("sell"))
        if not d or buy is None or sell is None:
            continue
        slot = by_date.setdefault(d, {"foreign": 0, "trust": 0, "dealer": 0, "_seen": 0})
        net = buy - sell
        if name == "Foreign_Investor":
            slot["foreign"] += net
        elif name == "Investment_Trust":
            slot["trust"] += net
        elif name in ("Dealer_self", "Dealer_Hedging", "Dealer"):
            slot["dealer"] += net
        else:
            continue  # Foreign_Dealer_Self 等不在 TPEx 三大法人合計口徑內
        slot["_seen"] += 1
    out = []
    for d, s in sorted(by_date.items()):
        if not s["_seen"]:
            continue
        out.append(
            {
                "date": d,
                "foreign_net": s["foreign"],
                "trust_net": s["trust"],
                "dealer_net": s["dealer"],
                "total_net": s["foreign"] + s["trust"] + s["dealer"],
                "source": "FinMind",
            }
        )
    return out


def fetch_finmind_institutional(client: HttpClient, code: str, start: date) -> SourceResult:
    url = f"{FINMIND_URL}?dataset=TaiwanStockInstitutionalInvestorsBuySell&data_id={code}"
    try:
        rows = parse_finmind_institutional(_finmind(client, "TaiwanStockInstitutionalInvestorsBuySell", code, start))
    except Exception as exc:  # noqa: BLE001
        return SourceResult.fail("FinMind 三大法人歷史", short_error(exc), url, secondary=True)
    if not rows:
        return SourceResult.fail("FinMind 三大法人歷史", "無資料", url, secondary=True)
    return SourceResult(source="FinMind 三大法人歷史", ok=True, data=rows, url=url, secondary=True)


def parse_finmind_shareholding(rows: List[dict]) -> List[dict]:
    out = []
    for r in rows:
        ratio = _to_float(r.get("ForeignInvestmentSharesRatio"))
        shares = _to_int(r.get("ForeignInvestmentShares"))
        if ratio is None and shares is None:
            continue
        out.append(
            {
                "date": str(r.get("date", ""))[:10],
                "foreign_shares": shares,
                "foreign_ratio": ratio,
                "issued_shares": _to_int(r.get("NumberOfSharesIssued")),
                "upper_limit_ratio": _to_float(r.get("ForeignInvestmentUpperLimitRatio")),
            }
        )
    return sorted(out, key=lambda x: x["date"])


def fetch_finmind_shareholding(client: HttpClient, code: str, start: date) -> SourceResult:
    url = f"{FINMIND_URL}?dataset=TaiwanStockShareholding&data_id={code}"
    try:
        rows = parse_finmind_shareholding(_finmind(client, "TaiwanStockShareholding", code, start))
    except Exception as exc:  # noqa: BLE001
        return SourceResult.fail("FinMind 外資持股", short_error(exc), url, secondary=True)
    if not rows:
        return SourceResult.fail("FinMind 外資持股", "無資料或欄位不符", url, secondary=True)
    return SourceResult(source="FinMind 外資持股", ok=True, data=rows, url=url, secondary=True)


def parse_finmind_margin(rows: List[dict]) -> List[dict]:
    out = []
    for r in rows:
        short_bal = _to_int(r.get("ShortSaleTodayBalance"))
        margin_bal = _to_int(r.get("MarginPurchaseTodayBalance"))
        if short_bal is None and margin_bal is None:
            continue
        out.append({"date": str(r.get("date", ""))[:10], "short_balance": short_bal, "margin_balance": margin_bal})
    return sorted(out, key=lambda x: x["date"])


def fetch_finmind_margin(client: HttpClient, code: str, start: date) -> SourceResult:
    url = f"{FINMIND_URL}?dataset=TaiwanStockMarginPurchaseShortSale&data_id={code}"
    try:
        rows = parse_finmind_margin(_finmind(client, "TaiwanStockMarginPurchaseShortSale", code, start))
    except Exception as exc:  # noqa: BLE001
        return SourceResult.fail("FinMind 融資融券", short_error(exc), url, secondary=True)
    if not rows:
        return SourceResult.fail("FinMind 融資融券", "無資料或欄位不符", url, secondary=True)
    return SourceResult(source="FinMind 融資融券", ok=True, data=rows, url=url, secondary=True)


# --------------------------------------------------------------------------- 集保股權分散


def _decode(raw: bytes) -> str:
    for enc in ("utf-8-sig", "big5", "cp950"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def parse_tdcc(csv_text: str, code: str) -> Optional[dict]:
    reader = csv.reader(io.StringIO(csv_text))
    header = next(reader, None)
    if not header:
        return None
    header = [h.strip().lstrip("﻿") for h in header]

    def col(keyword: str) -> Optional[int]:
        for i, h in enumerate(header):
            if keyword in h:
                return i
        return None

    i_date, i_code, i_level = col("資料日期"), col("證券代號"), col("持股分級")
    i_people, i_shares, i_ratio = col("人數"), col("股數"), col("比例")
    if None in (i_date, i_code, i_level, i_people, i_ratio):
        return None
    levels: Dict[int, dict] = {}
    data_date = None
    for row in reader:
        if len(row) <= max(i_date, i_code, i_level, i_people, i_ratio):
            continue
        if row[i_code].strip() != code:
            continue
        level = _to_int(row[i_level])
        if level is None:
            continue
        data_date = row[i_date].strip()
        levels[level] = {
            "people": _to_int(row[i_people]),
            "shares": _to_int(row[i_shares]) if i_shares is not None else None,
            "ratio": _to_float(row[i_ratio]),
        }
    if not levels or data_date is None:
        return None
    if len(data_date) == 8 and data_date.isdigit():
        data_date = f"{data_date[:4]}-{data_date[4:6]}-{data_date[6:]}"

    def ratio_sum(keys) -> Optional[float]:
        vals = [levels[k]["ratio"] for k in keys if k in levels and levels[k]["ratio"] is not None]
        return round(sum(vals), 2) if vals else None

    return {
        "date": data_date,
        "holders_total": (levels.get(17) or {}).get("people"),
        "big1000_ratio": ratio_sum([15]),
        "big1000_holders": (levels.get(15) or {}).get("people"),
        "big400_ratio": ratio_sum([12, 13, 14, 15]),
        "small50_ratio": ratio_sum(range(1, 9)),
        "source": "TDCC 開放資料",
    }


def fetch_tdcc(client: HttpClient, code: str) -> SourceResult:
    try:
        resp = client.get(TDCC_URL)
        text = _decode(resp.content)
    except Exception as exc:  # noqa: BLE001
        return SourceResult.fail("集保 股權分散表", short_error(exc), TDCC_URL)
    rec = parse_tdcc(text, code)
    if rec is None:
        return SourceResult.fail("集保 股權分散表", f"檔案中找不到 {code} 或表頭不符", TDCC_URL)
    return SourceResult(source="集保 股權分散表", ok=True, data=rec, url=TDCC_URL)


# --------------------------------------------------------------------------- 本地歷史累積


def merge_history(path: Path, new_rows: List[dict], columns: List[str], prefer_source: str) -> pd.DataFrame:
    """把新資料併入 CSV 歷史；同一日期以一手來源（prefer_source）優先。"""
    frames = []
    if path.exists():
        frames.append(pd.read_csv(path, dtype={"date": str}))
    if new_rows:
        frames.append(pd.DataFrame(new_rows))
    if not frames:
        return pd.DataFrame(columns=columns)
    df = pd.concat(frames, ignore_index=True)
    for c in columns:
        if c not in df.columns:
            df[c] = None
    df = df[columns]
    df["_rank"] = (df["source"] != prefer_source).astype(int)
    df = df.sort_values(["date", "_rank"]).drop_duplicates("date", keep="first").drop(columns="_rank")
    df = df.sort_values("date").reset_index(drop=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)
    return df


def cross_check(primary: Optional[dict], secondary_rows: List[dict]) -> Optional[str]:
    """一手（TPEx）與 FinMind 同日比對；不一致時回傳說明，不擇一。"""
    if not primary:
        return None
    match = next((r for r in secondary_rows if r["date"] == primary["date"]), None)
    if match is None:
        return f"FinMind 尚無 {primary['date']} 資料，未能交叉比對"
    diffs = [
        f"{label} TPEx {primary[k]:+,} / FinMind {match[k]:+,}"
        for k, label in (("foreign_net", "外資"), ("trust_net", "投信"), ("dealer_net", "自營商"))
        if primary[k] != match[k]
    ]
    return "與 FinMind 同日數字一致" if not diffs else "口徑差異：" + "；".join(diffs)


def window_sums(df: pd.DataFrame, n: int) -> Optional[dict]:
    if df is None or df.empty:
        return None
    tail = df.sort_values("date").tail(n)
    return {
        "days": int(len(tail)),
        "start": str(tail["date"].iloc[0]),
        "end": str(tail["date"].iloc[-1]),
        **{k: int(pd.to_numeric(tail[k], errors="coerce").fillna(0).sum()) for k in ("foreign_net", "trust_net", "dealer_net", "total_net")},
    }


def streak(df: pd.DataFrame, column: str) -> Optional[int]:
    """連續買超（正）或賣超（負）天數；0 表示最新一日為 0。"""
    if df is None or df.empty:
        return None
    vals = pd.to_numeric(df.sort_values("date")[column], errors="coerce").dropna().tolist()
    if not vals:
        return None
    last = vals[-1]
    if last == 0:
        return 0
    sign = 1 if last > 0 else -1
    count = 0
    for v in reversed(vals):
        if (v > 0 and sign > 0) or (v < 0 and sign < 0):
            count += 1
        else:
            break
    return sign * count


def default_start(today: date, days: int = 90) -> date:
    return today - timedelta(days=days)
