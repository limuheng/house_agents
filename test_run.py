# test_run.py - 當期資料更新及資料庫結果手動驗證
import os
import sqlite3

from tools.current_update import main as update_current_data

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
            print(f"{str(row[0]):<12} | {str(row[1]):<12} | {str(row[2]):<18} | {row[3]:<8.2f} | {row[4]:<14,} | {status}")
        print("-" * 80)
        
        conn.close()
    except Exception as e:
        print(f"❌ 讀取資料庫時遭遇錯誤: {str(e)}")

def main():
    print("====== 🚀 手動更新當期交易資料 ======")
    if update_current_data() != 0:
        return
    verify_database()

if __name__ == "__main__":
    main()
