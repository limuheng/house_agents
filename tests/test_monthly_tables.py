"""驗證月統計 Markdown 表格輸出，僅使用隔離 SQLite 資料庫。"""

import tempfile
import unittest
from pathlib import Path

import yaml

from tools.monthly_charts import ChartSettings, parse_month
from tools.monthly_tables import generate_tables, parse_args
from tools.real_estate_repository import RealEstateRepository


class MonthlyTableTests(unittest.TestCase):
    """以臨時資料庫驗證統計分類、單位及命令列參數。"""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        self.database = root / "tables_test.db"
        self.output = root / "tables"
        self.config = root / "config.yaml"
        self.config.write_text(yaml.safe_dump({
            "analysis_indicators": {"ping_intervals": [
                {"min": 0, "max": 30, "label": "0-30 坪"},
                {"min": 30, "max": 60, "label": "30-60 坪"},
            ]}
        }, allow_unicode=True), encoding="utf-8")
        repository = RealEstateRepository(str(self.database))
        repository.initialize()
        self._insert(repository, "2025-01-03", "北區|建案", 30, 100000, 0)
        self._insert(repository, "2025-01-10", "北區建案", 30, 120000, 0)
        self._insert(repository, "2025-01-15", "預售建案", 40, 250000, 1)

    def tearDown(self):
        self.directory.cleanup()

    @staticmethod
    def _insert(repository, day, project, total_ping, price, is_presale):
        with repository.transaction() as connection:
            connection.execute(
                "INSERT INTO real_estate_deals "
                "(deal_date, region, project_name, actual_ping, total_ping, "
                "price_per_ping, is_presale, is_cancelled, fingerprint) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?)",
                (day, "苗栗縣頭份市", project, 20, total_ping, price,
                 is_presale, f"{day}-{project}"),
            )

    def _settings(self):
        return ChartSettings(
            parse_month("2025.01"), parse_month("2025.02"), "頭份市",
            str(self.database), self.output, str(self.config),
        )

    def test_writes_four_markdown_tables_with_units_and_escaped_cells(self):
        path = generate_tables(self._settings())
        markdown = path.read_text(encoding="utf-8")

        self.assertTrue(path.is_file())
        self.assertEqual(path.suffix, ".md")
        self.assertEqual(markdown.count("| 月份 |"), 4)
        self.assertIn("## 成屋交易量", markdown)
        self.assertIn("## 成屋價格統計", markdown)
        self.assertIn("## 預售屋交易量", markdown)
        self.assertIn("## 預售屋價格統計", markdown)
        self.assertIn("總價平均（千萬元）", markdown)
        self.assertIn("單價平均（萬元／坪）", markdown)
        self.assertIn("0.33", markdown)
        self.assertIn("11.0", markdown)
        self.assertIn("北區\\|建案", markdown)
        self.assertIn("| 交易筆數 |", markdown)

    def test_accepts_chart_query_arguments(self):
        args = parse_args([
            "--start", "2025.01", "--end", "2025.12", "--area", "頭份市",
            "--db", "test.db", "--output-dir", "tables", "--config", "config.yaml",
            "--include-cancelled", "--font", "Noto Sans CJK TC",
        ])
        self.assertTrue(args.include_cancelled)
        self.assertEqual(args.area, "頭份市")
        self.assertEqual(args.font, "Noto Sans CJK TC")


if __name__ == "__main__":
    unittest.main()
