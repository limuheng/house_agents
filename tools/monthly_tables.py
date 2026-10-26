"""依資料庫交易資料產出指定區域與月份的 Markdown 月統計表。"""

import argparse
from datetime import datetime
from pathlib import Path

import pandas as pd

from tools.monthly_charts import (
    CONFIG_PATH,
    ChartData,
    ChartSettings,
    aggregate_prices,
    aggregate_volume,
    load_transactions,
    parse_month,
    prepare_chart_data,
    load_intervals,
)
from tools.real_estate_settings import DB_PATH


def _volume_table(data: ChartData) -> pd.DataFrame:
    """整理每月坪數級距交易量及該級距最熱絡建案。"""
    counts, leaders = aggregate_volume(data)
    rows = []
    for month in data.months:
        for item in data.intervals:
            label = item["label"]
            leader, leader_count = leaders.get((month, label), ("—", 0))
            rows.append({
                "月份": month,
                "實際室內坪數": label,
                "交易筆數": int(counts.loc[month, label]),
                "交易量最高建案／區域": leader,
                "該建案／區域筆數": int(leader_count),
            })
    return pd.DataFrame(rows)


def _price_table(data: ChartData) -> pd.DataFrame:
    """整理各級距價格平均、中位數及最高價交易標籤。"""
    metrics, leaders = aggregate_prices(data)
    total = metrics["total_price_estimate"]
    unit = metrics["price_per_ping"]
    total_leaders = leaders["total_price_estimate"]
    unit_leaders = leaders["price_per_ping"]
    rows = []
    for month in data.months:
        for item in data.intervals:
            label = item["label"]
            key = (month, label)
            total_stats = total.loc[key] if key in total.index else None
            unit_stats = unit.loc[key] if key in unit.index else None
            rows.append(_price_row(
                month, label, total_stats, unit_stats,
                total_leaders.get(key), unit_leaders.get(key),
            ))
    return pd.DataFrame(rows)


def _price_row(month, label, total_stats, unit_stats,
               total_leader, unit_leader) -> dict:
    """建立單月單級距價格資料列並轉換成圖表使用單位。"""
    return {
        "月份": month,
        "實際室內坪數": label,
        "總價平均（千萬元）": _scaled_stat(total_stats, "mean", 10_000_000),
        "總價中位數（千萬元）": _scaled_stat(total_stats, "median", 10_000_000),
        "總價最高價建案／區域": total_leader[0] if total_leader else "—",
        "單價平均（萬元／坪）": _scaled_stat(unit_stats, "mean", 10_000),
        "單價中位數（萬元／坪）": _scaled_stat(unit_stats, "median", 10_000),
        "單價最高價建案／區域": unit_leader[0] if unit_leader else "—",
    }


def _scaled_stat(stats, field: str, divisor: int) -> float | str:
    """將價格統計轉成顯示單位；該月無有效價格時以破折號表示。"""
    return round(float(stats[field]) / divisor, 2) if stats is not None else "—"


def _markdown_table(frame: pd.DataFrame) -> str:
    """將資料表安全轉成不依賴額外套件的 Markdown 表格。"""
    columns = [str(column) for column in frame.columns]
    rows = [[_escape_cell(value) for value in row] for row in frame.itertuples(index=False)]
    lines = [
        "| " + " | ".join(columns) + " |",
        "| " + " | ".join("---" for _ in columns) + " |",
    ]
    lines.extend("| " + " | ".join(row) + " |" for row in rows)
    return "\n".join(lines)


def _escape_cell(value) -> str:
    """跳脫 Markdown 儲存格中的分隔符號及換行。"""
    if pd.isna(value):
        return "—"
    return str(value).replace("|", "\\|").replace("\r\n", "<br>").replace("\n", "<br>")


def _report_markdown(data: ChartData, settings: ChartSettings) -> str:
    """依成屋及預售屋分類組成含四張統計表的 Markdown 報表。"""
    cancellation = "含解約" if settings.include_cancelled else "排除解約"
    lines = [
        f"# {settings.area} 房市月統計表",
        "",
        f"- 統計期間：{settings.start:%Y.%m} 至 {settings.end:%Y.%m}",
        f"- 解約交易：{cancellation}",
        "- 成交總價為每坪單價乘以權狀總坪數的估算值。",
        "",
    ]
    for is_presale, label in ((False, "成屋"), (True, "預售屋")):
        subset = data.frame[data.frame["is_presale"].astype(int) == int(is_presale)]
        category_data = ChartData(subset, data.months, data.intervals)
        lines.extend(_category_sections(category_data, label))
    return "\n".join(lines).rstrip() + "\n"


def _category_sections(data: ChartData, label: str) -> list[str]:
    """建立單一交易類型的交易量及價格統計章節。"""
    return [
        f"## {label}交易量",
        "",
        _markdown_table(_volume_table(data)),
        "",
        f"## {label}價格統計",
        "",
        _markdown_table(_price_table(data)),
        "",
    ]


def generate_tables(settings: ChartSettings) -> Path:
    """讀取指定資料並輸出成屋、預售屋月統計 Markdown 檔案。"""
    if not settings.area.strip():
        raise ValueError("--area 不可為空")
    if settings.start > settings.end:
        raise ValueError("--start 不可晚於 --end")
    intervals = load_intervals(settings.config)
    frame = load_transactions(settings)
    frame = frame[frame["is_presale"].fillna(0).astype(int).isin([0, 1])]
    data = prepare_chart_data(frame, settings, intervals)
    return _write_report(_report_markdown(data, settings), settings)


def _write_report(markdown: str, settings: ChartSettings) -> Path:
    """建立輸出資料夾並以唯一檔名寫入 UTF-8 Markdown。"""
    settings.output_dir.mkdir(parents=True, exist_ok=True)
    area = "".join(char if char.isalnum() or char in "-_" else "_"
                   for char in settings.area).strip("_") or "area"
    start, end = settings.start.strftime("%Y.%m"), settings.end.strftime("%Y.%m")
    stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S_%f")
    path = settings.output_dir / f"monthly_tables_{area}_{start}-{end}_{stamp}.md"
    path.write_text(markdown, encoding="utf-8")
    return path


def parse_args(argv=None):
    """解析與月圖表工具一致的查詢、範圍及輸出參數。"""
    parser = argparse.ArgumentParser(description="輸出指定區域的月房市 Markdown 統計表")
    parser.add_argument("--start", required=True, type=parse_month, help="起始年月，例如 2025.01")
    parser.add_argument("--end", required=True, type=parse_month, help="結束年月，例如 2025.12")
    parser.add_argument("--area", required=True, help="行政區關鍵字，例如 頭份市")
    parser.add_argument("--db", default=DB_PATH, help="SQLite 資料庫路徑")
    parser.add_argument("--output-dir", default="report/charts", help="Markdown 輸出資料夾")
    parser.add_argument("--config", default=CONFIG_PATH, help="坪數級距設定檔")
    parser.add_argument("--include-cancelled", action="store_true", help="將已標記解約交易納入統計")
    parser.add_argument("--font", help="相容圖表工具的字型參數；Markdown 不使用此設定")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    """執行 Markdown 表格輸出命令並回報檔案路徑。"""
    args = parse_args(argv)
    try:
        settings = ChartSettings(
            args.start, args.end, args.area, args.db, Path(args.output_dir),
            args.config, args.include_cancelled, args.font,
        )
        print(generate_tables(settings))
        return 0
    except Exception as error:
        print(f"【表格產生失敗】{error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
