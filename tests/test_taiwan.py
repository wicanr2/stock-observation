from datetime import date

import pandas as pd

from conftest import FakeClient, fixture_text

from monitor.sources import taiwan

TPEX_ROW = {
    "Date": "1151102",
    "SecuritiesCompanyCode": "3718",
    "CompanyName": "中光電投控",
    taiwan._TPEX_FOREIGN: "120000",
    taiwan._TPEX_TRUST: "-30000",
    taiwan._TPEX_DEALER: "5000",
    taiwan._TPEX_TOTAL: "95000",
}


def test_minguo_to_iso():
    assert taiwan.minguo_to_iso("1151102") == "2026-11-02"
    assert taiwan.minguo_to_iso("115/11/02") == "2026-11-02"
    assert taiwan.minguo_to_iso("2026-11-02") is None


def test_parse_tpex_3insti():
    rec = taiwan.parse_tpex_3insti([{"SecuritiesCompanyCode": "6488"}, TPEX_ROW], "3718")
    assert rec["date"] == "2026-11-02"
    assert rec["foreign_net"] == 120000
    assert rec["trust_net"] == -30000
    assert rec["total_net"] == 95000


def test_parse_tpex_missing_field_fails_open():
    row = dict(TPEX_ROW)
    row.pop(taiwan._TPEX_TRUST)
    assert taiwan.parse_tpex_3insti([row], "3718") is None


def test_parse_finmind_institutional_matches_tpex_scope():
    rows = [
        {"date": "2026-11-02", "stock_id": "3718", "name": "Foreign_Investor", "buy": 200000, "sell": 80000},
        {"date": "2026-11-02", "stock_id": "3718", "name": "Foreign_Dealer_Self", "buy": 9999, "sell": 0},
        {"date": "2026-11-02", "stock_id": "3718", "name": "Investment_Trust", "buy": 0, "sell": 30000},
        {"date": "2026-11-02", "stock_id": "3718", "name": "Dealer_self", "buy": 4000, "sell": 1000},
        {"date": "2026-11-02", "stock_id": "3718", "name": "Dealer_Hedging", "buy": 3000, "sell": 1000},
    ]
    (rec,) = taiwan.parse_finmind_institutional(rows)
    assert rec["foreign_net"] == 120000  # 不含外資自營商
    assert rec["dealer_net"] == 5000
    assert rec["total_net"] == 95000
    assert taiwan.cross_check(taiwan.parse_tpex_3insti([TPEX_ROW], "3718"), [rec]) == "與 FinMind 同日數字一致"


def test_cross_check_reports_difference():
    primary = taiwan.parse_tpex_3insti([TPEX_ROW], "3718")
    other = dict(primary, foreign_net=100000, source="FinMind")
    msg = taiwan.cross_check(primary, [other])
    assert msg.startswith("口徑差異") and "外資" in msg


def test_parse_tdcc():
    rec = taiwan.parse_tdcc(fixture_text("tdcc_sample.csv"), "3718")
    assert rec["date"] == "2026-10-30"
    assert rec["big1000_ratio"] == 54.55
    assert rec["big400_ratio"] == round(2.56 + 1.79 + 1.84 + 54.55, 2)
    assert rec["holders_total"] == 60538
    assert taiwan.parse_tdcc(fixture_text("tdcc_sample.csv"), "9999") is None


def test_fetch_tdcc_handles_bom_bytes():
    client = FakeClient([("tdcc.com.tw", fixture_text("tdcc_sample.csv").lstrip("\ufeff").encode("utf-8-sig"))])
    res = taiwan.fetch_tdcc(client, "3718")
    assert res.ok, res.error
    assert res.data["big1000_holders"] == 40


def test_merge_history_prefers_primary_source(tmp_path):
    path = tmp_path / "x.csv"
    taiwan.merge_history(path, [{"date": "2026-11-02", "foreign_net": 1, "trust_net": 0, "dealer_net": 0, "total_net": 1, "source": "FinMind"}], taiwan.INSTI_COLUMNS, "TPEx OpenAPI")
    df = taiwan.merge_history(path, [{"date": "2026-11-02", "foreign_net": 2, "trust_net": 0, "dealer_net": 0, "total_net": 2, "source": "TPEx OpenAPI"}], taiwan.INSTI_COLUMNS, "TPEx OpenAPI")
    assert len(df) == 1
    assert df.iloc[0]["source"] == "TPEx OpenAPI"
    assert int(df.iloc[0]["foreign_net"]) == 2


def test_window_sums_and_streak():
    df = pd.DataFrame(
        {
            "date": [f"2026-10-{d:02d}" for d in range(20, 27)],
            "foreign_net": [5, -1, 2, 3, 4, 6, 7],
            "trust_net": [0] * 7,
            "dealer_net": [-1] * 7,
            "total_net": [4, -2, 1, 2, 3, 5, 6],
        }
    )
    w = taiwan.window_sums(df, 5)
    assert w["days"] == 5 and w["foreign_net"] == 22
    assert taiwan.streak(df, "foreign_net") == 5
    assert taiwan.streak(df, "dealer_net") == -7
    assert taiwan.streak(df, "trust_net") == 0


def test_finmind_failure_is_secondary_and_reported():
    res = taiwan.fetch_finmind_institutional(FakeClient([]), "3718", date(2026, 9, 3))
    assert not res.ok and res.secondary
