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
    """以月份為欄整理各坪數級距交易量及領先建案。"""
    counts, leaders = aggregate_volume(data)
    rows = []
    for item in data.intervals:
        label = item["label"]
        rows.extend(_volume_rows(label, data.months, counts, leaders))
    return _pivot_frame(rows, data.months)


def _volume_rows(label, months, counts, leaders) -> list[dict]:
    """建立單一坪數級距的月交易筆數與領先建案列。"""
    count_row = _pivot_row(label, "交易筆數", months)
    leader_row = _pivot_row(label, "交易量最高建案／區域", months)
    for month in months:
        count_row[month] = int(counts.loc[month, label])
        leader = leaders.get((month, label))
        leader_row[month] = f"{leader[0]}（{leader[1]}筆）" if leader else "—"
    return [count_row, leader_row]


def _price_metric_table(data: ChartData, field: str,
                        divisor: int, unit: str) -> pd.DataFrame:
    """建立獨立的價格指標月表，包含樣本數、平均、中位數及最高案名。"""
    metrics, leaders = aggregate_prices(data)
    values = metrics[field]
    best_deals = leaders[field]
    counts = data.frame.groupby(["month", "size_label"]).size()
    rows = []
    for item in data.intervals:
        label = item["label"]
        rows.extend(_price_rows(
            label, data.months, values, best_deals, counts, divisor, unit
        ))
    return _pivot_frame(rows, data.months)


def _price_rows(label, months, values, leaders, counts,
                divisor: int, unit: str) -> list[dict]:
    """為單一坪數級距建立價格指標、樣本數及最高案名列。"""
    specs = (
        ("交易筆數", "count"),
        (f"平均（{unit}）", "mean"),
        (f"中位數（{unit}）", "median"),
        ("最高價建案／區域", "leader"),
    )
    rows = [_pivot_row(label, title, months) for title, _ in specs]
    for month in months:
        key = (month, label)
        stats = values.loc[key] if key in values.index else None
        best = leaders.get(key)
        values_by_stat = {
            "count": int(counts.get(key, 0)),
            "mean": _scaled_stat(stats, "mean", divisor),
            "median": _scaled_stat(stats, "median", divisor),
            "leader": best[0] if best else "—",
        }
        for row, (_, stat) in zip(rows, specs):
            row[month] = values_by_stat[stat]
    return rows


def _interior_metrics(data: ChartData) -> pd.DataFrame:
    """只以官方主建物面積計算室內實坪平均與中位數。"""
    if "main_building_ping" not in data.frame:
        return pd.DataFrame()
    values = pd.to_numeric(data.frame["main_building_ping"], errors="coerce")
    valid = data.frame.loc[values > 0, ["month", "size_label"]].copy()
    valid["main_building_ping"] = values[values > 0]
    return valid.groupby(["month", "size_label"])["main_building_ping"].agg(
        mean="mean", median="median"
    )


def _interior_table(data: ChartData) -> pd.DataFrame:
    """有主建物面積資料時，輸出室內實坪的月平均與中位數。"""
    stats = _interior_metrics(data)
    if stats.empty:
        return pd.DataFrame()
    rows = []
    for item in data.intervals:
        label = item["label"]
        for field, title in (("mean", "平均室內實坪"), ("median", "室內實坪中位數")):
            row = _pivot_row(label, title, data.months)
            for month in data.months:
                key = (month, label)
                row[month] = round(float(stats.loc[key, field]), 2) if key in stats.index else "—"
            rows.append(row)
    return _pivot_frame(rows, data.months)


def _pivot_row(label, statistic, months) -> dict:
    """建立月份為欄的表格空列。"""
    return {"權狀坪數（不含車位）": label, "統計項目": statistic, **{month: "—" for month in months}}


def _pivot_frame(rows, months) -> pd.DataFrame:
    """確保每張月表均以月份作為欄並維持指定順序。"""
    columns = ["權狀坪數（不含車位）", "統計項目", *months]
    return pd.DataFrame(rows, columns=columns)


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
        "- 坪數級距採建物移轉面積扣除車位面積；缺少車位面積的交易不納入級距。",
        "- 室內實坪僅採官方主建物面積；附屬建物與公設不計，缺值不推估。",
        "- 總價為每坪單價乘以不含車位權狀坪數的估算值。",
        "",
    ]
    for is_presale, label in ((False, "成屋"), (True, "預售屋")):
        subset = data.frame[data.frame["is_presale"].astype(int) == int(is_presale)]
        category_data = ChartData(subset, data.months, data.intervals)
        lines.extend(_category_sections(category_data, label))
    return "\n".join(lines).rstrip() + "\n"


def _category_sections(data: ChartData, label: str) -> list[str]:
    """建立單一交易類型的交易量、總價及單價月表章節。"""
    sections = [
        f"## {label}交易量",
        "",
        _markdown_table(_volume_table(data)),
        "",
        f"## {label}估算總價（千萬元）",
        "",
        _markdown_table(_price_metric_table(
            data, "total_price_estimate", 10_000_000, "千萬元"
        )),
        "",
        f"## {label}成交單價（萬元／坪）",
        "",
        _markdown_table(_price_metric_table(
            data, "price_per_ping", 10_000, "萬元／坪"
        )),
        "",
    ]
    interior = _interior_table(data)
    if not interior.empty:
        sections.extend([
            f"## {label}室內實坪（主建物）",
            "",
            _markdown_table(interior),
            "",
        ])
    return sections


def generate_tables(settings: ChartSettings) -> Path:
    """讀取指定資料並輸出成屋、預售屋月統計 Markdown 檔案。"""
    if not settings.area.strip():
        raise ValueError("--area 不可為空")
    if settings.start > settings.end:
        raise ValueError("--start 不可晚於 --end")
    intervals = load_intervals(settings.config)
    frame = load_transactions(settings)
    frame = frame[frame["is_presale"].fillna(0).astype(int).isin([0, 1])]
    data = prepare_chart_data(
        frame, settings, intervals,
        size_field="deed_ping", price_area_field="deed_ping",
    )
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
    parser.add_argument("--output-dir", default="report/tables", help="Markdown 輸出資料夾")
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
