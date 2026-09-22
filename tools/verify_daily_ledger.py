#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""日次経営台帳と経常利益画面の安全確認。外部データへは接続しない。"""
import json
import os
import re
import subprocess
import sys
import tempfile
import types
from datetime import datetime
from pathlib import Path

import openpyxl

BASE = Path(__file__).resolve().parent.parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

# 検証ではGoogleへ接続しないため、実行環境にSDKが無い場合だけ最小の読取口を代用する。
try:
    import gspread  # noqa: F401
except ModuleNotFoundError:
    gspread_stub = types.ModuleType("gspread")
    gspread_stub.authorize = lambda credentials: None
    gspread_stub.utils = types.SimpleNamespace(rowcol_to_a1=lambda row, col: "A1")
    sys.modules["gspread"] = gspread_stub
    google_module = types.ModuleType("google")
    oauth_module = types.ModuleType("google.oauth2")
    service_module = types.ModuleType("google.oauth2.service_account")
    service_module.Credentials = type("Credentials", (), {})
    sys.modules.setdefault("google", google_module)
    sys.modules.setdefault("google.oauth2", oauth_module)
    sys.modules["google.oauth2.service_account"] = service_module

import daily_ledger
import airmate_targets_layer
import budget_workbook_layer
import management_analysis_layer
import management_layer
import management_pl_workbook_layer
import management_sync_layer
import target_settings_layer


def verify_management():
    data = management_layer.get_dorayama_management()
    latest = data["latest"]
    assert latest["sales"] == 4286386
    assert latest["costOfSales"] == 1840450
    assert latest["grossProfit"] == 2445936
    assert latest["operatingExpenses"] == 2974830
    assert latest["costTotal"] == 4815280
    assert latest["profit"] == -528894
    assert latest["cumulative"] == -3400014
    assert latest["budget"] == 8462000
    assert data["budgetGap"] == 4175614
    assert data["achievement"] == 50.7
    assert data["breakEvenGap"] is None
    assert data["cumulative"]["sales"] == 38968098
    assert data["cumulative"]["grossProfit"] == 23498705
    assert data["cumulative"]["labor"] == 20892320
    assert data["cumulative"]["eventStaffing"] == 9608656
    assert data["cumulative"]["labor"] - data["cumulative"]["eventStaffing"] == 11283664
    assert data["cumulative"]["profit"] == -3400014
    assert data["automationProgress"]["completedCorrections"] == 1211
    assert data["automationProgress"]["failedCorrections"] == 0
    assert data["months"][0]["dataStatus"] == "Excel原本参考"
    assert data["months"][1]["dataStatus"] == "freee是正後"
    confirmed_months = data["months"][1:7]
    assert confirmed_months[0]["eventStaffing"] == 3964675
    assert confirmed_months[0]["internalLabor"] == 2057854
    assert confirmed_months[-1]["eventStaffing"] == 1073600
    assert confirmed_months[-1]["internalLabor"] == 1371142
    assert sum(row["internalLabor"] for row in confirmed_months) == 11283664
    event_labor = next(item["amount"] for item in data["latestBreakdown"] if item["label"] == "マネキン費（催事販売員の派遣）")
    store_side_labor = latest["labor"] - event_labor
    assert event_labor == 1073600
    assert store_side_labor == 1371142
    assert round(store_side_labor / latest["storeSales"] * 100, 1) == 76.2
    assert round(event_labor / latest["eventSales"] * 100, 1) == 43.2
    assert round(latest["storeSales"] * 0.25) == 450055
    assert round(latest["eventSales"] * 0.25) == 621542
    json.dumps(data, ensure_ascii=False, allow_nan=False)


def verify_airmate_targets():
    source = airmate_targets_layer.summary()
    assert source["annualSalesTarget"] == 101169074
    assert source["stores"]["event"]["annualSalesTarget"] == 74300000
    assert source["stores"]["store"]["annualSalesTarget"] == 26869074
    assert sum(row["total"] for row in source["months"]) == 101169074
    assert airmate_targets_layer.sales_target_for(2026, 1) is None
    assert airmate_targets_layer.sales_target_for(2026, 7) == 8462000
    assert airmate_targets_layer.sales_target_for(2026, 8) == 8298000
    assert airmate_targets_layer.event_daily_sales_target() == 220000
    assert airmate_targets_layer.event_sales_target(0) == 0
    assert airmate_targets_layer.event_sales_target(3) == 660000
    assert source["eventOperatingTarget"]["taxBasis"] == "税込"
    assert source["eventOperatingTarget"]["noEventDayAmountGross"] == 0
    assert source["costTargetUsable"] is False
    event_target = management_analysis_layer._event_target_summary([
        {"date": "2026-08-01", "name": "催事A", "venue": "会場1", "sales": 100000},
        {"date": "2026-08-01", "name": "催事A", "venue": "会場1", "sales": 0},
        {"date": "2026-08-01", "name": "催事B", "venue": "会場2", "sales": 200000},
        {"date": "2026-08-02", "name": "催事A", "venue": "会場1", "sales": 300000},
    ])
    assert event_target["eventDays"] == 3
    assert event_target["noEventDayTarget"] == 0
    assert event_target["target"] == 660000
    assert event_target["dailyTargets"][0]["targetSales"] == 440000
    json.dumps(source, ensure_ascii=False, allow_nan=False)


