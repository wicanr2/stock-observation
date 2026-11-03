# 開源專案選型：每日監控要接哪一個專案

- **檢索日期**：2026-11-03（Asia/Taipei）
- **需求**：監控 BB、NOK、中光電投控（3718）的「機構持股、新聞、漲跌預測」；不從零重寫，而是沿用一個成熟的開源專案繼續開發，並保留本 repo 重視證據的寫法。
- **結論**：以 [ZhuLinsen/daily_stock_analysis](https://github.com/ZhuLinsen/daily_stock_analysis)（以下稱 DSA）為上游引擎，固定在 `v3.33.0`（commit `bba8bf51bdbd3badc75a832c52ede058ef5ab871`）；本 repo 只補上 DSA 沒有、或需要更嚴格口徑的部分。

## 1. 候選比較

| 專案 | 授權 | 與需求的吻合處 | 主要缺口 | 判斷 |
|---|---|---|---|---|
| [daily_stock_analysis](https://github.com/ZhuLinsen/daily_stock_analysis) | MIT（[LICENSE](https://github.com/ZhuLinsen/daily_stock_analysis/blob/main/LICENSE)） | 支援美股與台股；上櫃股用 `.TWO` 後綴；已接櫃買中心三大法人；新聞搜尋＋LLM「看多／看空／震盪」決策儀表盤；原生支援 GitHub Actions 排程 | 報告只有簡體中文／英文／韓文；沒有美股 13F 機構持股；沒有集保股權分散；LLM 結論沒有事後命中率追蹤 | **採用**為上游 |
| [TauricResearch/TradingAgents](https://github.com/TauricResearch/TradingAgents) | Apache-2.0 | 多空研究員辯論（bull／bear debate）最貼近「看多看空」 | 定位是研究框架；每檔每次需多輪 LLM 呼叫、成本高；沒有台股籌碼與 13F；沒有現成排程與報告 | 不採用；日後若要做單檔深度辯論可另接 |
| [virattt/ai-hedge-fund](https://github.com/virattt/ai-hedge-fund) | MIT | 多位「投資人風格」代理人給出看多／看空訊號 | 資料主要依賴付費財報 API；以美股為主，無台股上櫃與三大法人 | 不採用 |
| [OpenBB-finance/OpenBB](https://github.com/OpenBB-finance/OpenBB) | Apache-2.0 | 資料平台完整，可接多家資料商 | 是資料／研究平台，不產生每日監控與預測；機構持股多需第三方金鑰 | 不採用（可當日後資料層備案） |
| [FinMind/FinMind](https://github.com/FinMind/FinMind) | Apache-2.0 | 台股三大法人、外資持股、融資融券歷史資料完整 | 是資料 API，不是監控／預測系統；集保分級為付費等級 | **作為次級資料來源**（補歷史、交叉檢查） |
| [microsoft/qlib](https://github.com/microsoft/qlib) | MIT | 量化研究與模型訓練框架 | 重型框架，需要自建資料管線；與新聞、持股監控無關 | 不採用 |

授權條款於檢索日從各 repo 的 `LICENSE` 檔核對。DSA 的台股能力以其 [市場支援文件](https://github.com/ZhuLinsen/daily_stock_analysis/blob/main/docs/market-support.md) 為準：上市 `.TW`、上櫃 `.TWO`，三大法人資料來自 TWSE T86 與櫃買中心 OpenAPI。

## 2. 怎麼「沿用」而不是重寫

```text
GitHub Actions（.github/workflows/daily-monitor.yml）
├─ job llm（唯讀權限、不保留 git 憑證）
│   └─ git clone DSA v3.33.0 → 驗證 commit → python main.py --stocks BB,NOK,3718.TWO --no-notify
│       └─ 產生 LLM 決策儀表盤（簡體）→ 上傳 artifact
└─ job monitor（可寫入 repo）
    ├─ python -m monitor run --dsa-report …
    │   ├─ SEC EDGAR：8-K/6-K、13G/13D、近 7 日新申報 13F（含前一季對照）
    │   ├─ 櫃買中心三大法人、集保股權分散（一手）＋ FinMind（次級，補歷史與交叉檢查）
    │   ├─ Google News／Yahoo 新聞標題（次級）
    │   ├─ 統計基準模型（walk-forward 邏輯迴歸，與天真基準比較）
    │   ├─ DSA 報告經 OpenCC 轉臺灣正體，擷取每檔「操作建議／評分／趨勢」
    │   └─ 兩種預測都寫入 monitor/data/predictions.csv，到期後計算命中率
    └─ commit monitor/reports/YYYY/YYYY-MM-DD.md 與 monitor/data/
```

- **不修改上游程式碼**：DSA 以固定 tag＋commit 執行；升級時只改 workflow 的 `DSA_VERSION`／`DSA_COMMIT`，並重跑測試。
- **需要改上游行為時**：先以 issue／PR 回饋上游；真的要長期分岔，再 fork DSA，將 workflow 的 `DSA_REPO` 指向 fork。
- **補強層全部可離線測試**：`tests/` 使用合成資料，不需網路與金鑰。

## 3. 本 repo 補上的部分，以及為什麼

| 補強 | 理由 |
|---|---|
| 13F 新申報探索＋前一季對照，依多方／空方避險／方向不明分類 | 延續 `AGENTS.md` 的機構持股口徑；DSA 沒有 13F |
| 13G/13D（≥5% 持有人）與公司申報 | 一手、可點擊的機構與公司資訊 |
| 集保股權分散、三大法人本地累積歷史 | 一手端點多半只給最新一期，需逐日保存才能回溯 |
| 統計基準模型與命中率追蹤 | 網路上的「預測漲跌」大多沒有事後檢驗；這裡讓 LLM 與統計模型都接受同一套到期檢驗 |
| 繁體中文報告與資料來源狀態表 | `AGENTS.md` 要求繁體中文、每項重要數字附來源、缺漏明寫 |

## 4. 已知限制

- 13F 是季末持股、季末後最多 45 日申報；每日監控只能捕捉「新申報」，不是即時資金流。
- 只用 CUSIP 全文檢索找 13F：已出清的機構不會被找到；全文索引也可能延遲。
- 中光電投控 2026-09-03 才掛牌（由中光電 5371 以 1:1 股份轉換而來），統計模型以 Yahoo 的 5371.TWO 歷史拼接；若 Yahoo 下架舊代碼，模型會改報「樣本不足」。
- 短期漲跌接近隨機；統計模型樣本外若未優於天真基準，報告一律寫「無明確訊號」。
- DSA 的 LLM 內容可能出錯或幻覺，報告中只當作意見，並與統計模型並列追蹤。
