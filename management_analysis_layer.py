#!/usr/bin/env python3
"""店長・経営者共通の多角分析データ（読み取り専用）。"""
from copy import deepcopy
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import budget_workbook_layer
import airmate_targets_layer
import management_layer
import management_pl_workbook_layer
import management_sync_layer
import target_settings_layer


JST = ZoneInfo("Asia/Tokyo")
BASE = Path(__file__).resolve().parent
AIR_MATE_ANALYSIS_PATH = BASE / "data" / "airmate_analysis_snapshot_2026-08-17.json"
PRODUCT_HISTORY_PATH = BASE / "data" / "product_analysis_history_2026.json"
LABOR_DAILY_HISTORY_PATH = BASE / "data" / "labor_daily_history_2026.json"
FIXED_COST_CATEGORIES = ("地代家賃", "賃借料", "水道光熱費", "通信費", "保険料")
FIXED_COST_PROVISIONAL_OVERRIDES = {}
FIXED_COST_RECONCILIATIONS = {
    "8月": {
        "status": "確定",
        "expectedTotal": 498666,
        "bookedTotal": 496708,
        "accrualAdjustment": 1958,
        "employeeContribution": 35000,
        "netCompanyBurden": 463666,
        "sourceLabel": "管理会計PL（8月固定費締め）",
        "note": (
            "freee記帳済496,708円に、上下水道の未計上月次調整1,958円を加えています。"
            "役員社宅の本人負担35,000円を差し引いた会社実負担は463,666円です。"
        ),
    },
}
LABOR_RECONCILIATIONS = {
    "8月": {
        "status": "再集計中",
        "accountingLabor": 388764,
        "accountingLaborNote": "管理会計PLは給与未反映で、法定福利費の支払額のみ",
        "payrollGross": 1314804,
        "payrollPeriod": "2026/7/16〜8/15・8/25支給",
        "timeeInvoice": 199990,
        "timeeWorkerCompensation": 184375,
        "timeeServiceFee": 15615,
        "shiftCostEstimate": 1728853,
        "unpricedShiftCount": 4,
        "unpricedShiftHours": 16.87,
        "missingPunchCount": 1,
        "sourceLabel": "給与確定資料・タイミー8月請求書・Airシフト勤務表の再照合",
        "note": (
            "1,728,853円は固定給社員も時給換算した勤務シフト原価の参考値で、給与確定額ではありません。"
            "時給未設定4勤務と退勤未打刻1勤務もあるため、8月内部人件費と経常利益は確定表示しません。"
        ),
    },
}
MONTH_LABELS = ["1月", "2月", "3月", "4月", "5月", "6月", "7月"]
MONTH_COLUMNS = ["F", "G", "H", "J", "K", "L", "N"]
NAVIGATION = [
    {"id": "store", "label": "店舗分析", "group": "売上"},
    {"id": "events", "label": "催事分析", "group": "売上"},
    {"id": "products", "label": "商品分析", "group": "売上"},
    {"id": "weekday-time", "label": "曜日・時間帯", "group": "売上"},
    {"id": "labor", "label": "人件費分析", "group": "コスト"},
    {"id": "cost-trend", "label": "コスト分析", "group": "コスト"},
    {"id": "costs", "label": "原価・経費", "group": "コスト"},
    {"id": "fixed", "label": "固定費・支払", "group": "コスト"},
    {"id": "overview", "label": "経営概況", "group": "判断"},
    {"id": "daily", "label": "日次経営台帳", "group": "判断"},
    {"id": "monthly", "label": "月次予実", "group": "判断"},
    {"id": "break-even", "label": "損益分岐点", "group": "判断"},
    {"id": "targets", "label": "目標設定", "group": "計画"},
    {"id": "last-year", "label": "昨年対比", "group": "計画"},
    {"id": "promotion", "label": "販売促進", "group": "計画"},
    {"id": "staffing", "label": "人員計画", "group": "計画"},
    {"id": "recruiting", "label": "求人計画", "group": "計画"},
    {"id": "repairs", "label": "修繕計画", "group": "計画"},
    {"id": "workbook", "label": "Excel原本", "group": "原本"},
]
REFERENCE_SHEETS = {
    "last-year": "昨年対比PL一覧",
    "promotion": "販売促進詳細",
    "staffing": "人員計画詳細",
    "recruiting": "求人費詳細",
    "repairs": "修繕詳細",
}
PL_LINES = [
    (3, "催事売上", "sales"), (4, "店舗売上", "sales"), (7, "売上合計", "sales-total"),
    (8, "仕入原価", "cost"), (9, "包材", "cost"), (10, "配送費", "cost"),
    (16, "原価合計", "cost-total"), (17, "粗利益", "profit"),
    (18, "役員報酬", "labor"), (19, "給与", "labor"), (20, "雑給・タイミー", "labor"),
    (22, "催事販売員", "labor"), (24, "人件費合計", "labor-total"),
    (31, "賃借料・地代家賃", "fixed"), (34, "水道光熱費", "fixed"),
    (38, "支払手数料", "fixed"), (39, "広告宣伝費", "fixed"),
    (45, "会議費", "fixed"), (48, "雑費・その他外注", "fixed"),
    (52, "販売管理費", "cost-total"), (53, "営業利益", "profit"),
    (61, "経常利益", "profit-total"),
]


