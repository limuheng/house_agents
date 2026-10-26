"""依資料庫交易資料產出指定區域與月份的四種月報圖表。"""

import argparse
from dataclasses import dataclass
from datetime import date, datetime
import re
import sqlite3
from pathlib import Path
import warnings

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.ticker import MaxNLocator
import pandas as pd
import yaml

from tools.real_estate_settings import DB_PATH

CONFIG_PATH = "config.yaml"
SUPPORTED_BUILDING_TYPES = r"^(住宅大樓|華廈|公寓|透天厝)"
QUERY = """
    SELECT deal_date, region, project_name, building_type, total_ping, actual_ping,
           deed_ping, main_building_ping, price_per_ping, is_presale, is_cancelled
    FROM real_estate_deals
    WHERE deal_date >= ? AND deal_date < ? AND instr(region, ?) > 0
"""


@dataclass(frozen=True)
class ChartSettings:
    """一組繪圖作業的查詢、範圍及輸出設定。"""

    start: date
    end: date
    area: str
    database: str
    output_dir: Path
    config: str
    include_cancelled: bool = False
    font: str | None = None


@dataclass
class ChartData:
    """整理後供不同圖表共用的交易資料及期間資訊。"""

    frame: pd.DataFrame
    months: list[str]
    intervals: list[dict]


def parse_month(value: str) -> date:
    """解析西元年月 YYYY.MM，並驗證月份範圍。"""
    if not re.fullmatch(r"\d{4}\.\d{2}", value):
        raise argparse.ArgumentTypeError("年月格式須為西元 YYYY.MM，例如 2025.01")
    try:
        year, month = map(int, value.split("."))
        return date(year, month, 1)
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"無效年月: {value}") from error


def next_month(value: date) -> date:
    """回傳指定月份的下一個月首日，作為 SQL 排除上界。"""
    return date(value.year + (value.month == 12), value.month % 12 + 1, 1)


def load_intervals(config_path: str) -> list[dict]:
    """讀取並驗證設定檔坪數級距，確保可穩定分類。"""
    with open(config_path, "r", encoding="utf-8") as stream:
        config = yaml.safe_load(stream) or {}
    intervals = config.get("analysis_indicators", {}).get("ping_intervals", [])
    if not intervals:
        raise ValueError("config.yaml 缺少 analysis_indicators.ping_intervals")
    for item in intervals:
        if float(item["min"]) >= float(item["max"]) or not item.get("label"):
            raise ValueError(f"無效坪數級距設定: {item}")
    return intervals


def load_transactions(settings: ChartSettings) -> pd.DataFrame:
    """以參數化 SQL 讀取指定區域及期間的交易紀錄。"""
    if not Path(settings.database).is_file():
        raise FileNotFoundError(f"找不到 SQLite 資料庫: {settings.database}")
    end_exclusive = next_month(settings.end)
    with sqlite3.connect(settings.database) as connection:
        frame = pd.read_sql_query(
            QUERY, connection,
            params=(settings.start.isoformat(), end_exclusive.isoformat(), settings.area),
        )
    building_types = frame["building_type"].fillna("").astype(str).str.strip()
    frame = frame[building_types.str.match(SUPPORTED_BUILDING_TYPES)].copy()
    if not settings.include_cancelled and not frame.empty:
        frame = frame[frame["is_cancelled"].fillna(0).astype(int) == 0].copy()
    return frame


def prepare_chart_data(frame: pd.DataFrame, settings: ChartSettings,
                       intervals: list[dict], size_field: str = "actual_ping",
                       price_area_field: str = "total_ping") -> ChartData:
    """新增月份、坪數級距、估算總價及可標註名稱欄位。"""
    months = [period.strftime("%Y.%m") for period in pd.period_range(
        settings.start, settings.end, freq="M"
    )]
    result = frame.copy()
    if result.empty:
        result = _empty_chart_frame(result)
    else:
        result["month"] = pd.to_datetime(result["deal_date"]).dt.strftime("%Y.%m")
        result["size_label"] = _assign_intervals(result[size_field], intervals)
        result["total_price_estimate"] = result["price_per_ping"] * result[price_area_field]
        result["plot_name"] = _plot_names(result)
        result = result[result["size_label"].notna()].copy()
    return ChartData(result, months, intervals)


