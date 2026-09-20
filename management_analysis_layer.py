#!/usr/bin/env python3
"""店長・経営者共通の多角分析データ（読み取り専用）。"""
from copy import deepcopy
import json
from datetime import datetime, timedelta
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
FIXED_COST_DETAIL_PATH = BASE / "data" / "fixed_cost_details_2026.json"
FIXED_COST_CATEGORIES = ("地代家賃", "賃借料", "水道光熱費", "通信費", "保険料")
FIXED_COST_PROVISIONAL_OVERRIDES = {}
FIXED_COST_RECONCILIATIONS = {
    "8月": {
        "status": "確定",
        "expectedTotal": 518494,
        "bookedTotal": 518494,
        "accrualAdjustment": 0,
        "employeeContribution": 35000,
        "netCompanyBurden": 483494,
        # 8月固定費の締め（498,666円）のあとでfreeeへ記帳された分。Excel側が同額に更新済みなら二重に足さない。
        "lateBookings": {"通信費": 16218, "水道光熱費": 3610},
        "lateBookingsBaseTotal": 498666,
        "sourceLabel": "管理会計PL（8月固定費締め）＋締め後のfreee記帳",
        "note": (
            "8月固定費の締め（498,666円）のあとに、freeeへソフトバンク16,218円（8/15・通信費）と"
            "ニチガス3,610円（8/27・水道光熱費）が記帳されたため加算しました。"
            "上下水道1,958円は、締め時に入れた月次調整と同じ請求がfreeeへ記帳されたため、調整側を外して二重計上を避けています。"
            "役員社宅の本人負担35,000円を差し引いた会社実負担は483,494円です。"
        ),
    },
}

# 締めに間に合った請求書で金額が確定している催事販売員費（税抜）。速報損益の仮置きより優先する。
KNOWN_EVENT_STAFFING = {
    "8月": {
        "amountExTax": 1702000,
        "amountIncTax": 1872200,
        "sourceLabel": "ディースパーク8月度請求書（2026-09-08受領・原本確認済み・支払予定9/30）",
    },
}

FLASH_ACCURACY_PATH = BASE / "data" / "flash_accuracy_2026.json"
FLASH_STAFFING_RATE_MONTHS = 3

