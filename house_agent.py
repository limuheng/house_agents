# house_agent.py - CrewAI 房地產情報監控核心主程式
import os
import sqlite3
import yaml
from crewai import Agent, Task, Crew, Process, LLM
from crewai.tools import tool

# 載入 YAML 配置
with open("config.yaml", "r", encoding="utf-8") as f:
    config = yaml.safe_load(f)

# 初始化資料庫連接
DB_FILE = "real_estate.db"
def init_db():
    with sqlite3.connect(DB_FILE) as conn:
        with open("schema.sql", "r", encoding="utf-8") as f:
            conn.executescript(f.read())
init_db()

# --- 定義符合最新 CrewAI 的 LLM 組態 ---
# 使用最新的大模型配置語法，完美適配樹莓派環境與最新 Gemini
gemini_llm = LLM(
    model="gemini/gemini-3.8-flash",
    temperature=0.2,
    max_retries=5,          # 遇到 429 錯誤時，最多自動重試 5 次
    timeout=60.0            # 超時等待時間
)

# --- 定義防禦性 SQL 查詢工具 (防止 Token 爆炸的核心) ---
@tool("Execute Database Analytical Query")
def execute_read_only_query(sql_string: str) -> str:
    """執行唯讀的 SQL 查詢來分析資料庫中的房地產實價登錄與解約數據。"""
    # 安全性防範：禁止任何修改型 SQL
    forbidden_keywords = ["insert", "update", "delete", "drop", "alter", "truncate"]
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
                
            # 格式化輸出
            result = [", ".join(columns)]
            for row in rows:
                result.append(", ".join(str(val) for val in row))
            return "\n".join(result)
    except Exception as e:
        return f"SQL 執行失敗，請修正語法錯誤。錯誤訊息: {str(e)}"

# --- 建立 Agents ---
data_collector = Agent(
    role="房地產數據工程師",
    goal=f"定期清洗並導入最新的房地產實登與解約資料，鎖定：{', '.join(config['monitoring_settings']['target_regions'])}",
    backstory="你精通台灣實價登錄的 CSV/XML 原始資料結構，負責精確計算實際坪數（主建物+附屬建物，扣除車位），將清洗後的潔淨數據upsert寫入本地 SQLite 資料庫。",
    verbose=True,
    llm=gemini_llm
)

market_analyst = Agent(
    role="房地產數據科學家",
    goal="精準看透與分析實登數據中的成交與解約趨勢，無情戮破代銷與建商的話術騙局。",
    backstory="你是一位對數據極度敏感的房市分析大師。擅長運用 SQL 聚合統計，將資料庫中的數萬筆紀錄精煉為客觀趨勢。你絕不盲信建商在潛銷期丟出的個體定錨高價。",
    tools=[execute_read_only_query],
    verbose=True,
    llm=gemini_llm
)

# --- 建立 Tasks ---
collect_task = Task(
    description=f"檢查本地暫存的實登與解約原始檔，將符合地區（{', '.join(config['monitoring_settings']['target_regions'])}）的資料清洗後寫入 SQLite 資料庫中。確保有正確處理解約事件標記。",
    expected_output="資料庫更新成功報告，列出本次新增買賣與預售解約的筆數。",
    agent=data_collector
)

# 動態組裝分析任務的坪數需求描述
intervals_desc = "\n".join([f"- {i['label']}: 範圍限制為實際/權狀坪數 {i['min']} 到 {i['max']} 坪" for i in config['analysis_indicators']['ping_intervals']])

# 子任務 1：只算坪數級距
analyze_task_1 = Task(
    description=(
        "使用資料庫分析工具，針對指標行政區（如：頭份市）統計以下級距的『成交單價中位數』、『總價中位數』與『去化總筆數』：\n"
        "- 0-30坪、30-40坪、40-50坪、50-60坪、60-70坪、70-100坪。\n"
        "請直接呼叫 SQL 工具取得這張統計表即可，不要做多餘的推論。"
    ),
    expected_output="各坪數級距的單價中位數、總價中位數與成交筆數統計列表。",
    agent=market_analyst
)

# 子任務 2：只算解約
analyze_task_2 = Task(
    description=(
        "使用資料庫分析工具，統計該指標行政區預售屋『解約（is_cancelled=1）』的數量與趨勢。\n"
        "同樣直接使用 SQL 工具查詢，並產出簡單的數字統計。"
    ),
    expected_output="該區預售屋的解約數量統計數據。",
    agent=market_analyst
)

# 子任務 3：整合數據進行房市風向解讀
analyze_task_3 = Task(
    description=(
        "結合前面兩個任務得到的統計數據（坪數分布與解約量），綜合解讀近期房市風向。\n"
        "分析高價戶與低樓層爛格局的成交比例是否存在反轉？建商是否有封鎖高樓層的跡象？"
    ),
    expected_output="一份無情看透市場風向的終極房市數據大解密 Markdown 報告。",
    agent=market_analyst
)

# ==========================================
# 🚀 組裝 Crew：把三個子任務依序放進去
# ==========================================
crew = Crew(
    agents=[data_collector, market_analyst],
    # 💡 關鍵：依序執行這三個小任務，AI 就不會在單一任務裡密集陷入 SQL 迴圈
    tasks=[collect_task, analyze_task_1, analyze_task_2, analyze_task_3],
    verbose=True,
    max_rpm=2  # 調降到 2，讓樹莓派走得慢但走得穩
)

# ======= 💡 請確保以下這段核心執行碼有被完整補上 =======
print("\n====== 🚀 開始執行 Agent 房產團隊任務 ======")

# 真正啟動 CrewAI 的關鍵指令！
result = crew.kickoff()

print("\n======  任務執行完畢，正在產生 MD 報告  ======")

# 將 Gemini 分析出來的最終文字，實質寫入到本地端的 md 檔案中
with open("real_estate_market_report.md", "w", encoding="utf-8") as f:
    f.write(str(result))
    
print(" 成功！報告已輸出至同資料夾下的: real_estate_market_report.md")

if __name__ == "__main__":
    main()

