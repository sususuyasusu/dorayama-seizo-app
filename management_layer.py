#!/usr/bin/env python3
"""どら山の月次予実スナップショット（読み取り専用）。

freeeやAir、元のExcelには書き込まず、検証済みの管理会計PLを画面用に整える。
"""

import json
from pathlib import Path

import airmate_targets_layer


BASE = Path(__file__).resolve().parent
SNAPSHOT_PATH = BASE / "data" / "freee_management_pl_2026-08-18.json"
# PLスナップショット(8/18)以降にfreee処理が進んだ分は、進捗だけ別ファイルで上書きする。
# PLの確定金額そのものは管理会計PL.xlsxが更新されるまで変えない。
PROGRESS_OVERRIDE_PATH = BASE / "data" / "freee_progress_2026-08-28.json"
BREAK_EVEN_REFERENCE = {
    "1月": 5770000,
    "2月": 11360000,
    "3月": 5610000,
    "4月": 7030000,
    "5月": 6690000,
    "6月": 7010000,
}
JANUARY_REFERENCE = {
    "month": "1月", "sales": 4790885, "costOfSales": 2635379,
    "grossProfit": 2155506, "operatingExpenses": 2286267, "profit": -130761,
    "labor": None, "rent": None, "operatingProfit": -130761,
}


def _snapshot():
    return json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))