# 月次確定に必要な資料の状態。state: 解消済み／先方待ち／社内対応／本人・税理士判断
CLOSE_CHECKLISTS = {
    "8月": [
        {"item": "固定費", "state": "解消済み", "detail": "518,494円で確定（締め後の記帳3件を反映済み）"},
        {"item": "管理会計PL（Excel）の8月列の更新", "state": "社内対応", "detail": "8/17時点の途中経過のまま（Excelは9/2以降未更新）。最新のfreee記帳で作り直したうえで、給与未反映などの注意点を保って反映する"},
        {"item": "催事販売員費の請求書", "state": "解消済み", "detail": "ディースパーク8月度 税抜1,702,000円を受領。帳簿の支払月ベース計上との重複整理が残る"},
        {"item": "Airシフトの時給未設定・打刻漏れ", "state": "解消済み", "detail": "本人確認で解消（大西さん時給1,250円・太田さん退勤16:00）"},
        {"item": "未計上経費のfreee登録", "state": "社内対応", "detail": "登録済み：コウヤマ・スターフライヤー・カイコム・木下製粉・グラフィック・ラクスル・Indeed。残：食品微生物センター（未払金の扱い）・正体不明2件"},
        {"item": "上野・川越・立川の会場別8月精算書", "state": "先方待ち", "detail": "会場別売上が分かる精算書が未着。会計上の催事売上が確定できない最大の要因"},
        {"item": "TakeEatsの8月注文・手数料明細", "state": "社内対応", "detail": "8月の受取完了48注文172,923円は把握済み。店頭払い分のAirレジ重複整理と手数料明細が残る"},
        {"item": "内部人件費の確定", "state": "社内対応", "detail": "給与確定額1,314,804円とシフト原価1,766,227円の整合・社会保険の会社負担配賦が残る"},
        {"item": "8月の減価償却", "state": "本人・税理士判断", "detail": "固定資産台帳と耐用年数が必要（3期分ゼロのまま）"},
    ],
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
        "shiftCostEstimate": 1766227,
        "unpricedShiftCount": 0,
        "unpricedShiftHours": 0,
        "missingPunchCount": 0,
        "sourceLabel": "給与確定資料・タイミー8月請求書・Airシフト勤務表の再照合",
        "note": (
            "1,766,227円は固定給社員も時給換算した勤務シフト原価の参考値で、給与確定額ではありません。"
            "時給未設定4勤務（大西実可子・時給1,250円で確定）と退勤未打刻1勤務"
            "（太田光・8/1退勤16:00で確定）は本人確認により解消済み。"
            "固定給社員の時給換算と、7/16〜8/15給与確定額1,314,804円との整合確認がまだのため、"
            "8月内部人件費と経常利益は引き続き確定表示しません。"
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


def _fixed_cost_detail_snapshot():
    if not FIXED_COST_DETAIL_PATH.is_file():
        return {"months": []}
    try:
        return json.loads(FIXED_COST_DETAIL_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"months": []}


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
    detail_snapshot = _fixed_cost_detail_snapshot()
    detail_by_month = {
        row.get("month"): row for row in detail_snapshot.get("months", [])
    }
    line_by_label = {
        line.get("label"): line
        for line in cost_analysis.get("lines", [])
        if line.get("label") in FIXED_COST_CATEGORIES
    }
    rows = []
    for month in cost_analysis.get("months", []):
        key = month.get("key")
        period = month.get("label") or key
        try:
            year_text, month_text = period.split("/")
            detail_month_key = f"{int(year_text):04d}-{int(month_text):02d}"
        except (AttributeError, TypeError, ValueError):
            detail_month_key = None
        month_detail = detail_by_month.get(detail_month_key, {})
        transactions = deepcopy(month_detail.get("transactions") or [])
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
            category_transactions = [
                row for row in transactions if row.get("category") == category
            ]
            details.append({
                "category": category,
                "amount": amount,
                "source": source,
                "evidence": evidence,
                "isProvisional": is_provisional,
                "transactions": category_transactions,
                "transactionTotal": sum(row.get("amount") or 0 for row in category_transactions),
            })
        late = reconciliation.get("lateBookings") or {}
        if fixed_cost_closed and late:
            base_total = sum(item["amount"] or 0 for item in details)
            # Excelがまだ締め時点の金額のときだけ加算する。更新済みなら加算せず、下の照合に任せる。
            if base_total == reconciliation.get("lateBookingsBaseTotal"):
                for item in details:
                    if item["category"] in late and item["amount"] is not None:
                        item["amount"] += late[item["category"]]
                        item["source"] = f"{item['source']}（締め後の記帳{late[item['category']]:,}円を加算）"
        known_amounts = [item["amount"] for item in details if item["amount"] is not None]
        total = sum(known_amounts) if known_amounts else None
        detail_total = sum(row.get("amount") or 0 for row in transactions)
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
            "period": period,
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
            "transactions": transactions,
            "transactionCount": len(transactions),
            "transactionTotal": detail_total if transactions else None,
            "transactionDifference": total - detail_total if total is not None and transactions else None,
            "transactionMatchesTotal": bool(transactions) and total == detail_total,
            "transactionSource": detail_snapshot.get("source"),
            "transactionUpdatedAt": detail_snapshot.get("generatedAt"),
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


def flash_components(summary, fixed_total, fixed_basis, event_staffing, staffing_basis):
    """運営ベース（Airメイト・Airレジ日次）の速報損益を、項目ごとの根拠つきで組み立てる。

    会計上の売上計上（会場入金ベース）とはずれるため、経常利益とは別物として扱う。
    コスト側は過去月の確定値とよく合うが、損益側は催事の計上時期でずれる。
    """
    sales = summary.get("sales") or 0
    known_cost = summary.get("knownCost") or 0
    cost_total = known_cost + (fixed_total or 0) + (event_staffing or 0)
    return {
        "opsSales": sales,
        "knownCost": known_cost,
        "fixedCost": fixed_total,
        "fixedCostBasis": fixed_basis,
        "eventStaffing": event_staffing,
        "eventStaffingBasis": staffing_basis,
        "costEstimate": cost_total,
        "opsProfit": sales - cost_total,
    }


def _month_end(year, month):
    if month == 12:
        return datetime(year, 12, 31, tzinfo=JST).date()
    return (datetime(year, month + 1, 1, tzinfo=JST) - timedelta(days=1)).date()


def _staffing_rate(monthly_rows, daily_totals_by_month):
    """直近の確定月について、催事販売員費÷催事の運営売上を求める（速報の仮置き用）。"""
    picked = []
    for index, row in enumerate(monthly_rows):
        if row.get("dataStatus") != "管理会計PL確定":
            continue
        ops_event = (daily_totals_by_month.get(f"2026-{index + 1:02d}") or {}).get("eventSales") or 0
        if row.get("eventStaffing") is not None and ops_event > 0:
            picked.append((row["eventStaffing"], ops_event))
    picked = picked[-FLASH_STAFFING_RATE_MONTHS:]
    total_ops = sum(item[1] for item in picked)
    if not picked or total_ops <= 0:
        return None, ""
    rate = sum(item[0] for item in picked) / total_ops
    return rate, f"直近{len(picked)}か月の確定実績（販売員費÷催事の運営売上＝{rate * 100:.1f}%）で仮置き"


def _flash_accuracy():
    try:
        return json.loads(FLASH_ACCURACY_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _attach_month_phases(monthly_rows, fixed_history, current_month_number, syncs, staffing_rate, staffing_basis):
    """月ごとに「確定／確定待ち／進行中／未着手」を付け、確定前の月には速報を添える。"""
    fixed_by_month = {row.get("key"): row for row in fixed_history}
    last_confirmed_fixed = None
    for index, row in enumerate(monthly_rows):
        month_number = index + 1
        label = row["month"]
        fixed_row = fixed_by_month.get(label) or {}
        if month_number == 1:
            # 1月は前期（第9期）の参考値。今期の確定・速報の対象にしない
            row["phase"] = "対象外"
            row["flash"] = None
            continue
        if row.get("dataStatus") == "管理会計PL確定":
            row["phase"] = "確定"
            row["flash"] = None
            if fixed_row.get("total") is not None:
                last_confirmed_fixed = (label, fixed_row["total"])
            continue
        if month_number < current_month_number:
            row["phase"] = "確定待ち"
        elif month_number == current_month_number:
            row["phase"] = "進行中"
        else:
            row["phase"] = "未着手"
        summary = (syncs.get(month_number) or {}).get("monthSummary")
        row["flash"] = None
        if row["phase"] == "未着手" or not summary or not summary.get("sales"):
            continue
        if fixed_row.get("status") == "確定" and fixed_row.get("total") is not None:
            fixed_total, fixed_basis = fixed_row["total"], "確定"
        elif last_confirmed_fixed:
            fixed_total = last_confirmed_fixed[1]
            fixed_basis = f"{last_confirmed_fixed[0]}の確定実績で仮置き"
        else:
            fixed_total, fixed_basis = None, "未取得"
        known_staffing = KNOWN_EVENT_STAFFING.get(label)
        if known_staffing:
            staffing, staffing_source = known_staffing["amountExTax"], known_staffing["sourceLabel"]
        elif staffing_rate is not None:
            staffing = round((summary.get("eventSales") or 0) * staffing_rate)
            staffing_source = staffing_basis
        else:
            staffing, staffing_source = None, "未取得"
        components = flash_components(summary, fixed_total, fixed_basis, staffing, staffing_source)
        components.update({
            "status": "速報",
            "basis": "運営ベース（Airメイト・Airレジ日次実績）",
            "throughDate": (syncs.get(month_number) or {}).get("latestDate"),
            "days": len((syncs.get(month_number) or {}).get("records") or []),
            "storeSales": summary.get("storeSales"),
            "eventSales": summary.get("eventSales"),
            "missing": [
                "会社負担の社会保険", "減価償却", "会場精算による会計上の催事売上", "未払費用・締め後の追加請求",
            ],
        })
        row["flash"] = components
        # 確定前の月は、会計途中の売上・原価ではなく運営ベースの売上で表示する（画面の数字をそろえる）
        if components["storeSales"] is not None and components["eventSales"] is not None:
            row["accountingSales"] = row.get("sales")
            row["storeSales"] = components["storeSales"]
            row["eventSales"] = components["eventSales"]
            row["sales"] = components["opsSales"]
            row["salesBasis"] = "運営売上（Airメイト・Airレジ日次）"
            row["costOfSales"] = None
            row["grossProfit"] = None
            if row.get("budget") is not None:
                row["budgetVariance"] = row["sales"] - row["budget"]
    return monthly_rows


def _close_status(monthly_rows, current_month_label, now):
    confirmed = [row["month"] for row in monthly_rows if row.get("phase") == "確定"]
    closing = next((row for row in monthly_rows if row.get("phase") == "確定待ち"), None)
    current = next((row for row in monthly_rows if row.get("phase") == "進行中"), None)
    checklist = CLOSE_CHECKLISTS.get(closing["month"]) if closing else None
    open_items = [item for item in (checklist or []) if item["state"] != "解消済み"]
    return {
        "confirmedThrough": confirmed[-1] if confirmed else None,
        "closingMonth": closing["month"] if closing else None,
        "currentMonth": current["month"] if current else current_month_label,
        "checklist": checklist,
        "openItemCount": len(open_items),
        "rule": (
            "経常利益は会計上の売上・費用がそろう翌月以降に確定します。確定前の月は、"
            "運営ベースの速報（売上・コスト見込）だけを別枠で表示し、経常利益とは呼びません。"
        ),
        "flashAccuracy": _flash_accuracy(),
        "asOf": now.isoformat(timespec="minutes"),
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
    fixed_history = _fixed_cost_history(cost_analysis)
    # 確定前の過去月（先月など）は、その月末時点の日次台帳で速報を組み立てる
    syncs = {now.month: sync}
    for index, row in enumerate(monthly_rows):
        month_number = index + 1
        if month_number == 1 or month_number >= now.month or row.get("dataStatus") == "管理会計PL確定":
            continue
        try:
            syncs[month_number] = management_sync_layer.get_management_sync(today=_month_end(now.year, month_number))
        except Exception:  # 速報が作れなくても確定側の表示は止めない
            continue
    staffing_rate, staffing_basis = _staffing_rate(
        monthly_rows, _monthly_daily_sales_totals(airmate_history)
    )
    monthly_rows = _attach_month_phases(
        monthly_rows, fixed_history, now.month, syncs, staffing_rate, staffing_basis
    )
    close_status = _close_status(monthly_rows, f"{now.month}月", now)
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
    provisional_pl = confirmed.get("augustProvisional", {})
    recon_number = int(provisional_month[:-1]) if str(provisional_month).endswith("月") else None
    recon_sync = syncs.get(recon_number) if recon_number and recon_number != now.month else sync
    recon_month_mismatch = recon_sync is None and provisional_month != current_month_label
    recon_sync = recon_sync or sync
    recon_summary = recon_sync.get("monthSummary") or month_preview

    def _diff(app_value, freee_value):
        return app_value - freee_value if app_value is not None and freee_value is not None else None

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
        "closeStatus": close_status,
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
            "history": fixed_history,
            "categories": list(FIXED_COST_CATEGORIES),
            "scopeNote": "固定費は地代家賃・賃借料・水道光熱費・通信費・保険料。カード引落、催事販売外注、消耗品、広告宣伝費は含めません。",
        },
        "freeeProgress": confirmed.get("automationProgress") or {},
        "impact": confirmed.get("impact"),
        "expenseAudit": confirmed.get("expenseAudit"),
        "augustReconciliation": {
            "provisionalMonth": provisional_month,
            "monthMismatch": recon_month_mismatch,
            "status": "要照合" if not recon_month_mismatch else "スナップショット更新待ち",
            "appAsOf": recon_sync.get("latestDate"),
            "appSales": recon_summary.get("sales"),
            "freeeSales": provisional_pl.get("sales"),
            "salesDifference": _diff(recon_summary.get("sales"), provisional_pl.get("sales")),
            "appStoreSales": recon_summary.get("storeSales"),
            "freeeStoreSales": provisional_pl.get("storeSales"),
            "storeDifference": _diff(recon_summary.get("storeSales"), provisional_pl.get("storeSales")),
            "appEventSales": recon_summary.get("eventSales"),
            "freeeEventSales": provisional_pl.get("eventSales"),
            "eventDifference": _diff(recon_summary.get("eventSales"), provisional_pl.get("eventSales")),
            "reason": (
                f"{provisional_month}の運営売上（Airメイト・Airレジ）と、freee管理会計PLの途中経過を同じ月どうしで比べています。"
                f"管理会計PLの{provisional_month}列は{provisional_pl.get('asOf') or '古い時点'}時点の途中経過のままで、最新のfreee記帳を反映していません。"
                "加えて運営売上は販売日ベース、freeeは会場精算・入金ベースのため、"
                "PLを最新化し、会場別の精算書とTakeEats明細がそろうまで、この差は確定できません。"
                if not recon_month_mismatch
                else f"freee側スナップショットは{provisional_month}分、運営速報は{current_month_label}分のため単純比較しない。管理会計PLの更新後に再照合"
            ),
        },
        "qualityChecks": sync.get("qualityChecks") or [],
        "decisions": confirmed.get("todayDecisions") or [],
    }
