# test_analyst.py - 驗證分析師特工的快捷趨勢工具
from tools.analyst_tools import query_region_trend_tool

def main():
    print("====== 🚀 模擬數據科學家特工呼叫一鍵速查工具 ======")
    
    # 模擬 AI 輸入「頭份市」進行查詢
    try:
        if hasattr(query_region_trend_tool, '_run'):
            result = query_region_trend_tool._run(location="頭份市")
        else:
            result = query_region_trend_tool(location="頭份市")
    except Exception:
        from tools.analyst_tools import query_region_trend_tool as raw_func
        result = raw_func("頭份市")
        
    print(result)

if __name__ == "__main__":
    main()
