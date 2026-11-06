"""新聞標題：Google News RSS（聚合）＋ Yahoo Finance。

只收錄標題、來源與連結，不擷取內文、不由程式判斷利多利空；
新聞情緒判讀交由上游 daily_stock_analysis 的 LLM（若有啟用），並另行標示。
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Dict, Iterable, List, Optional
from urllib.parse import quote

from ..httpclient import HttpClient, SourceResult, short_error

GOOGLE_NEWS_RSS = "https://news.google.com/rss/search?q={q}&hl={hl}&gl={gl}&ceid={ceid}"


def google_news_url(query: Dict[str, str], lookback_days: int) -> str:
    q = f"{query['q']} when:{lookback_days}d"
    return GOOGLE_NEWS_RSS.format(
        q=quote(q),
        hl=query.get("hl", "zh-TW"),
        gl=query.get("gl", "TW"),
        ceid=quote(query.get("ceid", "TW:zh-Hant")),
    )


def parse_rss(xml_text: str) -> List[dict]:
    root = ET.fromstring(xml_text)
    out: List[dict] = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        source_el = item.find("source")
        source = source_el.text.strip() if source_el is not None and source_el.text else None
        pub = item.findtext("pubDate")
        published = None
        if pub:
            try:
                published = parsedate_to_datetime(pub)
            except (TypeError, ValueError):
                published = None
        if source and title.endswith(f" - {source}"):
            title = title[: -len(f" - {source}")].strip()
        if title and link:
            out.append({"title": title, "link": link, "source": source or "Google News", "published": published})
    return out


def _norm_title(t: str) -> str:
    return re.sub(r"[\W_]+", "", t.lower())[:60]


def dedupe_and_trim(items: Iterable[dict], since: datetime, limit: int) -> List[dict]:
    seen = set()
    kept: List[dict] = []
    for it in items:
        pub: Optional[datetime] = it.get("published")
        if pub is not None:
            if pub.tzinfo is None:
                pub = pub.replace(tzinfo=timezone.utc)
            it["published"] = pub
            if pub < since:
                continue
        key = _norm_title(it["title"])
        if key in seen:
            continue
        seen.add(key)
        kept.append(it)
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    kept.sort(key=lambda x: x.get("published") or epoch, reverse=True)
    return kept[:limit]


def fetch_google_news(client: HttpClient, queries: List[Dict[str, str]], lookback_days: int) -> SourceResult:
    items: List[dict] = []
    errors: List[str] = []
    urls: List[str] = []
    for q in queries:
        url = google_news_url(q, lookback_days)
        urls.append(url)
        try:
            items.extend(parse_rss(client.get_text(url)))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"「{q['q']}」：{short_error(exc)}")
    if errors and not items:
        return SourceResult.fail("Google News RSS", "；".join(errors), urls[0] if urls else None, secondary=True)
    return SourceResult(
        source="Google News RSS",
        ok=True,
        data=items,
        url=urls[0] if urls else None,
        note=("部分查詢失敗：" + "；".join(errors)) if errors else None,
        secondary=True,
    )


def since_utc(now: datetime, lookback_days: int) -> datetime:
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now - timedelta(days=lookback_days)
