#!/usr/bin/env python3
"""管理会計PLのコスト分析用読み取り層。

Excelへは書き込まず、保存済みの値と科目構造だけを画面へ渡す。
"""
import os
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path

import openpyxl


# 原本は2026-08-16以降、iCloud Downloadsから運用資料/予算実績表へ移動済み。
# 読み取り専用。存在しない環境（Render等）ではJSONスナップショットへ自動フォールバック。
DEFAULT_PATH = Path(
    "/Users/suzuki3/Library/CloudStorage/Dropbox-Detale/D& W/どら山/運用資料/予算実績表/"
    "【第10期 どら山】管理会計PL.xlsx"
)
WORKBOOK_PATH = Path(os.environ.get("DORAYAMA_MANAGEMENT_PL_PATH", DEFAULT_PATH))
SHEET_NAME = "01_月次PL（管理会計）"
MONTH_COLUMNS = [
    ("B", "2月", "2026/2", "確定"),
    ("C", "3月", "2026/3", "確定"),
    ("D", "4月", "2026/4", "確定"),
    ("E", "5月", "2026/5", "確定"),
    ("F", "6月", "2026/6", "確定"),
    ("G", "7月", "2026/7", "確定"),
    ("H", "8月", "2026/8", "進行中"),
    ("I", "9月", "2026/9", "未入力"),
    ("J", "10月", "2026/10", "未入力"),
    ("K", "11月", "2026/11", "未入力"),
    ("L", "12月", "2026/12", "未入力"),
    ("M", "1月（翌年）", "2027/1", "未入力"),
]
SECTIONS = [
    {"id": "sales", "label": "売上", "rows": range(5, 8)},
    {"id": "materials", "label": "材料・包材原価／粗利益", "rows": range(8, 17)},
    {"id": "labor", "label": "人件費", "rows": range(17, 25)},
    {"id": "other", "label": "その他コスト", "rows": range(25, 45)},
    {"id": "profit", "label": "利益", "rows": range(45, 50)},
]
RATE_ROWS = {14, 16, 24, 46}
TOTAL_ROWS = {7, 13, 15, 23, 44, 45, 48}
PROFIT_ROWS = {15, 45, 48}
_CACHE = {"key": None, "value": None}