def verify_target_settings():
    data = target_settings_layer.get_target_settings()
    august = next(row for row in data["months"] if row["yearMonth"] == "2026-08")
    september = next(row for row in data["months"] if row["yearMonth"] == "2026-09")
    october = next(row for row in data["months"] if row["yearMonth"] == "2026-10")
    assert data["rates"] == {"material": 25.0, "labor": 25.0, "ordinaryProfit": 20.0}
    assert data["annual"]["store"] == 26869074
    assert data["annual"]["eventDays"] == 294
    assert data["annual"]["event"] == 64680000
    assert data["annual"]["total"] == 91549074
    assert august["storeTarget"] == 2198000
    assert august["eventDays"] == 46
    assert august["overlapDays"] == 15
    assert august["eventTarget"] == 10120000
    assert august["totalTarget"] == 12318000
    assert august["materialTarget"] == 3079500
    assert august["laborTarget"] == 3079500
    assert august["ordinaryProfitTarget"] == 2463600
    assert september["eventDays"] == 35
    assert september["eventTarget"] == 7700000
    assert october["eventDays"] == 14
    assert october["eventTarget"] == 3080000
    assert all(row["targetSales"] == row["eventCount"] * 220000 for row in august["daily"])
    assert next(row for row in august["daily"] if row["date"] == "2026-08-20")["targetSales"] == 440000
    json.dumps(data, ensure_ascii=False, allow_nan=False)


def verify_management_pl_workbook():
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "management.xlsx"
        book = openpyxl.Workbook()
        sheet = book.active
        sheet.title = management_pl_workbook_layer.SHEET_NAME
        values = {
            7: ("売上合計", 1000),
            13: ("材料・包材原価 計", 250),
            21: ("マネキン費（催事販売員の派遣）", 100),
            23: ("人件費 合計", 300),
            44: ("販売管理費 計", 500),
            45: ("営業利益", 250),
            48: ("経常利益", 250),
        }
        for row, (label, amount) in values.items():
            sheet[f"A{row}"] = label
            sheet[f"B{row}"] = amount
        book.save(path)
        result = management_pl_workbook_layer.read_workbook(path)
    assert result["available"] is True
    assert result["readOnly"] is True
    assert result["series"][0]["sales"] == 1000
    assert result["series"][0]["internalLabor"] == 200
    assert result["series"][0]["eventStaffing"] == 100
    assert result["series"][0]["other"] == 500
    assert result["series"][0]["sellingExpenses"] == 800
    assert result["series"][0]["profit"] == 250
    json.dumps(result, ensure_ascii=False, allow_nan=False)


