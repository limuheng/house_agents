"""隔離驗證當期 CSV 下載、更新與資料庫匯入。"""

import tempfile
import unittest
from pathlib import Path

from tools.current_update import (
    CurrentCsvDownloader,
    CurrentDataUpdateService,
    CurrentUpdateSettings,
)
from tools.real_estate_repository import RealEstateRepository

CSV_HEADER = (
    "鄉鎮市區,交易標的,土地位置建物門牌,交易年月日,移轉層次,建物型態,"
    "建物移轉總面積平方公尺,總價元,單價元平方公尺,車位總價元,備註,"
    "編號,主建物面積,附屬建物面積,建案名稱\n"
)
CSV_ROW = (
    "竹北市,房地,測試路1號,1150101,十層,住宅大樓,100,10000000,"
    "100000,0,,1,60,10,測試案\n"
)
VALID_CSV = (CSV_HEADER + CSV_HEADER + CSV_ROW).encode("utf-8")


class FakeResponse:
    """模擬 requests 回應，不連線外部服務。"""

    status_code = 200

    def __init__(self, content):
        self.content = content

    def raise_for_status(self):
        return None


class FakeSession:
    """依序提供預設回應並記錄官方請求。"""

    def __init__(self, content):
        self.content = content
        self.urls = []

    def get(self, url, timeout):
        self.urls.append(url)
        return FakeResponse(self.content)


class CurrentDownloadTests(unittest.TestCase):
    """確保下載只原子保存已驗證內容。"""

    def test_valid_csv_download_and_unavailable_response(self):
        session = FakeSession(VALID_CSV)
        with tempfile.TemporaryDirectory() as directory:
            path, status = CurrentCsvDownloader(session, pause=0).download(
                "k", "A", directory
            )
            missing_path, missing_status = CurrentCsvDownloader(
                FakeSession("檔案不存在".encode()), pause=0
            ).download("k", "B", directory)
            self.assertEqual(path.read_bytes(), VALID_CSV)
        self.assertEqual(status, "downloaded")
        self.assertIsNone(missing_path)
        self.assertEqual(missing_status, "unavailable")
        self.assertIn("k_lvr_land_a.csv", session.urls[0])

    def test_invalid_response_does_not_replace_existing_csv(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "k_lvr_land_a.csv"
            target.write_bytes(b"user data")
            downloader = CurrentCsvDownloader(FakeSession(b"error page"), retries=1)
            with self.assertRaises(ValueError):
                downloader.download("k", "A", directory)
            self.assertEqual(target.read_bytes(), b"user data")


class CurrentUpdateTests(unittest.TestCase):
    """確認不依賴 CrewAI 的服務可寫入隔離 SQLite 並安全重跑。"""

    def test_service_imports_current_county_data_idempotently(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = CurrentUpdateSettings(
                ("新竹縣",), ("竹北市",), str(Path(directory) / "csv")
            )
            repository = RealEstateRepository(str(Path(directory) / "test.db"))
            first = CurrentDataUpdateService(
                settings, repository.database_path,
                CurrentCsvDownloader(FakeSession(VALID_CSV), pause=0),
            ).run()
            second = CurrentDataUpdateService(
                settings, repository.database_path,
                CurrentCsvDownloader(FakeSession(VALID_CSV), pause=0),
            ).run()
            with repository.transaction() as connection:
                count = connection.execute(
                    "SELECT COUNT(*) FROM real_estate_deals"
                ).fetchone()[0]
        self.assertIn("下載成功: 2", first)
        self.assertIn("下載成功: 2", second)
        self.assertIn("失敗: 0", first)
        self.assertIn("失敗: 0", second)
        self.assertEqual(count, 1)


if __name__ == "__main__":
    unittest.main()
