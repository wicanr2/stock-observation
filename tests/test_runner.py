"""端到端：以合成資料取代所有網路來源，確認報告、索引、預測紀錄都能產生。"""

from datetime import date

import numpy as np
import pandas as pd

from conftest import FakeClient, fixture_text

from monitor import runner
from monitor.config import load_settings
from monitor.sources import taiwan


def _fake_downloader(symbol: str, period: str) -> pd.DataFrame:
    seed = sum(map(ord, symbol))
    rng = np.random.default_rng(seed)
    if symbol == "3718.TWO":
        idx = pd.bdate_range("2026-09-03", "2026-11-02")
    elif symbol == "5371.TWO":
        idx = pd.bdate_range("2022-01-03", "2026-09-02")
    else:
        idx = pd.bdate_range("2021-11-01", "2026-11-02")
    close = 50 * np.exp(np.cumsum(rng.normal(0, 0.02, len(idx))))
    return pd.DataFrame({"Close": close, "Volume": rng.integers(1e5, 2e5, len(idx))}, index=idx)


class _FakeTicker:
    def __init__(self, symbol):
        self.institutional_holders = pd.DataFrame(
            {"Date Reported": [pd.Timestamp("2026-09-30")], "Holder": ["SAMPLE HOLDER"], "pctHeld": [0.05], "Shares": [1000], "Value": [1e4], "pctChange": [0.1]}
        )
        self.major_holders = pd.DataFrame({"Value": [0.4, 300]}, index=["institutionsPercentHeld", "institutionsCount"])
        self.info = {"sharesShort": 12345, "sharesShortPriorMonth": 23456, "dateShortInterest": 1792022400}
        self.news = []


def _client():
    tpex = [
        {
            "Date": "1151102",
            "SecuritiesCompanyCode": "3718",
            taiwan._TPEX_FOREIGN: "1000",
            taiwan._TPEX_TRUST: "-200",
            taiwan._TPEX_DEALER: "0",
            taiwan._TPEX_TOTAL: "800",
        }
    ]
    finmind = {"status": 200, "msg": "success", "data": [
        {"date": "2026-10-30", "stock_id": "3718", "name": "Foreign_Investor", "buy": 500, "sell": 100},
        {"date": "2026-10-30", "stock_id": "3718", "name": "Investment_Trust", "buy": 0, "sell": 50},
        {"date": "2026-10-30", "stock_id": "3718", "name": "Dealer_self", "buy": 0, "sell": 0},
    ]}
    return FakeClient(
        [
            ("tpex_3insti_daily_trading", tpex),
            ("dataset=TaiwanStockInstitutionalInvestorsBuySell", finmind),
            ("api.finmindtrade.com", lambda url, params: finmind if params.get("dataset") == "TaiwanStockInstitutionalInvestorsBuySell" else {"status": 200, "data": []}),
            ("tdcc.com.tw", fixture_text("tdcc_sample.csv")),
            ("news.google.com", fixture_text("google_news_sample.xml")),
            ("efts.sec.gov", {"hits": {"total": {"value": 0}, "hits": []}}),
            ("data.sec.gov/submissions", {"filings": {"recent": {"form": ["8-K"], "filingDate": ["2026-11-01"], "accessionNumber": ["0001070235-26-000100"], "primaryDocument": ["bb-8k.htm"], "primaryDocDescription": ["8-K"]}}}),
        ]
    )


def test_end_to_end_report(tmp_path):
    settings = load_settings()
    dsa = tmp_path / "dsa.md"
    dsa.write_text(fixture_text("dsa_report_sample.md"), encoding="utf-8")
    ctx = runner.make_context(
        settings,
        today=date(2026, 11, 3),
        data_dir=tmp_path / "data",
        out_dir=tmp_path / "reports",
        dsa_report=dsa,
        dsa_version="v3.33.0",
        client=_client(),
    )
    ctx.downloader = _fake_downloader
    ctx.ticker_factory = _FakeTicker
    path = runner.run(ctx)

    text = path.read_text(encoding="utf-8")
    assert path.name == "2026-11-03.md"
    for needle in ("## 2. 總覽", "BB — BlackBerry", "中光電投控（3718）", "三大法人", "集保股權分散", "無法歸戶", "不構成投資建議", "統計基準模型", "LLM 判斷"):
        assert needle in text, needle
    # 中光電投控：價格拼接舊代碼，模型樣本足夠
    assert "5371.TWO" in text
    # 不得出現簡體殘留
    assert "决策" not in text
    assert (tmp_path / "reports" / "2026" / "2026-11-03-llm.md").exists()
    index = (tmp_path / "reports" / "README.md").read_text(encoding="utf-8")
    assert "2026-11-03" in index
    log = pd.read_csv(tmp_path / "data" / "predictions.csv", dtype=str)
    assert set(log["model"]) == {"logit_v1", "dsa_llm"}
    assert (tmp_path / "data" / "tw" / "3718_3insti.csv").exists()


def test_end_to_end_all_sources_down(tmp_path):
    settings = load_settings()
    ctx = runner.make_context(settings, today=date(2026, 11, 3), data_dir=tmp_path / "d", out_dir=tmp_path / "r", client=FakeClient([]))

    def broken(symbol, period):
        raise ConnectionError("offline")

    ctx.downloader = broken

    def broken_ticker(symbol):
        raise ConnectionError("offline")

    ctx.ticker_factory = broken_ticker
    path = runner.run(ctx)
    text = path.read_text(encoding="utf-8")
    assert "本次未取得" in text
    assert "| 失敗 |" in text