def verify_management_analysis():
    old_sync = management_analysis_layer.management_sync_layer.get_management_sync
    try:
        management_analysis_layer.management_sync_layer.get_management_sync = lambda today=None: {
            "connected": True,
            "latestDate": "2026-08-16",
            "monthSummary": {
                "sales": 4432083,
                "storeSales": 1269106,
                "eventSales": 3162977,
                "knownCost": 2880104,
                "profitBeforeFixed": 1551979,
                "fixedCostReference": 1135487,
                "paymentExpenseReference": 3276410,
                "fixedEvidencePendingCount": 45,
            },
            "records": [], "storeDetails": [], "eventDetails": [], "productTotals": [],
            "laborSummary": {}, "fixedDetails": [], "paymentMethods": [], "qualityChecks": [],
        }
        data = management_analysis_layer.get_management_analysis()
    finally:
        management_analysis_layer.management_sync_layer.get_management_sync = old_sync
    assert data["schemaVersion"] == 3
    assert len(data["monthly"]) == 12
    january = data["monthly"][0]
    july = data["monthly"][6]
    august = data["monthly"][7]
    cost_analysis = data["costAnalysis"]
    expected_july = next(row for row in cost_analysis["series"] if row["key"] == "7月")
    assert january["budget"] is None
    assert july["budget"] == 9862000
    assert july["sales"] == expected_july["sales"]
    assert july["profit"] == expected_july["profit"]
    assert july["budgetVariance"] == expected_july["sales"] - july["budget"]
    assert august["budget"] == 12318000
    assert august["storeBudget"] == 2198000
    assert august["eventBudget"] == 10120000
    # 確定前の月は、会計途中の売上ではなく運営売上（このテストでは見本の日次台帳）で表示し、会計側の値は別に残す
    assert august["sales"] == 4432083
    assert august["accountingSales"] == 3627802
    assert august["salesBasis"].startswith("運営売上")
    assert august["dataStatus"] == "管理会計PL進行中"
    assert august["phase"] == "確定待ち"
    assert august["profit"] is None
    assert january["phase"] == "対象外" and january["flash"] is None
    assert july["phase"] == "確定" and july["flash"] is None
    flash = august["flash"]
    assert flash["status"] == "速報"
    assert flash["fixedCost"] == 518494 and flash["fixedCostBasis"] == "確定"
    assert flash["eventStaffing"] == 1702000  # ディースパーク8月度請求書（税抜）
    assert flash["costEstimate"] == 2880104 + 518494 + 1702000
    assert flash["opsProfit"] == 4432083 - flash["costEstimate"]
    close_status = data["closeStatus"]
    assert close_status["confirmedThrough"] == "7月"
    assert close_status["closingMonth"] == "8月"
    assert close_status["openItemCount"] == len([
        item for item in close_status["checklist"] if item["state"] != "解消済み"
    ])
    this_month = datetime.now().month
    september = data["monthly"][8]
    assert september["phase"] == ("未着手" if this_month < 9 else "進行中" if this_month == 9 else "確定待ち")
    assert data["current"]["budget"] == 10050000
    assert data["current"]["budgetRemaining"] == 5617917
    assert data["current"]["budgetAchievement"] == 44.1
    assert data["goalSettings"]["months"][6]["yearMonth"] == "2026-08"
    assert data["goalSettings"]["months"][0]["actual"]["sales"] == 11575876
    assert data["goalSettings"]["months"][5]["actual"]["sales"] == 7229287
    assert data["goalSettings"]["months"][6]["actual"]["sales"] == 3627802
    assert data["goalSettings"]["months"][6]["actual"]["status"] == "進行中"
    assert data["events"]["targetPerEventDay"] == 220000
    assert data["events"]["target"] == 0
    assert cost_analysis["available"] is True
    assert len(cost_analysis["lines"]) >= 40
    confirmed_costs = [row for row in cost_analysis["series"] if row["status"] == "確定"]
    assert cost_analysis["summary"]["internalLabor"] == sum(row["internalLabor"] for row in confirmed_costs)
    assert cost_analysis["summary"]["eventStaffing"] == sum(row["eventStaffing"] for row in confirmed_costs)
    assert cost_analysis["summary"]["profit"] == sum(row["profit"] for row in confirmed_costs)
    assert cost_analysis["summary"]["totalCost"] == cost_analysis["summary"]["material"] + cost_analysis["summary"]["sellingExpenses"]
    assert cost_analysis["summary"]["other"] >= 0
    assert data["confirmed"]["cumulative"]["profit"] == cost_analysis["summary"]["profit"]
    labor_total_line = next(line for line in cost_analysis["lines"] if line["row"] == 23)
    assert all(
        row["internalLabor"] == labor_total_line["values"][row["key"]] - row["eventStaffing"]
        for row in confirmed_costs
    )
    august_cost = next(row for row in cost_analysis["series"] if row["key"] == "8月")
    assert august_cost["internalLabor"] is None
    assert august_cost["profit"] is None
    assert august_cost["laborReconciliation"]["status"] == "再集計中"
    assert next(line for line in cost_analysis["lines"] if line["row"] == 25)["label"] == "その他の外注費"
    weekday_history = data["weekdayTimeHistory"]
    assert weekday_history["from"] == "2026-01-01"
    # to/daily件数/合計は保存済みAirメイトCSVの最新日に追従して毎日伸びる
    # (management_sync_layer.read_airmate_history が today=date.today() 基準)。
    # 固定日付ではなく「増えている」ことだけを検証する。
    assert weekday_history["to"] >= "2026-08-29"
    assert len(weekday_history["daily"]) >= 241
    assert sum(row["storeSales"] for row in weekday_history["daily"]) >= 14996088
    assert sum(row["eventSales"] for row in weekday_history["daily"]) >= 25795932
    # augustReconciliationは実行日が9月に入ると「運営速報=9月分・freee=8月分」の
    # 月ずれ検知(monthMismatch)に切り替わる設計。固定値ではなく構造だけ検証する。
    august_recon = data["augustReconciliation"]
    if august_recon.get("monthMismatch"):
        assert august_recon["reason"]
    else:
        # 同じ月どうしの比較。差額は「運営売上 − 管理会計PLの途中経過」と一致する（PLの更新で値は変わる）
        assert august_recon["appStoreSales"] == 1269106
        assert august_recon["storeDifference"] == august_recon["appStoreSales"] - august_recon["freeeStoreSales"]
        assert august_recon["salesDifference"] == august_recon["appSales"] - august_recon["freeeSales"]
    assert data["freeeProgress"]["historicalUnassignedSales"]["amount"] == 0
    assert data["workbook"]["sheetCount"] == 15
    assert len(data["navigation"]) == 20
    assert any(item["id"] == "targets" for item in data["navigation"])
    # 「今日の速報」は先頭。催事の人件費は請求書の日割り（1会場1日37,000円・税抜）、目標の人件費率は目標設定に連動
    assert data["navigation"][0] == {"id": "today", "label": "今日の速報", "group": "速報"}
    board = data["todayBoard"]
    assert board["eventStaffDailyRate"] == 37000
    assert board["laborRateTarget"] == data["goalSettings"]["rates"]["labor"] == 25.0
    assert 111000 / 3 == 444000 / 12 == 1147000 / 31 == board["eventStaffDailyRate"]
    # 製造実績（作った数×売価）：店舗用と催事ごとに分け、今日より先の日は含めない。売価は商品別の販売額÷個数
    import production_layer
    from datetime import date
    prices = production_layer.unit_prices({"months": [
        {"items": [{"name": "黒どら", "quantity": 100, "sales": 30000}, {"name": "生どら", "quantity": 0, "sales": 0}]},
        {"items": [{"name": "黒どら", "quantity": 100, "sales": 30000}]},
    ]})
    assert prices == {"黒どら": 300}
    week = {"blocks": [
        {"name": "店舗用", "category": "店舗用", "products": [{"name": "黒どら", "actual": [10, 0, None, None, None, None, None]}]},
        {"name": "上野", "category": "催事用", "products": [
            {"name": "あんバター", "actual": [100, 50, 999, None, None, None, None]},
            {"name": "抹茶", "actual": [10, 0, 999, None, None, None, None]},
        ]},
    ]}
    rows = production_layer.aggregate_week(
        week, date(2026, 9, 14), {"黒どら": 300, "あんバター": 400, "旬どら": 450}, date(2026, 9, 1), date(2026, 9, 15)
    )
    assert sorted(rows) == ["2026-09-14", "2026-09-15"]
    assert rows["2026-09-14"]["blocks"] == {
        "店舗用": {"pieces": 10, "value": 3000}, "上野": {"pieces": 110, "value": 44500},
    }
    assert rows["2026-09-14"]["value"] == 47500 and rows["2026-09-14"]["pieces"] == 120
    assert rows["2026-09-15"]["blocks"] == {"上野": {"pieces": 50, "value": 20000}}
    assert data["production"] is None or data["production"]["source"]
    # 毎朝LINEへ送る速報：予算は画面と同じ配り方、数字がそろっていない項目は「入力待ち」と書き、0円や「収まり」と見せない
    import daily_brief
    fake = {
        "updatedAt": "2026-09-21T06:30", "todayBoard": {"laborRateTarget": 25, "eventStaffDailyRate": 37000},
        "goalSettings": {"months": [{"yearMonth": "2026-09", "storeTarget": 300, "eventTarget": 440000,
                                     "daily": [{"date": "2026-09-19", "eventCount": 0, "targetSales": 0},
                                               {"date": "2026-09-20", "eventCount": 1, "targetSales": 220000},
                                               {"date": "2026-09-21", "eventCount": 1, "targetSales": 220000}]}]},
        "weekdayTimeHistory": {"daily": [{"date": "2026-09-19", "storeTargetSales": 100},
                                         {"date": "2026-09-20", "storeTargetSales": 100},
                                         {"date": "2026-09-21", "storeTargetSales": 100}]},
        "daily": [{"date": "2026-09-20", "storeSales": 200, "eventSales": 150000, "storeLabor": 20000, "eventRows": 1}],
        "production": {"daily": [{"date": "2026-09-20", "value": 400000}]},
    }
    assert daily_brief.daily_targets(fake, "2026-09") == {
        "2026-09-19": (100, 0), "2026-09-20": (100, 220000), "2026-09-21": (100, 220000)}
    brief = daily_brief.build_brief(fake)
    assert brief["date"] == "2026-09-20" and brief["complete"] is True
    # LINEの文面は短く：数字を普段扱わないスタッフにも伝わるよう、判定は絵文字＋短い言葉のみ（率だけの「目標内」判定は撤去）
    # 人件費は当日売上でなく、その日の製造実績（作った数×売価）と比べる。文面にも比較先の金額を出す
    assert "人件費 57,000円／製造 400,000円" in brief["text"]
    assert "14.2%" in brief["text"] and "ちょうどよい" in brief["text"] and "目標内" not in brief["text"]
    assert "✅ 店舗 200円（＋100円）" in brief["text"]
    # 一日全体の一言まとめ（headline）：この日は売上未達・人件費は良好 → 中間判定
    assert "🙂 まずまず" in brief["text"]
    # 月間の人件費率は本人指示(2026-09-22)により売上比で見る（日々の判定は製造実績比のまま）
    assert "9月ここまで 売上150,200円（▲69,900円）　😥人件費かなり多め（37.9%）" in brief["text"]
    fake["daily"][0]["eventRows"] = 0
    fake["production"] = {"daily": []}
    pending = daily_brief.build_brief(fake)
    assert pending["complete"] is False
    assert "⏳ 催事 入力待ち" in pending["text"] and "製造実績待ちで判定なし" in pending["text"]
    assert "目標内" not in pending["text"] and "オーバー：" not in pending["text"]
    # 業界ウォッチと同じデザインのHTML：数字・状態がそのまま入り、グラフ（月ここまでの累積）も付く
    import brief_html
    block = brief_html.render_block(brief["data"])
    assert "昨日の速報" in block and "200円" in block and "57,000円" in block
    assert "人件費率" in block and "14.2%" in block and "目標内" in block
    assert block.count("<svg") == 2 and "売上の進み" in block and "人件費の進み" in block
    assert brief["data"]["charts"]["sales"]["actual"][-1] == 150200  # 9/20までの売上累積（店舗200＋催事150,000）は月初からの合計
    assert brief["data"]["charts"]["labor"]["allowed"][-1] == 100000  # 製造実績400,000×25%
    fake["production"] = None  # 製造表を読み取れなかった場合は「入力待ち」ではなく、読めなかった旨を書く（別の言葉で区別）
    unreadable = daily_brief.build_brief(fake)
    assert "製造実績を読めず判定なし" in unreadable["text"] and "製造実績待ちで判定なし" not in unreadable["text"]
    assert unreadable["complete"] is False
    assert "製造表を読み取れませんでした" in brief_html.render_block(unreadable["data"])
    assert "目標内" not in brief_html.render_block(unreadable["data"])
    assert list(dict.fromkeys(item["group"] for item in data["navigation"])) == [
        "速報", "売上", "コスト", "判断", "計画", "原本",
    ]
    snapshot = data["airmateAnalysis"]
    product_history = data["productHistory"]
    assert len(product_history["months"]) == 8
    assert product_history["months"][0]["key"] == "2026-01"
    assert product_history["months"][0]["totals"]["sales"] == 1421848
    assert product_history["months"][-1]["key"] == "2026-08"
    assert product_history["months"][-1]["status"] == "月途中"
    assert product_history["months"][-1]["totals"]["quantity"] == 3840
    assert product_history["months"][-1]["totals"]["sales"] == 1269106
    assert product_history["months"][-1]["items"][0]["name"] == "黒どら"
    assert product_history["months"][-1]["totals"]["estimatedCost"] + product_history["months"][-1]["totals"]["estimatedGrossProfit"] == product_history["months"][-1]["totals"]["sales"]
    labor_daily_history = data["laborDailyHistory"]
    assert [row["key"] for row in labor_daily_history["months"]] == [
        "2026-01", "2026-02", "2026-03", "2026-04", "2026-05", "2026-06", "2026-07", "2026-08",
    ]
    june_labor = next(row for row in labor_daily_history["months"] if row["key"] == "2026-06")
    assert june_labor["key"] == "2026-06"
    assert len(june_labor["daily"]) == 30
    assert sum(row["labor"] for row in june_labor["daily"]) == 1383635
    assert sum(row["storeSales"] for row in june_labor["daily"]) == 1849036
    assert round(sum(row["workHours"] for row in june_labor["daily"]), 2) == 904.84
    february_labor = next(row for row in labor_daily_history["months"] if row["key"] == "2026-02")
    july_labor = next(row for row in labor_daily_history["months"] if row["key"] == "2026-07")
    assert february_labor["dailyLaborTotal"] == 1532553
    assert february_labor["storeSalesTotal"] == 1990141
    assert july_labor["dailyLaborTotal"] == 1450245
    assert july_labor["storeSalesTotal"] == 1753271
    assert july_labor["eventSalesTotal"] == 3164339
    assert july_labor["combinedSalesTotal"] == 4917610
    assert july_labor["laborRate"] == 29.5
    assert july_labor["greenDays"] == 12
    assert july_labor["redDays"] == 19
    assert july_labor["eventSalesPendingDates"] == ["2026-07-07", "2026-07-13"]
    assert july_labor["daily"][0]["laborRate"] == 20.5
    assert july_labor["daily"][1]["laborRate"] == 25.3
    assert july_labor["daily"][6]["eventSalesStatus"] == "未取得"
    august_labor = next(row for row in labor_daily_history["months"] if row["key"] == "2026-08")
    assert august_labor["dailyLaborTotal"] == 1773251
    assert august_labor["storeSalesTotal"] == 2021444
    assert august_labor["eventSalesTotal"] == 7140009
    assert august_labor["combinedSalesTotal"] == 9161453
    assert august_labor["laborRate"] == 19.4
    assert len(august_labor["daily"]) == 31
    assert august_labor["eventSalesPendingDates"] == []
    products = snapshot["productAnalysis"]["items"]
    weekdays = snapshot["weekdayTimeAnalysis"]["weekdays"]
    assert products[0]["name"] == "黒どら"
    assert products[0]["sales"] == 489164
    assert products[5]["grossProfit"] == -1659
    assert products[9]["grossProfit"] == -8640
    assert weekdays[4]["day"] == "金"
    assert weekdays[4]["averageSales"] == 240572
    assert snapshot["weekdayTimeAnalysis"]["overallAverage"] == 187381
    assert "未取得" in snapshot["weekdayTimeAnalysis"]["timeBandStatus"]
    labor_analysis = snapshot["laborAnalysis"]
    assert labor_analysis["monthlyLabor"] == 148192
    assert labor_analysis["monthlySales"] == 3288305
    assert labor_analysis["monthlyLaborRate"] == 4.5
    assert labor_analysis["thresholdRate"] == 25.0
    assert labor_analysis["includedInInternalLabor"] is False
    assert "合算せず" in labor_analysis["accountingTreatment"]
    assert sum(row["sales"] for row in labor_analysis["daily"]) == 3288305
    assert labor_analysis["daily"][-1]["day"] == 17
    assert labor_analysis["daily"][-1]["sales"] == 125328
    sales_missed_days = [row["day"] for row in labor_analysis["daily"] if row["salesTargetMissed"]]
    assert sales_missed_days == [1, 2, 3, 6, 16, 17]
    assert all(not row["workHoursExceeded"] for row in labor_analysis["daily"])
    json.dumps(data, ensure_ascii=False, allow_nan=False)


