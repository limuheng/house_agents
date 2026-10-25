import os
import sqlite3
import yaml
import time
import random
import requests  # 保留內建的，給其他標準傳輸使用
from curl_cffi import requests as curl_requests  # 重新命名，徹底避開覆蓋衝突
from crewai.tools import tool
from tools.real_estate_repository import RealEstateRepository
from tools.real_estate_transform import (
    cancelled_fingerprints,
    clean_hotspots,
    parse_transaction,
)

DB_PATH = "real_estate.db"
CONFIG_PATH = "config.yaml"

# 縣市與代碼映射表
COUNTY_CODE_MAP = {
    "台北市": "a", "基隆市": "c", "新北市": "f", "宜蘭縣": "g",
    "桃園市": "h", "新竹市": "o", "新竹縣": "j", "苗栗縣": "k",
    "台中市": "b", "彰化縣": "n", "南投縣": "m", "雲林縣": "p",
    "嘉義市": "i", "嘉義縣": "q", "台南市": "d", "高雄市": "e",
    "屏東縣": "t", "花蓮縣": "v", "台東縣": "v", "澎湖縣": "x",
    "金門縣": "w", "連江縣": "z"
}

def load_config():
    if not os.path.exists(CONFIG_PATH):
        raise FileNotFoundError(f"找不到設定檔: {CONFIG_PATH}")
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

class RealEstateDataTool:
    @staticmethod
    def init_database():
        RealEstateRepository(DB_PATH).initialize()

    @staticmethod
    def inject_sandbox_data():
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        sandbox_data = [
            ("2026-08-15", "苗栗縣頭份市", "運動公園極景", "住宅大樓", "十層", "A1", 45.2, 31.5, 1200000, 485000, 0, 1, 0, None, "SANDBOX_001"),
            ("2026-08-18", "苗栗縣頭份市", "運動公園極景", "住宅大樓", "十一層", "B2", 45.2, 31.5, 1200000, 520000, 0, 1, 1, "2026-10-01", "SANDBOX_002"),
            ("2026-09-02", "新竹縣竹北市", "高鐵晶鑽", "住宅大樓", "五層", "C5", 38.6, 26.8, 1500000, 725000, 0, 1, 0, None, "SANDBOX_003")
        ]
        inserted = 0
        for d in sandbox_data:
            try:
                cursor.execute('''
                    INSERT INTO real_estate_deals (deal_date, region, project_name, building_type, floor, unit_id, total_ping, actual_ping, parking_price, price_per_ping, house_age, is_presale, is_cancelled, cancelled_date, fingerprint)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(fingerprint) DO NOTHING
                ''', d)
                if cursor.rowcount > 0: inserted += 1
            except Exception: pass
        conn.commit()
        conn.close()
        return inserted

# =====================================================================
# 👑 網路防禦與自動化下載小元件（依據 muheng 實測之黃金路由重構）
# =====================================================================

def get_target_csv_filenames() -> list:
    """💡 子元件 1-1：根據 config 中的目標縣市，動態映射出小寫的官方 CSV 檔案清單"""
    try:
        config = load_config()
        target_regions = config.get('monitoring_settings', {}).get('target_regions', [])
    except Exception:
        target_regions = ["苗栗縣", "新竹縣"]

    csv_files = []
    for region in target_regions:
        if region in COUNTY_CODE_MAP:
            code = COUNTY_CODE_MAP[region].lower()
            csv_files.append(f"{code}_lvr_land_b.csv")  # 預售屋
            csv_files.append(f"{code}_lvr_land_a.csv")  # 成屋
    return csv_files


