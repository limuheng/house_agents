"""驗證資料轉換、SQLite 持久化及報告輸出的職責邊界。"""

import tempfile
import unittest
from pathlib import Path

from tools.real_estate_repository import RealEstateRepository
from tools.real_estate_transform import clean_hotspots, parse_transaction
from tools.report_writer import write_markdown_report


class TransformationTests(unittest.TestCase):
    """驗證純資料轉換不依賴資料庫或報告輸出。"""

    def test_parse_transaction_and_clean_hotspots(self):
        row = {
            "總價元": 12000000,
            "主建物面積": 60,
            "附屬建物面積": 10,
            "建物移轉總面積平方公尺": 120,
            "車位移轉總面積平方公尺": 10,
            "單價元平方公尺": 100000,
            "交易年月日": "1150915",
            "鄉鎮市區": "頭份市",
            "建案名稱": "測試案",
            "土地位置建物門牌": "測試路1號",
            "移轉層次": "十層",
            "備註": "",
        }
        record = parse_transaction(row, 0, "k", "苗栗縣")
        self.assertEqual(record["deal_date"], "2026-09-15")
        self.assertEqual(record["region"], "苗栗縣頭份市")
        self.assertAlmostEqual(record["deed_ping"], 33.275)
        self.assertAlmostEqual(record["main_building_ping"], 18.15)
        self.assertEqual(clean_hotspots(["苗栗縣頭份市"]), ["頭份市"])


class PersistenceTests(unittest.TestCase):
    """使用暫存資料庫驗證持久化，不觸碰專案正式資料。"""

    def test_initialize_and_upsert(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = RealEstateRepository(str(Path(directory) / "test.db"))
            repository.initialize()
            record = {
                "deal_date": "2026-09-15", "region": "苗栗縣頭份市",
                "project_name": "測試案", "building_type": "住宅大樓",
                "floor": "十層", "unit_id": "A1", "total_ping": 36.3,
                "actual_ping": 21.2, "deed_ping": 33.275,
                "main_building_ping": 18.15, "parking_price": 0,
                "price_per_ping": 330580, "is_presale": 0,
                "is_cancelled": 0, "cancelled_date": None,
                "fingerprint": "test-fingerprint",
            }
            with repository.transaction() as conn:
                self.assertTrue(repository.upsert(conn.cursor(), record))
            updated = record | {
                "legacy_fingerprint": "test-fingerprint",
                "fingerprint": "test-fingerprint_2026-09-15_A1",
            }
            with repository.transaction() as conn:
                self.assertTrue(repository.upsert(conn.cursor(), updated))
            with repository.transaction() as conn:
                count, fingerprint = conn.execute(
                    "SELECT COUNT(*), MIN(fingerprint) FROM real_estate_deals"
                ).fetchone()
            self.assertEqual(count, 1)
            self.assertEqual(fingerprint, updated["fingerprint"])


class ReportingTests(unittest.TestCase):
    """驗證報告模組只負責輸出文字內容。"""

    def test_write_markdown_report(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "report.md"
            result = write_markdown_report("# 測試報告", str(target))
            self.assertEqual(result.read_text(encoding="utf-8"), "# 測試報告")


if __name__ == "__main__":
    unittest.main()
