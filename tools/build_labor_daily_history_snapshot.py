#!/usr/bin/env python3
"""Airシフト給与計算表などから指定月の日別人件費履歴を更新する。"""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from datetime import date
from pathlib import Path


BASE = Path(__file__).resolve().parents[1]
AUTOMATION_DATA = Path(
    "/Users/suzuki3/Library/CloudStorage/Dropbox-Detale/D& W/どら山/過去/"
    "dw_budget_profit_sheets_automation/data/input"
)
OUTPUT = BASE / "data" / "labor_daily_history_2026.json"


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--month", required=True, help="YYYY-MM")
    parser.add_argument("--output", type=Path, default=OUTPUT)
    return parser.parse_args()


def number(value) -> float:
    text = str(value or "0").replace(",", "").replace("¥", "").strip()
    return float(text or 0)


def hours(value) -> float:
    text = str(value or "0:00").strip()
    if ":" not in text:
        return number(text)
    hour, minute = text.split(":", 1)
    return int(hour) + int(minute) / 60


def day_bucket() -> dict:
    return {
        "labor": 0,
        "workHours": 0.0,
        "names": set(),
        "storeSales": 0,
        "eventSales": 0,
        "eventSalesStatus": "未取得",
    }


def load_worksheet(month_key: str, daily: dict[str, dict]) -> None:
    path = AUTOMATION_DATA / "airshift_worksheets" / f"airshift_worksheet_{month_key}.csv"
    with path.open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            day = str(row.get("日付") or "").replace("/", "-")
            if not day:
                continue
            bucket = daily[day]
            bucket["labor"] += round(number(row.get("合計")))
            bucket["workHours"] += hours(row.get("労働時間"))
            bucket["names"].add(str(row.get("氏名") or "氏名未設定").replace(" ", ""))


def load_taimee(month_key: str, daily: dict[str, dict]) -> None:
    path = AUTOMATION_DATA / "airshift" / f"taimee_{month_key}.csv"
    if not path.exists():
        return
    with path.open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            day = str(row.get("日付") or "").replace("/", "-")
            if not day:
                continue
            worked = number(row.get("実働時間"))
            amount = worked * number(row.get("時給")) + number(row.get("交通費"))
            bucket = daily[day]
            bucket["labor"] += round(amount)
            bucket["workHours"] += worked
            bucket["names"].add(str(row.get("スタッフ名") or "氏名未設定").replace(" ", ""))


def load_store_sales(month_key: str, daily: dict[str, dict]) -> None:
    path = AUTOMATION_DATA / "airregi" / f"airregi_{month_key}.csv"
    with path.open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            day = str(row.get("日付") or "").replace("/", "-")
            if day:
                daily[day]["storeSales"] += round(number(row.get("売上金額")))


def load_event_sales(month_key: str, daily: dict[str, dict]) -> None:
    path = AUTOMATION_DATA / "airmate" / f"airmate_{month_key}.csv"
    if not path.exists():
        return
    with path.open(encoding="cp932", newline="") as stream:
        for row in csv.DictReader(stream):
            if "催事" not in str(row.get("店舗名") or ""):
                continue
            day = str(row.get("日付") or "").replace("/", "-")
            if not day:
                continue
            daily[day]["eventSales"] += round(number(row.get("売上")))
            daily[day]["eventSalesStatus"] = "取得済み"


def build_month(month: str) -> dict:
    year, month_number = [int(value) for value in month.split("-")]
    month_key = f"{year:04d}_{month_number:02d}"
    daily = defaultdict(day_bucket)
    load_worksheet(month_key, daily)
    load_taimee(month_key, daily)
    load_store_sales(month_key, daily)
    load_event_sales(month_key, daily)

    rows = []
    for day_text in sorted(daily):
        parsed = date.fromisoformat(day_text)
        if (parsed.year, parsed.month) != (year, month_number):
            continue
        bucket = daily[day_text]
        combined = bucket["storeSales"] + bucket["eventSales"]
        rate = round(bucket["labor"] / combined * 100, 1) if combined else None
        rows.append({
            "day": parsed.day,
            "date": day_text,
            "labor": bucket["labor"],
            "workHours": round(bucket["workHours"], 2),
            "headcount": len(bucket["names"]),
            "storeSales": bucket["storeSales"],
            "eventSales": bucket["eventSales"],
            "combinedSales": combined,
            "laborRate": rate,
            "eventSalesStatus": bucket["eventSalesStatus"],
        })
    if not rows:
        raise RuntimeError(f"{month} の人件費明細がありません")

    labor_total = sum(row["labor"] for row in rows)
    store_total = sum(row["storeSales"] for row in rows)
    event_total = sum(row["eventSales"] for row in rows)
    combined_total = store_total + event_total
    pending = [row["date"] for row in rows if row["eventSalesStatus"] == "未取得"]
    return {
        "key": month,
        "month": f"{month_number}月",
        "period": f"{year}年{month_number}月",
        "status": "日別シフト実績",
        "dailyLaborTotal": labor_total,
        "storeSalesTotal": store_total,
        "workHours": round(sum(row["workHours"] for row in rows), 2),
        "daily": rows,
        "eventSalesTotal": event_total,
        "combinedSalesTotal": combined_total,
        "laborRate": round(labor_total / combined_total * 100, 1) if combined_total else None,
        "greenDays": sum(1 for row in rows if row["laborRate"] is not None and row["laborRate"] <= 25),
        "redDays": sum(1 for row in rows if row["laborRate"] is not None and row["laborRate"] > 25),
        "eventSalesPendingDates": pending,
        "combinedSalesSource": "店舗はAirレジ、催事はAirメイト日次実績",
    }


def main() -> None:
    args = arguments()
    snapshot = json.loads(args.output.read_text(encoding="utf-8"))
    month_data = build_month(args.month)
    months = [row for row in snapshot.get("months", []) if row.get("key") != args.month]
    months.append(month_data)
    snapshot["months"] = sorted(months, key=lambda row: row.get("key", ""))
    snapshot["updatedAt"] = date.today().isoformat()
    args.output.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        f"{month_data['period']}: 人件費 {month_data['dailyLaborTotal']:,}円、"
        f"{month_data['workHours']:,}時間、{len(month_data['daily'])}日分"
    )


if __name__ == "__main__":
    main()
