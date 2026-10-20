#!/usr/bin/env python3
"""店長向けの人件費画面のデータ（2026-10-20 本人指示）。

個人の給与・氏名・時給は一切含めない。出すのは「店舗の人件費の合計」「使える枠（円・時間）」「製造・売上との比較」だけ。
枠の出し方は経営管理と同じ（LABOR_CAPS）: 店舗の打刻人件費 ≦ 製造額（店舗用＋催事向け）×11% ＋ 店舗売上×14%。
月の枠 ＝（店舗目標＋催事目標）×25% − 催事販売員（37,000円×販売員付き催事日）。
"""
from calendar import monthrange
from datetime import date, datetime
from zoneinfo import ZoneInfo

import management_analysis_layer

JST = ZoneInfo("Asia/Tokyo")
WEEKDAYS = ["月", "火", "水", "木", "金", "土", "日"]
# 時間に直すときの目安の時給（Airシフト給与計算表 9〜10月の平均・残業と交通費込み 1,513円を丸めたもの）。個人の時給ではない
HOURLY_WAGE_ESTIMATE = 1500


def _round(value):
    return int(round(value)) if value is not None else None


def get_manager_labor():
    analysis = management_analysis_layer.get_management_analysis()
    now = datetime.now(JST)
    caps = (analysis.get("todayBoard") or {}).get("laborCaps") or management_analysis_layer.LABOR_CAPS
    staff_daily = float((analysis.get("todayBoard") or {}).get("eventStaffDailyRate") or 37000)
    updated = str(analysis.get("updatedAt") or "")[:10]
    month_key = now.strftime("%Y-%m")
    days_in_month = monthrange(now.year, now.month)[1]
    goal = next((m for m in analysis["goalSettings"]["months"] if m["yearMonth"] == month_key), None) or {}
    goal_days = {g["date"]: g for g in goal.get("daily", [])}
    staffed_days = sum(int(g.get("staffedEventCount", g.get("eventCount", 0)) or 0) for g in goal.get("daily", []))
    store_target, event_target = float(goal.get("storeTarget") or 0), float(goal.get("eventTarget") or 0)
    budget_total = (store_target + event_target) * caps["total"] / 100
    event_staff_budget = staffed_days * staff_daily
    store_shift_budget = budget_total - event_staff_budget

    production = analysis.get("production") or {}
    prod_by_date = {r["date"]: r for r in production.get("daily", [])}
    rows_by_date = {r["date"]: r for r in analysis.get("daily", []) if str(r.get("date", "")).startswith(month_key)}

    daily = []
    labor_to_date = cap_to_date = 0.0
    for day in range(1, days_in_month + 1):
        key = f"{month_key}-{day:02d}"
        if key > updated and key > now.strftime("%Y-%m-%d"):
            break
        row = rows_by_date.get(key)
        prod = prod_by_date.get(key)
        if not row and not prod:
            continue
        store_sales = float((row or {}).get("storeSales") or 0)
        store_labor = float((row or {}).get("storeLabor") or 0)
        prod_value = float((prod or {}).get("value") or 0)
        blocks = (prod or {}).get("blocks") or {}
        prod_store = float((blocks.get("店舗用") or {}).get("value") or 0)
        cap = prod_value * caps["production"] / 100 + store_sales * caps["storeSales"] / 100 if prod_value > 0 else None
        is_today = key == now.strftime("%Y-%m-%d")
        if cap is not None and store_labor > 0 and not is_today:
            labor_to_date += store_labor
            cap_to_date += cap
        if cap is None:
            verdict = "製造実績待ち" if prod is None or prod_value <= 0 else "—"
        elif is_today:
            verdict = "営業中"
        elif store_labor <= 0:
            verdict = "打刻待ち"
        else:
            verdict = "収まり" if store_labor <= cap else "オーバー"
        d = date.fromisoformat(key)
        daily.append({
            "date": key, "label": f"{d.month}/{d.day}（{WEEKDAYS[d.weekday()]}）", "isToday": is_today,
            "storeSales": _round(store_sales), "productionValue": _round(prod_value),
            "productionPieces": int((prod or {}).get("pieces") or 0),
            "productionStore": _round(prod_store), "productionEvent": _round(prod_value - prod_store),
            "storeLabor": _round(store_labor),
            "cap": _round(cap), "capHours": round(cap / HOURLY_WAGE_ESTIMATE, 1) if cap else None,
            "gap": _round(cap - store_labor) if cap is not None else None,
            "verdict": verdict,
        })

    elapsed_days = sum(1 for r in daily if not r["isToday"] and r["storeLabor"])
    remaining_days = max(days_in_month - now.day + 1, 1)
    remaining_budget = store_shift_budget - labor_to_date
    return {
        "generatedAt": now.isoformat(timespec="minutes"),
        "updatedAt": analysis.get("updatedAt"),
        "month": {"key": month_key, "label": f"{now.month}月", "daysInMonth": days_in_month},
        "rule": {
            "production": caps["production"], "storeSales": caps["storeSales"], "total": caps["total"],
            "eventStaffDaily": _round(staff_daily), "hourlyWage": HOURLY_WAGE_ESTIMATE,
        },
        "monthFrame": {
            "storeTarget": _round(store_target), "eventTarget": _round(event_target),
            "budgetTotal": _round(budget_total), "staffedEventDays": staffed_days,
            "eventStaffBudget": _round(event_staff_budget), "storeShiftBudget": _round(store_shift_budget),
            "perDay": _round(store_shift_budget / days_in_month), "perDayHours": round(store_shift_budget / days_in_month / HOURLY_WAGE_ESTIMATE, 1),
            "monthHours": _round(store_shift_budget / HOURLY_WAGE_ESTIMATE),
        },
        "monthToDate": {
            "days": elapsed_days, "storeLabor": _round(labor_to_date), "cap": _round(cap_to_date),
            "gap": _round(cap_to_date - labor_to_date),
            "remainingBudget": _round(remaining_budget), "remainingDays": remaining_days,
            "remainingPerDay": _round(remaining_budget / remaining_days),
            "remainingPerDayHours": round(remaining_budget / remaining_days / HOURLY_WAGE_ESTIMATE, 1),
        },
        "daily": daily,
        "notes": [
            "この画面には、個人の給与・時給・氏名は出ません。店舗の人件費の合計だけです。",
            f"1日の枠 ＝ 製造額（店舗用＋催事向け）×{caps['production']:.0f}% ＋ 店舗売上×{caps['storeSales']:.0f}%。工房では催事向けの商品も作るため、製造分は売上ではなく製造額で見ます。",
            f"月の枠 ＝（店舗目標＋催事目標）×{caps['total']:.0f}% − 催事販売員（1会場1日{_round(staff_daily):,}円）。目標どおりに売れた月は、会社全体の人件費が売上の{caps['total']:.0f}%に収まります。",
            f"時間は目安の時給{HOURLY_WAGE_ESTIMATE:,}円（残業・交通費込みの平均）で割ったものです。",
            "今日の分は営業中の途中経過です。製造実績と売上が入ると枠は増えます。",
        ],
    }