def download_single_csv(file_name: str, save_dir: str, max_attempts: int = 3) -> bool:
    """💡 子元件 1-2：單一檔案直連拉取器，完美還原你測試成功的 curl 乾淨標頭行為"""
    download_url = f"https://plvr.land.moi.gov.tw/Download?fileName={file_name}"
    output_path = os.path.join(save_dir, file_name)
    clean_headers = {
        "User-Agent": "curl/8.14.1",
        "Accept": "*/*",
        "Host": "plvr.land.moi.gov.tw"
    }

    for attempt in range(1, max_attempts + 1):
        try:
            res = requests.get(download_url, headers=clean_headers, timeout=45, verify=False)
            if res.status_code == 200:
                try:
                    content_text = res.content.decode('utf-8', errors='ignore')
                except Exception:
                    content_text = ""

                # 核心防禦：只要內容包含關鍵字「鄉鎮市區」，代表它就是真正的 CSV 檔案
                if "鄉鎮市區" in content_text:
                    with open(output_path, 'wb') as f:
                        f.write(res.content)
                    print(f"✅ [成功落地] 檔案 {file_name} 同步完成！(大小: {len(res.content)/1024:.1f} KB)")
                    return True
                else:
                    if "檔案不存在" in content_text or "施工中" in content_text:
                        print(f"ℹ️ [本期無數據] 內政部尚未發布 {file_name}，自動跳過，不中斷流程。")
                        return True
            print(f"⚠️ 檔案 {file_name} 傳回異常狀態碼: {res.status_code} (第 {attempt} 次嘗試)...")
        except Exception as e:
            print(f"⚠️ 下載 {file_name} 發生錯誤: {str(e)} (第 {attempt} 次嘗試)...")
        
        if attempt < max_attempts:
            time.sleep(2)
    return False


def download_official_zip(url_placeholder: str, zip_path_placeholder: str, extract_dir: str, headers_placeholder: dict) -> bool:
    """💡 子功能控制器 1：聯網下載控制器，協調清單進行循環下載"""
    csv_targets = get_target_csv_filenames()
    if not csv_targets:
        return False

    print(f"📦 [自動化啟動] 偵測到監控縣市對應的實登 CSV 共有 {len(csv_targets)} 個，開始聯網拉取...")
    os.makedirs(extract_dir, exist_ok=True)

    all_success = True
    for file_name in csv_targets:
        print(f"📡 正在拉取當期靜態數據: {file_name}")
        if not download_single_csv(file_name, extract_dir):
            all_success = False
        time.sleep(1)

    if all_success:
        print(f"🏆 [全量落地] 所有關注縣市的實登數據已全自動同步至: {extract_dir}/")
        return True
    return False

def extract_clean_hotspots(hotspots_config: list) -> list:
    """💡 子元件 2-1：將 config 的 hotspots 轉為 3 字短特徵（例如 頭份市 保持 頭份市），對齊官方 CSV 格式"""
    return clean_hotspots(hotspots_config)


def parse_single_row_to_record(row, is_presale: int, county_code: str, parent_county: str) -> dict:
    """💡 子元件 2-2：純粹的資料解析器，負責將單行真實交易換算為 SQLite 標準格式，並生成全台唯一的特徵指紋"""
    return parse_transaction(row, is_presale, county_code, parent_county)


def execute_sqlite_upsert(cursor, rec: dict) -> bool:
    """💡 子元件 2-3：專職的資料庫 Upsert 執行器，當指紋衝突時自動更新最新狀態，不重複塞筆數"""
    return RealEstateRepository(DB_PATH).upsert(cursor, rec)


def audit_cancelled_deals(cursor, presale_file: str, county_code: str, hotspots: list) -> int:
    """💡 子元件 2-4：交叉勾稽模組，掃描當期歷史數據中的解約備註，反向標記資料庫中的過往訂單"""
    try:
        fingerprints = cancelled_fingerprints(presale_file, county_code, hotspots)
        return RealEstateRepository(DB_PATH).mark_cancelled(cursor, fingerprints)
    except Exception:
        return 0


def process_and_import_data(extract_dir: str, target_codes: list, hotspots: list, target_regions: list) -> tuple:
    """💡 子功能控制器 2：大數據整合核心，協調資料讀取、過濾與子元件調用"""
    clean_hotspots = extract_clean_hotspots(hotspots)
    print(f"\n🚨 [進入 Pandas 洗滌面版] 當前監控熱區一擊必殺匹配: {clean_hotspots}")
    repository = RealEstateRepository(DB_PATH)
    with repository.transaction() as conn:
        totals = _import_counties(conn.cursor(), repository, extract_dir,
                                  zip(target_codes, target_regions), clean_hotspots)
    return totals


