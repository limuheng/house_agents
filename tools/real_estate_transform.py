"""實價登錄來源資料的純轉換邏輯。"""

from datetime import datetime

import pandas as pd


def clean_hotspots(hotspots: list[str]) -> list[str]:
    """將完整行政區名稱轉成官方 CSV 使用的區鄉鎮名稱。"""
    return [spot[-3:] if any(k in spot for k in ("市", "鄉", "鎮", "區")) else spot
            for spot in hotspots]


def _number(row, field: str) -> float:
    """將來源欄位轉為數值，缺值則以零處理。"""
    value = row.get(field, 0)
    return float(value) if pd.notna(value) else 0.0


def _deal_date(value) -> str:
    """將民國年月日轉成西元 ISO 日期。"""
    raw = str(value)
    if len(raw) < 6:
        return raw
    year = int(raw[:-4]) + 1911
    return f"{year}-{raw[-4:-2]}-{raw[-2:]}"


def parse_transaction(row, is_presale: int, county_code: str,
                      parent_county: str) -> dict | None:
    """將單筆官方 CSV 資料轉成資料庫欄位，不處理持久化。"""
    try:
        return _build_record(row, is_presale, county_code, parent_county)
    except (TypeError, ValueError, KeyError):
        return None


def _build_record(row, is_presale: int, county_code: str,
                  parent_county: str) -> dict | None:
    """依官方欄位計算坪數、單價及交易指紋。"""
    total_price = _number(row, "總價元")
    total_ping = _number(row, "建物移轉總面積平方公尺") * 0.3025
    actual_ping = (_number(row, "主建物面積") + _number(row, "附屬建物面積")) * 0.3025
    if actual_ping <= 0:
        actual_ping = total_ping
    if total_ping <= 0 or total_price <= 0:
        return None

    return _record_fields(row, is_presale, county_code, parent_county,
                          total_price, total_ping, actual_ping)


def _record_fields(row, is_presale: int, county_code: str,
                   parent_county: str, total_price: float,
                   total_ping: float, actual_ping: float) -> dict:
    """組合標準交易欄位與穩定指紋。"""
    unit_price = (round(total_price / total_ping) if is_presale else
                  _number(row, "單價元平方公尺") * 3.3058)
    project = row.get("建案名稱")
    address = str(row.get("土地位置建物門牌", "未知社區"))
    project_name = str(project) if pd.notna(project) and str(project) else address
    if project_name == "nan":
        project_name = address
    floor = str(row.get("移轉層次", ""))
    deal_date = _deal_date(row.get("交易年月日", ""))
    unit_id = str(row.get("編號", ""))
    legacy_fingerprint = f"{county_code}_{project_name}_{address}_{floor}_{total_ping:.3f}"
    fingerprint = f"{legacy_fingerprint}_{deal_date}_{unit_id}"
    cancelled = int("解約" in str(row.get("備註", "")))
    return {
        "deal_date": deal_date,
        "region": f"{parent_county}{row.get('鄉鎮市區', '')}",
        "project_name": project_name,
        "building_type": str(row.get("建物型態", "")),
        "floor": floor,
        "unit_id": unit_id,
        "total_ping": total_ping,
        "actual_ping": actual_ping,
        "parking_price": int(_number(row, "車位總價元")),
        "price_per_ping": int(unit_price),
        "is_presale": is_presale,
        "is_cancelled": cancelled,
        "cancelled_date": datetime.now().strftime("%Y-%m-%d") if cancelled else None,
        "fingerprint": fingerprint,
        "legacy_fingerprint": legacy_fingerprint,
    }


def cancelled_fingerprints(csv_path: str, county_code: str,
                           hotspots: list[str]) -> list[str]:
    """從預售 CSV 擷取焦點區已解約交易的指紋。"""
    df = pd.read_csv(csv_path, skiprows=[1], low_memory=False)
    df = df[df["鄉鎮市區"].isin(hotspots)]
    cancelled = df[df["備註"].str.contains("解約|取消", na=False, errors="ignore")]
    return [_fingerprint(row, county_code) for _, row in cancelled.iterrows()]


def _fingerprint(row, county_code: str) -> str:
    """依交易識別欄位產生與匯入程序一致的指紋。"""
    address = str(row.get("土地位置建物門牌", "未知社區"))
    project_value = row.get("建案名稱")
    project = str(project_value) if pd.notna(project_value) else address
    floor = str(row.get("移轉層次", ""))
    ping = _number(row, "建物移轉總面積平方公尺") * 0.3025
    legacy = f"{county_code}_{project}_{address}_{floor}_{ping:.3f}"
    deal_date = _deal_date(row.get("交易年月日", ""))
    unit_id = str(row.get("編號", ""))
    return f"{legacy}_{deal_date}_{unit_id}"
