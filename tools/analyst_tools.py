# tools/analyst_tools.py - 數據科學家專屬工具箱
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
            for row in rows[:50]: # 限制前 50 筆防止 Token 爆炸
                result.append(", ".join(str(val) for val in row))
            if len(rows) > 50:
                result.append(f"...(餘下 {len(rows)-50} 筆資料已省略，請改用 COUNT(*) 或 AVG 等聚合函數查詢)")
            return "\n".join(result)
    except Exception as e:
        return f"SQL 執行失敗，請修正語法錯誤。錯誤訊息: {str(e)}"


# 👑 參考舊 db_tool.py 的優點，升級為高規格、防幻覺的「區域趨勢一鍵查詢工具」
@tool("Quick Region Trend Lookup Tool")
def query_region_trend_tool(location: str) -> str:
    """
    【高階快捷工具】只要輸入行政區名稱（例如：頭份市、竹北市），
    即可一鍵撈出該區所有預售屋建案的成交總量、實際淨坪平均單價、以及被建商技術性解約的幽靈單數量。
    不需自寫 SQL 語法，優先推薦使用。
    """
    try:
        with sqlite3.connect(DB_FILE) as conn:
            cursor = conn.cursor()
            
            # 撈取核心統計數據（結合高規格資料表欄位）
            cursor.execute("""
                SELECT 
                    project_name as 建案名稱,
                    COUNT(*) as 總成交筆數,
                    SUM(CASE WHEN is_cancelled = 1 THEN 1 ELSE 0 END) as 已解約筆數,
                    ROUND(AVG(price_per_ping)) as 實際淨坪平均單價_元
                FROM real_estate_deals 
                WHERE region LIKE ? 
                GROUP BY project_name 
                ORDER BY 總成交筆數 DESC
            """, (f"%{location}%",))
            
            rows = cursor.fetchall()
            
        if not rows:
            return f"【查詢完成】在資料庫中找不到該區域（{location}）的歷史實登數據。"
            
        # 格式化成 2026 最適合 Agent 理解的精煉 Markdown 報表
        report = f"### 📊 【{location}】最新建案籌碼與價格走勢速查表\n"
        report += "| 建案名稱 | 總成交筆數 | 標記解約(幽靈單) | 實際室內淨坪均價 (每坪/元) |\n"
        report += "| :--- | :---: | :---: | :---: |\n"
        
        for row in rows[:30]: # 取前 30 大焦點建案，節省 Token
            # 計算解約警告符號
            cancel_warn = f"⚠️ {row[2]} 筆" if row[2] > 0 else "0 筆 (正常)"
            # 將單價換算成萬，方便閱讀
            price_wan = f"{row[3]/10000:.1f} 萬" if row[3] else "未明"
            
            report += f"| {row[0]} | {row[1]} 戶 | {cancel_warn} | {price_wan} |\n"
            
        return report
    except Exception as e:
        return f"快捷查詢失敗，原因：{str(e)}"
