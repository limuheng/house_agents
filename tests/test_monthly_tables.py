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
                {"min": 30, "max": 40, "label": "30-40 坪"},
                {"min": 40, "max": 50, "label": "40-50 坪"},
                {"min": 50, "max": 60, "label": "50-60 坪"},
            ]}
        }, allow_unicode=True), encoding="utf-8")
        repository = RealEstateRepository(str(self.database))
        repository.initialize()
        self._insert(repository, "2025-01-03", "北區|建案", 52.21, 44.96, 24.5, 331500, 0)
        self._insert(repository, "2025-01-10", "北區建案", 30, 30, 20, 120000, 0)
        self._insert(repository, "2025-01-15", "預售建案", 40, 35, None, 250000, 1)

    def tearDown(self):
        self.directory.cleanup()

    @staticmethod
    def _insert(repository, day, project, total_ping, deed_ping,
                main_building_ping, price, is_presale):
        with repository.transaction() as connection:
            connection.execute(
                "INSERT INTO real_estate_deals "
                "(deal_date, region, project_name, building_type, actual_ping, total_ping, "
                "deed_ping, main_building_ping, price_per_ping, is_presale, "
                "is_cancelled, fingerprint) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)",
                (day, "苗栗縣頭份市", project, "住宅大樓(11層含以上有電梯)",
                 20, total_ping, deed_ping,
                 main_building_ping, price, is_presale, f"{day}-{project}"),
            )

    def _settings(self):
        return ChartSettings(
            parse_month("2025.01"), parse_month("2025.02"), "頭份市",
            str(self.database), self.output, str(self.config),
        )

    def test_writes_month_pivoted_tables_with_separate_price_metrics(self):
        path = generate_tables(self._settings())
        markdown = path.read_text(encoding="utf-8")

        self.assertTrue(path.is_file())
        self.assertEqual(path.suffix, ".md")
        self.assertGreaterEqual(markdown.count("| 權狀坪數（不含車位） | 統計項目 |"), 7)
        self.assertIn("## 成屋交易量", markdown)
        self.assertIn("## 成屋估算總價（千萬元）", markdown)
        self.assertIn("## 成屋成交單價（萬元／坪）", markdown)
        self.assertIn("## 預售屋交易量", markdown)
        self.assertIn("## 預售屋估算總價（千萬元）", markdown)
        self.assertIn("## 預售屋成交單價（萬元／坪）", markdown)
        self.assertIn("| 權狀坪數（不含車位） | 統計項目 | 2025.01 | 2025.02 |", markdown)
        self.assertIn("權狀坪數（不含車位）", markdown)
        self.assertIn("## 成屋室內實坪（主建物）", markdown)
        self.assertIn("40-50 坪", markdown)
        self.assertIn("1.49", markdown)
        self.assertIn("33.15", markdown)
        self.assertIn("北區\\|建案", markdown)
        existing_total_table = markdown.split("## 成屋估算總價", maxsplit=1)[1].split(
            "## 成屋成交單價", maxsplit=1
        )[0]
        existing_unit_table = markdown.split("## 成屋成交單價", maxsplit=1)[1].split(
            "## 成屋室內實坪", maxsplit=1
        )[0]
        self.assertIn("| 40-50 坪 | 交易筆數 | 1 | 0 |", existing_total_table)
        self.assertIn("| 40-50 坪 | 平均（千萬元） | 1.49 | — |", existing_total_table)
        self.assertIn("| 40-50 坪 | 平均（萬元／坪） | 33.15 | — |", existing_unit_table)
        presale_table = markdown.split("## 預售屋估算總價", maxsplit=1)[1]
        self.assertNotIn("室內實坪（主建物）", presale_table)

    def test_accepts_chart_query_arguments(self):
        args = parse_args([
            "--start", "2025.01", "--end", "2025.12", "--area", "頭份市",
            "--db", "test.db", "--output-dir", "tables", "--config", "config.yaml",
            "--include-cancelled", "--font", "Noto Sans CJK TC",
        ])
        self.assertTrue(args.include_cancelled)
        self.assertEqual(args.area, "頭份市")
        self.assertEqual(args.font, "Noto Sans CJK TC")

    def test_default_output_directory_is_tables_folder(self):
        args = parse_args([
            "--start", "2025.01", "--end", "2025.12", "--area", "頭份市",
        ])
        self.assertEqual(args.output_dir, "report/tables")


if __name__ == "__main__":
    unittest.main()
