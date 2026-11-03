from datetime import date

from conftest import FakeClient, fixture_text

from monitor.sources import sec_edgar


def test_parse_infotable_sums_legs_and_skips_prn():
    legs = sec_edgar.parse_infotable(fixture_text("infotable_sample.xml"), "09228F103")
    assert legs.shares == 150_000  # 兩列（SOLE + DFND）合計，CUSIP 大小寫不敏感
    assert legs.put == 20_000
    assert legs.call == 30_000
    assert legs.prn_rows == 1  # PRN 不混入股數
    assert legs.value_shares == 1_500_000
    assert legs.issuer == "SAMPLE ISSUER"


def test_parse_infotable_other_cusip_empty():
    legs = sec_edgar.parse_infotable(fixture_text("infotable_sample.xml"), "654902204")
    assert legs.empty


def test_previous_quarter_end():
    assert sec_edgar.previous_quarter_end("2026-09-30") == "2026-06-30"
    assert sec_edgar.previous_quarter_end("2026-03-31") == "2025-12-31"
    assert sec_edgar.previous_quarter_end("bad") is None


def test_parse_efts_dedupes_and_prefers_infotable():
    payload = {
        "hits": {
            "total": {"value": 3},
            "hits": [
                {"_id": "0000000001-26-000001:primary_doc.xml", "_source": {"ciks": ["0000000001"], "display_names": ["SAMPLE CAPITAL LLC  (CIK 0000000001)"], "file_date": "2026-11-01", "period_ending": "2026-09-30", "form": "13F-HR"}},
                {"_id": "0000000001-26-000001:infotable.xml", "_source": {"ciks": ["0000000001"], "display_names": ["SAMPLE CAPITAL LLC  (CIK 0000000001)"], "file_date": "2026-11-01", "period_ending": "2026-09-30", "form": "13F-HR"}},
                {"_id": "0000000002-26-000009:table.xml", "_source": {"ciks": ["0000000002"], "display_names": ["TEST FUND (CIK 0000000002)"], "file_date": "2026-10-31", "period_ending": "2026-09-30", "form": "13F-HR/A"}},
            ],
        }
    }
    total, hits = sec_edgar.parse_efts(payload)
    assert total == 3
    assert len(hits) == 2
    first = hits[0]
    assert first.filer == "SAMPLE CAPITAL LLC"
    assert first.filename == "infotable.xml"
    assert first.adsh == "0000000001-26-000001"


def test_parse_recent_filings_filters_window_and_forms():
    payload = {
        "filings": {
            "recent": {
                "form": ["8-K", "SC 13G/A", "S-1", "10-Q"],
                "filingDate": ["2026-11-01", "2026-10-28", "2026-10-30", "2026-09-01"],
                "accessionNumber": ["0001-26-1", "0002-26-2", "0003-26-3", "0004-26-4"],
                "primaryDocument": ["a.htm", "b.htm", "c.htm", "d.htm"],
                "primaryDocDescription": ["8-K", "", "", "10-Q"],
                "reportDate": ["", "", "", "2026-08-31"],
            }
        }
    }
    rows = sec_edgar.parse_recent_filings(payload, date(2026, 10, 20), "1070235")
    assert [r["form"] for r in rows] == ["8-K", "SC 13G/A"]
    assert rows[1]["is_holder_filing"] is True
    assert rows[0]["url"].startswith("https://www.sec.gov/Archives/edgar/data/1070235/0001261/")


def test_change_text():
    assert sec_edgar.change_text(150, 120, True) == "+30（+25.0%）"
    assert sec_edgar.change_text(10, 0, True) == "新出現；前期未列"
    assert sec_edgar.change_text(0, 10, True) == "本期未列"
    assert sec_edgar.change_text(10, None, False) == "前期未取得"


def test_fetch_13f_positions_with_prior_comparison():
    efts = {
        "hits": {
            "total": {"value": 1},
            "hits": [
                {"_id": "0000000001-26-000005:infotable.xml", "_source": {"ciks": ["0000000001"], "display_names": ["SAMPLE CAPITAL LLC (CIK 0000000001)"], "file_date": "2026-11-01", "period_ending": "2026-09-30", "form": "13F-HR"}}
            ],
        }
    }
    submissions = {
        "filings": {
            "recent": {
                "form": ["13F-HR", "13F-HR"],
                "reportDate": ["2026-09-30", "2026-06-30"],
                "filingDate": ["2026-11-01", "2026-08-10"],
                "accessionNumber": ["0000000001-26-000005", "0000000001-26-000003"],
            }
        }
    }
    client = FakeClient(
        [
            ("efts.sec.gov", efts),
            ("000000000126000005/infotable.xml", fixture_text("infotable_sample.xml")),
            ("data.sec.gov/submissions/CIK0000000001.json", submissions),
            ("000000000126000003/index.json", {"directory": {"item": [{"name": "primary_doc.xml"}, {"name": "q2_infotable.xml"}]}}),
            ("000000000126000003/q2_infotable.xml", fixture_text("infotable_prior.xml")),
        ]
    )
    res = sec_edgar.fetch_13f_positions(client, "09228F103", date(2026, 11, 3), lookback_days=7)
    assert res.ok
    (pos,) = res.data["positions"]
    assert pos.current.shares == 150_000
    assert pos.prior is not None and pos.prior.shares == 120_000
    cls = sec_edgar.classify(pos)
    assert cls == {"A": True, "B": True, "C": True}


def test_fetch_13f_positions_network_failure_is_reported():
    res = sec_edgar.fetch_13f_positions(FakeClient([]), "09228F103", date(2026, 11, 3))
    assert not res.ok
    assert "offline" in res.error