def _automation_progress(snapshot):
    """freee進捗はスナップショット内蔵値より新しい上書きファイルを優先する。"""
    embedded = snapshot.get("automationProgress") or {}
    if not PROGRESS_OVERRIDE_PATH.is_file():
        return embedded
    try:
        override = json.loads(PROGRESS_OVERRIDE_PATH.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return embedded
    progress = override.get("automationProgress") or {}
    return progress or embedded


def build_impact(row, days, waste_reference=True):
    """「利益はどこに消えているか」を管理会計PLの月次行から組み立てる。

    2026-08-13に本番へ入った滝グラフ（コミットc2921ef）の再実装。
    旧実装は6月の固定値だったが、ここでは同じ画面の他の数字と必ず一致するよう
    PLスナップショット（またはExcel原本）の同じ行から計算する。
    """
    sales = row.get("sales")
    cost = row.get("costOfSales")
    gross = row.get("grossProfit")
    labor = row.get("labor")
    internal = row.get("internalLabor")
    event_staff = row.get("eventStaffing")
    opex = row.get("operatingExpenses")
    profit = row.get("profit")
    if None in (sales, cost, gross, opex, profit):
        return None
    if internal is None or event_staff is None:
        internal = labor if labor is not None else None
        event_staff = 0 if internal is not None else None
    if internal is None:
        return None
    other_cost = opex - internal - event_staff
    labor_total = internal + event_staff
    gross_rate = round(gross / sales * 100, 1) if sales else None
    labor_of_gross = round(labor_total / gross * 100, 1) if gross else None
    month = row.get("month") or ""
    result = "黒字" if profit >= 0 else "赤字"
    headline = (
        f"{month}は粗利益{gross:,}円に対して人件費が{labor_total:,}円"
        f"（労働分配率{labor_of_gross}%・目安は40〜50%）。"
        f"内部人件費{internal:,}円と催事販売外注{event_staff:,}円は分けて管理します。"
        f"経常利益は{abs(profit):,}円の{result}です。"
    )
    impact = {
        "month": month,
        "days": days,
        "waterfall": [
            {"label": "売上", "value": sales, "kind": "start"},
            {"label": "材料・包材原価", "value": -cost, "kind": "cost"},
            {"label": "粗利益", "value": gross, "kind": "subtotal"},
            {"label": "内部人件費", "value": -internal, "kind": "cost"},
            {"label": "催事販売外注", "value": -event_staff, "kind": "cost"},
            {"label": "その他コスト", "value": -other_cost, "kind": "cost"},
            {"label": "経常利益", "value": profit, "kind": "end"},
        ],
        "grossMarginRate": gross_rate,
        "laborOfGrossRate": labor_of_gross,
        "laborOfGrossTarget": "40〜50%",
        "daily": {
            "sales": round(sales / days) if days else None,
            "cost": round(cost / days) if days else None,
            "internalLabor": round(internal / days) if days else None,
            "eventStaffing": round(event_staff / days) if days else None,
            "otherCost": round(other_cost / days) if days else None,
        },
        "costSensitivityPer1pct": round(sales * 0.01),
        "headline": headline,
        "sourceNote": "同じ画面の管理会計PL月次行から計算（売上−原価−内部人件費−催事販売外注−その他コスト＝経常利益）",
    }
    if waste_reference:
        impact["waste"] = {
            "rate": 13.3,
            "monthlyCostEstimate": 63000,
            "note": "催事の廃棄率（FY2026・5ヶ月平均の参考値。当月単月の実測ではありません）。本店(Airレジ)は廃棄記録が無く含まれていません。半分に減らせれば月あたり約3.2万円の原価改善が見込めます。",
        }
    return impact


MONTH_DAYS = {"1月": 31, "2月": 28, "3月": 31, "4月": 30, "5月": 31, "6月": 30,
              "7月": 31, "8月": 31, "9月": 30, "10月": 31, "11月": 30, "12月": 31}


def _monthly_rows(snapshot):
    january = {
        **JANUARY_REFERENCE,
        "budget": airmate_targets_layer.sales_target_for(2026, 1),
        "breakEven": BREAK_EVEN_REFERENCE["1月"],
        "cumulative": None,
        "costTotal": JANUARY_REFERENCE["costOfSales"] + JANUARY_REFERENCE["operatingExpenses"],
        "dataStatus": "Excel原本参考",
        "sourceLabel": "2026年原本様式（freee是正後PLの対象外）",
    }
    rows = [january]
    cumulative = 0
    for source in snapshot["months"]:
        cumulative += source["profit"]
        rows.append({
            **source,
            "budget": airmate_targets_layer.sales_target_for(2026, int(source["month"].rstrip("月"))),
            "breakEven": BREAK_EVEN_REFERENCE.get(source["month"]),
            "cumulative": cumulative,
            "costTotal": source["costOfSales"] + source["operatingExpenses"],
            "dataStatus": "freee是正後",
            "sourceLabel": snapshot["source"],
        })
    return rows


def get_dorayama_management():
    snapshot = _snapshot()
    rows = _monthly_rows(snapshot)
    latest = rows[-1]
    cumulative = snapshot["cumulative"]
    automation = _automation_progress(snapshot)
    achievement = round(latest["sales"] / latest["budget"] * 100, 1) if latest.get("budget") else None
    break_even_rate = round(latest["sales"] / latest["breakEven"] * 100, 1) if latest.get("breakEven") else None
    return {
        "asOf": "2026-07-31",
        "period": "7月・freee是正後",
        "cumulativePeriod": snapshot["period"],
        "status": "corrected-management-pl",
        "statusLabel": "1,211件の部門設定完了・2〜7月最新管理会計PL",
        "sourceLabel": "実績：管理会計PL／freee進捗：2026年9月3日までの実行記録を反映",
        "sourceUpdatedAt": snapshot["snapshotAt"],
        "basis": snapshot["basis"],
        "latest": latest,
        "months": rows,
        "cumulative": cumulative,
        "augustProvisional": snapshot["augustProvisional"],
        "automationProgress": automation,
        "breakEvenGap": max(0, latest["breakEven"] - latest["sales"]) if latest.get("breakEven") else None,
        "budgetGap": max(0, latest["budget"] - latest["sales"]) if latest.get("budget") else None,
        "achievement": achievement,
        "breakEvenRate": break_even_rate,
        "todayDecisions": [
            {"level": "urgent", "title": "7月は528,894円の赤字", "detail": "最新の管理会計PLでは、売上4,286,386円に対して総コスト4,815,280円です。"},
            {"level": "normal", "title": "部門タグ未設定は損益科目で0件", "detail": "FY2024〜第10期の1,211件を設定し、1,200取引の金額・明細・借貸・品目に破損がないことを確認済み（8/21検証）。"},
            {"level": "normal", "title": "8/20〜28で過年度の帳簿を大幅正常化", "detail": "FY2024給与計上漏れ9,903,459円の是正、スズキヤスシ名義36件の計上（現金勘定の正常化）、FY2025未払費用二重18件の削除、セゾン口座振替18本の登録が完了し、freee全体の確度が上がっています。"},
            {"level": "urgent", "title": "PayPay銀行の未消込は一括登録しない", "detail": "登録済みと未登録が混在するため、freee画面で1件ずつ消し込みます（8/20に62件・8/28に明細78本を処理済み）。"},
            {"level": "watch", "title": "売掛の幽霊請求3件は本人の取消待ち", "detail": "未送付の下書き請求1,210,550円（INV-076／081／178）が売掛・売上に乗ったままです。freee請求書アプリでの取消が必要です。"},
            {"level": "normal", "title": "未確定分は確定利益へ混ぜない", "detail": "PayPay銀行残高差、社宅控除、減価償却3期分は解消するまで確定利益へ混ぜません。TakeEatsは第9期以降の消込済みを検証済み（残る宿題は第8期のみ）。"},
        ],
        "checks": [
            {"category": "店舗売上", "source": "Airレジ・決済明細", "status": "照合中", "reason": "8月のアプリ速報とfreee管理PLは1,269,106円で一致。TakeEats売上・手数料は別途確認待ち", "owner": "店長・管理", "next": "Airレジ、TakeEats、決済入金を照合"},
            {"category": "催事売上", "source": "Googleフォーム・Airメイト・催事精算書", "status": "要照合", "reason": "8月速報とfreee管理PLに1,897,625円の差", "owner": "管理", "next": "会場別速報とfreee入金を照合"},
            {"category": "原価", "source": "freee仕入・包材・決済", "status": "管理会計確定", "reason": "2〜7月は購入額を本店・催事の売上比で按分", "owner": "管理", "next": "在庫差を含むためPOS消費原価も参考併記"},
            {"category": "人件費", "source": "freee・Airシフト・freee人事労務", "status": "2〜7月反映", "reason": "大矢望央はデザイン100%、鈴木康之はどら山50%・デザイン50%", "owner": "管理", "next": "社宅家賃の給与控除額を確認"},
            {"category": "経費・固定費", "source": "freee・銀行・カード・証憑", "status": "一部保留", "reason": "減価償却3期、社宅控除、TakeEats、銀行残高差は最終確定前", "owner": "経営・管理", "next": "保留理由ごとに確定"},
            {"category": "部門設定", "source": "freee部門タグ", "status": "完了", "reason": "FY2024〜第10期の部門タグ未設定は損益科目で0件（8/21検証）。1,200取引を検算し破損0件", "owner": "管理", "next": "以後は新規取引を日次監視"},
            {"category": "銀行消込", "source": "PayPay銀行・freee自動で経理", "status": "進行中", "reason": "登録済み取引と未登録が混在するため一括登録禁止。8/20に62件19,840,638円、8/28に明細78本を処理済み。残件数は再集計待ち", "owner": "管理", "next": "第8期から1件ずつ消込を継続"},
            {"category": "売掛金", "source": "freee請求書・PayPay銀行", "status": "消込進行中", "reason": "8/20に11件1,994,270円、8/28にワンフラッグ2件・メリーチョコ3件を消込。幽霊請求2件1,071,400円は取消済み、3件1,210,550円は本人の取消操作待ち", "owner": "経営・管理", "next": "幽霊請求3件をfreee請求書アプリで取消し、残額を再集計"},
            {"category": "催事販売外注の科目", "source": "クローバーマネキン請求書・freee", "status": "ルール確定", "reason": "マネキン賃金は8/24から雑給（部門220・不課税）で計上。外注費は紹介所手数料のみ。集計は科目でなく実態（ディースパーク・マネキン等）で拾う", "owner": "管理", "next": "月次集計時に雑給（部門220）を催事販売外注へ含める"},
        ],
        "assumptions": [
            "2〜7月の原価：材料・包材の購入額を本店と催事の売上比で按分",
            "8月の日次原価：確定前のため運営速報とfreee管理会計を分離",
            "催事手数料：速報は売上の20%、確定は催事精算書を優先",
            "催事販売員（ディースパーク）：請求書実測の税抜18,500円／人日・原則2人体制を優先。旧概算45,000円／日は使わない",
            "催事マネキン賃金：8/24から雑給（部門220・不課税）。催事販売外注の集計は実態ベースで拾う",
            "配送料：7,150円／日（確定請求前）",
            "鈴木康之の人件費：どら山50%・デザイン50%／大矢望央：デザイン100%",
        ],
        "latestBreakdown": snapshot["latestBreakdown"],
        "impact": build_impact(latest, MONTH_DAYS.get(latest.get("month"), 30)),
        "expenseAudit": {
            "label": "参考：6月・freee明細と社内台帳の照合",
            "directTotal": 5970901,
            "status": "以前の直接費確認値・最新PLとは別基準",
            "items": [
                {"label": "仕入高", "amount": 1540153, "type": "変動費"},
                {"label": "外注費", "amount": 1963785, "type": "催事・外注"},
                {"label": "給料手当", "amount": 1077118, "type": "人件費"},
                {"label": "消耗品費", "amount": 585530, "type": "変動・間接"},
                {"label": "荷造運賃", "amount": 269956, "type": "配送費"},
                {"label": "地代家賃", "amount": 248880, "type": "固定費"},
                {"label": "賃借料", "amount": 197469, "type": "固定費"},
                {"label": "水道光熱費", "amount": 71147, "type": "固定費"},
                {"label": "支払手数料ほか", "amount": 16863, "type": "間接費"},
            ],
            "unresolved": [
                {"label": "銀行明細の未消込", "reason": "登録済み取引との未消込が混在するため一括登録しない（8/21計測11,868件・423,676,501円→8/20〜28に大量処理済み・残は再集計待ち）"},
                {"label": "PayPay銀行 第8期", "reason": "freee画面で1件ずつ消込を継続（8/20に62件・8/28に明細78本処理済み）"},
                {"label": "売掛金", "reason": "8/28までに16件消込・幽霊請求2件取消済み。幽霊3件1,210,550円は本人の取消操作待ち、残額は再集計待ち"},
                {"label": "8月売上", "reason": "催事側の差1,897,625円を照合中"},
                {"label": "TakeEats", "reason": "第9期以降は消込済みを検証（8/28）。残る宿題は第8期の売上未計上1,221,454円・手数料残509,042円のみ"},
                {"label": "減価償却", "reason": "FY2023以降3期分が未計上（税理士マター）"},
            ],
        },
    }