def verify_deduplication():
    rows = [
        {"source": "Gmail", "status": "速報", "orderId": "A-100", "amount": 12000, "evidence": ["注文メール"]},
        {"source": "カード明細", "status": "確定", "orderId": "A-100", "amount": 12000, "evidence": ["カード利用"]},
        {"source": "freee会計", "status": "突合済", "orderId": "A-100", "amount": 12000, "evidence": ["freee取引"]},
        {"source": "カード明細", "status": "確定", "vendor": "同一業者", "date": "2026-08-01", "amount": 5000},
        {"source": "カード明細", "status": "確定", "vendor": "同一業者", "date": "2026-08-01", "amount": 5000},
    ]
    result = daily_ledger.deduplicate(rows)
    assert len(result) == 3
    merged = next(row for row in result if row.get("orderId") == "A-100")
    assert merged["source"] == "freee会計"
    assert merged["duplicateCount"] == 2
    assert len(merged["sources"]) == 3


def verify_inventory_cost():
    result = daily_ledger.moving_average_usage_cost(10, 1000, [
        {"date": "2026-08-01", "kind": "receipt", "quantity": 10, "amount": 2000},
        {"date": "2026-08-02", "kind": "usage", "quantity": 4},
    ])
    assert result["usageCost"] == 600
    assert result["closingQuantity"] == 16.0
    assert result["closingValue"] == 2400
    empty = daily_ledger.moving_average_usage_cost(0, 0, [{"kind": "usage", "quantity": 5}])
    assert empty["usageCost"] == 0
    assert empty["closingValue"] == 0


