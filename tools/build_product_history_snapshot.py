#!/usr/bin/env python3
"""Airレジの商品別CSVから、月別の商品分析スナップショットを作る。"""
import csv
import json
import os
import re
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path


BASE = Path(__file__).resolve().parent.parent
INPUT_DIR = Path(os.environ.get(
    "DORAYAMA_AIRREGI_HISTORY_DIR",
    "/Users/suzuki3/Library/CloudStorage/Dropbox-Detale/D& W/どら山/過去/"
    "dw_budget_profit_sheets_automation/data/input/airregi",
))
OUTPUT_PATH = BASE / "data" / "product_analysis_history_2026.json"
PACKAGING_NAMES = {"ビニール袋", "箱(小)と紙袋", "箱(大)と紙袋"}
ESTIMATED_COST_RATE = Decimal("0.25")


def number(value):
    if value in (None, "", "-"):
        return Decimal("0")
    text = str(value).strip().replace(",", "").replace("¥", "").replace("￥", "")
    try:
        return Decimal(text)
    except InvalidOperation:
        return Decimal("0")


def integer(value):
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def share(value, total):
    if not total:
        return 0.0
    return round(value / total * 100, 1)


def category(name):
    if name in PACKAGING_NAMES:
        return "包材"
    if name in {"その他", "未設定", "商品別未分解（合計）"}:
        return "その他"
    return "FOOD"


def read_rows(path):
    content = path.read_bytes()
    for encoding in ("utf-8-sig", "cp932", "shift_jis", "utf-8"):
        try:
            text = content.decode(encoding)
            return list(csv.DictReader(text.splitlines()))
        except UnicodeDecodeError:
            continue
    return []


def build_month(path, year, month):
    items = defaultdict(lambda: {"quantity": Decimal("0"), "sales": Decimal("0")})
    first_day = None
    last_day = None
    source_rows = 0
    daily_customers = {}
    for row in read_rows(path):
        day_text = str(row.get("日付") or "").strip()
        name = str(row.get("商品名") or "").strip()
        if not day_text or not name or str(row.get("店舗名") or "").strip() != "どら山":
            continue
        try:
            day = date.fromisoformat(day_text)
        except ValueError:
            continue
        if (day.year, day.month) != (year, month):
            continue
        if name == "合計":
            name = "商品別未分解（合計）"
        quantity = number(row.get("数量"))
        sales = number(row.get("売上金額")) - number(row.get("値引き")) - number(row.get("返品"))
        items[name]["quantity"] += quantity
        items[name]["sales"] += sales
        source_rows += 1
        first_day = min(first_day, day) if first_day else day
        last_day = max(last_day, day) if last_day else day
        daily_customers[day.isoformat()] = max(
            daily_customers.get(day.isoformat(), Decimal("0")), number(row.get("客数"))
        )

    total_quantity = integer(sum((item["quantity"] for item in items.values()), Decimal("0")))
    total_sales = integer(sum((item["sales"] for item in items.values()), Decimal("0")))
    ordered = sorted(items.items(), key=lambda entry: (entry[1]["sales"], entry[1]["quantity"]), reverse=True)
    rows = []
    cumulative_sales = 0
    for rank, (name, values) in enumerate(ordered, start=1):
        quantity = integer(values["quantity"])
        sales = integer(values["sales"])
        estimated_cost = integer(values["sales"] * ESTIMATED_COST_RATE)
        estimated_gross_profit = sales - estimated_cost
        before = share(cumulative_sales, total_sales)
        abc = "A" if before < 70 else "B" if before < 90 else "C"
        cumulative_sales += sales
        rows.append({
            "rank": rank,
            "category": category(name),
            "name": name,
            "quantity": quantity,
            "quantityShare": share(quantity, total_quantity),
            "sales": sales,
            "salesShare": share(sales, total_sales),
            "estimatedCost": estimated_cost,
            "grossProfit": estimated_gross_profit,
            "grossProfitShare": 0.0,
            "abc": abc,
        })
    total_estimated_cost = sum(item["estimatedCost"] for item in rows)
    total_estimated_gross_profit = sum(item["grossProfit"] for item in rows)
    for item in rows:
        item["grossProfitShare"] = share(item["grossProfit"], total_estimated_gross_profit)

    month_end = date(year, month + 1, 1) if month < 12 else date(year + 1, 1, 1)
    complete = bool(last_day and (month_end - last_day).days == 1)
    return {
        "key": f"{year:04d}-{month:02d}",
        "label": f"{year}年{month}月",
        "shortLabel": f"{month}月",
        "period": {"from": first_day.isoformat() if first_day else None, "to": last_day.isoformat() if last_day else None},
        "status": "月次確定" if complete else "月途中",
        "sourceRows": source_rows,
        "customers": integer(sum(daily_customers.values(), Decimal("0"))),
        "totals": {
            "quantity": total_quantity,
            "sales": total_sales,
            "estimatedCost": total_estimated_cost,
            "estimatedGrossProfit": total_estimated_gross_profit,
        },
        "items": rows,
    }


def main():
    months = []
    paths = []
    for path in sorted(INPUT_DIR.glob("airregi_????_??.csv")):
        matched = re.fullmatch(r"airregi_(\d{4})_(\d{2})\.csv", path.name)
        if not matched:
            continue
        year, month = map(int, matched.groups())
        months.append(build_month(path, year, month))
        paths.append(path)
    updated_at = None
    if paths:
        updated_at = datetime.fromtimestamp(max(path.stat().st_mtime for path in paths)).astimezone().isoformat(timespec="minutes")
    payload = {
        "schemaVersion": 1,
        "source": "Airレジ月次商品別CSV（読み取り専用スナップショット）",
        "scope": "店舗（門仲どらやき どら山）",
        "updatedAt": updated_at,
        "costRate": 25.0,
        "costRule": "商品別確定原価の連携前は、確定運用ルールの原価率25%で推計する",
        "grossProfitStatus": "推計。商品別移動平均原価の連携後に確定へ置き換える",
        "abcRule": "売上構成比の累積70%までをA、90%までをB、以降をCとする",
        "months": months,
    }
    OUTPUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
