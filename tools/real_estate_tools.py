import os
import zipfile
import requests
import sqlite3
import yaml
import pandas as pd
from datetime import datetime
from crewai.tools import tool

# 縣市中文與內政部代碼對照表
COUNTY_CODE_MAP = {
    "台北市": "A", "基隆市": "C", "新北市": "F", "宜蘭縣": "G",
    "桃園市": "H", "新竹市": "O", "新竹縣": "J", "苗栗縣": "K",
    "台中市": "B", "彰化縣": "N", "南投縣": "M", "雲林縣": "P",
    "嘉義市": "I", "嘉義縣": "Q", "台南市": "D", "高雄市": "E",
    "屏東縣": "T", "花蓮縣": "U", "台東縣": "V", "澎湖縣": "X"
}

DB_PATH = "real_estate.db"
CONFIG_PATH = "config.yaml"

def load_config():
    """讀取 config.yaml 配置"""
    if not os.path.exists(CONFIG_PATH):
        raise FileNotFoundError(f"找不到設定檔: {CONFIG_PATH}")
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

@tool("Real Estate Data Collector Tool")
def real_estate_collector_tool() -> str:
    """
    動態讀取 config.yaml 的縣市與行政區，下載最新的內政部實價登錄與解約資料。
    利用 Pandas 精確扣除公設、計算淨坪、進行解約單勾稽，並原子化寫入 SQLite 資料庫。
    """
    try:
        # 讀取設定
        config = load_config()
        target_regions = config['monitoring_settings']['target_regions']
        hotspots = config['monitoring_settings']['hotspots']
        
        # 轉換成內政部代碼 (例如: ['O', 'J', 'H', 'K', 'B'])
        target_codes = [COUNTY_CODE_MAP[c] for c in target_regions if c in COUNTY_CODE_MAP]
        
        # 初始化資料庫
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS property_transactions (
                id TEXT PRIMARY KEY, county TEXT, district TEXT, case_name TEXT,
                address TEXT, floor TEXT, total_price REAL, net_ping REAL,
                price_per_ping REAL, is_cancelled BOOLEAN DEFAULT 0, updated_at TEXT
            )
        ''')
        conn.commit()
        
        # 下載與解壓縮 (此處邏輯與先前相同)
        url = "https://moi.gov.tw"
        zip_path = "history.zip"
        extract_dir = "extracted_data"
        
        response = requests.get(url, stream=True, timeout=30)
        if response.status_code == 200:
            with open(zip_path, 'wb') as f: f.write(response.content)
        with zipfile.ZipFile(zip_path, 'r') as zip_ref: zip_ref.extractall(extract_dir)
        
        total_inserted = 0
        total_cancelled = 0
        
        for county_code in target_codes:
            county_lower = county_code.lower()
            presale_file = os.path.join(extract_dir, f"{county_lower}_lvr_land_b.csv")
            
            if os.path.exists(presale_file):
                df = pd.read_csv(presale_file, skiprows=[1], low_memory=False)
                
                # 【優化】動態過濾：只留下 config.yaml 裡指定的焦點觀察行政區 (Hotspots)
                df = df[df['鄉鎮市區'].isin(hotspots)]
                df = df[df['建物型態'].str.contains('大樓|華廈|透天', na=False, regex=True)]
                
                for _, row in df.iterrows():
                    try:
                        main_area = float(row.get('主建物面積', 0))
                        sub_area = float(row.get('附屬建物面積', 0))
                        net_ping = (main_area + sub_area) * 0.3025
                        total_price = float(row.get('總價元', 0))
                        
                        if net_ping > 0 and total_price > 0:
                            price_per_ping = total_price / net_ping
                            case_name = str(row.get('建案名稱', '未知建案'))
                            address = str(row.get('土地位置建物門牌', ''))
                            floor = str(row.get('移轉層次', ''))
                            transfer_ping = str(row.get('建物移轉總面積平方公尺', ''))
                            unique_id = f"{county_code}_{case_name}_{address}_{floor}_{transfer_ping}"
                            
                            cursor.execute('''
                                INSERT INTO property_transactions (id, county, district, case_name, address, floor, total_price, net_ping, price_per_ping, updated_at)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                                ON CONFLICT(id) DO UPDATE SET total_price=excluded.total_price, net_ping=excluded.net_ping, price_per_ping=excluded.price_per_ping, updated_at=excluded.updated_at
                            ''', (unique_id, county_code, row.get('鄉鎮市區', ''), case_name, address, floor, total_price, net_ping, price_per_ping, datetime.now().isoformat()))
                            total_inserted += 1
                    except Exception: continue

                # 解約勾稽過濾
                df_cancel = pd.read_csv(presale_file, skiprows=[1], low_memory=False)
                df_cancel = df_cancel[df_cancel['鄉鎮市區'].isin(hotspots)]
                df_cancel = df_cancel[df_cancel['備註'].str.contains('解約|取消', na=False)]
                
                for _, row in df_cancel.iterrows():
                    cancel_id = f"{county_code}_{str(row.get('建案名稱','未知建案'))}_{str(row.get('土地位置建物門牌',''))}_{str(row.get('移轉層次',''))}_{str(row.get('建物移轉總面積平方公尺',''))}"
                    cursor.execute('UPDATE property_transactions SET is_cancelled = 1, updated_at = ? WHERE id = ? AND is_cancelled = 0', (datetime.now().isoformat(), cancel_id))
                    total_cancelled += cursor.rowcount

        conn.commit()
        conn.close()
        return f"【資料搜集成功】已讀取 config.yaml。焦點行政區共寫入/更新 {total_inserted} 筆交易，並自動勾稽標記 {total_cancelled} 筆解約單。"
    except Exception as e:
        return f"【資料搜集失敗】錯誤: {str(e)}"

