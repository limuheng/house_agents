# house_agent.py - CrewAI 房地產情報監控核心主程式
import os
import sqlite3
import yaml
from crewai import Agent, Task, Crew, Process, LLM
from crewai.tools import tool

# 💡 引入我們編寫的實體 Python 採集工具
from tools.real_estate_tools import real_estate_collector_tool
from tools.analyst_tools import query_region_trend_tool

from dotenv import load_dotenv
load_dotenv()  # 🎯 物理補丁：啟動時自動把同目錄下的 .env 變數灌進樹莓派記憶體中

# 載入 YAML 配置
with open("config.yaml", "r", encoding="utf-8") as f:
    config = yaml.safe_load(f)

DB_FILE = "real_estate.db"

# --- 🧠 定義符合最新 CrewAI 的 LLM 組態 ---
gemini_llm = LLM(
    model="openai/gpt-6-luna",
    temperature=0.2,
    max_retries=5,          # 遇到 429 錯誤時，最多自動重試 5 次
    timeout=60.0            # 超時等待時間
)

# --- 🤖 建立 Agents ---
data_collector = Agent(
    role="房地產數據工程師",
    goal=f"定期清洗並導入最新的房地產實登與解約資料，鎖定縣市：{', '.join(config['monitoring_settings']['target_regions'])}",
    backstory=(
        "你精通台灣實價登錄的自動化流程。你不需要自己去算數學，"
        "你的核心任務是直接執行 'Real Estate Data Collector Tool' 工具，"
        "讓實體 Python 腳本去處理複雜的下載、解壓、Pandas 公設扣除與解約勾稽，"
        "並確認資料已順利原子化更新至本地 SQLite 資料庫即可。"
    ),
    tools=[real_estate_collector_tool],
    verbose=True,
    llm=gemini_llm,
    max_iter=3
)

market_analyst = Agent(
    role="房地產數據科學家",
    goal="精準看透與分析實登數據中的成交與解約趨勢，無情戮破代銷與建商的話術騙局。",
    backstory=(
        "你是一位對數據極度敏感的房市分析大師。擅長運用 SQL 聚合統計，將資料庫中的紀錄精煉為客觀趨勢。"
        "你絕不盲信建商丟出的個體定錨高價。你會根據 config.yaml 中定義的坪數級距，編寫對應的 SQL 去統計市場真相。"
    ),
    tools=[query_region_trend_tool], # 💡 修改點：把 query_region_trend_tool 加進來
    verbose=True,
    llm=gemini_llm,
    max_iter=3
)

# --- 📋 建立 Tasks ---
collect_task = Task(
    description=(
        "請直接呼叫 'Real Estate Data Collector Tool' 工具。工具會自動讀取 config.yaml，"
        "將目標行政區的最新實價登錄與解約原始檔下載、清洗並更新至本地 SQLite 資料庫。"
        "請不要試圖自己去讀取大體積的 CSV，執行完工具後，直接回報工具產出的更新筆數與解約標記摘要即可。"
    ),
    expected_output="資料庫更新成功報告，列出本次焦點行政區新增/更新買賣與預售解約的筆率統計摘要。",
    agent=data_collector
)

intervals_desc = "\n".join([f"- {i['label']}: 範圍限制為實際室內淨坪 {i['min']} 到 {i['max']} 坪" for i in config['analysis_indicators']['ping_intervals']])

analyze_task_1 = Task(
    description=(
        f"使用資料庫分析工具 (Execute Database Analytical Query)，針對焦點觀察行政區：{config['monitoring_settings']['hotspots']}，"
        f"依據以下設定檔指定的【實際淨坪級距】編寫 SQL 統計各區的『成交單價中位數』、『總價中位數』與『總去化筆數』：\n"
        f"{intervals_desc}\n"
        "請直接編寫 SQL 的 GROUP BY 或 BETWEEN 語法取得統計數據即可，不要做多餘的文字推論。"
    ),
    expected_output="各行政區與各坪數級距的單價中位數、總價中位數與成交筆數統計列表。",
    agent=market_analyst
)

analyze_task_2 = Task(
    description=(
        f"使用資料庫分析工具，針對指標行政區（{config['monitoring_settings']['hotspots']}）"
        "統計預售屋中『已解約（is_cancelled=1）』的數量，並計算各區解約單佔總成交量的比例（解約率）。\n"
        "同樣直接使用 SQL 工具查詢，並產出精確的數字統計清單。\n"
        "提示：你可以優先呼叫 Quick Region Trend Lookup Tool 來快速獲取各行政區的建案成交與解約速查表，再配合 SQL 查詢進行深度剖析。"
    ),
    expected_output="焦點觀察區預售屋的解約數量與解約率排行榜統計數據。",
    agent=market_analyst
)

analyze_task_3 = Task(
    description=(
        "綜合前面兩個任務得到的統計數據（坪數單價分布與解約幽靈單比例），綜合解讀近期房市風向。\n"
        "分析高價戶與小坪數鳥籠案的成交比例是否存在反轉？是否有特定建案出現大量技術性定錨後解約的現象？\n"
        "提示：你可以優先呼叫 Quick Region Trend Lookup Tool 來快速獲取各行政區的建案成交與解約速查表，再配合 SQL 查詢進行深度剖析。"
    ),
    expected_output="一份無情看透市場風向的終極房市數據大解密 Markdown 報告。",
    agent=market_analyst
)

# ==========================================
# 🚀 組裝 Crew 與執行流程
# ==========================================
def main():
    crew = Crew(
        agents=[data_collector, market_analyst],
        tasks=[collect_task, analyze_task_1, analyze_task_2, analyze_task_3],
        process=Process.sequential,
        verbose=True,
        max_rpm=20  
    )

    print("\n====== 🚀 開始執行 Agent 房產團隊任務 ======")
    result = crew.kickoff()

    print("\n======  任務執行完編，正在產生 MD 報告  ======")
    output_filename = "real_estate_market_report.md"
    with open(output_filename, "w", encoding="utf-8") as f:
        f.write(str(result))
        
    print(f"🎉 成功！報告已輸出至同資料夾下的: {output_filename}")

if __name__ == "__main__":
    main()

