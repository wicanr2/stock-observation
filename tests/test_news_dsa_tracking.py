from datetime import datetime, timezone

import pandas as pd

from conftest import fixture_text

from monitor import dsa_bridge, tracking
from monitor.sources import news, yahoo


def test_parse_rss_strips_source_suffix_and_dedupes():
    items = news.parse_rss(fixture_text("google_news_sample.xml"))
    assert items[0]["title"] == "SAMPLE 測試新聞標題一"
    assert items[0]["source"] == "測試新聞網"
    since = datetime(2026, 10, 27, tzinfo=timezone.utc)
    kept = news.dedupe_and_trim(items, since, limit=10)
    assert [k["link"] for k in kept] == ["https://news.google.com/rss/articles/SAMPLE1"]


def test_google_news_url_encodes_query():
    url = news.google_news_url({"q": "中光電投控", "hl": "zh-TW", "gl": "TW", "ceid": "TW:zh-Hant"}, 7)
    assert "when%3A7d" in url and "ceid=TW%3Azh-Hant" in url


def test_yahoo_news_both_shapes():
    old = [{"title": "Old shape", "link": "https://x/1", "publisher": "P", "providerPublishTime": 1793000000}]
    new = [{"id": "1", "content": {"title": "New shape", "pubDate": "2026-11-02T10:00:00Z", "provider": {"displayName": "Q"}, "canonicalUrl": {"url": "https://x/2"}}}]
    parsed = yahoo.parse_news_items(old + new)
    assert [p["title"] for p in parsed] == ["Old shape", "New shape"]
    assert parsed[1]["source"] == "Q"


def test_dsa_summary_parse_after_conversion():
    body = dsa_bridge.to_traditional(fixture_text("dsa_report_sample.md"))
    assert "決策儀表盤" in body and "軟體" in body
    sigs = dsa_bridge.parse_summary(body)
    assert [(s.code, s.signal, s.score, s.trend) for s in sigs] == [
        ("BB", "觀望", 52.0, "震盪"),
        ("NOK", "買入", 71.0, "看多"),
        ("3718.TWO", "賣出", 33.0, "看空"),
    ]
    matched = dsa_bridge.match_codes(sigs, {"BB": "BB", "NOK": "NOK", "3718": "3718.TWO"})
    assert matched["3718"].trend == "看空"


def test_convert_report_adds_caveat(tmp_path):
    src = tmp_path / "report.md"
    src.write_text(fixture_text("dsa_report_sample.md"), encoding="utf-8")
    dest = tmp_path / "out" / "2026-11-03-llm.md"
    dsa_bridge.convert_report(src, dest, "v3.33.0", "2026-11-03")
    text = dest.read_text(encoding="utf-8")
    assert "未經本 repo 逐一核實" in text and "v3.33.0" in text


def test_llm_direction():
    assert tracking.llm_direction("看多") == 1
    assert tracking.llm_direction("強烈看空") == -1
    assert tracking.llm_direction("震盪") == 0
    assert tracking.llm_direction(None) == 0


def test_tracking_evaluate_counts_matured_hits(tmp_path):
    idx = pd.bdate_range("2026-10-01", periods=12)
    hist = pd.DataFrame({"close": [10, 11, 12, 13, 14, 15, 14, 13, 12, 11, 10, 9], "volume": 1}, index=idx)
    rows = [
        {"run_date": "x", "stock_id": "BB", "model": "logit_v1", "asof_date": f"{idx[0]:%Y-%m-%d}", "horizon": 5, "prob_up": 0.6, "direction": 1, "close_asof": 10, "beats_baseline": True, "label": "偏多"},
        {"run_date": "x", "stock_id": "BB", "model": "logit_v1", "asof_date": f"{idx[5]:%Y-%m-%d}", "horizon": 5, "prob_up": 0.6, "direction": 1, "close_asof": 15, "beats_baseline": True, "label": "偏多"},
        {"run_date": "x", "stock_id": "BB", "model": "logit_v1", "asof_date": f"{idx[9]:%Y-%m-%d}", "horizon": 5, "prob_up": 0.6, "direction": 1, "close_asof": 11, "beats_baseline": True, "label": "偏多"},
        {"run_date": "x", "stock_id": "BB", "model": "dsa_llm", "asof_date": f"{idx[0]:%Y-%m-%d}", "horizon": 5, "prob_up": None, "direction": 0, "close_asof": 10, "beats_baseline": None, "label": "觀望／震盪"},
    ]
    log = tracking.append(tmp_path / "p.csv", rows)
    stats = tracking.evaluate(log, {"BB": hist})
    s = stats[("BB", "logit_v1")]
    assert s["matured"] == 2 and s["hits"] == 1 and s["hit_rate"] == 0.5
    assert stats[("BB", "dsa_llm")]["directional"] == 0