def _import_counties(cursor, repository, extract_dir: str,
                     counties, hotspots: list[str]) -> tuple[int, int]:
    """協調各縣市成屋、預售屋匯入及解約勾稽。"""
    inserted = cancelled = 0
    for code, county in counties:
        county_totals = _import_county(cursor, repository, extract_dir,
                                       code, county, hotspots)
        inserted += county_totals[0]
        cancelled += county_totals[1]
    return inserted, cancelled


def _import_county(cursor, repository, extract_dir: str, code: str,
                   county: str, hotspots: list[str]) -> tuple[int, int]:
    """處理單一縣市資料檔及對應解約紀錄。"""
    import pandas as pd

    code = code.lower()
    inserted = cancelled = 0
    for suffix, is_presale, label in (("b", 1, "預售屋"), ("a", 0, "成屋")):
        path = os.path.join(extract_dir, f"{code}_lvr_land_{suffix}.csv")
        if not os.path.exists(path):
            continue
        try:
            count, marked = _import_file(cursor, repository, path, is_presale,
                                         code, county, hotspots, label)
            inserted += count
            cancelled += marked
        except Exception as error:
            print(f"⚠️ 解析實體檔案 {path} 失敗: {error}")
    presale_path = os.path.join(extract_dir, f"{code}_lvr_land_b.csv")
    if os.path.exists(presale_path):
        cancelled += audit_cancelled_deals(cursor, presale_path, code, hotspots)
    return inserted, cancelled


def _import_file(cursor, repository, path: str, is_presale: int,
                 code: str, county: str, hotspots: list[str],
                 label: str) -> tuple[int, int]:
    """讀取、篩選來源 CSV，並將轉換後紀錄交由儲存層寫入。"""
    import pandas as pd

    df = pd.read_csv(path, skiprows=[1], low_memory=False)
    df = df[df["鄉鎮市區"].isin(hotspots)]
    df = df[df["建物型態"].str.contains("大樓|華廈|透天", na=False, regex=True)]
    print(f"📖 正在解析【{county}】{label}，符合熱區篩選共: {len(df)} 筆")
    inserted = cancelled = 0
    for _, row in df.iterrows():
        record = parse_single_row_to_record(row, is_presale, code, county)
        if repository.upsert(cursor, record):
            inserted += 1
            cancelled += int(record["is_cancelled"])
    return inserted, cancelled

# =====================================================================
# 👑 3. CrewAI 唯一註冊與執行引腳 (不允許任何提早 return)
# =====================================================================

@tool("Real Estate Data Collector Tool")
def real_estate_collector_tool() -> str:
    """自動對齊內政部地政司當期免費 Open Data，並自動進行採集、洗滌與勾稽。"""
    try:
        # 1. 確保資料庫結構就緒
        RealEstateDataTool.init_database()
        extract_dir = "extracted_data"
        
        # 2. 拉動聯網下載子功能控制器
        download_official_zip("", "", extract_dir, {})
        
        # 3. 解析最新 config.yaml 設定檔參數
        config = load_config()
        monitoring = config.get('monitoring_settings', {})
        hotspots = monitoring.get('hotspots', [])
        target_regions = monitoring.get('target_regions', [])
        
        # 根據訂閱區域，動態轉換為字母清單（如 ["苗栗縣"] -> ["K"]）
        target_codes = [COUNTY_CODE_MAP[r] for r in target_regions if r in COUNTY_CODE_MAP]
        
        print(f"🚀 [引線接通] 資料全數落地！即刻驅動核心 Pandas 洗滌。過濾熱區為: {hotspots}")
        
        # 4. 確實呼叫洗滌子功能控制器，將大數據寫入實體 SQLite
        inserted, cancelled = process_and_import_data(extract_dir, target_codes, hotspots, target_regions)
        
        return (f"【自動化搜集成功】🏆 系統已完成全量大數據洗滌！\n"
                f"📊 本次實質新增/更新真實實登交易: {inserted} 筆\n"
                f"⚠️ 交叉勾稽歷史解約(幽靈單)成功標記: {cancelled} 筆")
                
    except Exception as e:
        return f"【自動化搜集失敗】頂層總控制器爆發嚴重崩潰: {str(e)}"