def _row_months(row_number):
    values = budget_workbook_layer.row_values("どら山店舗・催事年間PL一覧", row_number)
    return [values.get(f"{column}{row_number}") for column in MONTH_COLUMNS]


def _pnl_lines():
    lines = []
    for row_number, label, kind in PL_LINES:
        values = budget_workbook_layer.row_values("どら山店舗・催事年間PL一覧", row_number)
        lines.append({
            "row": row_number,
            "label": label,
            "kind": kind,
            "previous": values.get(f"B{row_number}"),
            "total": values.get(f"C{row_number}"),
            "ratio": values.get(f"D{row_number}"),
            "yearOnYear": values.get(f"E{row_number}"),
            "months": [values.get(f"{column}{row_number}") for column in MONTH_COLUMNS],
        })
    return lines


def _monthly_daily_sales_totals(daily_history):
    """日次実績(Airメイト・Airレジ)を年月ごとに積み上げる。"""
    totals = {}
    for row in daily_history or []:
        ym = str(row.get("date") or "")[:7]
        if not ym:
            continue
        bucket = totals.setdefault(ym, {"storeSales": 0, "eventSales": 0, "days": 0})
        bucket["storeSales"] += row.get("storeSales") or 0
        bucket["eventSales"] += row.get("eventSales") or 0
        bucket["days"] += 1
    return totals


