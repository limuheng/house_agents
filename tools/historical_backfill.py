"""分季下載、轉換並回補歷史實價登錄資料。"""

from dataclasses import dataclass
from datetime import date
import os
from pathlib import Path
import time

import pandas as pd
import requests
import yaml
from crewai.tools import tool

from tools.real_estate_repository import RealEstateRepository
from tools.real_estate_tools import COUNTY_CODE_MAP, DB_PATH
from tools.real_estate_transform import clean_hotspots, parse_transaction

CONFIG_PATH = "config.yaml"
BASE_URL = "https://plvr.land.moi.gov.tw//DownloadSeason"
REQUIRED_COLUMNS = {"鄉鎮市區", "建物型態", "交易年月日", "總價元"}


@dataclass(frozen=True)
class BackfillSettings:
    """歷史回補所需的不可變設定。"""

    start_season: str
    end_season: str
    regions: tuple[str, ...]
    hotspots: tuple[str, ...]
    data_types: tuple[str, ...]
    history_dir: str


@dataclass
class BackfillSummary:
    """累計回補結果，供工具回報及驗收。"""

    downloaded: int = 0
    cached: int = 0
    unavailable: int = 0
    failed: int = 0
    inserted: int = 0

    def render(self, start: str, end: str, seasons: int) -> str:
        """輸出簡潔且可稽核的回補摘要。"""
        return (
            f"季度範圍: {start}-{end} ({seasons} 季); 成功下載: {self.downloaded}; "
            f"快取重用: {self.cached}; "
            f"官方無資料: {self.unavailable}; 失敗檔案: {self.failed}; "
            f"新增/更新交易: {self.inserted}"
        )


def parse_season(value: str) -> tuple[int, int]:
    """解析民國季度代碼，例如 109S1。"""
    try:
        year, quarter = value.upper().split("S")
        parsed = int(year), int(quarter)
    except (ValueError, AttributeError) as error:
        raise ValueError(f"季度格式錯誤: {value}，預期格式如 109S1") from error
    if parsed[0] < 1 or parsed[1] not in range(1, 5):
        raise ValueError(f"季度超出範圍: {value}")
    return parsed


def format_season(year: int, quarter: int) -> str:
    """將民國年與季度組成官方端點代碼。"""
    return f"{year:03d}S{quarter}"


