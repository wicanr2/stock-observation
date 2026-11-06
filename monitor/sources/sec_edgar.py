"""SEC EDGAR 一手資料：公司／持股人公告、13F 新申報探索與 information table 解析。

口徑（與 AGENTS.md 一致）：
- 13F 普通股／Call 只證明「已揭露的多方曝險」，不證明整體淨做多。
- 13F 不揭露一般放空股數；Put 只能當空方或避險「線索」。
- 13F 是季末持股、季末後 45 日內申報的延遲資料，不是本週交易。
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Dict, List, Optional, Tuple

from ..httpclient import HttpClient, SourceResult, short_error

EFTS_URL = "https://efts.sec.gov/LATEST/search-index"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik10}.json"
ARCHIVE_DIR = "https://www.sec.gov/Archives/edgar/data/{cik}/{adsh_nodash}"

# 公司公告中值得列入每日監控的表單。13G/13D 是 ≥5% 實益持有人申報（一手機構持股證據）。
FILING_FORMS_OF_INTEREST = {
    "8-K", "8-K/A", "6-K", "6-K/A", "10-Q", "10-K", "20-F", "40-F",
    "SC 13G", "SC 13G/A", "SC 13D", "SC 13D/A",
    "SCHEDULE 13G", "SCHEDULE 13G/A", "SCHEDULE 13D", "SCHEDULE 13D/A",
    "3", "4", "144", "DEF 14A", "S-8",
}
HOLDER_FORMS = {f for f in FILING_FORMS_OF_INTEREST if "13G" in f or "13D" in f}

FORM_LABELS = {
    "8-K": "重大事件（8-K）",
    "6-K": "外國發行人報告（6-K）",
    "10-Q": "季報（10-Q）",
    "10-K": "年報（10-K）",
    "20-F": "外國發行人年報（20-F）",
    "4": "內部人持股變動（Form 4）",
    "3": "內部人初次持股（Form 3）",
    "144": "內部人擬出售（Form 144）",
}


def cik10(cik: str) -> str:
    return str(int(str(cik).strip())).zfill(10)


def cik_int(cik: str) -> str:
    return str(int(str(cik).strip()))


def filing_index_url(cik: str, adsh: str) -> str:
    return f"{ARCHIVE_DIR.format(cik=cik_int(cik), adsh_nodash=adsh.replace('-', ''))}/{adsh}-index.htm"


def previous_quarter_end(period: str) -> Optional[str]:
    try:
        y, m, _ = (int(x) for x in period.split("-"))
    except ValueError:
        return None
    mapping = {3: (y - 1, 12, 31), 6: (y, 3, 31), 9: (y, 6, 30), 12: (y, 9, 30)}
    if m not in mapping:
        return None
    py, pm, pd_ = mapping[m]
    return f"{py:04d}-{pm:02d}-{pd_:02d}"


# --------------------------------------------------------------------------- 公司公告


def parse_recent_filings(payload: dict, since: date, cik: str) -> List[dict]:
    recent = (payload or {}).get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    out: List[dict] = []
    for i, form in enumerate(forms):
        try:
            fdate = date.fromisoformat(recent["filingDate"][i])
        except (KeyError, IndexError, ValueError):
            continue
        if fdate < since or form not in FILING_FORMS_OF_INTEREST:
            continue
        adsh = recent["accessionNumber"][i]
        primary = (recent.get("primaryDocument") or [""] * len(forms))[i]
        desc = (recent.get("primaryDocDescription") or [""] * len(forms))[i]
        base = ARCHIVE_DIR.format(cik=cik_int(cik), adsh_nodash=adsh.replace("-", ""))
        out.append(
            {
                "form": form,
                "filing_date": fdate,
                "report_date": (recent.get("reportDate") or [""] * len(forms))[i] or None,
                "accession": adsh,
                "description": desc or FORM_LABELS.get(form.replace("/A", ""), form),
                "url": f"{base}/{primary}" if primary else filing_index_url(cik, adsh),
                "index_url": filing_index_url(cik, adsh),
                "is_holder_filing": form in HOLDER_FORMS,
            }
        )
    out.sort(key=lambda r: r["filing_date"], reverse=True)
    return out


def fetch_company_filings(client: HttpClient, cik: str, since: date) -> SourceResult:
    url = SUBMISSIONS_URL.format(cik10=cik10(cik))
    try:
        payload = client.get_json(url)
    except Exception as exc:  # noqa: BLE001
        return SourceResult.fail("SEC EDGAR 公司申報", short_error(exc), url)
    return SourceResult(
        source="SEC EDGAR 公司申報",
        ok=True,
        data=parse_recent_filings(payload, since, cik),
        url=f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik10(cik)}",
    )


# --------------------------------------------------------------------------- 13F 解析


@dataclass
class Legs:
    """同一 CUSIP 的各腿合計（單位：SH 股數；PRN 另計不混入）。"""

    shares: int = 0
    call: int = 0
    put: int = 0
    value_shares: int = 0
    value_call: int = 0
    value_put: int = 0
    prn_rows: int = 0
    rows: int = 0
    issuer: Optional[str] = None
    title: Optional[str] = None

    @property
    def empty(self) -> bool:
        return self.rows == 0


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _child_text(elem: ET.Element, name: str) -> Optional[str]:
    for child in elem.iter():
        if _local(child.tag) == name and child.text is not None:
            return child.text.strip()
    return None


def _to_int(text: Optional[str]) -> Optional[int]:
    if text is None:
        return None
    t = text.replace(",", "").strip()
    if not t:
        return None
    try:
        return int(float(t))
    except ValueError:
        return None


def parse_infotable(xml_text: str, cusip: str) -> Legs:
    """解析 13F information table，回傳指定 CUSIP 的股票／Call／Put 合計。"""
    legs = Legs()
    cusip = cusip.upper()
    root = ET.fromstring(xml_text.encode("utf-8") if isinstance(xml_text, str) else xml_text)
    for row in root.iter():
        if _local(row.tag) != "infoTable":
            continue
        if (_child_text(row, "cusip") or "").upper() != cusip:
            continue
        legs.rows += 1
        legs.issuer = legs.issuer or _child_text(row, "nameOfIssuer")
        legs.title = legs.title or _child_text(row, "titleOfClass")
        amount = _to_int(_child_text(row, "sshPrnamt")) or 0
        amount_type = (_child_text(row, "sshPrnamtType") or "SH").upper()
        value = _to_int(_child_text(row, "value")) or 0
        put_call = (_child_text(row, "putCall") or "").strip().upper()
        if amount_type != "SH":
            legs.prn_rows += 1
            continue
        if put_call == "PUT":
            legs.put += amount
            legs.value_put += value
        elif put_call == "CALL":
            legs.call += amount
            legs.value_call += value
        else:
            legs.shares += amount
            legs.value_shares += value
    return legs


def pick_infotable_name(names: List[str]) -> Optional[str]:
    xmls = [n for n in names if n.lower().endswith(".xml") and n.lower() != "primary_doc.xml"]
    if not xmls:
        return None
    preferred = [n for n in xmls if re.search(r"info|table", n, re.I)]
    return (preferred or xmls)[0]


def find_infotable_url(client: HttpClient, cik: str, adsh: str) -> Optional[str]:
    base = ARCHIVE_DIR.format(cik=cik_int(cik), adsh_nodash=adsh.replace("-", ""))
    payload = client.get_json(f"{base}/index.json")
    items = (payload or {}).get("directory", {}).get("item", [])
    name = pick_infotable_name([str(i.get("name", "")) for i in items])
    return f"{base}/{name}" if name else None


# --------------------------------------------------------------------------- 13F 探索


@dataclass
class ThirteenFHit:
    cik: str
    filer: str
    adsh: str
    form: str
    file_date: str
    period: str
    filename: Optional[str] = None


def parse_efts(payload: dict) -> Tuple[int, List[ThirteenFHit]]:
    hits_obj = (payload or {}).get("hits", {})
    total = hits_obj.get("total", {})
    total_n = int(total.get("value", 0)) if isinstance(total, dict) else int(total or 0)
    by_adsh: Dict[str, ThirteenFHit] = {}
    for hit in hits_obj.get("hits", []):
        src = hit.get("_source", {})
        _id = str(hit.get("_id", ""))
        adsh = src.get("adsh") or _id.split(":", 1)[0]
        filename = _id.split(":", 1)[1] if ":" in _id else None
        ciks = src.get("ciks") or []
        names = src.get("display_names") or []
        if not adsh or not ciks:
            continue
        filer = re.sub(r"\s*\(CIK \d+\)\s*$", "", names[0]).strip() if names else ciks[0]
        current = by_adsh.get(adsh)
        hit_obj = ThirteenFHit(
            cik=str(ciks[0]),
            filer=filer,
            adsh=adsh,
            form=str(src.get("form") or src.get("root_form") or ""),
            file_date=str(src.get("file_date") or ""),
            period=str(src.get("period_ending") or ""),
            filename=filename,
        )
        is_table = bool(filename) and pick_infotable_name([filename]) is not None
        if current is None or (is_table and not (current.filename and pick_infotable_name([current.filename]))):
            by_adsh[adsh] = hit_obj
    return total_n, sorted(by_adsh.values(), key=lambda h: (h.file_date, h.filer), reverse=True)


def search_13f_filings(
    client: HttpClient, cusip: str, start: date, end: date, max_filings: int
) -> Tuple[int, List[ThirteenFHit], bool]:
    hits: Dict[str, ThirteenFHit] = {}
    total = 0
    offset = 0
    while True:
        params = {
            "q": f'"{cusip}"',
            "forms": "13F-HR,13F-HR/A",
            "dateRange": "custom",
            "startdt": start.isoformat(),
            "enddt": end.isoformat(),
            "from": offset,
        }
        payload = client.get_json(EFTS_URL, params=params)
        total, page = parse_efts(payload)
        if not page:
            break
        for h in page:
            hits.setdefault(h.adsh, h)
        offset += len(payload.get("hits", {}).get("hits", []))
        if len(hits) >= max_filings or offset >= total or offset >= 1000:
            break
    ordered = sorted(hits.values(), key=lambda h: (h.file_date, h.filer), reverse=True)
    truncated = len(ordered) > max_filings or total > offset
    return total, ordered[:max_filings], truncated


def _prior_filing(client: HttpClient, cik: str, prev_period: str) -> Optional[Tuple[str, str]]:
    payload = client.get_json(SUBMISSIONS_URL.format(cik10=cik10(cik)))
    recent = payload.get("filings", {}).get("recent", {})
    best: Optional[Tuple[str, str]] = None
    report_dates = recent.get("reportDate") or []
    for i, form in enumerate(recent.get("form", [])):
        if form != "13F-HR" or i >= len(report_dates) or report_dates[i] != prev_period:
            continue
        fdate = recent["filingDate"][i]
        if best is None or fdate > best[1]:
            best = (recent["accessionNumber"][i], fdate)
    return best


@dataclass
class ThirteenFPosition:
    hit: ThirteenFHit
    current: Legs
    prior: Optional[Legs] = None
    prior_adsh: Optional[str] = None
    prior_note: Optional[str] = None
    url: str = ""
    prior_url: Optional[str] = None
    notes: List[str] = field(default_factory=list)


def fetch_13f_positions(
    client: HttpClient,
    cusip: str,
    today: date,
    lookback_days: int = 7,
    max_filings: int = 40,
    compare_prior: bool = True,
) -> SourceResult:
    start = today - timedelta(days=lookback_days)
    search_url = (
        "https://efts.sec.gov/LATEST/search-index?q=%22" + cusip + "%22&forms=13F-HR,13F-HR/A"
        f"&dateRange=custom&startdt={start.isoformat()}&enddt={today.isoformat()}"
    )
    try:
        total, hits, truncated = search_13f_filings(client, cusip, start, today, max_filings)
    except Exception as exc:  # noqa: BLE001
        return SourceResult.fail("SEC EDGAR 全文檢索（13F）", short_error(exc), search_url)

    positions: List[ThirteenFPosition] = []
    errors: List[str] = []
    for hit in hits:
        try:
            if hit.filename and pick_infotable_name([hit.filename]):
                base = ARCHIVE_DIR.format(cik=cik_int(hit.cik), adsh_nodash=hit.adsh.replace("-", ""))
                table_url = f"{base}/{hit.filename}"
            else:
                table_url = find_infotable_url(client, hit.cik, hit.adsh)
            if not table_url:
                errors.append(f"{hit.filer}：找不到 information table")
                continue
            legs = parse_infotable(client.get_text(table_url), cusip)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{hit.filer}：{short_error(exc)}")
            continue
        if legs.empty:
            # 全文命中但表格中沒有此 CUSIP（例如出現在附註），不列入。
            continue
        pos = ThirteenFPosition(hit=hit, current=legs, url=filing_index_url(hit.cik, hit.adsh))
        if hit.form.endswith("/A"):
            pos.notes.append("修正申報（13F-HR/A），可能是重述或補充，不與原件相加")
        if legs.prn_rows:
            pos.notes.append(f"{legs.prn_rows} 列為 PRN（本金）單位，未併入股數")
        if compare_prior and hit.period:
            prev = previous_quarter_end(hit.period)
            try:
                prior = _prior_filing(client, hit.cik, prev) if prev else None
                if prior is None:
                    pos.prior_note = f"前期（{prev}）13F-HR 未在申報人近期清單中找到"
                else:
                    prior_table = find_infotable_url(client, hit.cik, prior[0])
                    if prior_table:
                        pos.prior = parse_infotable(client.get_text(prior_table), cusip)
                        pos.prior_adsh = prior[0]
                        pos.prior_url = filing_index_url(hit.cik, prior[0])
                    else:
                        pos.prior_note = "前期 information table 未取得"
            except Exception as exc:  # noqa: BLE001
                pos.prior_note = f"前期查詢失敗：{short_error(exc)}"
        positions.append(pos)

    note_parts = [f"EFTS 命中 {total} 份文件（去重後解析 {len(hits)} 份申報）"]
    if truncated:
        note_parts.append(f"超過上限 {max_filings} 份，已截斷")
    if errors:
        note_parts.append("部分失敗：" + "；".join(errors[:5]))
    return SourceResult(
        source="SEC EDGAR 全文檢索（13F）",
        ok=True,
        data={"positions": positions, "start": start, "end": today, "truncated": truncated, "errors": errors},
        url=search_url,
        note="；".join(note_parts),
    )


# --------------------------------------------------------------------------- 多空分類


def classify(pos: ThirteenFPosition) -> Dict[str, bool]:
    """依 AGENTS.md：A 多方（股票或 Call）、B 空方／避險線索（Put）、C 方向不明（多腿並存）。"""
    c = pos.current
    long_leg = c.shares > 0 or c.call > 0
    put_leg = c.put > 0
    return {"A": long_leg, "B": put_leg, "C": long_leg and put_leg}


def change_text(cur: int, prev: Optional[int], prior_known: bool) -> str:
    if not prior_known:
        return "前期未取得"
    prev = prev or 0
    if prev == 0 and cur > 0:
        return "新出現；前期未列"
    if cur == 0 and prev > 0:
        return "本期未列"
    if cur == 0 and prev == 0:
        return "—"
    diff = cur - prev
    return f"{diff:+,}（{diff / prev:+.1%}）"
