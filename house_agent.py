# house_agent.py - CrewAI 房地產情報監控核心主程式
import argparse
import yaml
from crewai import Agent, Task, Crew, Process, LLM

from tools.analyst_tools import query_region_trend_tool
from tools.report_writer import write_markdown_report
from tools.historical_backfill import build_preview, historical_backfill_tool

from dotenv import load_dotenv
load_dotenv()  # 🎯 物理補丁：啟動時自動把同目錄下的 .env 變數灌進樹莓派記憶體中

# 載入 YAML 配置
with open("config.yaml", "r", encoding="utf-8") as f:
    config = yaml.safe_load(f)

# --- 🧠 定義符合最新 CrewAI 的 LLM 組態 ---
gemini_llm = LLM(
    model="openai/gpt-6-luna",
    temperature=0.2,
    max_retries=5,          # 遇到 429 錯誤時，最多自動重試 5 次
    timeout=60.0            # 超時等待時間
)

# --- 🤖 建立分析 Agent ---
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

historical_data_agent = Agent(
    role="歷史房地產資料回補工程師",
    goal="依設定逐季補齊內政部歷史實價登錄，並回報下載與資料庫匯入摘要。",
    backstory=(
        "你只負責呼叫歷史資料回補工具。工具會逐季取得設定縣市的買賣與預售 CSV，"
        "驗證後沿用既有資料轉換與 SQLite 去重流程。不得自行拼接網址或宣稱未完成的工作成功。"
    ),
    tools=[historical_backfill_tool],
    verbose=True,
    llm=gemini_llm,
    max_iter=2
)

# --- 📋 建立分析 Tasks ---
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

historical_backfill_task = Task(
    description=(
        "請直接呼叫 Historical Real Estate Backfill Tool 一次，"
        "依設定從 109S1 起逐季下載所有監控縣市的買賣 A 與預售 B 資料，"
        "並回報完成季度、下載/快取/無資料/失敗數及新增或更新筆數。"
        "不要自行下載、讀取大型 CSV 或直接操作資料庫。"
    ),
    expected_output="歷史資料回補結果摘要，列明季度範圍、檔案狀態及資料庫匯入筆數。",
    agent=historical_data_agent
)

# ==========================================
# 🚀 組裝 Crew 與執行流程
# ==========================================
def _run_historical_backfill() -> None:
    """只執行使用者明確指定的歷史回補 Crew。"""
    crew = Crew(
        agents=[historical_data_agent],
        tasks=[historical_backfill_task],
        process=Process.sequential,
        verbose=True,
        max_rpm=10
    )
    print(crew.kickoff())


def _parse_args():
    """解析一般分析、回補預覽及實際回補選項。"""
    parser = argparse.ArgumentParser(description="房地產市場分析與歷史回補")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--plan-backfill", action="store_true", help="僅預覽回補範圍")
    mode.add_argument("--backfill-history", action="store_true", help="執行歷史資料回補")
    return parser.parse_args()


def main():
    args = _parse_args()
    if args.plan_backfill:
        print(build_preview())
        return
    if args.backfill_history:
        _run_historical_backfill()
        return
    crew = Crew(
        agents=[market_analyst],
        tasks=[analyze_task_1, analyze_task_2, analyze_task_3],
        process=Process.sequential,
        verbose=True,
        max_rpm=20  
    )

    print("\n====== 🚀 開始執行 Agent 房產團隊任務 ======")
    result = crew.kickoff()

    print("\n======  任務執行完編，正在產生 MD 報告  ======")
    output_filename = "real_estate_market_report.md"
    write_markdown_report(str(result), output_filename)
        
    print(f"🎉 成功！報告已輸出至同資料夾下的: {output_filename}")

if __name__ == "__main__":
    main()
