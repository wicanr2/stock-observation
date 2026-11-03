"""接上游 daily_stock_analysis（DSA）產出的 LLM 決策儀表盤。

DSA 只支援簡體中文／英文／韓文報告；這裡用 OpenCC（s2twp）轉成臺灣正體用語，
並擷取「分析結果摘要」中的每檔結論（操作建議、評分、趨勢判斷）。
DSA 內容由 LLM 生成，數字與新聞未經本 repo 逐一核實，報告中一律標示為 LLM 意見。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

DSA_REPO = "https://github.com/ZhuLinsen/daily_stock_analysis"

# 例：🟡 **新萊應材(300260)**: 賣出 | 評分 35 | 看空
_SUMMARY_RE = re.compile(
    r"\*\*(?P<name>.+?)\((?P<code>[^()]+)\)\*\*\s*[:：]\s*(?P<signal>[^|]+?)\s*\|\s*\S+\s+(?P<score>-?\d+(?:\.\d+)?)\s*\|\s*(?P<trend>.+?)\s*$"
)


@dataclass
class DsaSignal:
    code: str
    name: str
    signal: str
    score: Optional[float]
    trend: str


def to_traditional(text: str) -> str:
    try:
        from opencc import OpenCC
    except ImportError:  # pragma: no cover - requirements 已列入
        return text
    return OpenCC("s2twp").convert(text)


def parse_summary(markdown: str) -> List[DsaSignal]:
    out: List[DsaSignal] = []
    for line in markdown.splitlines():
        m = _SUMMARY_RE.search(line.strip())
        if not m:
            continue
        try:
            score = float(m.group("score"))
        except ValueError:
            score = None
        out.append(
            DsaSignal(
                code=m.group("code").strip(),
                name=m.group("name").strip(),
                signal=m.group("signal").strip(),
                score=score,
                trend=m.group("trend").strip(),
            )
        )
    return out


def match_codes(signals: List[DsaSignal], dsa_codes: Dict[str, str]) -> Dict[str, DsaSignal]:
    """dsa_codes: {stock_id: dsa_code}；DSA 可能輸出大小寫或去後綴的代碼，寬鬆比對。"""

    def norm(c: str) -> str:
        return c.upper().replace(".TWO", "").replace(".TW", "")

    out: Dict[str, DsaSignal] = {}
    for sid, code in dsa_codes.items():
        for s in signals:
            if s.code.upper() == code.upper() or norm(s.code) == norm(code):
                out[sid] = s
                break
    return out


def convert_report(src: Path, dest: Path, version: str, run_date: str) -> str:
    raw = src.read_text(encoding="utf-8")
    body = to_traditional(raw)
    header = (
        f"# 上游 LLM 決策儀表盤：{run_date}\n\n"
        f"> 本檔由 [daily_stock_analysis]({DSA_REPO})（{version}）自動產生，再以 OpenCC 轉為臺灣正體。\n"
        "> 內容是大型語言模型的意見，其中的價格、新聞與「買賣點位」**未經本 repo 逐一核實**，"
        "可能有錯誤或幻覺；不得當作已驗證的機構持股或交易紀錄。本檔不構成投資建議。\n\n---\n\n"
    )
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(header + body, encoding="utf-8")
    return body