def _monthly_rows(confirmed, goal_settings=None, daily_history=None):
    rows = []
    store = _row_months(4)
    events = _row_months(3)
    confirmed_by_month = {item["month"]: item for item in confirmed["months"]}
    targets = airmate_targets_layer.calendar_year(2026)
    effective_targets = {
        row["yearMonth"]: row for row in (goal_settings or {}).get("months", [])
    }
    daily_totals_by_month = _monthly_daily_sales_totals(daily_history)
    for index, target in enumerate(targets):
        label = f"{index + 1}月"
        source = confirmed_by_month.get(label)
        if source is None and label == "8月":
            provisional = confirmed.get("augustProvisional") or {}
            source = {
                **provisional,
                "month": label,
                "operatingExpenses": provisional.get("operatingExpenses") if provisional.get("operatingExpenses") is not None else (
                    provisional.get("grossProfit") - provisional.get("profit")
                    if provisional.get("grossProfit") is not None and provisional.get("profit") is not None
                    else None
                ),
                "breakEven": None,
                "cumulative": None,
                "dataStatus": provisional.get("dataStatus") or "freee進行中・要照合",
                "sourceLabel": provisional.get("sourceLabel") or "freee管理会計PL（8月進行中）",
            }
        source = source or {
            "month": label,
            "sales": None,
            "costOfSales": None,
            "grossProfit": None,
            "operatingExpenses": None,
            "profit": None,
            "breakEven": None,
            "cumulative": None,
            "dataStatus": "未確定",
            "sourceLabel": "未取得",
        }
        ym_key = f"2026-{index + 1:02d}"
        daily_totals = daily_totals_by_month.get(ym_key)
        daily_sales_note = None
        if source.get("dataStatus") != "管理会計PL確定" and daily_totals and daily_totals["days"] > 0:
            source = {
                **source,
                "storeSales": daily_totals["storeSales"],
                "eventSales": daily_totals["eventSales"],
            }
            daily_sales_note = (
                f"Airメイト・Airレジ日次実績{daily_totals['days']}日分の合計"
                "（大本のExcelは未反映・速報値）"
            )
        actual_store = store[index] if index < len(store) and index < 7 else None
        actual_events = events[index] if index < len(events) and index < 7 else None
        effective = effective_targets.get(f"2026-{index + 1:02d}") or {}
        budget = effective.get("totalTarget", target.get("total"))
        sales = source.get("sales")
        rows.append({
            **source,
            "budget": budget,
            "storeBudget": effective.get("storeTarget", target.get("store")),
            "eventBudget": effective.get("eventTarget", target.get("event")),
            "storeSales": source.get("storeSales") if source.get("storeSales") is not None else actual_store,
            "eventSales": source.get("eventSales") if source.get("eventSales") is not None else actual_events,
            "salesSourceNote": daily_sales_note,
            "budgetVariance": sales - budget if sales is not None and budget is not None else None,
            "breakEvenVariance": sales - source["breakEven"]
            if sales is not None and source.get("breakEven") is not None else None,
            "budgetSource": "店舗Airメイト＋催事カレンダー" if effective else (
                "Airメイト目標" if budget is not None else "Airメイト対象期間外"
            ),
        })
    return rows


def _workbook_financials(source):
    sales = source.get("sales")
    material = source.get("material")
    selling_expenses = source.get("sellingExpenses")
    profit = source.get("profit")
    gross_profit = sales - material if sales is not None and material is not None else None
    return {
        "sales": sales,
        "storeSales": source.get("storeSales"),
        "eventSales": source.get("eventSales"),
        "costOfSales": material,
        "grossProfit": gross_profit,
        "operatingExpenses": selling_expenses,
        "labor": source.get("labor"),
        "rent": source.get("rent"),
        "internalLabor": source.get("internalLabor"),
        "eventStaffing": source.get("eventStaffing"),
        "operatingProfit": source.get("operatingProfit"),
        "profit": profit,
        "costTotal": material + selling_expenses
        if material is not None and selling_expenses is not None else None,
        "dataStatus": "管理会計PL確定" if source.get("status") == "確定" else "管理会計PL進行中",
        "sourceLabel": "【第10期 どら山】管理会計PL.xlsx",
        "accountingInternalLabor": source.get("accountingInternalLabor"),
        "accountingProfit": source.get("accountingProfit"),
        "shiftCostEstimate": source.get("shiftCostEstimate"),
        "laborReconciliation": source.get("laborReconciliation"),
    }