def _empty_chart_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """為空查詢補齊繪圖階段會使用的欄位。"""
    for column in ("month", "size_label", "plot_name"):
        frame[column] = pd.Series(dtype="object")
    frame["total_price_estimate"] = pd.Series(dtype="float64")
    return frame


def _assign_intervals(values: pd.Series, intervals: list[dict]) -> pd.Series:
    """依設定級距分類實際室內坪數，未涵蓋的坪數不納入圖表。"""
    labels = pd.Series(index=values.index, dtype="object")
    numeric = pd.to_numeric(values, errors="coerce")
    for item in intervals:
        matches = (numeric >= float(item["min"])) & (numeric < float(item["max"]))
        labels.loc[matches & labels.isna()] = item["label"]
    return labels


def _plot_names(frame: pd.DataFrame) -> pd.Series:
    """優先標示建案名稱；缺少名稱時退回行政區域。"""
    names = frame["project_name"].fillna("").astype(str).str.strip()
    return names.where(names.ne(""), frame["region"].fillna("未標示"))


def aggregate_volume(data: ChartData) -> tuple[pd.DataFrame, dict]:
    """計算每月級距筆數，並取得筆數最多的建案／區域。"""
    frame = data.frame
    counts = frame.groupby(["month", "size_label"]).size()
    leaders = {}
    for key, group in frame.groupby(["month", "size_label"]):
        ranking = group["plot_name"].value_counts()
        leaders[key] = (str(ranking.index[0]), int(ranking.iloc[0]))
    grid = pd.MultiIndex.from_product(
        [data.months, [item["label"] for item in data.intervals]],
        names=["month", "size_label"],
    )
    return counts.reindex(grid, fill_value=0).unstack(fill_value=0), leaders


def aggregate_prices(data: ChartData) -> tuple[dict, dict]:
    """計算總價估算及每坪單價的月平均、中位數與最高價交易。"""
    values = ("total_price_estimate", "price_per_ping")
    metrics, leaders = {}, {}
    frame = data.frame
    for field in values:
        valid = frame[pd.to_numeric(frame[field], errors="coerce") > 0]
        grouped = valid.groupby(["month", "size_label"])[field]
        metrics[field] = grouped.agg(mean="mean", median="median")
        leaders[field] = _price_leaders(valid, field)
    return metrics, leaders


def _price_leaders(frame: pd.DataFrame, field: str) -> dict:
    """取每月級距最高成交價的建案或區域標籤。"""
    leaders = {}
    for key, group in frame.groupby(["month", "size_label"]):
        row = group.loc[group[field].idxmax()]
        leaders[key] = (str(row["plot_name"]), float(row[field]))
    return leaders


def configure_font(font_name: str | None = None) -> None:
    """選擇系統字型或指定字型檔；缺少中文字型時提出警告。"""
    if font_name and Path(font_name).is_file():
        font_manager.fontManager.addfont(font_name)
        font_name = font_manager.FontProperties(fname=font_name).get_name()
    candidates = [font_name] if font_name else [
        "Noto Sans CJK TC", "Noto Sans CJK SC", "Microsoft JhengHei",
        "PingFang TC", "WenQuanYi Zen Hei",
    ]
    available = {font.name for font in font_manager.fontManager.ttflist}
    selected = next((name for name in candidates if name in available), None)
    if selected:
        plt.rcParams["font.family"] = selected
    elif font_name:
        raise ValueError(f"找不到指定字型: {font_name}")
    else:
        warnings.warn("未找到中文字型；圖表中文可能無法正確顯示。可用 --font 指定字型。")
    plt.rcParams["axes.unicode_minus"] = False


def _save_figure(figure, path: Path) -> Path:
    """輸出 PNG 並釋放 Matplotlib 資源。"""
    try:
        figure.savefig(path, dpi=160, bbox_inches="tight")
    finally:
        plt.close(figure)
    return path


