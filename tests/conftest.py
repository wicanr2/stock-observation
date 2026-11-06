"""測試共用工具：以假的 HTTP client 取代網路，所有回應都是 tests/fixtures 的合成資料。"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Tuple, Union

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FIXTURES = ROOT / "tests" / "fixtures"


class FakeResponse:
    def __init__(self, body: Union[str, bytes, dict, list]):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False)
        self.content = body.encode("utf-8") if isinstance(body, str) else body
        self.text = self.content.decode("utf-8", errors="replace")
        self.status_code = 200

    def json(self) -> Any:
        return json.loads(self.text)


class FakeClient:
    """依 URL 子字串回應；未設定的 URL 一律丟出例外（模擬網路失敗）。"""

    def __init__(self, routes: List[Tuple[str, Union[str, bytes, dict, list, Callable]]]):
        self.routes = routes
        self.calls: List[Tuple[str, Dict]] = []

    def get(self, url: str, params=None, headers=None):
        self.calls.append((url, params or {}))
        for key, body in self.routes:
            if key in url:
                if callable(body):
                    body = body(url, params or {})
                return FakeResponse(body)
        raise ConnectionError(f"offline test: no route for {url}")

    def get_json(self, url: str, **kw):
        return self.get(url, **kw).json()

    def get_text(self, url: str, encoding=None, **kw):
        return self.get(url, **kw).text


def fixture_text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES
