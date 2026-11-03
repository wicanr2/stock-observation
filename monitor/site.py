"""建置 GitHub Pages 靜態儀表板。

    python -m monitor site --out _site          # 只用已 commit 的每日資料
    python -m monitor site --out _site --live   # 另抓盤中報價、最新新聞與櫃買三大法人

輸出：
    _site/index.html、app.js、style.css      ← monitor/web/
    _site/data/dashboard.json                ← monitor/data/dashboard.json（每日）
    _site/data/live.json                     ← 盤中刷新（--live）
    _site/data/reports.json                  ← 每日監控與週報的報告清單（連到 GitHub）
"""

from __future__ import annotations

import os
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional
from zoneinfo import ZoneInfo

from . import export
from .config import Settings
from .httpclient import HttpClient, SourceResult
from .sources import news as news_src
from .sources import quotes, taiwan, yahoo

REPO_ROOT = Path(__file__).resolve().parent.parent
WEB_DIR = Path(__file__).resolve().parent / "web"

_REPORT_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})(-llm)?\.md$")


def list_reports(root: Path, repo: str, branch: str) -> Dict[str, List[dict]]:
    def scan(base: Path, kind: str) -> List[dict]:
        rows = []
        if not base.exists():
            return rows
        for p in base.glob("*/*.md"):
            m = _REPORT_RE.match(p.name)
            if not m:
                continue
            rel = p.relative_to(root).as_posix()
            rows.append(
                {
                    "date": m.group(1),
                    "kind": kind if not m.group(2) else f"{kind}-llm",
                    "path": rel,
                    "url": f"https://github.com/{repo}/blob/{branch}/{rel}",
                }
            )
        return sorted(rows, key=lambda r: (r["date"], r["kind"]), reverse=True)

    return {
        "daily": scan(root / "monitor" / "reports", "daily"),
        "weekly": scan(root / "reports", "weekly"),
    }


def build_live(
    settings: Settings,
    client: Optional[HttpClient] = None,
    ticker_factory: Optional[Callable] = None,
    now: Optional[datetime] = None,
) -> dict:
    client = client or HttpClient()
    now = now or datetime.now(ZoneInfo(settings.timezone))
    lookback = int(settings.news.get("lookback_days", 7))
    limit = int(settings.news.get("max_items_per_stock", 8))
    stocks = {}
    sources: List[SourceResult] = []
    for st in settings.stocks:
        entry: dict = {}
        q = quotes.fetch_quote(st, ticker_factory)
        sources.append(q)
        entry["quote"] = q.data if q.ok else None
        entry["quote_error"] = None if q.ok else q.error
        items: List[dict] = []
        if st.news_queries:
            r = news_src.fetch_google_news(client, st.news_queries, lookback)
            sources.append(r)
            if r.ok:
                items.extend(r.data)
        if st.is_us:
            r = yahoo.fetch_news(st, ticker_factory)
            sources.append(r)
            if r.ok:
                items.extend(r.data)
        entry["news"] = news_src.dedupe_and_trim(items, news_src.since_utc(now, lookback), limit)
        if st.is_tw:
            r = taiwan.fetch_tpex_3insti(client, st.tw_code or st.id)
            sources.append(r)
            entry["tw_3insti"] = r.data if r.ok else None
        stocks[st.id] = entry
    return export.clean(
        {
            "fetched_at": now,
            "stocks": stocks,
            "sources": [
                {"source": s.source, "ok": s.ok, "error": s.error, "note": s.note, "url": s.url, "secondary": s.secondary}
                for s in sources
            ],
        }
    )


def build_site(
    settings: Settings,
    out: Path,
    data_dir: Optional[Path] = None,
    live: bool = False,
    client: Optional[HttpClient] = None,
    ticker_factory: Optional[Callable] = None,
    repo_root: Optional[Path] = None,
) -> Path:
    repo_root = repo_root or REPO_ROOT
    data_dir = data_dir or repo_root / "monitor" / "data"
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(WEB_DIR, out)
    (out / "data").mkdir(parents=True, exist_ok=True)

    dashboard = data_dir / "dashboard.json"
    if dashboard.exists():
        shutil.copyfile(dashboard, out / "data" / "dashboard.json")

    repo = os.environ.get("GITHUB_REPOSITORY", "wicanr2/stock-observation")
    branch = os.environ.get("GITHUB_REF_NAME") or "main"
    export.write_json(out / "data" / "reports.json", {"repo": repo, "branch": branch, **list_reports(repo_root, repo, branch)})

    if live:
        export.write_json(out / "data" / "live.json", build_live(settings, client, ticker_factory))
    return out