def _apply_latest_management_pl(confirmed, analysis):
    if not analysis.get("available") or not analysis.get("series"):
        return confirmed
    merged = deepcopy(confirmed)
    workbook_by_month = {row["key"]: row for row in analysis["series"]}
    confirmed_months = []
    cumulative_profit = 0
    for row in merged.get("months", []):
        workbook_row = workbook_by_month.get(row.get("month"))
        if not workbook_row or workbook_row.get("status") != "確定":
            continue
        row.update(_workbook_financials(workbook_row))
        cumulative_profit += workbook_row.get("profit") or 0
        row["cumulative"] = cumulative_profit
        confirmed_months.append(row)
    provisional_row = next(
        (row for row in analysis.get("series", []) if row.get("status") != "確定"),
        None,
    )
    if provisional_row:
        provisional = _workbook_financials(provisional_row)
        provisional.update({
            "asOf": merged.get("augustProvisional", {}).get("asOf"),
            "status": f"管理会計PL{provisional_row.get('status') or '進行中'}・最終確定前",
            "month": provisional_row.get("key"),
        })
        merged["augustProvisional"] = provisional
    if not confirmed_months:
        return merged
    latest = confirmed_months[-1]
    merged["latest"] = latest
    # 「利益はどこに消えているか」は最新Excelの数字で必ず作り直し、画面内の他の数字と一致させる
    merged["impact"] = management_layer.build_impact(
        latest, management_layer.MONTH_DAYS.get(latest.get("month"), 30)
    )
    merged["period"] = f"{latest['month']}・最新Excel確定"
    merged["statusLabel"] = "最新Excel反映・2〜7月管理会計PL"
    merged["sourceLabel"] = "実績：最新管理会計PL Excel／売上予算：店舗Airメイト＋催事カレンダー"
    merged["sourceUpdatedAt"] = analysis.get("updatedAt")
    merged["achievement"] = round(latest["sales"] / latest["budget"] * 100, 1) if latest.get("budget") else None
    merged["budgetGap"] = max(0, latest["budget"] - latest["sales"]) if latest.get("budget") else None
    merged["breakEvenGap"] = max(0, latest["breakEven"] - latest["sales"]) if latest.get("breakEven") else None
    merged["breakEvenRate"] = round(latest["sales"] / latest["breakEven"] * 100, 1) if latest.get("breakEven") else None
    summary = analysis.get("summary") or {}
    sales = summary.get("sales")
    material = summary.get("material")
    labor = sum((row.get("labor") or 0) for row in analysis["series"] if row.get("status") == "確定")
    selling_expenses = sum((row.get("sellingExpenses") or 0) for row in analysis["series"] if row.get("status") == "確定")
    gross_profit = sales - material if sales is not None and material is not None else None
    rent_line = next((line for line in analysis.get("lines", []) if line.get("label") == "地代家賃"), {})
    rent = rent_line.get("confirmedTotal")
    merged["cumulative"] = {
        **merged.get("cumulative", {}),
        "sales": sales,
        "storeSales": sum((row.get("storeSales") or 0) for row in analysis["series"] if row.get("status") == "確定"),
        "eventSales": sum((row.get("eventSales") or 0) for row in analysis["series"] if row.get("status") == "確定"),
        "costOfSales": material,
        "grossProfit": gross_profit,
        "grossMargin": round(gross_profit / sales * 100, 1) if sales and gross_profit is not None else None,
        "labor": labor,
        "operatingExpenses": selling_expenses,
        "operatingProfit": sum((row.get("operatingProfit") or 0) for row in analysis["series"] if row.get("status") == "確定"),
        "profit": summary.get("profit"),
        "laborDistributionRate": round(labor / gross_profit * 100, 1) if gross_profit else None,
        "rent": rent,
        "rentRate": round(rent / gross_profit * 100, 1) if rent is not None and gross_profit else None,
        "operatingProfitToGrossProfit": round(sum((row.get("operatingProfit") or 0) for row in analysis["series"] if row.get("status") == "確定") / gross_profit * 100, 1) if gross_profit else None,
        "eventStaffing": summary.get("eventStaffing"),
    }
    detail_lines = [line for line in analysis.get("lines", []) if line.get("kind") == "detail" and line.get("section") in {"materials", "labor", "other"}]
    merged["latestBreakdown"] = [{
        "label": line["label"],
        "amount": line.get("values", {}).get(latest["month"]),
        "group": line.get("sectionLabel"),
    } for line in detail_lines if line.get("values", {}).get(latest["month"]) not in (None, 0)]
    profit = latest.get("profit")
    total_cost = latest.get("sales") - profit if latest.get("sales") is not None and profit is not None else None
    result_label = "黒字" if profit is not None and profit >= 0 else "赤字"
    if merged.get("todayDecisions") and profit is not None:
        merged["todayDecisions"][0] = {
            "level": "normal" if profit >= 0 else "urgent",
            "title": f"{latest['month']}は{abs(profit):,}円の{result_label}",
            "detail": f"最新の管理会計PLでは、売上{latest['sales']:,}円に対して総コスト{total_cost:,}円です。",
        }
    return merged


