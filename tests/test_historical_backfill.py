"""歷史回補季度、下載驗證與隔離匯入測試。"""

import tempfile
import unittest
from pathlib import Path

from tools.historical_backfill import (
    BackfillSettings,
    HistoricalBackfillService,
    HistoricalCsvDownloader,
    HistoricalTransactionImporter,
    build_download_url,
    season_range,
    validate_csv_content,
)
from tools.real_estate_repository import RealEstateRepository


CSV_HEADER = (
    "鄉鎮市區,交易標的,土地位置建物門牌,交易年月日,移轉層次,建物型態,"
    "建物移轉總面積平方公尺,總價元,單價元平方公尺,車位總價元,備註,"
    "編號,主建物面積,附屬建物面積,建案名稱\n"
)


class FakeResponse:
    """提供 requests.Response 測試替身。"""

    status_code = 200

    def __init__(self, content, content_type="application/octet-stream"):
        self.content = content
        self.headers = {"Content-Type": content_type}

    def raise_for_status(self):
        return None


class FakeSession:
    """記錄下載網址並回傳預先指定內容。"""

    def __init__(self, content, content_type="application/octet-stream"):
        self.content = content
        self.content_type = content_type
        self.urls = []

    def get(self, url, timeout):
        self.urls.append(url)
        return FakeResponse(self.content, self.content_type)


class SeasonTests(unittest.TestCase):
    """驗證季度序列與官方 URL 格式。"""

    def test_season_range_and_url(self):
        self.assertEqual(season_range("109S1", "109S4"), [
            "109S1", "109S2", "109S3", "109S4"
        ])
        self.assertEqual(
            build_download_url("109S1", "k", "A"),
            "https://plvr.land.moi.gov.tw//DownloadSeason?"
            "season=109S1&fileName=K_lvr_land_A.csv"
        )

    def test_invalid_season_range(self):
        with self.assertRaises(ValueError):
            season_range("110S1", "109S4")


class DownloadTests(unittest.TestCase):
    """驗證 CSV 回應檢查、快取及錯誤頁拒絕。"""

    def test_download_valid_csv_and_reuse_cache(self):
        content = (CSV_HEADER + CSV_HEADER).encode("utf-8")
        session = FakeSession(content)
        with tempfile.TemporaryDirectory() as directory:
            downloader = HistoricalCsvDownloader(session=session, pause=0)
            path, status = downloader.download("109S1", "k", "A", directory)
            cached_path, cached_status = downloader.download(
                "109S1", "k", "A", directory
            )
        self.assertEqual(status, "downloaded")
        self.assertEqual(cached_status, "cached")
        self.assertEqual(path, cached_path)
        self.assertEqual(len(session.urls), 1)

    def test_reject_non_csv_content(self):
        self.assertFalse(validate_csv_content(b"system error"))

    def test_empty_official_system_page_is_unavailable(self):
        html = b'<td class="text_bg">\xe7\xb3\xbb\xe7\xb5\xb1\xe8\xa8\x8a\xe6\x81\xaf</td>'
        session = FakeSession(html, "text/html;charset=UTF-8")
        with tempfile.TemporaryDirectory() as directory:
            downloader = HistoricalCsvDownloader(session=session, pause=0)
            path, status = downloader.download("109S1", "k", "B", directory)
        self.assertIsNone(path)
        self.assertEqual(status, "unavailable")
        self.assertEqual(len(session.urls), 1)


class ImportTests(unittest.TestCase):
    """以暫存 CSV/SQLite 驗證回補重跑冪等。"""

    def test_import_same_file_twice(self):
        row = [
            "竹北市", "房地", "測試路1號", "1090215", "十層", "住宅大樓",
            "100", "10000000", "100000", "0", "", "1", "60", "10", "測試案"
        ]
        second_row = row.copy()
        second_row[3] = "1090515"
        csv_text = (CSV_HEADER + CSV_HEADER + ",".join(row) + "\n"
                    + ",".join(second_row) + "\n")
        with tempfile.TemporaryDirectory() as directory:
            csv_path = Path(directory) / "K_lvr_land_A.csv"
            csv_path.write_text(csv_text, encoding="utf-8")
            repository = RealEstateRepository(str(Path(directory) / "test.db"))
            repository.initialize()
            importer = HistoricalTransactionImporter(repository)
            first = importer.import_file(csv_path, "k", "新竹縣", "A", ("竹北市",))
            second = importer.import_file(csv_path, "k", "新竹縣", "A", ("竹北市",))
            with repository.transaction() as conn:
                count = conn.execute("SELECT COUNT(*) FROM real_estate_deals").fetchone()[0]
        self.assertEqual(first, 2)
        self.assertEqual(second, 2)
        self.assertEqual(count, 2)


class PreviewTests(unittest.TestCase):
    """確認預覽僅計算範圍，不呼叫下載或資料庫。"""

    def test_preview_file_count(self):
        settings = BackfillSettings(
            "109S1", "109S2", ("苗栗縣",), ("頭份市",),
            ("A", "B"), "unused"
        )
        result = HistoricalBackfillService(settings).preview()
        self.assertIn("共 4 個檔案", result)
        self.assertIn("未下載/寫入", result)


if __name__ == "__main__":
    unittest.main()