def verify_daily_payload():
    old_cost = daily_ledger.cost_layer.get_cost
    old_week = daily_ledger.data_layer.get_week_blocks
    old_inventory = daily_ledger.inventory_layer.get_inventory
    old_sync = daily_ledger.management_sync_layer.get_management_sync
    try:
        daily_ledger.cost_layer.get_cost = lambda tab=None: {
            "tab": "0810今週", "store": {"actual": 400}, "event": {"actual": 600},
            "total": {"actual": 1000},
            "days": [{"d": "月", "totalPlan": 2000, "totalActual": 1000,
                      "storeActual": 400, "eventActual": 600, "laborAir": 100}] + [{} for _ in range(6)],
        }
        daily_ledger.data_layer.get_week_blocks = lambda tab=None: {
            "tab": "0810今週",
            "days": [{"label": "月8/10", "date": "8/10"}] + [{"label": "", "date": ""} for _ in range(6)],
            "blocks": [
                {"category": "店舗用", "products": [{"actual": [1, None, None, None, None, None, None]}]},
                {"category": "催事用", "products": [{"actual": [2, None, None, None, None, None, None]}]},
            ],
        }
        daily_ledger.inventory_layer.get_inventory = lambda: {"items": [{"name": "砂糖"}], "needCount": 1}
        daily_ledger.management_sync_layer.get_management_sync = lambda: {
            "connected": False, "records": [], "counts": {}, "monthSummary": None,
            "qualityChecks": [], "errors": [],
        }
        data = daily_ledger.get_daily_ledger()
    finally:
        daily_ledger.cost_layer.get_cost = old_cost
        daily_ledger.data_layer.get_week_blocks = old_week
        daily_ledger.inventory_layer.get_inventory = old_inventory
        daily_ledger.management_sync_layer.get_management_sync = old_sync
    first = data["days"][0]
    assert first["salesPreliminary"] == 1000
    assert first["materialEstimate"] == 250
    assert first["eventFeeEstimate"] == 120
    assert first["eventStaffDeliveryEstimate"] == 52150
    assert first["knownCost"] == 52620
    assert first["profitBeforeFixed"] == -51620
    assert data["summary"]["inventoryNeedCount"] == 1
    json.dumps(data, ensure_ascii=False, allow_nan=False)