def _cost_breakdown(confirmed):
    return [{
        "label": item["label"], "kind": item["group"], "amount": item["amount"],
    } for item in confirmed.get("latestBreakdown", []) if item.get("amount") not in (None, 0)]


def _airmate_analysis_snapshot():
    return json.loads(AIR_MATE_ANALYSIS_PATH.read_text(encoding="utf-8"))


def _product_analysis_history():
    if not PRODUCT_HISTORY_PATH.is_file():
        return {
            "schemaVersion": 1,
            "source": "未取得",
            "scope": "店舗（門仲どらやき どら山）",
            "months": [],
            "grossProfitStatus": "商品別月次データを取得できません",
        }
    return json.loads(PRODUCT_HISTORY_PATH.read_text(encoding="utf-8"))


def _labor_daily_history():
    if not LABOR_DAILY_HISTORY_PATH.is_file():
        return {
            "schemaVersion": 1,
            "source": "未取得",
            "months": [],
            "scopeNote": "日別人件費データを取得できません",
        }
    return json.loads(LABOR_DAILY_HISTORY_PATH.read_text(encoding="utf-8"))


def _add_operational_labor(cost_analysis, labor_daily_history):
    """進行中の会計月へ、保存済みの日別シフト人件費を別項目で添える。"""
    enriched = deepcopy(cost_analysis)
    labor_by_month = {
        row.get("month"): row for row in labor_daily_history.get("months", [])
    }
    for row in enriched.get("series", []):
        labor_row = labor_by_month.get(row.get("key"))
        if row.get("status") == "確定" or not labor_row:
            continue
        operational = labor_row.get("dailyLaborTotal")
        if operational is None:
            continue
        reconciliation = LABOR_RECONCILIATIONS.get(row.get("key"))
        if reconciliation:
            row["accountingInternalLabor"] = row.get("internalLabor")
            row["accountingProfit"] = row.get("profit")
            row["shiftCostEstimate"] = operational
            row["laborReconciliation"] = deepcopy(reconciliation)
            row["internalLabor"] = None
            row["labor"] = None
            row["sellingExpenses"] = None
            row["operatingProfit"] = None
            row["profit"] = None
            continue
        row["operationalInternalLabor"] = operational
        row["accountingInternalLabor"] = row.get("internalLabor")
        row["operationalInternalLaborSource"] = "Airシフト給与計算表＋タイミー（Excel未反映・速報値）"
        row["operationalInternalLaborStatus"] = "運営実績（Excel未反映）"
        row["accountingInternalLaborStatus"] = "給与未反映・法定福利費のみ"
    return enriched


