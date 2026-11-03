"""儀表板資料輸出與網站建置（全部離線、合成資料）。"""

import json
from datetime import date, datetime
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from conftest import FakeClient, fixture_text

import test_runner as tr
from monitor import export, runner, site
from monitor.config import load_settings
from monitor.sources import quotes, taiwan


def test_clean_handles_nan_numpy_and_timestamps():
    out = export.clean({"a": float("nan"), "b": np.int64(3), "c": pd.Timestamp("2026-11-02"), "d": [np.float64(1.5), None], "e": np.bool_(True)})
    assert out == {"a": None, "b": 3, "c": "2026-11-02T00:00:00", "d": [1.5, None], "e": True}
    json.dumps(out, allow_nan=False)


def _run(tmp_path):
    settings = load_settings()
    ctx = runner.make_context(settings, today=date(2026, 11, 3), data_dir=tmp_path / "data", out_dir=tmp_path / "reports", client=tr._client())
    ctx.downloader = tr._fake_downloader
    ctx.ticker_factory = tr._FakeTicker
    runner.run(ctx)
    return settings


def test_dashboard_json_written_and_valid(tmp_path):
    _run(tmp_path)
    raw = (tmp_path / "data" / "dashboard.json").read_text(encoding="utf-8")
    assert "NaN" not in raw
    d = json.loads(raw)
    assert d["schema"] == export.SCHEMA_VERSION
    ids = [s["id"] for s in d["stocks"]]
    assert ids == ["BB", "NOK", "3718"]
    bb = d["stocks"][0]
    assert len(bb["price"]["series"]) <= export.PRICE_ROWS
    assert bb["us"]["thirteen_f"]["ok"] is True
    assert bb["prediction"]["ok"] is True
    tw = d["stocks"][2]["tw"]
    assert tw["latest"]["data"]["foreign_net"] == 1000
    assert tw["tdcc"]["data"]["big1000_ratio"] == 54.55
    assert d["stocks"][2]["price"]["spliced_at"].startswith("2026-09-03")


class _LiveTicker(tr._FakeTicker):
    def __init__(self, symbol):
        super().__init__(symbol)
        self.fast_info = {"previousClose": 50.0}

    def history(self, period, interval):
        idx = pd.date_range("2026-11-02 09:30", "2026-11-03 10:00", freq="30min", tz="America/New_York")
        return pd.DataFrame({"Close": np.linspace(49, 51, len(idx))}, index=idx)


def test_parse_intraday_keeps_last_session_only():
    df = _LiveTicker("BB").history("1d", "5m")
    bars = quotes.parse_intraday(df)
    assert all(b["t"].startswith("2026-11-03") for b in bars)
    assert bars[-1]["p"] == 51.0


def test_fetch_quote_change_pct():
    res = quotes.fetch_quote(load_settings().stocks[0], _LiveTicker)
    assert res.ok
    assert abs(res.data["change_pct"] - 0.02) < 1e-9


def test_build_site_with_live(tmp_path):
    settings = _run(tmp_path)
    repo_root = tmp_path / "repo"
    (repo_root / "monitor" / "reports" / "2026").mkdir(parents=True)
    (repo_root / "monitor" / "reports" / "2026" / "2026-11-03.md").write_text("x", encoding="utf-8")
    (repo_root / "monitor" / "reports" / "2026" / "2026-11-03-llm.md").write_text("x", encoding="utf-8")
    (repo_root / "reports" / "2026").mkdir(parents=True)
    (repo_root / "reports" / "2026" / "2026-10-30.md").write_text("x", encoding="utf-8")
    out = site.build_site(
        settings, tmp_path / "_site", data_dir=tmp_path / "data", live=True,
        client=tr._client(), ticker_factory=_LiveTicker, repo_root=repo_root,
    )
    for name in ("index.html", "app.js", "style.css", "data/dashboard.json", "data/live.json", "data/reports.json"):
        assert (out / name).exists(), name
    reports = json.loads((out / "data" / "reports.json").read_text(encoding="utf-8"))
    assert [r["kind"] for r in reports["daily"]] == ["daily-llm", "daily"]
    assert reports["weekly"][0]["url"].endswith("/reports/2026/2026-10-30.md")
    live = json.loads((out / "data" / "live.json").read_text(encoding="utf-8"))
    assert live["stocks"]["BB"]["quote"]["price"] == 51.0
    assert live["stocks"]["3718"]["tw_3insti"]["date"] == "2026-11-02"
    assert live["stocks"]["3718"]["news"][0]["title"].startswith("SAMPLE")


def test_build_live_fail_open():
    def broken(symbol):
        raise ConnectionError("offline")

    live = site.build_live(load_settings(), client=FakeClient([]), ticker_factory=broken, now=datetime(2026, 11, 3, tzinfo=ZoneInfo("Asia/Taipei")))
    assert live["stocks"]["BB"]["quote"] is None
    assert "offline" in live["stocks"]["BB"]["quote_error"]
    assert all(not s["ok"] for s in live["sources"])


def test_build_site_without_dashboard(tmp_path):
    out = site.build_site(load_settings(), tmp_path / "_site", data_dir=tmp_path / "empty", repo_root=tmp_path)
    assert (out / "index.html").exists()
    assert not (out / "data" / "dashboard.json").exists()


def test_web_assets_do_not_use_innerhtml():
    js = (site.WEB_DIR / "app.js").read_text(encoding="utf-8")
    assert ".innerHTML" not in js and ".outerHTML" not in js and "insertAdjacentHTML" not in js
    assert "eval(" not in js


def test_tpex_fixture_shape_still_used():
    # 防止 TPEx 欄位常數被誤改：網站即時刷新沿用同一個解析器
    assert taiwan.parse_tpex_3insti(tr._client().get_json("tpex_3insti_daily_trading"), "3718")["total_net"] == 800
    assert fixture_text("tdcc_sample.csv")
