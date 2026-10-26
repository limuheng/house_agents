"""驗證月圖表日期、篩選、指標及輸出檔案；不觸碰正式資料庫。"""

import argparse
import tempfile
import unittest
from pathlib import Path
import warnings
from unittest.mock import patch

import matplotlib.pyplot as plt
import yaml

from tools.monthly_charts import (
    ChartSettings,
    ChartData,
    aggregate_prices,
    aggregate_volume,
    generate_charts,
    load_transactions,
    next_month,
    parse_month,
    plot_price_chart,
    plot_volume_chart,
    prepare_chart_data,
)
from matplotlib.ticker import MaxNLocator
from tools.real_estate_repository import RealEstateRepository


def _render_without_font_warnings(settings):
    """避開 CrewAI 舊 warning wrapper 與 Matplotlib 新參數不相容。"""
    return _run_with_compatible_matplotlib_warnings(lambda: generate_charts(settings))


def _run_with_compatible_matplotlib_warnings(callback):
    """執行繪圖測試時相容舊 warning wrapper 的呼叫參數。"""
    original_warn = warnings.warn

    def compatible_warn(message, category=UserWarning, stacklevel=1,
                        source=None, **kwargs):
        kwargs.pop("skip_file_prefixes", None)
        return original_warn(message, category, stacklevel=stacklevel, source=source)

    warnings.warn = compatible_warn
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return callback()
    finally:
        warnings.warn = original_warn


class MonthArgumentTests(unittest.TestCase):
    """驗證年月格式、日期邊界與參數錯誤。"""

    def test_parse_month_and_next_month(self):
        january = parse_month("2025.01")
        self.assertEqual(january.isoformat(), "2025-01-01")
        self.assertEqual(next_month(parse_month("2025.12")).isoformat(), "2026-01-01")

    def test_reject_invalid_month_format(self):
        for value in ("114.01", "2025-01", "2025.13"):
            with self.subTest(value=value), self.assertRaises(argparse.ArgumentTypeError):
                parse_month(value)