def _fixed_cost_history(cost_analysis):
    """管理会計PLから重複のない固定費5科目を月別に集計する。"""
    line_by_label = {
        line.get("label"): line
        for line in cost_analysis.get("lines", [])
        if line.get("label") in FIXED_COST_CATEGORIES
    }
    rows = []
    for month in cost_analysis.get("months", []):
        key = month.get("key")
        status = month.get("status")
        overrides = FIXED_COST_PROVISIONAL_OVERRIDES.get(key, {})
        reconciliation = FIXED_COST_RECONCILIATIONS.get(key, {})
        fixed_cost_closed = reconciliation.get("status") == "確定"
        details = []
        missing = []
        has_provisional_source = False
        for category in FIXED_COST_CATEGORIES:
            saved = (line_by_label.get(category) or {}).get("values", {}).get(key)
            source = reconciliation.get("sourceLabel") or "管理会計PL（Excel原本）"
            evidence = "固定費締め済み" if fixed_cost_closed else "保存済み"
            amount = saved
            is_provisional = False
            if status != "確定" and not fixed_cost_closed:
                if category in overrides:
                    amount = overrides[category]
                    source = "freee経費ミラー（Excel未反映・速報値）"
                    evidence = "Excel未反映・freee速報値"
                    is_provisional = True
                elif not saved:
                    amount = None
                    evidence = "未反映"
            if is_provisional:
                has_provisional_source = True
            if amount is None:
                missing.append(category)
            details.append({
                "category": category,
                "amount": amount,
                "source": source,
                "evidence": evidence,
                "isProvisional": is_provisional,
            })
        known_amounts = [item["amount"] for item in details if item["amount"] is not None]
        total = sum(known_amounts) if known_amounts else None
        is_lower_bound = bool(missing) and total is not None
        expected_total = reconciliation.get("expectedTotal")
        fixed_cost_reconciled = fixed_cost_closed and total == expected_total and not missing
        if status == "確定" or fixed_cost_reconciled:
            display_status = "確定"
        elif fixed_cost_closed:
            display_status = "要再照合"
        elif total is not None:
            display_status = "進行中・一部のみ"
        else:
            display_status = "未取得"
        rows.append({
            "key": key,
            "period": month.get("label") or key,
            "status": display_status,
            "total": total,
            "isLowerBound": is_lower_bound,
            "missingCategories": missing,
            "details": details,
            "hasProvisionalSource": has_provisional_source,
            "bookedTotal": reconciliation.get("bookedTotal"),
            "accrualAdjustment": reconciliation.get("accrualAdjustment"),
            "employeeContribution": reconciliation.get("employeeContribution"),
            "netCompanyBurden": reconciliation.get("netCompanyBurden"),
            "reconciliationNote": reconciliation.get("note"),
        })
    return rows


def _event_target_summary(details):
    per_day = airmate_targets_layer.event_daily_sales_target()
    by_date = {}
    for item in details:
        day = item.get("date")
        if not day:
            continue
        key = (str(item.get("name") or "名称未設定"), str(item.get("venue") or ""))
        by_date.setdefault(day, set()).add(key)
    daily = [{
        "date": day,
        "eventCount": len(events),
        "targetSales": airmate_targets_layer.event_sales_target(len(events)),
    } for day, events in sorted(by_date.items())]
    event_days = sum(item["eventCount"] for item in daily)
    sales = sum(item.get("sales") or 0 for item in details)
    target = airmate_targets_layer.event_sales_target(event_days)
    return {
        "targetPerEventDay": per_day,
        "noEventDayTarget": 0,
        "eventDays": event_days,
        "calendarDays": len(daily),
        "target": target,
        "sales": sales,
        "variance": sales - target,
        "achievement": round(sales / target * 100, 1) if target else None,
        "dailyTargets": daily,
        "rule": "税込220,000円 × 催事数 × 開催日数。催事のない日は0円",
        "scopeNote": "取得済みの催事日程分。今後の開催予定は日程登録後に加算。",
    }