def plot_volume_chart(data: ChartData, settings: ChartSettings,
                      is_presale: bool, stamp: str) -> Path:
    """繪製月交易量分組長條圖並標註各組交易量領先建案。"""
    table, leaders = aggregate_volume(data)
    labels = [item["label"] for item in data.intervals]
    figure, axis = plt.subplots(figsize=(max(12, len(data.months) * 1.1), 8))
    positions = range(len(data.months))
    width = 0.8 / max(len(labels), 1)
    for index, label in enumerate(labels):
        offset = (index - (len(labels) - 1) / 2) * width
        bars = axis.bar([x + offset for x in positions], table[label], width, label=label)
        _annotate_volume(axis, bars, data.months, label, leaders)
    return _finish_volume_chart(figure, axis, data, settings, is_presale, stamp)


def _annotate_volume(axis, bars, months: list[str], label: str,
                     leaders: dict) -> None:
    """在非零長條上標示該月級距交易量最高的建案及筆數。"""
    for month, bar in zip(months, bars):
        count = int(bar.get_height())
        leader = leaders.get((month, label))
        if count and leader:
            name, leader_count = leader
            axis.annotate(
                f"{name[:8]}:{leader_count}",
                (bar.get_x() + bar.get_width() / 2, count),
                xytext=(0, 3), textcoords="offset points", ha="center",
                va="bottom", rotation=75, fontsize=6,
            )


def _finish_volume_chart(figure, axis, data: ChartData,
                         settings: ChartSettings, is_presale: bool,
                         stamp: str) -> Path:
    """完成交易量圖標題、圖例及檔案輸出。"""
    label = "預售屋" if is_presale else "成屋"
    cancellation_note = "含解約" if settings.include_cancelled else "排除解約"
    axis.set_title(f"{settings.area} {label}每月各坪數級距交易數量（{cancellation_note}）")
    axis.set_xlabel("交易月份")
    axis.set_ylabel("交易筆數")
    axis.yaxis.set_major_locator(MaxNLocator(integer=True))
    axis.set_xticks(range(len(data.months)), data.months, rotation=45, ha="right")
    axis.legend(title="實際室內坪數")
    axis.grid(axis="y", alpha=0.25)
    if not data.frame[data.frame["is_presale"] == int(is_presale)].shape[0]:
        axis.text(0.5, 0.5, "期間內無符合交易資料", transform=axis.transAxes, ha="center")
    figure.tight_layout()
    return _save_figure(figure, _output_path(settings, _chart_kind(is_presale, "monthly_trade_count"), stamp))


def plot_price_chart(data: ChartData, settings: ChartSettings,
                     is_presale: bool, stamp: str) -> Path:
    """繪製總價估算與每坪單價的平均、中位數月趨勢及最高價標註。"""
    metrics, leaders = aggregate_prices(data)
    figure, axes = plt.subplots(2, 1, figsize=(max(12, len(data.months) * 1.1), 12))
    labels = [item["label"] for item in data.intervals]
    _plot_price_metric(axes[0], data, labels, metrics["total_price_estimate"],
                       leaders["total_price_estimate"], "估算成交總價", "千萬元", 10_000_000)
    _plot_price_metric(axes[1], data, labels, metrics["price_per_ping"],
                       leaders["price_per_ping"], "成交單價", "萬元／坪", 10_000)
    label = "預售屋" if is_presale else "成屋"
    cancellation_note = "含解約" if settings.include_cancelled else "排除解約"
    figure.suptitle(
        f"{settings.area} {label}每月價格統計（平均／中位數；{cancellation_note}）\n"
        "估算總價 = 每坪單價 × 權狀總坪數"
    )
    figure.tight_layout()
    return _save_figure(figure, _output_path(settings, _chart_kind(is_presale, "monthly_price_stats"), stamp))


def _plot_price_metric(axis, data: ChartData, labels: list[str],
                       metric: pd.DataFrame, leaders: dict,
                       title: str, unit: str, divisor: int) -> None:
    """繪製單一價格指標的平均、中位數線及最高價交易標籤。"""
    for label in labels:
        means, medians = [], []
        for month in data.months:
            values = metric.loc[(month, label)] if (month, label) in metric.index else None
            means.append(values["mean"] / divisor if values is not None else float("nan"))
            medians.append(values["median"] / divisor if values is not None else float("nan"))
        line, = axis.plot(data.months, means, marker="o", label=f"{label} 平均")
        axis.plot(data.months, medians, marker="s", linestyle="--",
                  color=line.get_color(), label=f"{label} 中位數")
        _annotate_price_leaders(axis, data.months, label, leaders, divisor)
    axis.set_title(f"{title}（{unit}）")
    axis.set_ylabel(unit)
    axis.tick_params(axis="x", rotation=45)
    axis.grid(alpha=0.25)
    axis.legend(fontsize=7, ncol=2)
    if not data.frame.empty:
        return
    axis.text(0.5, 0.5, "期間內無符合交易資料", transform=axis.transAxes, ha="center")


