"""實價登錄資料的 SQLite 持久化介面。"""

import sqlite3
from contextlib import contextmanager


class RealEstateRepository:
    """封裝資料表初始化與交易資料寫入。"""

    def __init__(self, database_path: str):
        self.database_path = database_path

    def connect(self):
        """建立 SQLite 連線，呼叫端負責關閉。"""
        return sqlite3.connect(self.database_path)

    @contextmanager
    def transaction(self):
        """提供具交易提交與自動關閉語意的資料庫工作區。"""
        conn = self.connect()
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def initialize(self) -> None:
        """建立交易表及查詢索引，保留既有 schema。"""
        with self.transaction() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS real_estate_deals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    deal_date TEXT NOT NULL, region TEXT NOT NULL,
                    project_name TEXT, building_type TEXT, floor TEXT,
                    unit_id TEXT, total_ping REAL, actual_ping REAL,
                    deed_ping REAL, main_building_ping REAL,
                    parking_price INTEGER, price_per_ping INTEGER,
                    house_age REAL, is_presale BOOLEAN DEFAULT 0,
                    is_cancelled BOOLEAN DEFAULT 0, cancelled_date TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    fingerprint TEXT UNIQUE
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_region ON real_estate_deals(region)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_project ON real_estate_deals(project_name)")

    def upsert(self, cursor, record: dict) -> bool:
        """新增或更新交易，不清除已存在的解約標記。"""
        if not record:
            return False
        try:
            self._migrate_legacy_fingerprint(cursor, record)
            cursor.execute(_UPSERT_SQL, _record_values(record))
            return cursor.rowcount > 0
        except sqlite3.Error:
            return False

    def _migrate_legacy_fingerprint(self, cursor, record: dict) -> None:
        """首次遇到舊指紋時升級為含交易日期的指紋。"""
        legacy = record.get("legacy_fingerprint")
        if not legacy or legacy == record["fingerprint"]:
            return
        try:
            cursor.execute(
                "UPDATE real_estate_deals SET fingerprint=? WHERE fingerprint=?",
                (record["fingerprint"], legacy)
            )
        except sqlite3.IntegrityError:
            # 新指紋若已存在，保留舊列並由後續 Upsert 更新新列。
            return

    def mark_cancelled(self, cursor, fingerprints: list[str]) -> int:
        """依指紋標記解約交易並回傳更新筆數。"""
        changed = 0
        for fingerprint in fingerprints:
            cursor.execute(
                "UPDATE real_estate_deals SET is_cancelled=1, "
                "cancelled_date=date('now','localtime') "
                "WHERE fingerprint=? AND is_cancelled=0", (fingerprint,)
            )
            changed += cursor.rowcount
        return changed


_UPSERT_SQL = """
    INSERT INTO real_estate_deals (
        deal_date, region, project_name, building_type, floor, unit_id,
        total_ping, actual_ping, deed_ping, main_building_ping,
        parking_price, price_per_ping, house_age,
        is_presale, is_cancelled, cancelled_date, fingerprint
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?)
    ON CONFLICT(fingerprint) DO UPDATE SET
        total_ping=excluded.total_ping,
        actual_ping=excluded.actual_ping,
        deed_ping=excluded.deed_ping,
        main_building_ping=excluded.main_building_ping,
        price_per_ping=excluded.price_per_ping,
        is_cancelled=CASE WHEN real_estate_deals.is_cancelled=1 THEN 1 ELSE excluded.is_cancelled END,
        cancelled_date=CASE WHEN real_estate_deals.is_cancelled=1 THEN real_estate_deals.cancelled_date ELSE excluded.cancelled_date END
"""


def _record_values(record: dict) -> tuple:
    """依 SQL 欄位順序建立參數，避免字串拼接查詢。"""
    return tuple(record[key] for key in (
        "deal_date", "region", "project_name", "building_type", "floor",
        "unit_id", "total_ping", "actual_ping", "deed_ping",
        "main_building_ping", "parking_price",
        "price_per_ping", "is_presale", "is_cancelled", "cancelled_date",
        "fingerprint"
    ))
