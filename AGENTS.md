# 專案開發指引

## 專案目的

本專案從內政部不動產交易 Open Data 下載 CSV，整理交易與解約資料至 SQLite，並提供市場分析。當期資料更新由一般 Python 程式執行，不依賴 CrewAI；使用者手動更新後，`house_agent.py` 僅作為選用的市場分析入口，不會自動下載或更新資料。歷史回補仍保留獨立的 CrewAI Agent 入口。

## 主要檔案

- `house_agent.py`：市場分析 Agent 與歷史回補 Agent 的選用入口；一般分析不會採集資料。
- `tools/current_update.py`：不依賴 CrewAI 的當期資料下載、驗證及匯入命令。
- `tools/real_estate_settings.py`：共用資料庫路徑與縣市代碼。
- `tools/transaction_importer.py`：歷史及當期資料共用的 CSV 篩選、轉換、匯入與解約勾稽。
- `tools/real_estate_transform.py`：官方交易資料的純轉換與解約指紋擷取。
- `tools/real_estate_repository.py`：SQLite schema 初始化、交易寫入與解約標記。
- `tools/analyst_tools.py`：資料庫查詢、坪數級距統計與熱區解約分析。
- `tools/monthly_charts.py`：依地區及年月產出成屋／預售屋月交易量與價格 PNG 圖表；僅納入電梯大樓、華廈、公寓、透天厝；總價以千萬元、單價以萬元／坪顯示，交易數量刻度為整數。
- `tools/monthly_tables.py`：依不含車位權狀坪數分級，僅納入電梯大樓、華廈、公寓、透天厝；Markdown 月表以月份為欄，總價與單價分表，有資料時附主建物室內坪數統計。
- `requirements-charts.txt`：月圖表工具所需 Matplotlib 相依套件。
- `tools/report_writer.py`：Markdown 報告檔案輸出。
- `tools/historical_backfill.py`：歷史季度資料下載、驗證、回補協調及工具入口。
- `config.yaml`：監控縣市、觀察熱區及分析級距設定。
- `test_run.py`：手動執行當期更新並抽查資料庫；`test_analyst.py`：手動驗證區域速查工具。
- `real_estate.db`、`extracted_data/`、`report/`：執行資料與報告；修改前先確認其是否為使用者資料。

## 開發原則

- 維持採集、資料轉換、持久化、分析與報告輸出的職責分離。
- 固定、可重現的資料更新與季度統計由一般 Python 程式負責，設計成可重複執行並供 cron 呼叫；CrewAI 僅用於需要彈性自然語言推理的選用市場分析，不作為排程或例行統計的必要依賴。
- 當期採集及排程流程不得依賴 CrewAI；Agent 與一般流程應共用轉換、Repository 及分析模組，避免重複實作領域邏輯。
- 排程流程應能非互動執行、回報錯誤狀態，且避免重複資料或失敗時留下不完整更新；圖表應標示季度、樣本數及指標定義。
- 新指標優先由設定驅動或新增獨立分析函式，不要將所有邏輯塞進 Agent 主流程。
- 修改資料庫 schema 或資料指紋時，先確認舊資料相容性及重複資料處理方式。
- 交易指紋需包含交易日期以區分跨季同戶交易；舊指紋應由 Repository 安全相容，不可直接丟棄既有紀錄。
- 例行當期更新不透過 CrewAI Tool；變更仍在使用的 Agent Tool 介面時，應同步更新 Agent、Task 與測試。
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
- `house_agent.py` 一般執行只做市場分析，不會更新資料；執行前由使用者手動呼叫 `tools.current_update`。
- `--plan-backfill` 僅預覽，不下載或寫入；`--backfill-history` 才呼叫回補 Agent 並寫入資料庫。
- 歷史檔放在 `extracted_data/history/{season}/`；當期 CSV 放在 `extracted_data/current/`，兩者分開管理。歷史無效快取應保留並回報。

## 使用方式

以下命令均從專案根目錄 `/home/muheng/house_agents` 執行：

1. **更新當期交易（不使用 Agent／不消耗 LLM token）**：
   `./venv/bin/python -m tools.current_update`
   讀取 `config.yaml` 的監控縣市與熱區，下載 A/B CSV 至 `extracted_data/current/`，驗證後匯入 `real_estate.db`。
   可用 `--config`、`--db`、`--data-dir` 指定設定檔、資料庫或 CSV 目錄，例如：
   `./venv/bin/python -m tools.current_update --config config.yaml --db real_estate.db --data-dir extracted_data/current`
2. **市場分析（使用 LLM）**：
   `./venv/bin/python house_agent.py`
   僅查詢現有資料庫並產生 `real_estate_market_report.md`；不會自動更新。建議先執行步驟 1。
