# monitor：BB、NOK、中光電投控每日監控

每個交易日自動產生一份繁體中文報告，內容涵蓋：

| 面向 | 美股（BB、NOK） | 台股（中光電投控 3718） |
|---|---|---|
| 機構持股 | SEC EDGAR 近 7 日新申報 13F（含前一季對照，依多方／Put／多腿分類）、13G/13D、Yahoo 聚合（次級）、市場 short interest（無法歸戶） | 櫃買中心三大法人（一手）＋ FinMind 歷史（次級）、外資持股比率、融資融券、集保股權分散 |
| 新聞 | SEC 公司申報（8-K／6-K 等）、Google News、Yahoo 新聞標題 | Google News 標題；重大訊息請以公開資訊觀測站為準 |
| 漲跌預測 | 統計基準模型＋上游 LLM（daily_stock_analysis）看多／看空，兩者都記錄並追蹤到期命中率 | 同左；價格歷史以 5371.TWO 拼接 2026-09-03 以前的資料 |

為什麼選 daily_stock_analysis 當上游、本 repo 補了什麼，見 [`docs/project-selection.md`](../docs/project-selection.md)。

## 輸出

- Web 儀表板：<https://wicanr2.github.io/stock-observation/>（見下節）
- 報告：`monitor/reports/YYYY/YYYY-MM-DD.md`；索引：[`monitor/reports/README.md`](reports/README.md)
- 上游 LLM 決策儀表盤（若啟用）：`monitor/reports/YYYY/YYYY-MM-DD-llm.md`
- 累積資料：`monitor/data/predictions.csv`（預測紀錄）、`monitor/data/tw/*.csv`（三大法人與集保歷史）、`monitor/data/dashboard.json`（儀表板資料）

## Web 儀表板（持續監控）

網址（啟用後）：<https://wicanr2.github.io/stock-observation/>

- **總覽**：三檔的最新價格、走勢縮圖、統計模型機率、LLM 判斷與機構面摘要；點選進入個股。
- **個股頁**：收盤價走勢（1 個月～1 年，含 MA20／MA60、可切換表格）、今日盤中走勢、漲跌預測與事後命中率、機構持股（美股 13F 多方／Put／多腿分類、13G/13D、Yahoo 聚合；台股三大法人每日買賣超小圖、外資持股、融券、集保大戶）、新聞與公告。
- **自動更新**：頁面每 5 分鐘重新讀取資料；`.github/workflows/dashboard.yml` 在台股（台北 09–15 時）與美股（台北 21–05 時）交易時段每 30 分鐘重新抓盤中報價、新聞標題與櫃買三大法人並重新部署；每日監控完成後也會自動部署。
- 盤中報價來自 Yahoo（次級、可能延遲 15 分鐘以上），只顯示在頁面上，不寫入預測紀錄。GitHub 排程在尖峰時段可能延遲或略過。
- 頁面只讀同站的 `data/*.json`，不在瀏覽器端呼叫第三方 API；外部字串一律以純文字寫入。
- 這個 repo 是公開的，Pages 網站也會公開；內容與 repo 裡的報告相同。

本機預覽：

```bash
python -m monitor site --out _site          # 加 --live 會另抓盤中報價與新聞
python -m http.server -d _site 8000         # 開 http://localhost:8000
```

> 想要能即時下指令分析的互動介面，可在自己的電腦另外執行上游 daily_stock_analysis 的 Web 工作台（`python main.py --webui`），它需要常駐的機器與 LLM 金鑰；本儀表板則不需要伺服器。

## 在 GitHub 上啟用

1. 將本分支合併到預設分支（GitHub 只會在預設分支執行排程）。
2. `Settings → Secrets and variables → Actions`：

   | 名稱 | 種類 | 必要性 | 用途 |
   |---|---|---|---|
   | `SEC_USER_AGENT` | Variable | **建議** | SEC 要求可聯絡的 User-Agent，例如 `你的名字 you@example.com`；未設定時 SEC 可能拒絕請求 |
   | `GEMINI_API_KEY` | Secret | 選用 | 啟用上游 LLM 分析（最簡單的選項）；也可改用 `OPENAI_API_KEY`、`ANTHROPIC_API_KEY`、`DEEPSEEK_API_KEY`、`AIHUBMIX_KEY` 任一 |
   | `GEMINI_MODEL`／`OPENAI_MODEL`／`OPENAI_BASE_URL`／`ANTHROPIC_MODEL` | Variable | 選用 | 指定模型；未設定時用上游預設 |
   | `TAVILY_API_KEYS`／`SERPAPI_API_KEYS`／`BRAVE_API_KEYS`／`BOCHA_API_KEYS`／`ANSPIRE_API_KEYS` | Secret | 選用 | 上游 LLM 的新聞搜尋；未設定時 LLM 可用的新聞較少 |
   | `FINMIND_TOKEN` | Secret | 選用 | FinMind 免費註冊的 token，提高請求額度 |

3. `Actions → 每日監控（BB、NOK、中光電投控） → Run workflow` 手動跑一次確認。之後每個台北時間週二至週六 07:30 自動執行（涵蓋前一晚美股收盤與前一日台股盤後）。
4. `Settings → Pages → Build and deployment → Source` 選 **GitHub Actions**，再到 `Actions → 監控儀表板（GitHub Pages） → Run workflow` 部署一次。之後每日監控完成與交易時段每 30 分鐘會自動更新。

沒有設定任何 LLM 金鑰時，workflow 仍會產生報告，只是「LLM 判斷」欄寫「未啟用」。

## 本機執行

```bash
pip install -r requirements-dev.txt
python -m pytest -q                       # 離線測試（合成資料）
SEC_USER_AGENT="你的名字 you@example.com" python -m monitor run
python -m monitor run --dsa-report /path/to/report_20261103.md --dsa-version v3.33.0
```

## 新增或修改標的

編輯 [`watchlist.yaml`](watchlist.yaml)：美股填 `sec_cik`、`cusip`；台股填 `yahoo`（上市 `.TW`／上櫃 `.TWO`）與 `tw_code`。識別碼不確定時留空，程式會略過該來源並在報告中註明，不會猜測。

## 判讀規則（與 `AGENTS.md` 一致）

- 13F 普通股／Call 只證明已揭露的多方曝險；Put 只是空方或避險線索；13F 不揭露一般空頭部位。
- short interest、融券餘額、三大法人都是類別或全市場合計，**無法歸戶**到特定機構。
- 13F 是季末持股的延遲揭露，不是本週交易；台股三大法人才是盤後實際買賣。
- 統計模型樣本外未優於天真基準時，一律寫「無明確訊號」；LLM 內容只當意見，並追蹤命中率。
- 抓取失敗寫「本次未取得」，不以估算補齊。報告不構成投資建議。

## 升級上游

修改 `.github/workflows/daily-monitor.yml` 的 `DSA_VERSION` 與 `DSA_COMMIT`（以 `git ls-remote --tags https://github.com/ZhuLinsen/daily_stock_analysis` 查 tag 指向的 commit），手動執行一次 workflow，確認 `-llm.md` 的摘要仍能被 `monitor/dsa_bridge.py` 解析。
