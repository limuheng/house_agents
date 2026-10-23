import sqlite3
import yaml
from crewai.tools import tool

DB_PATH = "real_estate.db"

@tool("Real Estate SQL Query Tool")
def real_estate_sql_tool(sql_query: str) -> str:
    """
    專供資深量化分析師呼叫的資料庫查詢工具。
    輸入標準的 SQLite SELECT 語法，即可查詢經清洗後的房地產交易與解約數據。
    為了資安防護，本工具僅允許 SELECT 唯讀操作。
    """
    # 資安防線：禁止任何修改型指令
    forbidden_words = ["insert", "update", "delete", "drop", "alter", "create", "replace"]
    if any(word in sql_query.lower() for word in forbidden_words):
        return "【拒絕執行】為了系統安全，本工具僅允許 SELECT 唯讀查詢。"
        
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute(sql_query)
        rows = cursor.fetchall()
        
        # 取得欄位名稱，方便 AI 閱讀理解
        colnames = [desc[0] for desc in cursor.description]
        conn.close()
        
        if not rows:
            return "【查詢完成】查無符合條件的數據。"
            
        # 格式化輸出，限制回傳行數防止 Token 爆炸
        result_str = f"欄位資訊: {colnames}\n"
        for row in rows[:50]:  # 最多回傳 50 行聚合數據
            result_str += f"{list(row)}\n"
        if len(rows) > 50:
            result_str += f"...(餘下 {len(rows)-50} 筆資料已省略，請改用 COUNT(*) 或 GROUP BY 進行聚合查詢)"
            
        return result_str
    except Exception as e:
        return f"【SQL 執行錯誤】: {str(e)}"