def _amount(value):
    if value in (None, ""):
        return None
    try:
        return int(Decimal(str(value)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _rate(value):
    if value in (None, ""):
        return None
    try:
        return round(float(value) * 100, 1)
    except (TypeError, ValueError):
        return None


def _cell_value(sheet, cell, is_rate=False):
    value = sheet[cell].value
    return _rate(value) if is_rate else _amount(value)


def _line_kind(row):
    if row in RATE_ROWS:
        return "rate"
    if row in PROFIT_ROWS:
        return "profit"
    if row in TOTAL_ROWS:
        return "total"
    return "detail"


def _section_for(row):
    return next(section for section in SECTIONS if row in section["rows"])


def read_workbook(path):
    path = Path(path)
    book = openpyxl.load_workbook(path, read_only=True, data_only=True)
    if SHEET_NAME not in book.sheetnames:
        raise ValueError(f"必要なシートがありません: {SHEET_NAME}")
    sheet = book[SHEET_NAME]
    months = [
        {"column": column, "key": key, "label": label, "status": status}
        for column, key, label, status in MONTH_COLUMNS
    ]
    lines = []
    for row in range(5, 50):
        label = sheet[f"A{row}"].value
        if label in (None, ""):
            continue
        section = _section_for(row)
        is_rate = row in RATE_ROWS
        lines.append({
            "row": row,
            "label": str(label).strip(),
            "section": section["id"],
            "sectionLabel": section["label"],
            "kind": _line_kind(row),
            "format": "rate" if is_rate else "amount",
            "values": {
                key: _cell_value(sheet, f"{column}{row}", is_rate)
                for column, key, _label, _status in MONTH_COLUMNS
            },
            "confirmedTotal": _cell_value(sheet, f"N{row}", is_rate),
            "previousPeriod": _cell_value(sheet, f"O{row}", is_rate),
        })
    by_row = {line["row"]: line for line in lines}

    def value(row, month):
        return by_row.get(row, {}).get("values", {}).get(month)

    series = []
    for month in months:
        key = month["key"]
        sales = value(7, key)
        if sales is None:
            continue
        total_labor = value(23, key)
        event_staffing = value(21, key)
        other_cost = value(44, key)
        series.append({
            **month,
            "sales": sales,
            "storeSales": value(5, key),
            "eventSales": value(6, key),
            "internalLabor": total_labor - event_staffing
            if total_labor is not None and event_staffing is not None else None,
            "eventStaffing": event_staffing,
            "labor": total_labor,
            "rent": value(31, key),
            "material": value(13, key),
            "other": other_cost,
            "sellingExpenses": total_labor + other_cost
            if other_cost is not None and total_labor is not None else None,
            "operatingProfit": value(45, key),
            "nonOperating": value(47, key),
            "profit": value(48, key),
        })
    confirmed = [row for row in series if row["status"] == "確定"]

    def total(field):
        values = [row[field] for row in confirmed if row.get(field) is not None]
        return sum(values) if values else None

    stat = path.stat()
    return {
        "available": True,
        "readOnly": True,
        "fileName": path.name,
        "sourceSheet": SHEET_NAME,
        "updatedAt": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="minutes"),
        "months": months,
        "sections": [{"id": section["id"], "label": section["label"]} for section in SECTIONS],
        "lines": lines,
        "series": series,
        "summary": {
            "period": "2〜7月確定",
            "sales": total("sales"),
            "internalLabor": total("internalLabor"),
            "eventStaffing": total("eventStaffing"),
            "labor": total("labor"),
            "material": total("material"),
            "other": total("other"),
            "sellingExpenses": total("sellingExpenses"),
            "totalCost": (
                total("material") + total("sellingExpenses")
                if total("material") is not None and total("sellingExpenses") is not None else None
            ),
            "operatingProfit": total("operatingProfit"),
            "nonOperating": total("nonOperating"),
            "profit": total("profit"),
        },
        "rule": "Excelを正本として読み取り。催事販売員は内部人件費から除外し、外注費として別表示。",
    }


SNAPSHOT_FALLBACK_PATH = Path(__file__).resolve().parent / "data" / "management_pl_workbook_snapshot.json"


def _snapshot_fallback():
    """Excel原本を読めない環境（Render等）ではリポジトリ同梱スナップショットを使う。

    スナップショットは tools/build_management_pl_snapshot.py がExcelから読み取り専用で生成。
    """
    import json
    data = json.loads(SNAPSHOT_FALLBACK_PATH.read_text(encoding="utf-8"))
    data["source"] = "snapshot"
    data["rule"] = (data.get("rule") or "") + "（Excel原本を直接読めない環境のため、保存済みスナップショットを表示）"
    return data


def get_cost_analysis():
    try:
        key = (str(WORKBOOK_PATH), WORKBOOK_PATH.stat().st_mtime_ns)
        if _CACHE["key"] != key:
            _CACHE["value"] = read_workbook(WORKBOOK_PATH)
            _CACHE["key"] = key
        return _CACHE["value"]
    except (FileNotFoundError, OSError, ValueError, KeyError) as error:
        try:
            key = ("snapshot", SNAPSHOT_FALLBACK_PATH.stat().st_mtime_ns)
            if _CACHE["key"] != key:
                _CACHE["value"] = _snapshot_fallback()
                _CACHE["key"] = key
            return _CACHE["value"]
        except (FileNotFoundError, OSError, ValueError, KeyError):
            pass
        return {
            "available": False,
            "readOnly": True,
            "fileName": WORKBOOK_PATH.name,
            "sourceSheet": SHEET_NAME,
            "updatedAt": None,
            "months": [],
            "sections": [],
            "lines": [],
            "series": [],
            "summary": {},
            "rule": "管理会計PLを取得できないため、既存の確定集計を表示。",
            "reason": str(error),
        }
