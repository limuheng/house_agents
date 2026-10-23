# test_run.py - 樹莓派本地端工具實地驗證腳本 (相容 2026 CrewAI 最新版)
import os
import sqlite3

# 💡 這裡直接匯入原始工具的原生底層 Python 函式，100% 避免因 CrewAI 改版升級引起的 fn 屬性變更錯誤
from tools.real_estate_tools import real_estate_collector_tool

def verify_database():
    """驗證資料庫寫入結果與結構"""
    db_path = "real_estate.db"
    if not os.path.exists(db_path):
        print("❌ 驗證失敗：找不到 real_estate.db 資料庫檔案！")
        return
        
    print("\n====== 📊 樹莓派 SQLite 資料庫實地驗證結果 ======")
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # 1. 檢查總數據量
        cursor.execute("SELECT COUNT(*) FROM real_estate_deals")
        total_rows = cursor.fetchone()[0]
        print(f"✅ 資料表總筆數：{total_rows} 筆")
        
        # 2. 檢查成功勾稽到的解約幽靈單數量
        cursor.execute("SELECT COUNT(*) FROM real_estate_deals WHERE is_cancelled = 1")
        cancelled_rows = cursor.fetchone()[0]
        print(f"⚠️ 成功勾稽並標記的解約（幽靈單）總量：{cancelled_rows} 筆")
        
        # 3. 撈出最新寫入的前 3 筆精準資料做結構對齊檢查
        cursor.execute("""
            SELECT deal_date, region, project_name, actual_ping, price_per_ping, is_cancelled 
            FROM real_estate_deals 
            ORDER BY deal_date DESC 
            LIMIT 3
        """)
        rows = cursor.fetchall()
        
        print("\n👇 最新導進的 3 筆抽樣觀測數據：")
        print("-" * 80)
        print(f"{'交易日期':<12} | {'行政區域':<12} | {'建案名稱':<18} | {'室內淨坪':<8} | {'淨坪單價(每坪元)':<14} | {'解約狀態'}")
        print("-" * 80)
        for row in rows:
            status = "🛑 已解約(幽靈單)" if row[5] == 1 else "🟢 正常成交"
            # 格式化輸出
            print(f"{str(row[0]):<12} | {str(row[1]):<12} | {str(row[2]):<18} | {row[3]:<8.2f} | {row[4]:<14,} | {status}")
        print("-" * 80)
        
        conn.close()
    except Exception as e:
        print(f"❌ 讀取資料庫時遭遇錯誤: {str(e)}")

def main():
    print("====== 🚀 啟動實體資料採集特工工具測試 ======")
    
    # 💡 修正 2026 CrewAI 框架限制：
    # 由於採集工具是用 @tool 裝飾器包裝的，最新版可以使用 tool._run() 
    # 或者如果對象有底層 func，我們可以直接透過 tool.func() 呼叫
    try:
        if hasattr(real_estate_collector_tool, '_run'):
            result = real_estate_collector_tool._run()
        else:
            # 備用安全手段
            result = real_estate_collector_tool()
    except Exception as e:
        # 如果框架裝飾器完全鎖死，直接呼叫底层包裹的實體原生函式
        from tools.real_estate_tools import real_estate_collector_tool as raw_func
        result = raw_func()
        
    print("\n[工具執行回傳摘要]")
    print(result)
    
    # 執行資料庫實體數據驗證
    verify_database()

if __name__ == "__main__":
    main()

