import sqlite3
from crewai.tools import tool

DB_FILE = "real_estate.db"

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
            for row in rows[:50]:
                result.append(", ".join(str(val) for val in row))
            if len(rows) > 50:
                result.append(f"...(餘下 {len(rows)-50} 筆資料已省略，請改用 COUNT(*) 或 AVG 等聚合函數查詢)")
            return "\n".join(result)
    except Exception as e:
        return f"SQL 執行失敗，請修正語法錯誤。錯誤訊息: {str(e)}"

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
            
        report = f"### 📊 【{location}】最新建案籌碼與價格走勢速查表\n"
        report += "| 建案名稱 | 總成交筆數 | 標記解約(幽靈單) | 實際室室淨坪均價 (每坪/元) |\n"
        report += "| :--- | :---: | :---: | :---: |\n"
        for row in rows[:30]:
            cancel_warn = f"⚠️ {row[2]} 筆" if row[2] > 0 else "0 筆 (正常)"
            report += f"| {row[0]} | {row[1]} 戶 | {cancel_warn} | {row[3]:,} 元 |\n"
        return report
    except Exception as e:
        return f"快捷查詢失敗，原因：{str(e)}"
