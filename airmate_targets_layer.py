#!/usr/bin/env python3
"""Airメイトで確認済みの売上目標を読み取る。Airメイト側への書き込みは行わない。"""
import json
from pathlib import Path


SOURCE_PATH = Path(__file__).resolve().parent / "data" / "airmate_targets_2026.json"


def _load():
    return json.loads(SOURCE_PATH.read_text(encoding="utf-8"))


def get_month(year, month):
    key = f"{year:04d}-{month:02d}"
    return next((dict(row) for row in _load()["months"] if row["yearMonth"] == key), None)


def sales_target_for(year, month):
    row = get_month(year, month)
    return row["total"] if row else None


def event_daily_sales_target():
    return _load()["eventOperatingTarget"]["amountGross"]


def event_sales_target(event_days):
    return max(int(event_days or 0), 0) * event_daily_sales_target()


def calendar_year(year):
    return [
        get_month(year, month) or {
            "yearMonth": f"{year:04d}-{month:02d}",
            "event": None,
            "store": None,
            "total": None,
        }
        for month in range(1, 13)
    ]


def summary():
    source = _load()
    return {
        "source": source["source"],
        "sourceUrl": source["sourceUrl"],
        "verifiedAt": source["verifiedAt"],
        "fiscalYear": source["fiscalYear"],
        "period": source["period"],
        "annualSalesTarget": source["annualSalesTarget"],
        "costTargetUsable": source["costTargetUsable"],
        "costTargetNote": source["costTargetNote"],
        "eventOperatingTarget": dict(source["eventOperatingTarget"]),
        "stores": source["stores"],
        "months": [dict(row) for row in source["months"]],
    }