def verify_phase2_sync():
    assert management_sync_layer.normalize_date(46235) == "2026-08-01"
    values = {
        management_sync_layer.TABS["store"]: [
            ["店舗日次"],
            ["日付", "Airレジ売上税込", "原材料費", "包材費", "人件費合計", "配送費", "廃棄金額"],
            ["2026/08/10", "1000", "250", "50", "100", "10", "5"],
        ],
        management_sync_layer.TABS["event"]: [
            ["催事日次"],
            ["日付", "報告状態", "売上税込", "原材料費", "包材費", "販売員費", "会場手数料", "配送費", "廃棄数"],
            ["2026/08/10", "AirMate", "0", "0", "0", "0", "0", "0", "0"],
            ["2026/08/18", "予定", "999", "250", "50", "100", "200", "10", "0"],
        ],
        management_sync_layer.TABS["labor"]: [
            ["月", "人件費合計"], ["46235", "999"],
        ],
        management_sync_layer.TABS["fixed"]: [
            ["月", "実績額", "証憑"], ["46235", "200", "未確認"],
        ],
        management_sync_layer.TABS["expense"]: [
            ["月", "合計"], ["46235", "5000"],
        ],
    }
    parsed = management_sync_layer.parse_management_values(values, management_sync_layer.date(2026, 8, 17))
    assert len(parsed["records"]) == 1
    row = parsed["records"][0]
    assert row["eventRows"] == 1
    assert row["eventSales"] == 0
    assert row["sales"] == 1000
    assert row["knownCost"] == 415
    assert parsed["monthSummary"]["fixedCostReference"] == 200
    assert parsed["monthSummary"]["fixedEvidencePendingCount"] == 1
    assert parsed["monthSummary"]["profitBeforeFixed"] == 585
    day = {"dateIso": "2026-08-10", "salesPreliminary": 9999, "status": "速報"}
    daily_ledger._merge_sync([day], parsed)
    assert day["salesPreliminary"] == 1000
    assert day["status"] == "連携速報"
    json.dumps(parsed, ensure_ascii=False, allow_nan=False)