3. **預覽歷史回補（不下載、不寫入）**：
   `./venv/bin/python house_agent.py --plan-backfill`
4. **執行歷史回補（會下載並寫入；會啟動 CrewAI Agent）**：
   `./venv/bin/python house_agent.py --backfill-history`
5. **執行自動化測試（隔離測試資料）**：
   `./venv/bin/python -m unittest discover -s tests -v`
6. **手動更新並抽查正式資料庫**：
   `./venv/bin/python test_run.py`（會連網更新並寫入設定的資料庫，效果包含步驟 1，非純測試。）
7. **手動查詢區域速查工具**：
   `./venv/bin/python test_analyst.py`
8. **安裝月圖表相依套件（首次使用）**：
   在專案根目錄安裝 Python 繪圖套件 Matplotlib：
   `./venv/bin/pip install -r requirements-charts.txt`
   `requirements-charts.txt` 已列出 Matplotlib；Pandas、PyYAML 等由專案環境提供。若 Debian/Raspberry Pi OS 尚無中文字型，再執行：
   `sudo apt-get update && sudo apt-get install -y fontconfig fonts-noto-cjk`
   安裝字型後可用 `fc-cache -f` 更新字型快取；也可用圖表命令的 `--font` 指定已安裝的字型名稱或字型檔路徑。
   `fc-list :lang=zh family | head` 查詢可用字型。
9. **繪製指定期間及地區的月圖表**：
   `./venv/bin/python -m tools.monthly_charts --start 2025.01 --end 2025.12 --area 頭份市`
   預設輸出至 `report/charts/`，自動建立目錄並產生四張 PNG。檔名格式為
   `{圖表目的}_{地區}_{起始年月}-{結束年月}_{繪製時間}.png`。
    預設排除已標記解約的交易；如需納入可加 `--include-cancelled`。另可用 `--db`、`--config`、`--output-dir` 指定資料庫、級距設定及輸出目錄。
10. **輸出指定期間及地區的月統計表**：
    `./venv/bin/python -m tools.monthly_tables --start 2025.01 --end 2025.12 --area 頭份市`
     預設輸出單一 Markdown 檔至 `report/tables/`，包含成屋／預售屋的交易量及價格統計表。查詢參數與月圖表一致；預設排除解約，可用 `--include-cancelled` 納入。價格單位為千萬元及萬元／坪，數量以整數呈現。

## 目前工作進度

- 已新增歷史回補 CrewAI Agent；設定從 `109S1`（2020 年第 1 季）開始，終點 `latest`，涵蓋 `target_regions` 及買賣 A、預售 B。
- 歷史來源使用 `DownloadSeason` GET，每季檔案分開快取至 `extracted_data/history/{season}/`；快取可續跑，下載內容先驗證再原子落檔。
- `--plan-backfill` 僅列出工作範圍；`--backfill-history` 才啟動 Agent、下載並匯入 SQLite。尚未對正式資料庫執行全量回補。
- 官方對無資料季度可能回傳 HTTP 200 的「系統訊息」HTML；確認非 CSV 後列為「官方無資料」，不當成下載成功或格式錯誤。
- 歷史匯入沿用熱區篩選、轉換與 Repository；交易指紋加入交易日期及戶別，並在 Upsert 時相容升級舊指紋。
- 當期更新已改為獨立 Python 流程；市場分析入口已移除當期採集 Agent／Task，不會自動更新。
- `tools/current_update.py` 與歷史回補共用不依賴 CrewAI 的交易匯入器；舊 `tools/real_estate_tools.py` 當期採集工具已移除。
- CSV 轉換會保存不含車位權狀坪數及主建物坪數；月統計表以不含車位坪數分級、估算總價，主建物坪數作為室內實坪參考，缺值不推估。月圖表仍維持原本分級規則。
- Markdown 表格依成屋／預售屋分類，月份為欄；交易量、估算總價、成交單價分表，價格表按坪數級距附交易筆數。總價單位為千萬元、單價為萬元／坪，筆數為整數。
- 圖表及 Markdown 表格共用交易型態篩選，只保留 `住宅大樓`、`華廈`、`公寓`、`透天厝`；套房及其他建物類型排除。
- 資料庫 schema 新增 `deed_ping` 與 `main_building_ping`；開發資料庫已移除，重新執行歷史回補會建立新 schema。歷史回補會重試缺少的快取 CSV，官方無資料則略過。
- 最近驗證：`./venv/bin/python -m unittest discover -s tests -v`，測試涵蓋日期邊界、參數化區域篩選、無效資料庫、空資料、彙總與四張 PNG 輸出。未對正式資料庫執行圖表產生。
- 預覽不連網、不寫入；執行前仍應確認設定、備份資料庫，並先觀察官方無資料與失敗檔案統計。