def get_management_analysis():
    now = datetime.now(JST)
    confirmed = management_layer.get_dorayama_management()
    labor_daily_history = _labor_daily_history()
    cost_analysis = _add_operational_labor(
        management_pl_workbook_layer.get_cost_analysis(), labor_daily_history
    )
    confirmed = _apply_latest_management_pl(confirmed, cost_analysis)
    sync = management_sync_layer.get_management_sync(today=now.date())
    lines = _pnl_lines()
    goal_settings = target_settings_layer.get_target_settings(cost_analysis)
    airmate_history = management_sync_layer.read_airmate_history(now.date())
    monthly_rows = _monthly_rows(confirmed, goal_settings, airmate_history)
    month_preview = sync.get("monthSummary") or {}
    workbook_catalog = budget_workbook_layer.catalog()
    legacy_errors = sum(item["errorCount"] for item in workbook_catalog if item["quality"] == "旧様式参考")
    current_sales = month_preview.get("sales")
    current_cost = month_preview.get("knownCost")
    current_target = airmate_targets_layer.get_month(now.year, now.month) or {}
    current_goal = next(
        (row for row in goal_settings["months"] if row["yearMonth"] == f"{now.year:04d}-{now.month:02d}"),
        {},
    )
    current_budget = current_goal.get("totalTarget", current_target.get("total"))
    store_details = sync.get("storeDetails") or []
    event_details = sync.get("eventDetails") or []
    event_target = _event_target_summary(event_details)
    airmate_analysis = _airmate_analysis_snapshot()
    product_history = _product_analysis_history()
    provisional_month = (
        next((row.get("key") for row in cost_analysis.get("months", []) if row.get("status") != "確定"), None)
        or confirmed.get("augustProvisional", {}).get("month")
        or "8月"
    )
    current_month_label = f"{now.month}月"
    return {
        "schemaVersion": 3,
        "mode": "read-only",
        "updatedAt": now.isoformat(timespec="minutes"),
        "currentMonthLabel": current_month_label,
        "navigation": NAVIGATION,
        "referenceSheets": REFERENCE_SHEETS,
        "workbook": {
            "source": "【2026年 どら山】予算計画（原本様式）.xlsx",
            "sheetCount": len(workbook_catalog),
            "catalog": workbook_catalog,
            "legacyFormulaErrorCount": legacy_errors,
            "rule": "15シートを原本表示に残し、2026確定実績と旧様式参考を分離",
        },
        "managementPlSource": {
            "source": cost_analysis.get("fileName") or "【第10期 どら山】管理会計PL.xlsx",
            "period": cost_analysis.get("summary", {}).get("period") or confirmed.get("cumulativePeriod"),
            "status": confirmed.get("statusLabel"),
            "updatedAt": cost_analysis.get("updatedAt") or confirmed.get("sourceUpdatedAt"),
        },
        "confirmed": confirmed,
        "budget": airmate_targets_layer.summary(),
        "goalSettings": goal_settings,
        "current": {
            **month_preview,
            "latestDate": sync.get("latestDate"),
            "connected": bool(sync.get("connected")),
            "budget": current_budget,
            "storeBudget": current_goal.get("storeTarget", current_target.get("store")),
            "eventBudget": current_goal.get("eventTarget", current_target.get("event")),
            "budgetAchievement": round(current_sales / current_budget * 100, 1)
            if current_sales is not None and current_budget else None,
            "budgetRemaining": max(current_budget - current_sales, 0)
            if current_sales is not None and current_budget is not None else None,
            "knownMarginRate": round((current_sales - current_cost) / current_sales * 100, 1)
            if current_sales and current_cost is not None else None,
        },
        "monthly": monthly_rows,
        "pnlLines": lines,
        "costBreakdown": _cost_breakdown(confirmed),
        "costAnalysis": cost_analysis,
        "daily": sync.get("records") or [],
        "airmateDaily": sync.get("airmateDaily") or [],
        "store": {
            "details": store_details,
            "sales": sum(item.get("sales") or 0 for item in store_details),
            "customers": sum(item.get("customers") or 0 for item in store_details),
            "units": sum(item.get("units") or 0 for item in store_details),
        },
        "events": {
            "details": event_details,
            "sales": sum(item.get("sales") or 0 for item in event_details),
            "commission": sum(item.get("commission") or 0 for item in event_details),
            "profitBeforeFixed": sum(item.get("profitBeforeFixed") or 0 for item in event_details),
            **event_target,
        },
        "products": sync.get("productTotals") or [],
        "productHistory": product_history,
        "laborDailyHistory": labor_daily_history,
        "airmateAnalysis": airmate_analysis,
        "weekdayTimeHistory": {
            "source": "保存済みAirメイト日次CSV（読み取り専用）",
            "updatedAt": airmate_analysis.get("updatedAt"),
            "from": min((row["date"] for row in airmate_history), default=None),
            "to": max((row["date"] for row in airmate_history), default=None),
            "daily": airmate_history,
            "timeBands": (airmate_analysis.get("weekdayTimeAnalysis") or {}).get("timeBands", []),
            "timeBandStatus": "時間帯別売上はAirメイトCSVに含まれていないため未取得。0円とは扱いません。",
            "personHourStatus": "日別・店舗別の実労働時間が同じ粒度で揃うまで未取得。推測表示しません。",
        },
        "labor": sync.get("laborSummary") or {},
        "fixed": {
            "details": sync.get("fixedDetails") or [],
            "paymentMethods": sync.get("paymentMethods") or [],
            "referenceTotal": month_preview.get("fixedCostReference"),
            "paymentExpenseReference": month_preview.get("paymentExpenseReference"),
            "evidencePendingCount": month_preview.get("fixedEvidencePendingCount"),
            "history": _fixed_cost_history(cost_analysis),
            "categories": list(FIXED_COST_CATEGORIES),
            "scopeNote": "固定費は地代家賃・賃借料・水道光熱費・通信費・保険料。カード引落、催事販売外注、消耗品、広告宣伝費は含めません。",
        },
        "freeeProgress": confirmed.get("automationProgress") or {},
        "impact": confirmed.get("impact"),
        "expenseAudit": confirmed.get("expenseAudit"),
        "augustReconciliation": {
            "provisionalMonth": provisional_month,
            "monthMismatch": provisional_month != current_month_label,
            "status": "要照合" if provisional_month == current_month_label else "スナップショット更新待ち",
            "appAsOf": sync.get("latestDate"),
            "appSales": current_sales,
            "freeeSales": confirmed.get("augustProvisional", {}).get("sales"),
            "salesDifference": (
                current_sales - confirmed.get("augustProvisional", {}).get("sales")
                if current_sales is not None and confirmed.get("augustProvisional", {}).get("sales") is not None
                else None
            ),
            "appStoreSales": month_preview.get("storeSales"),
            "freeeStoreSales": confirmed.get("augustProvisional", {}).get("storeSales"),
            "storeDifference": (
                month_preview.get("storeSales") - confirmed.get("augustProvisional", {}).get("storeSales")
                if month_preview.get("storeSales") is not None and confirmed.get("augustProvisional", {}).get("storeSales") is not None
                else None
            ),
            "appEventSales": month_preview.get("eventSales"),
            "freeeEventSales": confirmed.get("augustProvisional", {}).get("eventSales"),
            "eventDifference": (
                month_preview.get("eventSales") - confirmed.get("augustProvisional", {}).get("eventSales")
                if month_preview.get("eventSales") is not None and confirmed.get("augustProvisional", {}).get("eventSales") is not None
                else None
            ),
            "reason": (
                "店舗分は一致。差額は催事側にあり、TakeEats売上・手数料とAirレジ差も含めて確定前"
                if provisional_month == current_month_label
                else f"freee側スナップショットは{provisional_month}分、運営速報は{current_month_label}分のため単純比較しない。管理会計PLの更新後に再照合"
            ),
        },
        "qualityChecks": sync.get("qualityChecks") or [],
        "decisions": confirmed.get("todayDecisions") or [],
    }
