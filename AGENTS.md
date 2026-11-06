# Repository instructions

- 所有自然語言內容使用繁體中文，語氣中性、重視證據。
- 報告路徑固定為 `reports/YYYY/YYYY-MM-DD.md`。
- 每次新增報告後，以最新日期在前更新 `reports/README.md`。
- 追蹤標的固定為：BB、NOK、ONDS、RCAT、INTC、MRVL；除非使用者另行修改。
- 機構持股以最新可取得的 13F／監管揭露為核心；必須寫明申報季度與資料截止日。
- 每檔股票都要依機構區分「多方」、「空方／避險線索」與「方向不明」，列出機構名稱、部位種類、期對期變化與證據。
- 13F 普通股持倉只能證明多方曝險，不能證明該機構整體淨做多；13F 也不揭露一般空頭股票部位。
- Put 選擇權與 short interest 可作空方或避險線索，但 put 可能是保護性避險，市場總 short interest 也不能直接歸因到特定機構。沒有可核實機構名稱時必須寫「無法歸戶」，不得猜測。
- 區分「申報期持股變化」與「本週交易」；不得把 13F 延遲資料寫成即時資金流。
- 優先引用 SEC EDGAR、公司 IR 或其他一手文件；聚合網站僅作交叉檢查。
- 每項重要數字附可點擊來源。資料不一致時呈現口徑差異，不任選一個數字冒充確定值。
- 不得捏造、補齊或估算無法驗證的持股數據。
- 報告不構成投資建議。

## 每日監控（monitor/）

- 每日監控標的固定為：BB、NOK、中光電投控（3718，上櫃）；定義於 `monitor/watchlist.yaml`，除非使用者另行修改。上述週報標的不受影響。
- 每日報告路徑固定為 `monitor/reports/YYYY/YYYY-MM-DD.md`，上游 LLM 內容另存 `monitor/reports/YYYY/YYYY-MM-DD-llm.md`；每次新增後以最新日期在前更新 `monitor/reports/README.md`。
- 上游 LLM 引擎為 ZhuLinsen/daily_stock_analysis，以固定 tag＋commit 執行；其輸出一律標示為「LLM 意見、未經核實」，不得當作已驗證的持股、新聞或交易紀錄，也不得把其中的數字搬進機構持股表格。
- 漲跌預測必須同時呈現樣本外成績與天真基準；未優於基準時寫「無明確訊號」。預測需寫入 `monitor/data/predictions.csv` 並以到期實際收盤追蹤命中率，不得只報機率而不報檢驗結果。
- 台股三大法人、融券餘額為類別或全市場合計，必須寫「無法歸戶」；集保大戶不等於機構持股。
- 測試只能使用 `tests/fixtures` 的合成資料，合成資料不得出現在報告中。
- Web 儀表板（`monitor/web/`）只讀 `monitor/data/dashboard.json` 與建置時產生的 `live.json`；盤中報價為次級來源，只供顯示，不寫入預測紀錄或報告。頁面文字同樣使用繁體中文，外部字串只能以純文字寫入（不得使用 innerHTML）。
