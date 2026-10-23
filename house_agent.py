# house_agent.py - CrewAI 房地產情報監控核心主程式
import os
import sqlite3
import yaml
from crewai import Agent, Task, Crew, Process, LLM
from crewai.tools import tool

# 💡 引入我們編寫的實體 Python 採集工具
from tools.real_estate_tools import real_estate_collector_tool

# 載入 YAML 配置
with open("config.yaml", "r", encoding="utf-8") as f:
    config = yaml.safe_load(f)

DB_FILE = "real_estate.db"

# --- 🧠 定義符合最新 CrewAI 的 LLM 組態 ---
# 完美適配您目前運行的 2026 最新環境與 gemini-3.8-flash
gemini_llm = LLM(
    model="gemini/gemini-3.8-flash", # ⚡ 修正：對齊您現行運行的核心大腦
    temperature=0.2,
    max_retries=5,          # 遇到 429 錯誤時，最多自動重試 5 次
    timeout=60.0            # 超時等待時間
)

# --- 🔒 定義防禦性 SQL 查詢工具 ---
@tool("Execute Database Analytical Query")
def execute_read_only_query(sql_string: str) -> str:
    """執行唯讀的 SQL 查詢來分析資料庫中的房地產實價登錄與解約數據。"""
    forbidden_keywords = ["insert", "update", "delete", "drop", "alter", "truncate", "create"]
    if any(kw in sql_string.lower() for kw in forbidden_keywords):
        return "錯誤：基於安全性考量，禁止執行任何非唯讀的 SQL 修改指令。"
    
    try:
        with sqlite3.connect(DB_FILE) as conn:
            cursor = conn.cursor()
            cursor.execute(sql_string)
            columns = [description[0] for description in cursor.description]
            rows = cursor.fetchall()
            
            if not rows:
                return "查詢成功，但資料庫中尚無符合此條件的數據。"
                
            result = [", ".join(columns)]
            # 限制前 50 筆，防樹莓派記憶體與 Token 爆炸
            for row in rows[:50]:
                result.append(", ".join(str(val) for val in row))
            if len(rows) > 50:
                result.append(f"...(餘下 {len(rows)-50} 筆資料已省略，請改用 COUNT(*) 或 AVG 等聚合函數查詢)")
            return "\n".join(result)
    except Exception as e:
        return f"SQL 執行失敗，請修正語法錯誤。錯誤訊息: {str(e)}"

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
    tools=[execute_read_only_query],
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
        "同樣直接使用 SQL 工具查詢，並產出精確的數字統計清單。"
    ),
    expected_output="焦點觀察區預售屋的解約數量與解約率排行榜統計數據。",
    agent=market_analyst
)

analyze_task_3 = Task(
    description=(
        "綜合前面兩個任務得到的統計數據（坪數單價分布與解約幽靈單比例），綜合解讀近期房市風向。\n"
        "分析高價戶與小坪數鳥籠案的成交比例是否存在反轉？是否有特定建案出現大量技術性定錨後解約的現象？"
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
        max_rpm=2  
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

