"""與 CrewAI 無關的官方 CSV 篩選、轉換及匯入流程。"""

from pathlib import Path

import pandas as pd

from tools.real_estate_repository import RealEstateRepository
from tools.real_estate_transform import (
    cancelled_fingerprints,
    clean_hotspots,
    parse_transaction,
)

REQUIRED_COLUMNS = {"鄉鎮市區", "建物型態", "交易年月日", "總價元"}


class TransactionCsvImporter:
    """共用歷史與當期 CSV 匯入器，透過 Repository 寫入資料庫。"""

    def __init__(self, repository: RealEstateRepository):
        self.repository = repository

    def import_file(self, path: Path, code: str, county: str,
                    data_type: str, hotspots: tuple[str, ...]) -> int:
        """篩選熱區資料並匯入，保留歷史回補的整數回傳介面。"""
        return self._import_file(path, code, county, data_type, hotspots, False)[0]

    def import_file_with_reconciliation(
        self, path: Path, code: str, county: str,
        data_type: str, hotspots: tuple[str, ...],
    ) -> tuple[int, int]:
        """匯入資料並在同一交易中勾稽預售解約。"""
        return self._import_file(path, code, county, data_type, hotspots, True)

    def _import_file(self, path: Path, code: str, county: str,
                     data_type: str, hotspots: tuple[str, ...],
                     reconcile: bool) -> tuple[int, int]:
        """驗證 CSV、篩選資料並以單一資料庫交易完整提交。"""
        frame = pd.read_csv(path, skiprows=[1], low_memory=False)
        missing = REQUIRED_COLUMNS.difference(frame.columns)
        if missing:
            raise ValueError(f"CSV 欄位缺漏: {', '.join(sorted(missing))}")
        selected = self._filter_rows(frame, hotspots)
        with self.repository.transaction() as connection:
            cursor = connection.cursor()
            inserted = self._persist_rows(selected, code, county, data_type, cursor)
            cancelled = self._reconcile(path, code, hotspots, cursor) if reconcile else 0
        return inserted, cancelled

    def _filter_rows(self, frame: pd.DataFrame,
                     hotspots: tuple[str, ...]) -> pd.DataFrame:
        """維持既有的熱區與建物型態篩選規則。"""
        selected = frame[frame["鄉鎮市區"].isin(clean_hotspots(list(hotspots)))]
        return selected[selected["建物型態"].str.contains(
            "大樓|華廈|透天", na=False, regex=True
        )]

    def _persist_rows(self, frame: pd.DataFrame, code: str,
                      county: str, data_type: str, cursor) -> int:
        """以單檔資料庫交易寫入標準化交易列。"""
        inserted = 0
        is_presale = int(data_type == "B")
        for _, row in frame.iterrows():
            record = parse_transaction(row, is_presale, code, county)
            inserted += int(self.repository.upsert(cursor, record))
        return inserted

    def _reconcile(self, path: Path, code: str,
                   hotspots: tuple[str, ...], cursor) -> int:
        """依預售 CSV 中的解約註記反向標記既有交易。"""
        fingerprints = cancelled_fingerprints(str(path), code, list(hotspots))
        return self.repository.mark_cancelled(cursor, fingerprints)