def verify_airmate_daily_reference():
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "input" / "airmate" / "airmate_2026_08.csv"
        path.parent.mkdir(parents=True)
        path.write_text(
            "店舗名,日付,売上,売上目標,昨年売上,客数\n"
            "【門前仲町】どら山,2026-08-01,80000,90000,70000,60\n"
            "【催事】どら山,2026-08-01,120000,110000,100000,90\n",
            encoding="cp932",
        )
        previous = path.parent / "airmate_2026_07.csv"
        previous.write_text(
            "店舗名,日付,売上,売上目標,昨年売上,客数\n"
            "【門前仲町】どら山,2026-07-31,50000,60000,45000,40\n",
            encoding="cp932",
        )
        rows = management_sync_layer._read_airmate_daily_reference(
            management_sync_layer.date(2026, 8, 18), directory
        )
        history = management_sync_layer.read_airmate_history(
            management_sync_layer.date(2026, 8, 18), directory
        )
    assert len(rows) == 1
    assert rows[0]["sales"] == 200000
    assert rows[0]["targetSales"] == 200000
    assert rows[0]["previousYearSales"] == 170000
    assert rows[0]["storeTargetSales"] == 90000
    assert rows[0]["eventTargetSales"] == 110000
    assert rows[0]["storeCustomers"] == 60
    assert rows[0]["eventCustomers"] == 90
    assert rows[0]["storeRows"] == 1
    assert rows[0]["eventRows"] == 1
    assert len(history) == 2
    assert history[0]["date"] == "2026-07-31"
    json.dumps(rows, ensure_ascii=False, allow_nan=False)