class MonthlyChartIntegrationTests(unittest.TestCase):
    """以隔離 SQLite 驗證彙總、取消篩選、標註與四張 PNG。"""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        self.database = root / "chart_test.db"
        self.output = root / "not-created-yet" / "charts"
        self.config = root / "config.yaml"
        self.config.write_text(yaml.safe_dump({
            "analysis_indicators": {"ping_intervals": [
                {"min": 0, "max": 30, "label": "0-30"},
                {"min": 30, "max": 60, "label": "30-60"},
            ]}
        }, allow_unicode=True), encoding="utf-8")
        repository = RealEstateRepository(str(self.database))
        repository.initialize()
        self._insert(repository, "2025-01-03", "苗栗縣頭份市", "North Project", 20, 30, 100000, 0, 0)
        self._insert(repository, "2025-01-10", "苗栗縣頭份市", "North Project", 20, 30, 120000, 0, 0)
        self._insert(repository, "2025-01-15", "苗栗縣頭份市", "Pre-sale", 40, 40, 250000, 1, 0)
        self._insert(repository, "2025-01-20", "苗栗縣頭份市", "Cancelled", 20, 30, 90000, 1, 1)
        self._insert(repository, "2025-01-22", "新竹縣竹北市", "Other Area", 20, 30, 99000, 0, 0)

    def tearDown(self):
        self.directory.cleanup()

    @staticmethod
    def _insert(repository, day, area, project, actual, total, price, presale, cancelled):
        with repository.transaction() as connection:
            connection.execute(
                "INSERT INTO real_estate_deals "
                "(deal_date, region, project_name, actual_ping, total_ping, "
                "price_per_ping, is_presale, is_cancelled, fingerprint) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (day, area, project, actual, total, price, presale, cancelled,
                 f"{day}-{project}"),
            )

    def _settings(self, include_cancelled=False):
        return ChartSettings(
            parse_month("2025.01"), parse_month("2025.02"), "頭份市",
            str(self.database), self.output, str(self.config), include_cancelled,
            "DejaVu Sans",
        )

    def test_parameterized_area_and_cancelled_filter(self):
        active = load_transactions(self._settings())
        inclusive = load_transactions(self._settings(include_cancelled=True))
        self.assertEqual(len(active), 3)
        self.assertEqual(len(inclusive), 4)
        self.assertTrue(active["region"].str.contains("頭份市").all())

    def test_missing_database_does_not_create_a_file(self):
        missing = Path(self.directory.name) / "missing.db"
        settings = ChartSettings(
            parse_month("2025.01"), parse_month("2025.01"), "頭份市",
            str(missing), self.output, str(self.config),
        )
        with self.assertRaises(FileNotFoundError):
            load_transactions(settings)
        self.assertFalse(missing.exists())

    def test_monthly_aggregates_and_leader_labels(self):
        settings = self._settings()
        data = prepare_chart_data(
            load_transactions(settings), settings,
            yaml.safe_load(self.config.read_text(encoding="utf-8"))[
                "analysis_indicators"]["ping_intervals"],
        )
        volume, volume_leaders = aggregate_volume(data)
        metrics, price_leaders = aggregate_prices(data)
        self.assertEqual(data.months, ["2025.01", "2025.02"])
        self.assertEqual(volume.loc["2025.01", "0-30"], 2)
        self.assertEqual(volume_leaders[("2025.01", "0-30")], ("North Project", 2))
        self.assertEqual(metrics["price_per_ping"].loc[("2025.01", "0-30"), "mean"], 110000)
        self.assertEqual(price_leaders["price_per_ping"][("2025.01", "0-30")][0], "North Project")

    def test_chart_price_units_are_converted_without_changing_aggregates(self):
        settings = self._settings()
        frame = load_transactions(settings)
        intervals = yaml.safe_load(self.config.read_text(encoding="utf-8"))[
            "analysis_indicators"]["ping_intervals"]
        data = prepare_chart_data(frame, settings, intervals)
        existing = ChartData(
            data.frame[data.frame["is_presale"] == 0].copy(), data.months, intervals
        )
        captured = []

        def capture(figure, path):
            captured.append(figure)
            return path

        with patch("tools.monthly_charts._save_figure", side_effect=capture):
            _run_with_compatible_matplotlib_warnings(
                lambda: plot_price_chart(existing, settings, False, "test")
            )
        try:
            axes = captured[0].axes
            self.assertAlmostEqual(axes[0].lines[0].get_ydata()[0], 0.33)
            self.assertAlmostEqual(axes[1].lines[0].get_ydata()[0], 11)
            self.assertEqual(axes[0].get_ylabel(), "千萬元")
            self.assertEqual(axes[1].get_ylabel(), "萬元／坪")
        finally:
            for figure in captured:
                plt.close(figure)

    def test_trade_count_chart_uses_integer_ticks(self):
        settings = self._settings()
        intervals = yaml.safe_load(self.config.read_text(encoding="utf-8"))[
            "analysis_indicators"]["ping_intervals"]
        data = prepare_chart_data(load_transactions(settings), settings, intervals)
        existing = ChartData(
            data.frame[data.frame["is_presale"] == 0].copy(), data.months, intervals
        )
        captured = []

        def capture(figure, path):
            captured.append(figure)
            return path

        with patch("tools.monthly_charts._save_figure", side_effect=capture):
            _run_with_compatible_matplotlib_warnings(
                lambda: plot_volume_chart(existing, settings, False, "test")
            )
        try:
            locator = captured[0].axes[0].yaxis.get_major_locator()
            self.assertIsInstance(locator, MaxNLocator)
            self.assertTrue(all(float(value).is_integer() for value in locator.tick_values(0, 3)))
        finally:
            for figure in captured:
                plt.close(figure)

    def test_creates_output_directory_and_four_named_pngs(self):
        outputs = _render_without_font_warnings(self._settings())
        self.assertTrue(self.output.is_dir())
        self.assertEqual(len(outputs), 4)
        self.assertEqual({path.read_bytes()[:8] for path in outputs}, {b"\x89PNG\r\n\x1a\n"})
        names = [path.name for path in outputs]
        self.assertTrue(any("existing_monthly_trade_count" in name for name in names))
        self.assertTrue(any("existing_monthly_price_stats" in name for name in names))
        self.assertTrue(any("presale_monthly_trade_count" in name for name in names))
        self.assertTrue(any("presale_monthly_price_stats" in name for name in names))
        self.assertTrue(all("頭份市_2025.01-2025.02_" in name for name in names))

    def test_empty_period_still_writes_four_charts(self):
        settings = self._settings()
        settings = ChartSettings(
            settings.start, settings.end, "不存在地區", settings.database,
            settings.output_dir, settings.config, False, settings.font,
        )
        outputs = _render_without_font_warnings(settings)
        self.assertEqual(len(outputs), 4)
        self.assertTrue(all(path.stat().st_size > 0 for path in outputs))


if __name__ == "__main__":
    unittest.main()
