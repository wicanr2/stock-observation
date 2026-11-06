"""共用 HTTP 工具：節流、重試、明確的失敗紀錄。

所有資料來源都採 fail-open：抓取失敗時回傳 ``SourceResult(ok=False, ...)``，
報告會寫「本次未取得」，絕不以 0 或舊值補齊。
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import requests

DEFAULT_UA = "stock-observation-monitor/1.0 (+https://github.com/wicanr2/stock-observation)"

# 每個主機的最小請求間隔（秒）。SEC 公告上限 10 req/s；TWSE/TPEx 有非正式限流。
_HOST_INTERVAL = {
    "www.sec.gov": 0.15,
    "data.sec.gov": 0.15,
    "efts.sec.gov": 0.25,
    "www.tpex.org.tw": 1.0,
    "www.twse.com.tw": 2.0,
    "api.finmindtrade.com": 0.5,
    "smart.tdcc.com.tw": 1.0,
    "news.google.com": 0.5,
}


@dataclass
class SourceResult:
    """單一資料來源的抓取結果與狀態，供報告「資料來源狀態」表使用。"""

    source: str
    ok: bool
    data: Any = None
    error: Optional[str] = None
    url: Optional[str] = None
    note: Optional[str] = None
    secondary: bool = False  # True 表示聚合來源，只作交叉檢查

    @classmethod
    def fail(cls, source: str, error: str, url: Optional[str] = None, **kw: Any) -> "SourceResult":
        return cls(source=source, ok=False, error=error, url=url, **kw)


@dataclass
class FetchLog:
    entries: List[SourceResult] = field(default_factory=list)

    def add(self, result: SourceResult) -> SourceResult:
        self.entries.append(result)
        return result


class HttpClient:
    def __init__(self, timeout: int = 20, retries: int = 2) -> None:
        self.session = requests.Session()
        self.timeout = timeout
        self.retries = retries
        self._last: Dict[str, float] = {}
        self._lock = threading.Lock()

    @staticmethod
    def sec_user_agent() -> str:
        # SEC 要求宣告可聯絡的 User-Agent（公司／個人名稱 + email）。
        # 請在 GitHub repo 的 Actions variables 設定 SEC_USER_AGENT。
        return os.environ.get("SEC_USER_AGENT") or DEFAULT_UA

    def _throttle(self, host: str) -> None:
        interval = _HOST_INTERVAL.get(host, 0.2)
        with self._lock:
            wait = interval - (time.time() - self._last.get(host, 0.0))
            if wait > 0:
                time.sleep(wait)
            self._last[host] = time.time()

    def get(
        self,
        url: str,
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> requests.Response:
        host = urlparse(url).netloc
        hdrs = {"User-Agent": DEFAULT_UA, "Accept": "*/*"}
        if host.endswith("sec.gov"):
            hdrs["User-Agent"] = self.sec_user_agent()
        if headers:
            hdrs.update(headers)
        last_exc: Optional[Exception] = None
        for attempt in range(self.retries + 1):
            self._throttle(host)
            try:
                resp = self.session.get(url, params=params, headers=hdrs, timeout=self.timeout)
                if resp.status_code in (429, 500, 502, 503, 504) and attempt < self.retries:
                    time.sleep(2 * (attempt + 1))
                    continue
                resp.raise_for_status()
                return resp
            except requests.RequestException as exc:
                last_exc = exc
                if attempt < self.retries:
                    time.sleep(2 * (attempt + 1))
        assert last_exc is not None
        raise last_exc

    def get_json(self, url: str, **kw: Any) -> Any:
        return self.get(url, **kw).json()

    def get_text(self, url: str, encoding: Optional[str] = None, **kw: Any) -> str:
        resp = self.get(url, **kw)
        if encoding:
            resp.encoding = encoding
        return resp.text


def short_error(exc: BaseException) -> str:
    text = f"{type(exc).__name__}: {exc}"
    return text if len(text) <= 160 else text[:157] + "..."