def _annotate_price_leaders(axis, months: list[str], label: str,
                            leaders: dict, divisor: int) -> None:
    """標示每月級距最高價交易的建案或行政區。"""
    for month in months:
        leader = leaders.get((month, label))
        if leader:
            axis.annotate(leader[0][:8], (month, leader[1] / divisor), fontsize=6,
                          xytext=(3, 4), textcoords="offset points", rotation=45)


def _chart_kind(is_presale: bool, purpose: str) -> str:
    """組合成屋／預售屋與圖表目的名稱。"""
    category = "presale" if is_presale else "existing"
    return f"{category}_{purpose}"


def _safe_area(area: str) -> str:
    """移除檔名不允許字元，保留中文、英數與連字號。"""
    return re.sub(r"[^\w-]+", "_", area, flags=re.UNICODE).strip("_") or "area"


def _output_path(settings: ChartSettings, purpose: str, stamp: str) -> Path:
    """建立輸出資料夾並組合含期間與產圖時間的唯一檔名。"""
    settings.output_dir.mkdir(parents=True, exist_ok=True)
    area = _safe_area(settings.area)
    start = settings.start.strftime("%Y.%m")
    end = settings.end.strftime("%Y.%m")
    filename = f"{purpose}_{area}_{start}-{end}_{stamp}.png"
    return settings.output_dir / filename


def generate_charts(settings: ChartSettings) -> list[Path]:
    """載入資料並產生買賣類型各自的交易量及價格圖。"""
    if not settings.area.strip():
        raise ValueError("--area 不可為空")
    if settings.start > settings.end:
        raise ValueError("--start 不可晚於 --end")
    intervals = load_intervals(settings.config)
    frame = load_transactions(settings)
    frame = frame[frame["is_presale"].fillna(0).astype(int).isin([0, 1])]
    data = prepare_chart_data(frame, settings, intervals)
    configure_font(settings.font)
    stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S_%f")
    outputs = []
    for is_presale in (False, True):
        subset = data.frame[data.frame["is_presale"].astype(int) == int(is_presale)].copy()
        category_data = ChartData(subset, data.months, intervals)
        outputs.append(plot_volume_chart(category_data, settings, is_presale, stamp))
        outputs.append(plot_price_chart(category_data, settings, is_presale, stamp))
    return outputs


def parse_args(argv=None):
    """解析月份區間、地區、資料庫、輸出及繪圖字型參數。"""
    parser = argparse.ArgumentParser(description="繪製指定區域的月房市統計圖")
    parser.add_argument("--start", required=True, type=parse_month, help="起始年月，例如 2025.01")
    parser.add_argument("--end", required=True, type=parse_month, help="結束年月，例如 2025.12")
    parser.add_argument("--area", required=True, help="行政區關鍵字，例如 頭份市")
    parser.add_argument("--db", default=DB_PATH, help="SQLite 資料庫路徑")
    parser.add_argument("--output-dir", default="report/charts", help="圖表輸出資料夾")
    parser.add_argument("--config", default=CONFIG_PATH, help="坪數級距設定檔")
    parser.add_argument("--include-cancelled", action="store_true", help="將已標記解約交易納入統計")
    parser.add_argument("--font", help="系統中文字型名稱，例如 Noto Sans CJK TC")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    """執行 CLI 並回報產出的 PNG 路徑；錯誤時回傳非零狀態。"""
    args = parse_args(argv)
    try:
        settings = ChartSettings(
            args.start, args.end, args.area, args.db, Path(args.output_dir),
            args.config, args.include_cancelled, args.font,
        )
        for path in generate_charts(settings):
            print(path)
        return 0
    except Exception as error:
        print(f"【圖表產生失敗】{error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
