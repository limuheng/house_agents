# 專案開發指引

## 專案目的

本專案使用 CrewAI 協調房地產資料採集與市場分析流程，從內政部不動產交易 Open Data 下載 CSV，整理交易與解約資料至 SQLite，並依設定產生統計與報告。

## 主要檔案

- `house_agent.py`：CrewAI Agent、Task 與主執行流程。
- `tools/real_estate_tools.py`：官方資料下載、解析、SQLite 寫入與解約勾稽。
- `tools/real_estate_transform.py`：官方交易資料的純轉換與解約指紋擷取。
- `tools/real_estate_repository.py`：SQLite schema 初始化、交易寫入與解約標記。
- `tools/analyst_tools.py`：資料庫查詢、坪數級距統計與熱區解約分析。
- `tools/report_writer.py`：Markdown 報告檔案輸出。
- `tools/historical_backfill.py`：歷史季度資料下載、驗證、回補協調及工具入口。
- `config.yaml`：監控縣市、觀察熱區及分析級距設定。
- `test_run.py`、`test_analyst.py`：手動驗證腳本。
- `real_estate.db`、`extracted_data/`、`report/`：執行資料與報告；修改前先確認其是否為使用者資料。

## 開發原則

- 維持採集、資料轉換、持久化、分析與報告輸出的職責分離。
- 新指標優先由設定驅動或新增獨立分析函式，不要將所有邏輯塞進 Agent 主流程。
- 修改資料庫 schema 或資料指紋時，先確認舊資料相容性及重複資料處理方式。
- 交易指紋需包含交易日期以區分跨季同戶交易；舊指紋應由 Repository 安全相容，不可直接丟棄既有紀錄。
- 保持既有 CrewAI Tool 的名稱與介面相容；若要變更，應同步更新 Agent、Task 與測試。
- Python 函式長度不得超過 80 行；函式應維持精簡、單一職責，過長或含多種流程的函式應拆分成可獨立測試的輔助函式。
- 新增或修改程式碼時，沿用附近檔案的 Python 風格，並為重要資料轉換與領域規則補上繁體中文說明。
- 遵循 SOLID 原則；高層流程應依賴清楚的介面與設定，不應將特定資料庫或傳輸實作散布於各模組。

## 資料與安全

- 不要讀取、提交或輸出 `.env` 中的秘密資訊。
- 不要在未確認前覆寫或刪除資料庫、下載 CSV、報告及其他既有使用者資料。
- 網路下載應驗證 HTTP 狀態、回應內容與 CSV 格式；不得默默略過憑證驗證錯誤。
- SQL 使用參數化查詢；資料庫連線應確保在錯誤時也會關閉。
- 對缺欄、空值、格式變更與無資料情況提供明確處理，避免將解析失敗偽裝成成功。
- 分析結果應清楚標示資料期間、樣本量與指標定義；不要將統計關聯直接描述成已證實的因果。

## 驗證

- 修改分析邏輯後，執行適用的分析驗證腳本，並檢查空資料與邊界級距。
- 修改採集或資料庫寫入邏輯時，優先使用隔離的測試資料，不要未經確認就對正式資料庫執行採集。
- 網路測試可能連接外部服務；執行前確認其副作用與是否會更新本地資料。
- 一般執行不會啟動歷史回補；`--plan-backfill` 僅預覽，`--backfill-history` 才會呼叫回補 Agent 並寫入資料庫。
- 歷史檔放在 `extracted_data/history/{season}/`；不要覆蓋當期 CSV，既有無效快取應保留並回報。

## 目前工作進度

- 已新增歷史回補 CrewAI Agent；設定從 `109S1`（2020 年第 1 季）開始，終點 `latest`，涵蓋 `target_regions` 及買賣 A、預售 B。
- 歷史來源使用 `DownloadSeason` GET，每季檔案分開快取至 `extracted_data/history/{season}/`；快取可續跑，下載內容先驗證再原子落檔。
- `--plan-backfill` 僅列出工作範圍；`--backfill-history` 才啟動 Agent、下載並匯入 SQLite。尚未對正式資料庫執行全量回補。
- 官方對無資料季度可能回傳 HTTP 200 的「系統訊息」HTML；確認非 CSV 後列為「官方無資料」，不當成下載成功或格式錯誤。
- 歷史匯入沿用熱區篩選、轉換與 Repository；交易指紋加入交易日期及戶別，並在 Upsert 時相容升級舊指紋。
- 最近驗證：`./venv/bin/python -m unittest discover -s tests -v`，10 項測試通過；`--plan-backfill` 預覽為 109S1 至 115S4、28 季、5 縣市、A/B 共 280 個檔案。
- 預覽不連網、不寫入；執行前仍應確認設定、備份資料庫，並先觀察官方無資料與失敗檔案統計。
