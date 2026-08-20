# Stock Observation

以公開資料追蹤特定美股的機構持股變化，重點不是重述單一網站數字，而是辨認：機構資金是否持續增減、集中度是否改變，以及哪些變化可能只是申報時差或資料口徑差異。

## 追蹤標的

- BlackBerry（NYSE: `BB`）
- Nokia（NYSE: `NOK`，ADR）
- Ondas Holdings（NASDAQ: `ONDS`）
- Red Cat Holdings（NASDAQ: `RCAT`）
- Intel（NASDAQ: `INTC`）
- Marvell Technology（NASDAQ: `MRVL`）

## 更新頻率

每週五上午 08:00（Asia/Taipei）產生一份繁體中文報告，寫入：

```text
reports/YYYY/YYYY-MM-DD.md
```

報告會更新 [`reports/README.md`](reports/README.md) 索引並推送到 GitHub。

## 方法與限制

美國機構持股主要來自 Form 13F；申報通常落後季末最多 45 天，因此「本週報告」代表本週可取得的最新揭露，不等於機構本週交易。報告應：

1. 優先引用 SEC EDGAR 13F、公司 IR／監管文件等一手資料。
2. 以 Nasdaq、MarketBeat、Fintel 等聚合資料作交叉檢查，不把單一聚合站當唯一真相。
3. 比較最新可得申報期與前一期，區分新增、加碼、減碼、出清。
4. 依可驗證證據區分機構的多方、空方／避險與方向不明部位；列出主要機構名稱及判定依據。
5. 普通股 13F 持倉通常只能證明多方曝險；13F 不揭露一般放空股數。Put 選擇權、short interest 或監管文件可作空方線索，但 put 也可能是避險，不能直接把它寫成裸放空。
6. 註明資料截止日、申報季度、ADR／普通股口徑及資料缺漏。
7. 無法可靠取得的數字留白並說明，不推估、不捏造。

> 本 repo 是研究觀察筆記，不構成投資建議。
