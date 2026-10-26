"""不透過 Agent 下載並匯入內政部最新實價登錄資料。"""

import argparse
import csv
from dataclasses import dataclass
from io import StringIO
import os
from pathlib import Path
import tempfile
import time

import requests
import yaml

from tools.real_estate_repository import RealEstateRepository
from tools.real_estate_settings import COUNTY_CODE_MAP, DB_PATH
from tools.transaction_importer import TransactionCsvImporter

BASE_URL = "https://plvr.land.moi.gov.tw/Download"
REQUIRED_HEADERS = {"鄉鎮市區", "建物型態", "交易年月日", "總價元"}


@dataclass(frozen=True)
class CurrentUpdateSettings:
    """當期下載、篩選及持久化所需設定。"""

    regions: tuple[str, ...]
    hotspots: tuple[str, ...]
    data_dir: str = "extracted_data/current"


@dataclass
class UpdateSummary:
    """累計當期資料下載及匯入結果。"""

    downloaded: int = 0
    unavailable: int = 0
    failed: int = 0
    inserted: int = 0
    cancelled: int = 0

    def render(self) -> str:
        """輸出本次工作的明確結果摘要。"""
        return (f"下載成功: {self.downloaded}; 官方無資料: {self.unavailable}; "
                f"失敗: {self.failed}; 新增/更新交易: {self.inserted}; "
                f"解約標記: {self.cancelled}")


def load_current_settings(config_path: str = "config.yaml") -> CurrentUpdateSettings:
    """讀取監控區域及當期資料保存位置，拒絕未知縣市。"""
    with open(config_path, "r", encoding="utf-8") as stream:
        config = yaml.safe_load(stream) or {}
    monitoring = config.get("monitoring_settings", {})
    regions = tuple(monitoring.get("target_regions", ()))
    unknown = [region for region in regions if region not in COUNTY_CODE_MAP]
    if not regions:
        raise ValueError("monitoring_settings.target_regions 不可為空")
    if unknown:
        raise ValueError(f"監控縣市無效或缺少代碼: {', '.join(unknown)}")
    current = config.get("current_update", {})
    return CurrentUpdateSettings(
        regions=regions,
        hotspots=tuple(monitoring.get("hotspots", ())),
        data_dir=current.get("data_dir", "extracted_data/current"),
    )


def validate_csv_content(content: bytes) -> bool:
    """僅接受首列包含必要官方欄位的 CSV，拒絕錯誤頁面。"""
    first_line = content.decode("utf-8-sig", errors="replace").splitlines()
    if not first_line:
        return False
    headers = set(next(csv.reader(StringIO(first_line[0]))))
    return REQUIRED_HEADERS.issubset(headers)


def is_officially_unavailable(content: bytes) -> bool:
    """辨識官方明確回覆無資料的頁面。"""
    text = content.decode("utf-8-sig", errors="replace")
    return any(message in text for message in ("檔案不存在", "查無資料", "無資料"))


class CurrentCsvDownloader:
    """下載並驗證最新 CSV；僅在完整驗證後原子更新當期快取。"""

    def __init__(self, session=None, retries: int = 3, pause: float = 1.0):
        self.session = session or requests
        self.retries = retries
        self.pause = pause

    def download(self, code: str, data_type: str,
                 data_dir: str) -> tuple[Path | None, str]:
        """下載一個縣市及類型，回傳路徑與 downloaded/unavailable。"""
        filename = f"{code.lower()}_lvr_land_{data_type.lower()}.csv"
        target = Path(data_dir) / filename
        url = f"{BASE_URL}?fileName={filename}"
        response = self._request(url)
        if response is None:
            return None, "unavailable"
        self._persist(target, response.content)
        return target, "downloaded"

    def _request(self, url: str):
        """使用標準 TLS 驗證下載，重試暫時性 HTTP 或內容錯誤。"""
        for attempt in range(self.retries):
            try:
                response = self.session.get(url, timeout=60)
                if response.status_code == 404:
                    return None
                response.raise_for_status()
                if is_officially_unavailable(response.content):
                    return None
                if not validate_csv_content(response.content):
                    raise ValueError("官方回應不是有效實價登錄 CSV")
                return response
            except (requests.RequestException, ValueError):
                if attempt + 1 == self.retries:
                    raise
                time.sleep(self.pause * (attempt + 1))
        return None

    def _persist(self, target: Path, content: bytes) -> None:
        """先寫入同目錄暫存檔，再原子替換通過驗證的快取。"""
        if not validate_csv_content(content):
            raise ValueError(f"CSV 標頭驗證失敗: {target.name}")
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=target.parent, suffix=".part", delete=False
            ) as temporary:
                temporary.write(content)
                temporary_path = temporary.name
            os.replace(temporary_path, target)
        finally:
            if temporary_path and os.path.exists(temporary_path):
                os.unlink(temporary_path)


class CurrentDataUpdateService:
    """協調官方最新資料下載、共用匯入及結果統計。"""

    def __init__(self, settings: CurrentUpdateSettings, database_path: str = DB_PATH,
                 downloader=None):
        self.settings = settings
        self.repository = RealEstateRepository(database_path)
        self.downloader = downloader or CurrentCsvDownloader()
        self.importer = TransactionCsvImporter(self.repository)

    def run(self) -> str:
        """逐縣市下載 A/B 檔並匯入，單檔失敗不阻斷其他檔案。"""
        self.repository.initialize()
        summary = UpdateSummary()
        for county in self.settings.regions:
            for data_type in ("A", "B"):
                self._process_file(county, data_type, summary)
        return summary.render()

    def _process_file(self, county: str, data_type: str,
                      summary: UpdateSummary) -> None:
        """處理單一縣市類型，並累計下載、匯入或錯誤狀態。"""
        code = COUNTY_CODE_MAP[county]
        try:
            path, _status = self.downloader.download(
                code, data_type, self.settings.data_dir
            )
            if path is None:
                summary.unavailable += 1
                return
            summary.downloaded += 1
            inserted, cancelled = self._import(path, code, county, data_type)
            summary.inserted += inserted
            summary.cancelled += cancelled
        except Exception as error:
            summary.failed += 1
            print(f"⚠️ 當期資料處理失敗 {county}/{data_type}: {error}")

    def _import(self, path: Path, code: str, county: str,
                data_type: str) -> tuple[int, int]:
        """共用一般匯入；預售資料額外反向勾稽解約。"""
        args = (path, code, county, data_type, self.settings.hotspots)
        if data_type == "B":
            return self.importer.import_file_with_reconciliation(*args)
        return self.importer.import_file(*args), 0


def parse_args(argv=None):
    """解析獨立執行入口的設定檔與資料庫選項。"""
    parser = argparse.ArgumentParser(description="下載並匯入最新實價登錄資料")
    parser.add_argument("--config", default="config.yaml", help="設定檔路徑")
    parser.add_argument("--db", default=DB_PATH, help="SQLite 資料庫路徑")
    parser.add_argument("--data-dir", help="CSV 保存路徑，預設讀取 config.yaml")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    """執行不依賴 CrewAI 的當期下載及匯入流程。"""
    args = parse_args(argv)
    try:
        settings = load_current_settings(args.config)
        if args.data_dir:
            settings = CurrentUpdateSettings(
                settings.regions, settings.hotspots, args.data_dir
            )
        print(CurrentDataUpdateService(settings, args.db).run())
        return 0
    except Exception as error:
        print(f"【當期資料更新失敗】{error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
