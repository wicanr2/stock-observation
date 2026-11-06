"""把每日監控結果寫成繁體中文 Markdown。

寫作規則對齊 AGENTS.md：重要數字附來源、聚合來源標示為次級、資料缺漏寫
「本次未取得」、13F 延遲資料不寫成即時資金流、空方線索無法歸戶時明寫。
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, List, Optional

import pandas as pd

from . import dsa_bridge
from .sources import sec_edgar, taiwan

if TYPE_CHECKING:  # pragma: no cover
    from .config import Settings
    from .runner import RunContext, StockBundle

NA = "本次未取得"


# --------------------------------------------------------------------------- 格式工具


def fi(v: Optional[float]) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "—"
    return f"{int(round(v)):,}"


def fs(v: Optional[float]) -> str:
    """有號整數（買賣超）。"""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "—"
    return f"{int(round(v)):+,}"


def fp(v: Optional[float], digits: int = 1, signed: bool = True) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "—"
    return f"{v:+.{digits}%}" if signed else f"{v:.{digits}%}"


def fd(v) -> str:
    if v is None:
        return "—"
    try:
        return f"{pd.Timestamp(v):%Y-%m-%d}"
    except (ValueError, TypeError):
        return str(v)


def link(text: str, url: Optional[str]) -> str:
    return f"[{text}]({url})" if url else text


def esc(text: str) -> str:
    return str(text).replace("|", "／").replace("\n", " ").strip()


def currency(stock) -> str:
    return "新台幣" if stock.is_tw else "美元"


# --------------------------------------------------------------------------- 個股段落


def _price_section(b: "StockBundle") -> List[str]:
    out = ["#### 價格與技術狀態（描述，不是預測）", ""]
    if not b.prices.ok or not b.desc:
        out.append(f"- 價格資料{NA}（{esc(b.prices.error or '')}）。")
        return out + [""]
    d = b.desc
    st = b.stock
    out.append(
        f"- 最後收盤：{d['last_close']:,.2f} {currency(st)}（{fd(d['last_date'])}，"
        f"{link('Yahoo Finance', b.prices.url)}，次級來源；已含除權息調整）。"
    )
    out.append(f"- 報酬：1 日 {fp(d['ret_1d'])}、5 日 {fp(d['ret_5d'])}、20 日 {fp(d['ret_20d'])}。")
    rsi = "—" if d["rsi14"] is None else f"{d['rsi14']:.1f}"
    out.append(f"- 相對均線：MA20 {fp(d['ma20_gap'])}、MA60 {fp(d['ma60_gap'])}；RSI14 {rsi}。")
    if st.listed_since:
        out.append(f"- 上述統計只用 {st.listed_since} 掛牌後的 {d['rows']} 筆日線；{esc(st.security_note)}。")
    if b.prices.note:
        out.append(f"- 歷史拼接：{esc(b.prices.note)}。")
    return out + [""]


def _thirteen_f_tables(b: "StockBundle") -> List[str]:
    res = b.thirteen_f
    out: List[str] = []
    if res is None:
        return out
    if not res.ok:
        out += [f"**近期 13F 新申報**：{NA}（{esc(res.error or '')}；{link('查詢連結', res.url)}）。未取得不代表沒有新申報。", ""]
        return out
    data = res.data
    positions: List[sec_edgar.ThirteenFPosition] = data["positions"]
    window = f"{fd(data['start'])}～{fd(data['end'])}"
    out.append(
        f"**近期 13F 新申報（申報日 {window}；一手：{link('SEC EDGAR 全文檢索', res.url)}）**"
    )
    out.append("")
    if not positions:
        out += [
            "- 檢索期間內沒有解析到含本 CUSIP 的 13F 申報。可能原因：非申報季、全文索引延遲，"
            "或機構已出清（出清者不會被 CUSIP 檢索找到）。不能據此推論機構沒有持股。",
            "",
        ]
        if res.note:
            out += [f"- 檢索狀態：{esc(res.note)}。", ""]
        return out

    periods = sorted({p.hit.period for p in positions if p.hit.period})
    out.append(
        f"- 申報季度截止日：{'、'.join(periods) or '—'}。下表是季末持股，不是本週交易；"
        "「前期」為同一申報人前一季 13F-HR 原件，不含修正申報。"
    )
    if res.note:
        out.append(f"- 檢索狀態：{esc(res.note)}。")
    out.append("")

    def row_link(p: sec_edgar.ThirteenFPosition) -> str:
        cur = link("本期", p.url)
        prev = link("前期", p.prior_url) if p.prior_url else "前期—"
        return f"{cur}／{prev}"

    a_rows, b_rows, c_rows = [], [], []
    for p in positions:
        cls = sec_edgar.classify(p)
        known = p.prior is not None
        pr = p.prior or sec_edgar.Legs()
        filer = esc(p.hit.filer)
        meta = f"{p.hit.period or '—'}／{p.hit.file_date or '—'}（{p.hit.form}）"
        for label, cur, prev, val in (
            ("股票", p.current.shares, pr.shares, p.current.value_shares),
            ("Call", p.current.call, pr.call, p.current.value_call),
        ):
            if cur > 0:
                a_rows.append(
                    f"| {filer} | {label} | {fi(cur)}／{fi(prev) if known else '—'} | {fi(val)} | "
                    f"{sec_edgar.change_text(cur, prev, known)} | {meta} | {row_link(p)} |"
                )
        if cls["B"]:
            b_rows.append(
                f"| {filer} | Put | {fi(p.current.put)}／{fi(pr.put) if known else '—'} | {fi(p.current.value_put)} | "
                f"{sec_edgar.change_text(p.current.put, pr.put, known)} | {meta} | {row_link(p)} |"
            )
        if cls["C"]:
            prev_txt = f"{fi(pr.shares)}／{fi(pr.call)}／{fi(pr.put)}" if known else "前期未取得"
            c_rows.append(
                f"| {filer} | {fi(p.current.shares)}／{fi(p.current.call)}／{fi(p.current.put)} | {prev_txt} | "
                f"多腿並存，不能推得淨方向 | {row_link(p)} |"
            )
        for note in p.notes + ([p.prior_note] if p.prior_note else []):
            out.append(f"- {filer}：{esc(note)}。")
    if out[-1] != "":
        out.append("")

    head = "| 機構 | 部位 | 本期／前期數量 | 申報 value 欄（原值） | 期對期變化 | 季度／申報日（表單） | SEC 原件 |\n|---|---|---:|---:|---|---|---|"
    out += ["A. 多方證據：只證明已揭露多方曝險，不代表整體淨多", "", head]
    out += a_rows or ["| 本次解析的申報中無 | — | — | — | — | — | — |"]
    out += ["", "B. 空方／避險線索：Put 可能是保護性避險，不等於裸做空", "", head]
    out += b_rows or ["| 本次解析的申報中無具名 Put | — | — | — | — | — | 13F 不揭露一般空頭股數 |"]
    out += [
        "",
        "C. 方向不明：多腿並存",
        "",
        "| 機構 | 本期 股票／Call／Put | 前期 股票／Call／Put | 判讀 | SEC 原件 |",
        "|---|---:|---:|---|---|",
    ]
    out += c_rows or ["| 本次解析的申報中無 | — | — | — | — |"]
    out += [
        "",
        "數量單位為 13F `SH` 股數（選擇權為標的等值股數，不是合約張數或 delta）；value 欄依原件引用，"
        "不自行換算千元，也不加總成全市場持股。",
        "",
    ]
    return out


def _holder_filings(b: "StockBundle") -> List[str]:
    res = b.filings
    if res is None:
        return []
    if not res.ok:
        return [f"**≥5% 持有人申報（13G/13D）**：{NA}（{esc(res.error or '')}）。", ""]
    holders = [f for f in res.data if f["is_holder_filing"]]
    out = [f"**≥5% 持有人申報（13G/13D；一手：{link('SEC EDGAR', res.url)}）**", ""]
    if not holders:
        out += ["- 公告掃描期間內沒有 13G/13D。", ""]
        return out
    for f in holders:
        out.append(f"- {fd(f['filing_date'])}：{f['form']} — {link(esc(f['description']), f['url'])}（申報人名稱請見原件）")
    return out + [""]


def _yahoo_section(b: "StockBundle") -> List[str]:
    res = b.yahoo_holders
    if res is None:
        return []
    if not res.ok:
        return [f"**Yahoo 機構持股聚合（次級）**：{NA}（{esc(res.error or '')}）。", ""]
    data = res.data
    major = data["major"]
    out = [f"**Yahoo 機構持股聚合（次級，只作交叉檢查；{link('來源', res.url)}）**", ""]
    pct = major.get("institutionsPercentHeld")
    cnt = major.get("institutionsCount")
    out.append(
        f"- 機構持股比例 {fp(pct, 2, signed=False)}、機構家數 {fi(cnt)}；口徑由 Yahoo 決定，"
        "各機構申報期不同，不是同一時點的全體機構總量。"
    )
    rows = data["institutional"][:5]
    if rows:
        out += ["", "| 機構（Yahoo 前五） | 申報期 | 股數 | 占比 | Yahoo 標示變化 |", "|---|---|---:|---:|---:|"]
        for r in rows:
            out.append(
                f"| {esc(r['holder'])} | {fd(r['date_reported'])} | {fi(r['shares'])} | "
                f"{fp(r['pct_held'], 2, signed=False)} | {fp(r['pct_change'])} |"
            )
    short = data.get("short") or {}
    if short.get("shares_short") is not None:
        out += [
            "",
            f"- 市場總 short interest（Yahoo 轉載，次級）：{fi(short['shares_short'])} 股（結算日 {fd(short['settlement_date'])}；"
            f"前期 {fi(short.get('shares_short_prior'))} 股）。**無法歸戶**到任何具名機構，也不能與 13F Put 相加。",
        ]
    return out + [""]


def _tw_section(b: "StockBundle") -> List[str]:
    out = ["#### 機構籌碼（台股）", ""]
    latest = b.tw_latest
    hist = b.tw_history
    if latest is not None and latest.ok:
        r = latest.data
        out.append(
            f"- 最新交易日 {r['date']} 三大法人（一手：{link('櫃買中心 OpenAPI', latest.url)}，單位：股）："
            f"外資及陸資 {fs(r['foreign_net'])}、投信 {fs(r['trust_net'])}、自營商 {fs(r['dealer_net'])}、合計 {fs(r['total_net'])}。"
        )
        if b.tw_crosscheck:
            out.append(f"- 交叉檢查：{esc(b.tw_crosscheck)}。")
    else:
        err = esc(latest.error) if latest is not None and latest.error else ""
        out.append(f"- 最新交易日三大法人（櫃買中心）{NA}（{err}）。")

    w5 = taiwan.window_sums(hist, 5)
    w20 = taiwan.window_sums(hist, 20)
    if w5 and w20:
        out += [
            "",
            f"三大法人累積（本 repo 累積之歷史，{w20['start']}～{w20['end']}；來源標記見 `monitor/data/tw/`）",
            "",
            "| 類別 | 近 5 日淨買賣超 | 近 20 日淨買賣超 | 連續天數（+買／−賣） | 判讀類別 |",
            "|---|---:|---:|---:|---|",
        ]
        for key, label in (("foreign_net", "外資及陸資（不含外資自營商）"), ("trust_net", "投信"), ("dealer_net", "自營商（含避險）")):
            s5, s20 = w5[key], w20[key]
            if s5 * s20 < 0:
                cat = "C 方向不明（近期轉向）"
            elif s20 > 0:
                cat = "A 多方動作（淨買超）"
            elif s20 < 0:
                cat = "B 減碼／空方線索（淨賣超≠放空）"
            else:
                cat = "—"
            out.append(f"| {label} | {fs(s5)} | {fs(s20)} | {fs(taiwan.streak(hist, key))} | {cat} |")
        if w20["days"] < 20:
            out.append(f"\n實際只有 {w20['days']} 個交易日資料（{b.stock.listed_since or ''} 掛牌或歷史尚在累積），20 日欄位以實際天數計。")
        out += [
            "",
            "三大法人是類別合計，**無法歸戶到個別機構**；淨賣超可能是減碼、換股或避險，不等於放空。"
            "自營商含權證避險部位，方向解讀需保守。這是交易日盤後的實際買賣，與美股 13F 的季末持股性質不同。",
        ]
    out.append("")

    sh = b.tw_shareholding
    if sh is not None and sh.ok and sh.data:
        last = sh.data[-1]
        first = sh.data[0]
        out.append(
            f"- 外資持股比率（FinMind 轉載 TWSE/TPEx 公開資料，次級）：{last['date']} 為 "
            f"{last['foreign_ratio'] if last['foreign_ratio'] is not None else '—'}%（持股 {fi(last['foreign_shares'])} 股）；"
            f"{first['date']} 為 {first['foreign_ratio'] if first['foreign_ratio'] is not None else '—'}%。{link('來源', sh.url)}"
        )
    elif sh is not None:
        out.append(f"- 外資持股比率：{NA}（{esc(sh.error or '')}）。")

    mg = b.tw_margin
    if mg is not None and mg.ok and mg.data:
        last = mg.data[-1]
        prev = mg.data[-6] if len(mg.data) >= 6 else mg.data[0]
        out.append(
            f"- 融券餘額（空方線索，**無法歸戶**；FinMind，次級；單位沿用來源欄位、未換算）：{last['date']} 為 "
            f"{fi(last['short_balance'])}，{prev['date']} 為 {fi(prev['short_balance'])}；"
            f"融資餘額 {fi(last['margin_balance'])}。{link('來源', mg.url)}"
        )
    elif mg is not None:
        out.append(f"- 融資融券：{NA}（{esc(mg.error or '')}）。")

    td = b.tdcc
    th = b.tdcc_history
    if td is not None and td.ok:
        r = td.data
        line = (
            f"- 集保股權分散（一手：{link('集保結算所開放資料', td.url)}，{r['date']}）：千張以上大戶持股 {r['big1000_ratio']}%"
            f"（{fi(r['big1000_holders'])} 人）、400 張以上 {r['big400_ratio']}%、50 張以下 {r['small50_ratio']}%，總人數 {fi(r['holders_total'])}。"
        )
        if th is not None and len(th) >= 2:
            prev = th.iloc[-2]
            try:
                diff = float(r["big1000_ratio"]) - float(prev["big1000_ratio"])
                line += f" 千張大戶比例較 {prev['date']} {diff:+.2f} 個百分點。"
            except (TypeError, ValueError):
                pass
        else:
            line += " 前一週資料尚未累積，無週變化。"
        out.append(line)
        out.append("  - 集保大戶可能是法人、董監或自然人，**不能等同機構持股**。")
    elif td is not None:
        out.append(f"- 集保股權分散：{NA}（{esc(td.error or '')}）。")
    return out + [""]


def _news_section(b: "StockBundle") -> List[str]:
    out = ["#### 新聞與公告", ""]
    if b.filings is not None:
        if b.filings.ok:
            company = [f for f in b.filings.data if not f["is_holder_filing"]]
            if company:
                out.append(f"公司與內部人申報（一手：{link('SEC EDGAR', b.filings.url)}）")
                out.append("")
                for f in company[:8]:
                    out.append(f"- {fd(f['filing_date'])}：{f['form']} — {link(esc(f['description']), f['url'])}")
                out.append("")
            else:
                out += ["- 公告掃描期間內沒有新的公司申報。", ""]
        else:
            out += [f"- SEC 公司申報{NA}（{esc(b.filings.error or '')}）。", ""]
    if b.stock.is_tw:
        out += ["- 台股重大訊息請以[公開資訊觀測站](https://mops.twse.com.tw/)原文為準；本節新聞僅為標題聚合。", ""]
    if b.news:
        out.append("新聞標題（Google News／Yahoo 聚合，次級；僅列標題，未逐篇核實內容，也不由程式判斷利多利空）")
        out.append("")
        for n in b.news:
            when = fd(n["published"]) if n.get("published") else "日期不明"
            out.append(f"- {when}｜{esc(n['source'])}｜{link(esc(n['title']), n['link'])}")
    else:
        errs = [esc(s.error) for s in b.news_sources if not s.ok and s.error]
        out.append(f"- 新聞{NA if errs else '：期間內無符合的標題'}{('（' + '；'.join(errs) + '）') if errs else ''}。")
    return out + [""]


def _prediction_section(ctx: "RunContext", b: "StockBundle") -> List[str]:
    p = b.prediction
    out = ["#### 漲跌預測（兩種方法並列，都要接受事後檢驗）", ""]
    out.append(f"**1. 統計基準模型（{p.horizon} 個交易日方向；walk-forward 邏輯迴歸）**")
    out.append("")
    if not p.ok:
        out.append(f"- 未產生機率：{esc(p.reason or '')}。")
    else:
        out.append(
            f"- 截至 {fd(p.asof)} 收盤：未來 {p.horizon} 個交易日上漲機率 **{p.prob_up:.1%}** → **{p.signal}**。"
        )
        out.append(
            f"- 樣本外（{fd(p.oos_start)}～{fd(p.oos_end)}，{p.n_oos} 筆）：命中率 {fp(p.acc_model, 1, False)}，"
            f"天真基準 {fp(p.acc_naive, 1, False)}，期間實際上漲比例 {fp(p.up_rate_oos, 1, False)}；"
            f"Brier 技巧分數 {('—' if p.bss is None else f'{p.bss:+.3f}')}；AUC {('—' if p.auc is None else f'{p.auc:.3f}')}。"
        )
        out.append(
            "- 判讀規則：命中率需比天真基準高 2 個百分點以上且 Brier 技巧分數 > 0，才把機率解讀為偏多／偏空；"
            "否則一律「無明確訊號」。每日樣本的預測期間重疊，樣本外成績會高估有效樣本數。"
        )
    out.append("")
    out.append("**2. 上游 LLM 判斷（daily_stock_analysis）**")
    out.append("")
    if b.dsa:
        rel = ctx.dsa_report_rel
        detail = f"；完整內容見 [{rel}]({Path(rel).name})" if rel else ""
        score = "—" if b.dsa.score is None else f"{b.dsa.score:g}"
        out.append(f"- 操作建議：{esc(b.dsa.signal)}｜評分 {score}｜趨勢：{esc(b.dsa.trend)}{detail}。")
        out.append("- 這是 LLM 綜合行情、新聞後的意見，未經本 repo 驗證；預測期間未由上游定義，本 repo 以 "
                   f"{p.horizon} 個交易日事後評估。")
    else:
        out.append("- 本次未啟用或未取得（需在 GitHub Secrets 設定任一 LLM 金鑰，見 `monitor/README.md`）。")
    out.append("")
    if b.track:
        out += ["**事後命中追蹤（`monitor/data/predictions.csv`）**", "", "| 方法 | 已記錄 | 有方向 | 已到期 | 命中 | 命中率 | 同期上漲比例 |", "|---|---:|---:|---:|---:|---:|---:|"]
        names = {"logit_v1": "統計基準模型", "dsa_llm": "LLM（DSA）"}
        for model, s in sorted(b.track.items()):
            out.append(
                f"| {names.get(model, model)} | {s['logged']} | {s['directional']} | {s['matured']} | {s['hits']} | "
                f"{fp(s['hit_rate'], 1, False)} | {fp(s['up_rate'], 1, False)} |"
            )
        out += ["", "「同期上漲比例」是同一批到期窗口中實際上漲的比例，可當作永遠猜漲的對照。", ""]
    return out


def render_stock(ctx: "RunContext", b: "StockBundle") -> List[str]:
    st = b.stock
    title = f"### {st.display_name}（{st.exchange}；{esc(st.security_note)}）"
    out = [title, ""]
    out += _price_section(b)
    if st.is_us:
        out += ["#### 機構持股（美股）", ""]
        out += _thirteen_f_tables(b)
        out += _holder_filings(b)
        out += _yahoo_section(b)
    if st.is_tw:
        out += _tw_section(b)
    out += _news_section(b)
    out += _prediction_section(ctx, b)
    return out


# --------------------------------------------------------------------------- 總覽與整份報告


def _overview_institutional(b: "StockBundle") -> str:
    if b.stock.is_us:
        res = b.thirteen_f
        if res is None or not res.ok:
            return f"13F {NA}"
        pos = res.data["positions"]
        if not pos:
            return "期間內無含本股的新 13F"
        a = sum(1 for p in pos if sec_edgar.classify(p)["A"])
        bb = sum(1 for p in pos if sec_edgar.classify(p)["B"])
        return f"新 13F {len(pos)} 份（多方腿 {a}、Put {bb}）"
    hist = b.tw_history
    w5 = taiwan.window_sums(hist, 5)
    if not w5:
        return f"三大法人 {NA}"
    return f"三大法人近 {w5['days']} 日 {fs(w5['total_net'])} 股"


def render(ctx: "RunContext", bundles: List["StockBundle"]) -> str:
    names = "、".join(b.stock.name if b.stock.is_tw else b.stock.id for b in bundles)
    gen = ctx.generated_at
    lines = [
        f"# 每日監控：{ctx.today:%Y-%m-%d}（{names}）",
        "",
        "> 由 `python -m monitor run` 自動產生。內容是公開資料整理與模型輸出，**不構成投資建議**。"
        "數字缺漏時寫「本次未取得」，不以估算補齊。",
        "",
        "## 1. 資料範圍與時差",
        "",
        f"- 產生時間：{gen:%Y-%m-%d %H:%M}（{ctx.settings.timezone}）。",
    ]
    last_dates = [f"{b.stock.id} {fd(b.desc['last_date'])}" for b in bundles if b.desc]
    if last_dates:
        lines.append(f"- 價格最後交易日（各市場當地日期）：{'、'.join(last_dates)}。")
    lines += [
        "- 美股 13F：只檢索最近幾天**新申報**且含該 CUSIP 的文件；13F 是季末持股、最遲季末後 45 日申報，**不是即時資金流，也不是本週交易**。",
        "- 台股三大法人：交易日盤後資料；集保股權分散：每週一份。兩者與 13F 的時間性質不同，不能直接比較。",
        f"- 上游 LLM：daily_stock_analysis {ctx.dsa_version}。",
        "",
        "## 2. 總覽",
        "",
        "| 股票 | 最後收盤（日期） | 5 日 | 20 日 | 機構面本次資訊 | 統計模型 P(5日漲)／判讀 | LLM 判斷 |",
        "|---|---:|---:|---:|---|---|---|",
    ]
    for b in bundles:
        d = b.desc
        price = f"{d['last_close']:,.2f}（{fd(d['last_date'])}）" if d else NA
        r5 = fp(d["ret_5d"]) if d else "—"
        r20 = fp(d["ret_20d"]) if d else "—"
        p = b.prediction
        pred = f"{p.prob_up:.0%}／{p.signal}" if p.ok else f"—／{esc(p.reason or '')}"
        llm = f"{esc(b.dsa.signal)}／{esc(b.dsa.trend)}" if b.dsa else "未啟用"
        lines.append(f"| {b.stock.display_name} | {price} | {r5} | {r20} | {_overview_institutional(b)} | {pred} | {llm} |")
    lines += ["", "## 3. 個股", ""]
    for b in bundles:
        lines += render_stock(ctx, b)
    lines += ["## 4. 資料來源狀態", "", "| 來源 | 狀態 | 一手／次級 | 說明 |", "|---|---|---|---|"]
    for e in ctx.log.entries:
        status = "成功" if e.ok else "失敗"
        kind = "次級" if e.secondary else "一手"
        detail = esc(e.note or e.error or "")
        lines.append(f"| {link(esc(e.source), e.url)} | {status} | {kind} | {detail} |")
    lines += [
        "",
        "## 5. 方法與限制",
        "",
        "- 13F 普通股／Call 只證明已揭露的多方曝險，不證明機構整體淨做多；13F 不揭露一般空頭股票部位。",
        "- Put 與 short interest 只能作空方或避險線索；市場總 short interest 與台股融券餘額都**無法歸戶**到特定機構。",
        "- 「新出現」指前期原件未列，不等於已證實首次買進；修正申報（/A）不與原件相加。",
        "- 統計模型只用價格與成交量，未納入基本面與事件；它的用途是當作 LLM 與市場評論的對照組。",
        f"- LLM 結論來自上游開源專案 [{'daily_stock_analysis'}]({dsa_bridge.DSA_REPO})，可能出錯或產生幻覺。",
        "- 本報告不構成投資建議。",
        "",
    ]
    return "\n".join(lines)


def update_index(out_dir: Path, today: date, settings: "Settings") -> None:
    index = out_dir / "README.md"
    names = "、".join(s.name if s.is_tw else s.id for s in settings.stocks)
    entry = f"- [{today:%Y-%m-%d}：{names} 每日監控]({today:%Y}/{today:%Y-%m-%d}.md)"
    header = [
        "# 每日監控報告索引",
        "",
        "由 `.github/workflows/daily-monitor.yml` 每個交易日自動產生；`-llm.md` 為上游 LLM 決策儀表盤（若有啟用）。",
        "",
    ]
    entries: List[str] = []
    if index.exists():
        entries = [l for l in index.read_text(encoding="utf-8").splitlines() if l.startswith("- [")]
    entries = [e for e in entries if f"({today:%Y}/{today:%Y-%m-%d}.md)" not in e]
    entries.append(entry)

    def key(e: str) -> str:
        return e[3:13]

    entries.sort(key=key, reverse=True)
    index.write_text("\n".join(header + entries) + "\n", encoding="utf-8")