def verify_html():
    html = (BASE / "templates" / "store_manager.html").read_text(encoding="utf-8")
    required_texts = (
        "売上から経常利益まで", "日次経営台帳", "経常利益", "商品分析",
        "曜日・時間帯分析", "店舗／催事", "開始日", "終了日", "平均客数",
        "平均客単価", "平均人時売上", "コスト分析", "内部人件費率",
        "通しの経常利益", "売上＋コスト積み上げ", "1催事1日基準",
        "目標設定", "Googleカレンダー × 220,000円", "原価目標", "人件費目標",
        "経常利益目標", "同日開催の確認", "2会場44万円", "目標を保存",
        "Airメイト・freee・カレンダーへ書き戻しません",
        "目標設定で保存した数字を共通利用", "目標設定を全関連画面へ反映",
        "設定売上目標", "月間催事目標", "固定費5科目を月ごとに確認",
        "原価率を変更すると", "goalDailyReferences", "applyGoalSettingsAcrossState",
        "実績売上", "goalActualTable", "goal-actual-label",
        "門前仲町本店で販売する売上の目標", "催事売上は含めません",
        "Googleカレンダーに登録した各催事", "売上実績（店舗＋催事）",
        "催事のない日は0円", "channelMonthlyRows", "channelAnnualChart",
        "channel-store-month", "channel-event-month", "年間の店舗売上",
        "historicalPaceRows=(state.data.weekdayTimeHistory?.daily||[]).filter",
        "livePaceRows=(state.data.daily||[]).filter",
        "実績取得後の目標差", "月末時点の目標差",
        "年間の催事売上", "管理会計PL反映済み", "日次速報との差",
        "棒・点にカーソルを合わせると金額を表示",
        "historicalLaborCalendar", "日別シフト人件費合計",
        "月次確定との差（未日別配賦）", "判定は行いません",
        "日別人件費率＝シフト人件費÷（販売日の店舗売上＋催事売上）",
        "以下は緑、超過は赤", "催事売上未取得・暫定",
    )
    for text in required_texts:
        assert text in html, text
    for removed_text in ("終了日から指定日数", "日数を適用", "weekday-days", "weekday-apply-days"):
        assert removed_text not in html, removed_text
    for fixed_formula in ("sales*.25", "row.sales*.25", "actual-sales*.25"):
        assert fixed_formula not in html, fixed_formula
    assert "currentMonth?salesPaceChart" not in html
    assert "#NAME?" not in html
    scripts = [html.split("<script>", 1)[1].split("</script>", 1)[0]]
    index_html = (BASE / "templates" / "index.html").read_text(encoding="utf-8")
    assert 'id="nav-manager"' in index_html
    assert "location.href='/store-manager'" in index_html
    assert '<span class="ic">📈</span>経常利益' in index_html
    scripts.extend(re.findall(r"<script>(.*?)</script>", index_html, flags=re.S))
    node = os.environ.get("DORAYAMA_NODE")
    if node:
        for script in scripts:
            with tempfile.NamedTemporaryFile("w", suffix=".js", encoding="utf-8", delete=False) as handle:
                handle.write(script)
                path = handle.name
            try:
                subprocess.run([node, "--check", path], check=True, capture_output=True, text=True)
            finally:
                Path(path).unlink(missing_ok=True)


if __name__ == "__main__":
    verify_management()
    verify_airmate_targets()
    verify_target_settings()
    verify_management_pl_workbook()
    verify_management_analysis()
    verify_deduplication()
    verify_inventory_cost()
    verify_daily_payload()
    verify_phase2_sync()
    verify_airmate_daily_reference()
    verify_html()
    print("OK: freee是正後PL、Airメイト目標、8月差額、計算、重複排除、読取連携、画面構文を確認")
