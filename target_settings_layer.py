#!/usr/bin/env python3
"""店舗・催事の目標設定。Airメイト・Googleカレンダー・Excel原本へは書き込まない。"""
from calendar import monthrange
from copy import deepcopy
from datetime import date, datetime, timedelta
import json
from pathlib import Path
import re
from zoneinfo import ZoneInfo

import airmate_targets_layer


BASE = Path(__file__).resolve().parent
CALENDAR_PATH = BASE / "data" / "event_calendar_2026.json"
OVERRIDES_PATH = BASE / "data" / "management_target_overrides.json"
JST = ZoneInfo("Asia/Tokyo")
DEFAULT_RATES = {"material": 25.0, "labor": 25.0, "ordinaryProfit": 20.0}
RATE_KEYS = tuple(DEFAULT_RATES)


def _load_json(path, fallback):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return deepcopy(fallback)


def _fiscal_months():
    return [(2026, month) for month in range(2, 13)] + [(2027, 1)]


def _month_dates(year, month):
    return [date(year, month, day) for day in range(1, monthrange(year, month)[1] + 1)]


def _calendar_schedule():
    source = _load_json(CALENDAR_PATH, {"events": []})
    events = []
    for raw in source.get("events", []):
        try:
            start = date.fromisoformat(raw["start"])
            end = date.fromisoformat(raw["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if end < start:
            continue
        events.append({**raw, "startDate": start, "endDate": end})
    return source, events


def _calendar_month(year, month, events):
    days = []
    event_rows = []
    first = date(year, month, 1)
    last = date(year, month, monthrange(year, month)[1])
    for item in events:
        if item["endDate"] < first or item["startDate"] > last:
            continue
        overlap_start = max(item["startDate"], first)
        overlap_end = min(item["endDate"], last)
        count = (overlap_end - overlap_start).days + 1
        event_rows.append({
            "name": item.get("name") or "名称未設定",
            "venue": item.get("venue") or "",
            "start": item["startDate"].isoformat(),
            "end": item["endDate"].isoformat(),
            "daysInMonth": count,
            "tentative": bool(item.get("tentative")),
        })
    for current in _month_dates(year, month):
        active = [item for item in events if item["startDate"] <= current <= item["endDate"]]
        days.append({
            "date": current.isoformat(),
            "eventCount": len(active),
            "targetSales": airmate_targets_layer.event_sales_target(len(active)),
            "events": [item.get("name") or "名称未設定" for item in active],
            "tentativeCount": sum(1 for item in active if item.get("tentative")),
        })
    return {
        "eventDays": sum(item["eventCount"] for item in days),
        "calendarDays": sum(1 for item in days if item["eventCount"]),
        "overlapDays": sum(1 for item in days if item["eventCount"] >= 2),
        "tentativeEventDays": sum(item["tentativeCount"] for item in days),
        "daily": days,
        "events": event_rows,
    }


def _number(value, minimum=0, maximum=1_000_000_000):
    try:
        number = int(round(float(value)))
    except (TypeError, ValueError, OverflowError):
        raise ValueError("金額は数字で入力してください")
    if number < minimum or number > maximum:
        raise ValueError("金額が入力可能な範囲を超えています")
    return number


def _rate(value):
    try:
        number = round(float(value), 1)
    except (TypeError, ValueError, OverflowError):
        raise ValueError("率は数字で入力してください")
    if number < 0 or number > 100:
        raise ValueError("率は0〜100%で入力してください")
    return number


def _load_overrides():
    raw = _load_json(OVERRIDES_PATH, {})
    rates = dict(DEFAULT_RATES)
    for key in RATE_KEYS:
        if key in (raw.get("rates") or {}):
            try:
                rates[key] = _rate(raw["rates"][key])
            except ValueError:
                pass
    months = {}
    for key, values in (raw.get("months") or {}).items():
        if not isinstance(values, dict):
            continue
        cleaned = {}
        for field in ("store", "event"):
            if field in values:
                try:
                    cleaned[field] = _number(values[field])
                except ValueError:
                    pass
        if cleaned:
            months[key] = cleaned
    return {"updatedAt": raw.get("updatedAt"), "rates": rates, "months": months}


def _actual_by_month(cost_analysis):
    result = {}
    for row in (cost_analysis or {}).get("series", []):
        year = None
        month = None
        for value in (row.get("yearMonth"), row.get("label"), row.get("key"), row.get("month")):
            text = str(value or "").strip()
            full = re.fullmatch(r"(\d{4})[/-](\d{1,2})(?:月)?", text)
            short = re.fullmatch(r"(\d{1,2})月", text)
            if full:
                year, month = int(full.group(1)), int(full.group(2))
                break
            if short:
                month = int(short.group(1))
                year = 2027 if month == 1 else 2026
                break
        if year is None or month is None or month < 1 or month > 12:
            continue
        sales = row.get("sales")
        material = row.get("material")
        labor = row.get("internalLabor")
        profit = row.get("profit")
        result[f"{year:04d}-{month:02d}"] = {
            "sales": sales,
            "materialRate": round(material / sales * 100, 1) if sales and material is not None else None,
            "laborRate": round(labor / sales * 100, 1) if sales and labor is not None else None,
            "ordinaryProfitRate": round(profit / sales * 100, 1) if sales and profit is not None else None,
            "status": row.get("status") or "未確定",
        }
    return result


def get_target_settings(cost_analysis=None):
    calendar_source, events = _calendar_schedule()
    overrides = _load_overrides()
    airmate = {row["yearMonth"]: row for row in airmate_targets_layer.summary()["months"]}
    actuals = _actual_by_month(cost_analysis)
    rows = []
    for year, month in _fiscal_months():
        key = f"{year:04d}-{month:02d}"
        source = airmate.get(key) or {}
        calendar_month = _calendar_month(year, month, events)
        base_store = source.get("store") or 0
        base_event = airmate_targets_layer.event_sales_target(calendar_month["eventDays"])
        override = overrides["months"].get(key) or {}
        store_target = override.get("store", base_store)
        event_target = override.get("event", base_event)
        total = store_target + event_target
        rates = overrides["rates"]
        rows.append({
            "yearMonth": key,
            "label": f"{year}年{month}月",
            "shortLabel": f"{month}月",
            "storeBase": base_store,
            "storeTarget": store_target,
            "storeSource": "手動変更" if "store" in override else "Airメイト目標",
            "airmateEventReference": source.get("event"),
            "eventBase": base_event,
            "eventTarget": event_target,
            "eventSource": "手動変更" if "event" in override else "Googleカレンダー × 220,000円",
            "totalTarget": total,
            "materialTarget": round(total * rates["material"] / 100),
            "laborTarget": round(total * rates["labor"] / 100),
            "ordinaryProfitTarget": round(total * rates["ordinaryProfit"] / 100),
            "unallocatedRate": round(100 - sum(rates.values()), 1),
            **calendar_month,
            "actual": actuals.get(key),
        })
    return {
        "schemaVersion": 1,
        "period": "2026年2月〜2027年1月",
        "fiscalYear": 2026,
        "readOnlySources": True,
        "editableTargetStore": "アプリ専用設定ファイル",
        "updatedAt": overrides.get("updatedAt"),
        "rates": overrides["rates"],
        "eventRate": airmate_targets_layer.event_daily_sales_target(),
        "calendar": {
            "source": calendar_source.get("source") or "Googleカレンダー「どら山 催事」",
            "verifiedAt": calendar_source.get("verifiedAt"),
            "rule": calendar_source.get("rule"),
        },
        "annual": {
            "store": sum(row["storeTarget"] for row in rows),
            "event": sum(row["eventTarget"] for row in rows),
            "total": sum(row["totalTarget"] for row in rows),
            "eventDays": sum(row["eventDays"] for row in rows),
        },
        "months": rows,
        "rule": "店舗はAirメイト月目標、催事は1催事1日220,000円。催事のない日は0円、同日2催事は440,000円。",
        "writeBoundary": "変更はアプリ専用の目標設定だけに保存し、Airメイト・Googleカレンダー・Excel・管理会計PLは変更しません。",
    }


def save_target_settings(payload, cost_analysis=None):
    if not isinstance(payload, dict):
        raise ValueError("保存内容を確認できません")
    rates_input = payload.get("rates") or {}
    rates = {key: _rate(rates_input.get(key, DEFAULT_RATES[key])) for key in RATE_KEYS}
    if sum(rates.values()) > 100:
        raise ValueError("原価率・人件費率・経常利益率の合計は100%以下にしてください")
    valid_months = {f"{year:04d}-{month:02d}" for year, month in _fiscal_months()}
    _, events = _calendar_schedule()
    airmate = {row["yearMonth"]: row for row in airmate_targets_layer.summary()["months"]}
    months = {}
    for key, values in (payload.get("months") or {}).items():
        if key not in valid_months or not isinstance(values, dict):
            continue
        year, month = (int(value) for value in key.split("-"))
        base_store = (airmate.get(key) or {}).get("store") or 0
        base_event = airmate_targets_layer.event_sales_target(
            _calendar_month(year, month, events)["eventDays"]
        )
        store = _number(values.get("store", base_store))
        event = _number(values.get("event", base_event))
        changed = {}
        if store != base_store:
            changed["store"] = store
        if event != base_event:
            changed["event"] = event
        if changed:
            months[key] = changed
    saved = {
        "schemaVersion": 1,
        "updatedAt": datetime.now(JST).isoformat(timespec="minutes"),
        "rates": rates,
        "months": months,
    }
    temporary = OVERRIDES_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps(saved, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(OVERRIDES_PATH)
    return get_target_settings(cost_analysis)