def season_range(start: str, end: str) -> list[str]:
    """建立含起訖季度的連續季度清單。"""
    start_year, start_quarter = parse_season(start)
    end_year, end_quarter = parse_season(end)
    first = start_year * 4 + start_quarter - 1
    last = end_year * 4 + end_quarter - 1
    if first > last:
        raise ValueError("回補起始季度不可晚於結束季度")
    return [format_season(index // 4, index % 4 + 1)
            for index in range(first, last + 1)]


def current_season(today: date | None = None) -> str:
    """依本地日期推算目前季度，作為 latest 的搜尋上限。"""
    today = today or date.today()
    return format_season(today.year - 1911, (today.month - 1) // 3 + 1)


def load_backfill_settings(config_path: str = CONFIG_PATH) -> BackfillSettings:
    """讀取並驗證歷史回補設定與縣市代碼。"""
    with open(config_path, "r", encoding="utf-8") as stream:
        config = yaml.safe_load(stream) or {}
    settings = config.get("historical_backfill", {})
    monitoring = config.get("monitoring_settings", {})
    regions = tuple(monitoring.get("target_regions", ()))
    unknown = [region for region in regions if region not in COUNTY_CODE_MAP]
    if unknown:
        raise ValueError(f"缺少縣市代碼設定: {', '.join(unknown)}")
    data_types = tuple(settings.get("data_types", ("A", "B")))
    if not data_types or any(item not in {"A", "B"} for item in data_types):
        raise ValueError("data_types 僅接受 A、B，且不可為空")
    start = settings.get("start_season", "109S1")
    end = settings.get("end_season", "latest")
    parse_season(start)
    if end == "latest":
        end = current_season()
    parse_season(end)
    season_range(start, end)
    return BackfillSettings(
        start, end, regions, tuple(monitoring.get("hotspots", ())),
        data_types, settings.get("history_dir", "extracted_data/history")
    )


def build_download_url(season: str, county_code: str, data_type: str) -> str:
    """組合官方逐季 CSV 下載網址。"""
    parse_season(season)
    if data_type not in {"A", "B"}:
        raise ValueError(f"不支援的資料類型: {data_type}")
    filename = f"{county_code.upper()}_lvr_land_{data_type}.csv"
    return f"{BASE_URL}?season={season}&fileName={filename}"


def validate_csv_content(content: bytes) -> bool:
    """確認回應是含有官方行政區欄位的 CSV，而非錯誤頁。"""
    text = content.decode("utf-8-sig", errors="replace")
    return "鄉鎮市區" in text.splitlines()[0] if text else False


class HistoricalCsvDownloader:
    """負責 HTTP 下載、內容檢查與可續跑的本地快取。"""

    def __init__(self, session=None, retries: int = 3, pause: float = 0.5):
        self.session = session or requests
        self.retries = retries
        self.pause = pause

    def download(self, season: str, code: str, data_type: str,
                 history_dir: str) -> tuple[Path | None, str]:
        """下載單一季別檔案，回傳路徑及 downloaded/cached/unavailable。"""
        filename = f"{code.upper()}_lvr_land_{data_type}.csv"
        target = Path(history_dir) / season / filename
        if target.exists():
            self._validate_existing(target)
            return target, "cached"
        url = build_download_url(season, code, data_type)
        response = self._request(url)
        time.sleep(self.pause)
        if response is None:
            return None, "unavailable"
        self._persist_csv(target, response.content)
        return target, "downloaded"

    def _request(self, url: str):
        """重試暫時性錯誤；明確辨識官方無資料訊息。"""
        for attempt in range(self.retries):
            try:
                response = self.session.get(url, timeout=60)
                if response.status_code == 404:
                    return None
                response.raise_for_status()
                content_type = response.headers.get("Content-Type", "")
                if _is_unavailable(response.content, content_type):
                    return None
                if not validate_csv_content(response.content):
                    raise ValueError("回應不是預期的實價登錄 CSV")
                return response
            except (requests.RequestException, ValueError):
                if attempt + 1 == self.retries:
                    raise
                time.sleep(self.pause * (attempt + 1))
        return None

    def _persist_csv(self, target: Path, content: bytes) -> None:
        """先驗證再寫入暫存檔，成功後原子更名。"""
        if not validate_csv_content(content):
            raise ValueError(f"CSV 標頭驗證失敗: {target.name}")
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(target.suffix + ".part")
        temporary.write_bytes(content)
        os.replace(temporary, target)

    def _validate_existing(self, path: Path) -> None:
        """遇到無效既有快取時停止，不覆蓋使用者檔案。"""
        if not validate_csv_content(path.read_bytes()):
            raise ValueError(f"既有快取 CSV 格式錯誤，保留原檔: {path}")


def _is_unavailable(content: bytes, content_type: str = "") -> bool:
    """判斷官方回覆是否明確表示此季尚無資料。"""
    text = content.decode("utf-8-sig", errors="replace")
    if any(message in text for message in ("檔案不存在", "查無資料", "無資料")):
        return True
    is_html = "text/html" in content_type.lower() or "<html" in text.lower()
    return is_html and "系統訊息" in text and "鄉鎮市區" not in text


class HistoricalTransactionImporter:
    """將單一歷史 CSV 轉換並透過 Repository 分檔提交。"""

    def __init__(self, repository: RealEstateRepository):
        self.repository = repository

    def import_file(self, path: Path, code: str, county: str,
                    data_type: str, hotspots: tuple[str, ...]) -> int:
        """篩選熱區並寫入資料庫，回傳成功 upsert 筆數。"""
        frame = pd.read_csv(path, skiprows=[1], low_memory=False)
        missing = REQUIRED_COLUMNS.difference(frame.columns)
        if missing:
            raise ValueError(f"CSV 欄位缺漏: {', '.join(sorted(missing))}")
        filtered = self._filter_rows(frame, hotspots)
        is_presale = int(data_type == "B")
        return self._persist_rows(filtered, code, county, is_presale)

    def _filter_rows(self, frame: pd.DataFrame,
                     hotspots: tuple[str, ...]) -> pd.DataFrame:
        """維持與現行採集流程一致的熱區及建物型態篩選。"""
        clean = clean_hotspots(list(hotspots))
        selected = frame[frame["鄉鎮市區"].isin(clean)]
        return selected[selected["建物型態"].str.contains(
            "大樓|華廈|透天", na=False, regex=True
        )]

    def _persist_rows(self, frame: pd.DataFrame, code: str,
                      county: str, is_presale: int) -> int:
        """逐檔交易提交，避免單一季失敗回滾全部歷史資料。"""
        inserted = 0
        with self.repository.transaction() as connection:
            cursor = connection.cursor()
            for _, row in frame.iterrows():
                record = parse_transaction(row, is_presale, code, county)
                inserted += int(self.repository.upsert(cursor, record))
        return inserted


class HistoricalBackfillService:
    """協調歷史季度、縣市、下載與資料匯入。"""

    def __init__(self, settings: BackfillSettings, downloader=None,
                 repository=None):
        self.settings = settings
        self.downloader = downloader or HistoricalCsvDownloader()
        self.repository = repository or RealEstateRepository(DB_PATH)
        self.importer = HistoricalTransactionImporter(self.repository)

    def preview(self) -> str:
        """列出回補範圍與檔案數，不下載或寫入資料。"""
        seasons = season_range(self.settings.start_season, self.settings.end_season)
        files = len(seasons) * len(self.settings.regions) * len(self.settings.data_types)
        return (f"預覽：{self.settings.start_season} 至 {self.settings.end_season}，"
                f"{len(seasons)} 季、{len(self.settings.regions)} 縣市、"
                f"{len(self.settings.data_types)} 類型，共 {files} 個檔案；未下載/寫入。")

    def run(self) -> str:
        """執行逐季回補，單檔失敗記錄後繼續處理。"""
        seasons = season_range(self.settings.start_season, self.settings.end_season)
        self.repository.initialize()
        summary = BackfillSummary()
        for season in seasons:
            self._process_season(season, summary)
        return summary.render(
            self.settings.start_season, self.settings.end_season, len(seasons)
        )

    def _process_season(self, season: str, summary: BackfillSummary) -> None:
        """處理單季所有設定縣市與資料類型。"""
        for county in self.settings.regions:
            code = COUNTY_CODE_MAP[county]
            for data_type in self.settings.data_types:
                self._process_file(season, county, code, data_type, summary)

    def _process_file(self, season: str, county: str, code: str,
                      data_type: str, summary: BackfillSummary) -> None:
        """下載並匯入一個縣市季度檔案，更新對應摘要。"""
        try:
            path, status = self.downloader.download(
                season, code, data_type, self.settings.history_dir
            )
            if path is None:
                summary.unavailable += 1
                return
            setattr(summary, status, getattr(summary, status) + 1)
            summary.inserted += self.importer.import_file(
                path, code, county, data_type, self.settings.hotspots
            )
        except Exception as error:
            summary.failed += 1
            print(f"⚠️ 回補失敗 {season}/{code}/{data_type}: {error}")


def build_preview(config_path: str = CONFIG_PATH) -> str:
    """載入設定並產生不觸及網路或資料庫的回補預覽。"""
    settings = load_backfill_settings(config_path)
    return HistoricalBackfillService(settings).preview()


@tool("Historical Real Estate Backfill Tool")
def historical_backfill_tool() -> str:
    """逐季下載並匯入設定縣市自 2020 年起的買賣與預售歷史資料。"""
    try:
        settings = load_backfill_settings()
        return HistoricalBackfillService(settings).run()
    except Exception as error:
        return f"【歷史資料回補失敗】{error}"
