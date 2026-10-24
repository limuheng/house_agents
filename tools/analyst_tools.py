import os
import sqlite3
import yaml
import pandas as pd
from crewai.tools import tool

DB_PATH = "real_estate.db"
CONFIG_PATH = "config.yaml"

# =====================================================================
# 🧱 核心拆解子元件（Sub-functions）
# =====================================================================

def load_analyst_config() -> tuple:
    """💡 子元件 1：安全讀取動態設定檔，回傳坪數級距與監控熱區"""
    if not os.path.exists(CONFIG_PATH):
        raise FileNotFoundError(f"找不到設定檔: {CONFIG_PATH}")
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    
    intervals = config.get('analysis_indicators', {}).get('ping_intervals', [])
    hotspots = config.get('monitoring_settings', {}).get('hotspots', [])
    return intervals, hotspots


def calculate_interval_metrics(df_all: pd.DataFrame, intervals: list) -> pd.DataFrame:
    """💡 子元件 2：專職計算各實際淨坪級距之價量中位數與解約量 (繞過 SQLite 無中位數限制)"""
    interval_results = []
    
    for iv in intervals:
        min_p = float(iv['min'])
        max_p = float(iv['max'])
        label = iv['label']
        
        # 進行記憶體切片過濾
        sub_df = df_all[(df_all['actual_ping'] >= min_p) & (df_all['actual_ping'] < max_p)].copy()
        
        if sub_df.empty:
            interval_results.append({
                "級距標籤": label, "去化量": 0, 
                "單價中位數(元/坪)": "N/A", "總價中位數": "N/A", "已解約幽靈單": 0
            })
            continue
            
        # 計算預估總價（單價 * 權狀總坪數）
        sub_df['estimated_total'] = sub_df['price_per_ping'] * sub_df['total_ping']
        
        # 精確中位數計算
        med_price = int(sub_df['price_per_ping'].median())
        med_total = int(sub_df['estimated_total'].median())
        cancel_count = int(sub_df['is_cancelled'].sum())
        
        interval_results.append({
            "級距標籤": label,
            "去化量": len(sub_df),
            "單價中位數(元/坪)": f"{med_price:,}",
            "總價中位數": f"{(med_total / 10000):.1f} 萬",
            "已解約幽靈單": cancel_count
        })
        
    return pd.DataFrame(interval_results)


def calculate_regional_cancellation_rates(df_all: pd.DataFrame, hotspots: list) -> pd.DataFrame:
    """💡 子元件 3：專職計算焦點熱區的技術性解約率與黑名單排行榜"""
    region_results = []
    
    # 短行政區特徵提取（如 '竹北市' 保持 '竹北市'，'苗栗縣頭份市' 轉為 '頭份市'）
    clean_spots = [s[-3:] if any(k in s for k in ["市", "鄉", "鎮", "區"]) else s for s in hotspots]
    
    for spot in clean_spots:
        # 模糊匹配資料庫中的 region 欄位
        r_df = df_all[df_all['region'].str.contains(spot, na=False)]
        
        if r_df.empty:
            continue
            
        total_deals = len(r_df)
        cancel_deals = int(r_df['is_cancelled'].sum())
        cancel_rate = (cancel_deals / total_deals * 100) if total_deals > 0 else 0.0
        
        region_results.append({
            "行政區域": spot,
            "總成交筆數": total_deals,
            "已解約幽靈單": cancel_deals,
            "技術性解約率": cancel_rate  # 保持浮點數以利後續排序
        })
        
    if not region_results:
        return pd.DataFrame()
        
    # 轉成 DataFrame 並依據解約率從高到低進行無情排行
    df_regions = pd.DataFrame(region_results).sort_values(by="技術性解約率", ascending=False)
    # 格式化輸出百分比字串
    df_regions['技術性解約率'] = df_regions['技術性解約率'].map(lambda x: f"{x:.1f} %")
    return df_regions

# =====================================================================
# 👑 CrewAI 頂層唯一呼叫引腳 (不改變原先命名，主程式無痛對齊)
# =====================================================================

@tool("Quick Region Trend Lookup Tool")
def query_region_trend_tool() -> str:
    """一鍵速查本地 SQLite 資料庫，自動依據 config.yaml 的坪數級距，計算出精確的單總價中位數、去化量與解約幽靈單比例。"""
    if not os.path.exists(DB_PATH):
        return "【查詢失敗】找不到實體 real_estate.db 資料庫，請催促數據工程師先執行採集工具。"
        
    try:
        # 1. 讀取動態設定檔參數 (子元件 1)
        intervals, hotspots = load_analyst_config()
        
        # 2. 將全量數據加載至記憶體
        conn = sqlite3.connect(DB_PATH)
        df_all = pd.read_sql_query("SELECT * FROM real_estate_deals", conn)
        conn.close()
        
        if df_all.empty:
            return "【情報提示】實體資料庫目前為空空如也狀態，請先啟動採集特工注入真實數據。"
            
        # 🧱 開始依模組化拼裝文字報告
        report = ["\n" + "="*80, "📊 房地產數據科學家防禦武器：動態級距與中位數核心查核報告", "="*80]
        
        # 3. 調用級距計算機 (子元件 2)
        report.append("\n👑 【指標一：各實際淨坪級距價量中位數精確審計】")
        df_intervals = calculate_interval_metrics(df_all, intervals)
        report.append(df_intervals.to_string(index=False))
        
        # 4. 調用解約率排行計算機 (子元件 3)
        report.append("\n👑 【指標二：焦點行政區預售解約率黑名單】")
        df_regions = calculate_regional_cancellation_rates(df_all, hotspots)
        
        if not df_regions.empty:
            report.append(df_regions.to_string(index=False))
        else:
            report.append("  (當前監控熱區內尚無實體登錄成交或解約紀錄)")
            
        report.append("="*80)
        return "\n".join(report)
        
    except Exception as e:
        return f"【工具底層崩潰】Quick Region Trend Lookup Tool 內部分解執行故障: {str(e)}"
