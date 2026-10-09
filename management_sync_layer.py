#!/usr/bin/env python3
"""既存の予実連携シートを読み取り専用で日次経営台帳へ渡す。

この層はシートを作成・更新しない。日次の運営速報だけを取り込み、
月次確定損益は management_layer の確定資料を優先する。
"""
import csv
import json
import os
import re
import time
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path

import data_layer


SHEET_ID = os.environ.get(
    "DORAYAMA_MANAGEMENT_SHEET_ID",
    "1PxLrwb2x2ZDs0DaWgmGuwW-6IzRvqXYJhywsGzyLftY",
)
TABS = {
    "store": "03_実績_店舗日次",
    "event": "04_実績_催事日次",
    "labor": "10_人件費分析",
    "fixed": "11_固定費明細",
    "expense": "14_経費内訳_どら山",
}
FLASH_TAB = "_flash_daily"  # クラウドが書く昨日の売上・人件費（Mac停止日の代役）
EVENT_STAFF_DAILY_ESTIMATE = 37000  # ディースパーク日額（税抜）。management_analysis_layer.EVENT_STAFF_DAILY_RATE と同額
_STORE_LIKE_EVENT_VENUES = ("富岡八幡宮",)  # target_settings_layer.STORE_LIKE_VENUES と同じ
FORM_SHEET_ID = "1v0w_oAmbTmw3t9oOhCFVWv87ysm2gpOnNQbGAlALpIc"  # 催事日報フォームの回答シート
FORM_GID = 816782526
FORM_KEY = "_form_rows"
FORM_COL_STORE, FORM_COL_DATE, FORM_COL_DATE_MANUAL, FORM_COL_SALES_INCL = 2, 1, 19, 18
_FORM_CACHE = {"at": 0.0, "rows": None, "good_at": 0.0}
_FORM_WS = None
_CACHE = {"at": 0.0, "date": None, "value": None}
_CACHE_TTL = 90.0
_SHEET = None
LOCAL_DATA_DIR = Path(os.environ.get(
    "DORAYAMA_MANAGEMENT_DATA_DIR",
    "/Users/suzuki3/Library/CloudStorage/Dropbox-Detale/D& W/どら山/過去/dw_budget_profit_sheets_automation/data",
))


def _is_store_like_event(row):
    text = f"{row.get('催事名') or ''}{row.get('場所') or ''}"
    return any(name in text for name in _STORE_LIKE_EVENT_VENUES)


def _number(value):
    if value in (None, "", "-"):
        return None
    text = str(value).strip().replace(",", "").replace("¥", "").replace("￥", "")
    negative = text.startswith("(") and text.endswith(")")
    text = text.strip("()")
    try:
        result = int(Decimal(text).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        return -result if negative else result
    except (InvalidOperation, ValueError):
        return None


def normalize_date(value, reference_year=None):
    """Googleの日付シリアル値と一般的な日付表記を日付だけに正規化する。"""
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)) or str(value).strip().replace(".", "", 1).isdigit():
        try:
            serial = int(float(value))
            if 20000 <= serial <= 80000:
                return (date(1899, 12, 30) + timedelta(days=serial)).isoformat()
        except (ValueError, OverflowError):
            pass
    text = str(value).strip().replace("年", "/").replace("月", "/").replace("日", "")
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y", "%Y.%m.%d"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            pass
    for fmt in ("%m/%d", "%m-%d"):
        try:
            parsed = datetime.strptime(text, fmt).date()
            return date(reference_year or date.today().year, parsed.month, parsed.day).isoformat()
        except ValueError:
            pass
    return None


def _records(values, header_name):
    header_index = next(
        (index for index, row in enumerate(values or []) if header_name in [str(v).strip() for v in row]),
        None,
    )
    if header_index is None:
        return []
    headers = [str(value).strip() for value in values[header_index]]
    rows = []
    for raw in values[header_index + 1:]:
        if not any(str(value).strip() for value in raw):
            continue
        rows.append({header: raw[index] if index < len(raw) else "" for index, header in enumerate(headers) if header})
    return rows


def _sum_values(row, *keys):
    values = [_number(row.get(key)) for key in keys]
    return sum(value for value in values if value is not None)


def _month_matches(value, target):
    # シートの「月」列は「2026/8」「2026/08」「2026-8」のような年月だけの表記がある。
    # 年月日として読めない場合は年月表記として照合する。
    text = str(value or "").strip()
    month_only = re.fullmatch(r"(\d{4})[/\-年]\s*(\d{1,2})月?", text)
    if month_only:
        year, month = map(int, month_only.groups())
        return year == target.year and month == target.month
    normalized = normalize_date(value, target.year)
    if not normalized:
        return False
    parsed = date.fromisoformat(normalized)
    return parsed.year == target.year and parsed.month == target.month


def parse_management_values(values_by_tab, today=None):
    """取得済みセル値を安全な日次速報へ変換する。テストでも外部接続なしで使う。"""
    today = today or date.today()
    target_month = (today.year, today.month)
    daily = {}
    store_details = []
    event_details = []
    product_totals = {}

    def daily_row(day_iso):
        return daily.setdefault(day_iso, {
            "date": day_iso,
            "storeSales": 0,
            "eventSales": 0,
            "storeMaterial": 0,
            "eventMaterial": 0,
            "storePackaging": 0,
            "eventPackaging": 0,
            "storeLabor": 0,
            "eventStaff": 0,
            "eventCommission": 0,
            "delivery": 0,
            "waste": 0,
            "storeRows": 0,
            "eventRows": 0,
            "eventReportStates": [],
        })

    store_rows = _records(values_by_tab.get(TABS["store"], []), "日付")
    for row in store_rows:
        day_iso = normalize_date(row.get("日付"), today.year)
        if not day_iso:
            continue
        parsed = date.fromisoformat(day_iso)
        if (parsed.year, parsed.month) != target_month or parsed > today:
            continue
        item = daily_row(day_iso)
        item["storeSales"] += _number(row.get("Airレジ売上税込")) or 0
        item["storeMaterial"] += _number(row.get("原材料費")) or 0
        item["storePackaging"] += _number(row.get("包材費")) or 0
        item["storeLabor"] += _number(row.get("人件費合計")) or 0
        item["delivery"] += _number(row.get("配送費")) or 0
        item["waste"] += _number(row.get("廃棄金額")) or 0
        item["storeRows"] += 1
        quantities = {}
        for product in ("黒どら", "白どら", "あんバター", "旬どら", "その他"):
            quantity = _number(row.get(product)) or 0
            quantities[product] = quantity
            product_totals[product] = product_totals.get(product, 0) + quantity
        store_details.append({
            "date": day_iso,
            "store": str(row.get("店舗名") or "どら山"),
            "sales": _number(row.get("Airレジ売上税込")) or 0,
            "customers": _number(row.get("客数")),
            "unitPrice": _number(row.get("客単価")),
            "units": _number(row.get("販売個数合計")),
            "labor": _number(row.get("人件費合計")) or 0,
            "material": _number(row.get("原材料費")) or 0,
            "packaging": _number(row.get("包材費")) or 0,
            "quantities": quantities,
            "status": "連携速報",
        })

    # Macが止まった日の代わり: クラウド(GitHub Actions)が取った昨日の売上・人件費。
    # 正本（予実シートの実績）に数字が入っている日はそちらが優先で、空の日だけ埋める。
    for row in _records(values_by_tab.get(FLASH_TAB, []), "日付"):
        day_iso = normalize_date(row.get("日付"), today.year)
        if not day_iso:
            continue
        parsed = date.fromisoformat(day_iso)
        if (parsed.year, parsed.month) != target_month or parsed > today:
            continue
        sales = _number(row.get("Airレジ売上税込"))
        labor = _number(row.get("人件費合計"))
        status = str(row.get("状態") or "")
        note = status.split("：", 1)[1].strip() if "：" in status else ""
        # 「タイミー未登録」は毎日付く恒常的な断り書きなので文面には出さない（状態欄には残る）
        note = " / ".join(part for part in note.split(" / ") if "未登録" not in part).strip()
        # クラウドの取得エラー（例外メッセージ）をそのまま文面に出さない。
        # 正本（Macの集計）に人件費がある日は、クラウド側の失敗は無関係なので断り書きごと消す。
        if "例外" in note or "Timeout" in note or "Call log" in note:
            has_master_labor = bool(day_iso in daily and daily[day_iso].get("storeLabor"))
            note = "" if has_master_labor else "人件費を自動取得できませんでした（実際より低く出ている恐れがあります）"
        if note and day_iso in daily:
            # 退勤の打刻漏れ・時給0など、人件費が低く出ている恐れは、正本を使う日でも文面に添える
            daily[day_iso]["flashNote"] = note
        if sales is None:
            continue  # 売上が取れていない日は「入力待ち」のままにする（0円と見せない）
        existing = daily.get(day_iso)
        if existing and (existing["storeSales"] or existing["storeLabor"]):
            continue
        item = daily_row(day_iso)
        item["storeSales"] = sales
        item["storeLabor"] = labor or 0
        item["storeRows"] = max(item["storeRows"], 1)
        if note:
            item["flashNote"] = note
        store_details.append({
            "date": day_iso,
            "store": "どら山",
            "sales": sales,
            "customers": None,
            "unitPrice": None,
            "units": None,
            "labor": labor or 0,
            "material": 0,
            "packaging": 0,
            "quantities": {},
            "status": "クラウド速報" + ("" if labor is not None else "（人件費は取得できず）"),
        })

    event_rows = _records(values_by_tab.get(TABS["event"], []), "日付")
    for row in event_rows:
        day_iso = normalize_date(row.get("日付"), today.year)
        if not day_iso:
            continue
        parsed = date.fromisoformat(day_iso)
        if (parsed.year, parsed.month) != target_month or parsed > today:
            continue
        event_sales = _number(row.get("売上税込"))
        if parsed == today and event_sales in (None, 0):
            continue
        item = daily_row(day_iso)
        # シートの「販売員費」に実額があればそれを使う。空欄の日は、下の後処理で
        # ディースパークの日額（税抜37,000円）×その日の催事数で見積もる。
        staff_cost = _number(row.get("販売員費")) or 0
        # 過去のAirメイト0円は欠損ではなく有効な実績として保持する。
        item["eventSales"] += event_sales or 0
        item["eventMaterial"] += _number(row.get("原材料費")) or 0
        item["eventPackaging"] += _number(row.get("包材費")) or 0
        item["eventStaff"] += staff_cost
        item["eventCommission"] += _number(row.get("会場手数料")) or 0
        item["delivery"] += _number(row.get("配送費")) or 0
        item["waste"] += _number(row.get("廃棄数")) or 0
        item["eventRows"] += 1
        state = str(row.get("報告状態") or "").strip()
        if state and state not in item["eventReportStates"]:
            item["eventReportStates"].append(state)
        for product in ("黒どら", "白どら", "あんバター", "旬どら", "皮だけ", "その他"):
            product_totals[product] = product_totals.get(product, 0) + (_number(row.get(product)) or 0)
        event_cost = (
            (_number(row.get("原材料費")) or 0) + (_number(row.get("包材費")) or 0) +
            staff_cost + (_number(row.get("会場手数料")) or 0) +
            (_number(row.get("配送費")) or 0)
        )
        event_details.append({
            "date": day_iso,
            "name": str(row.get("催事名") or "名称未設定"),
            "venue": str(row.get("場所") or ""),
            "sales": event_sales or 0,
            "customers": _number(row.get("客数")),
            "units": _number(row.get("販売個数合計")),
            "commission": _number(row.get("会場手数料")) or 0,
            "staffCost": staff_cost,
            "delivery": _number(row.get("配送費")) or 0,
            "material": _number(row.get("原材料費")) or 0,
            "packaging": _number(row.get("包材費")) or 0,
            "profitBeforeFixed": (event_sales or 0) - event_cost,
            "status": state or "連携速報",
        })

    # 催事売上もMac停止日の代役。Airメイトの昨日の催事売上をクラウドが取って専用タブに書く。
    # 催事の実績行が正本に無い日だけ、催事が1件以上あった日に限って埋める（催事なしの日は触らない）。
    for row in _records(values_by_tab.get(FLASH_TAB, []), "日付"):
        day_iso = normalize_date(row.get("日付"), today.year)
        if not day_iso:
            continue
        parsed = date.fromisoformat(day_iso)
        if (parsed.year, parsed.month) != target_month or parsed > today:
            continue
        event_sales = _number(row.get("催事売上"))
        event_count = _number(row.get("催事件数")) or 0
        if event_sales is None or event_count <= 0:
            continue
        existing = daily.get(day_iso)
        if existing and existing["eventRows"]:
            continue
        item = daily_row(day_iso)
        item["eventSales"] = event_sales
        item["eventRows"] = max(item["eventRows"], 1)
        state = "クラウド速報"
        if state not in item["eventReportStates"]:
            item["eventReportStates"].append(state)
        event_details.append({
            "date": day_iso,
            "name": "催事（Airメイト）",
            "venue": "",
            "sales": event_sales,
            "customers": None,
            "units": None,
            "commission": 0,
            "staffCost": 0,
            "delivery": 0,
            "material": 0,
            "packaging": 0,
            "profitBeforeFixed": event_sales,
            "status": state,
        })

    # 催事日報フォームの直接取り込み：同期タスクが遅れて催事売上が0円のままの日を、
    # フォームの回答（会場ごとの税込売上の合計）で補う。Airメイト・シートに実績がある日は触らない。
    form_sales, form_dropped = _form_scan(values_by_tab.get(FORM_KEY) or [])
    _merge_manual_reports(form_sales, values_by_tab.get(MANUAL_REPORT_TAB) or [])
    # 読めなかった日報行は黙って捨てず、その日の行に記録する（速報が「確定」と言い切らないための根拠）
    for day_iso, reasons in form_dropped.items():
        try:
            dropped_day = date.fromisoformat(day_iso)
        except ValueError:
            continue
        if (dropped_day.year, dropped_day.month) == target_month and dropped_day <= today:
            daily_row(day_iso)["eventFormDropped"] = reasons
    for day_iso, venues in sorted(form_sales.items()):
        parsed = date.fromisoformat(day_iso)
        if (parsed.year, parsed.month) != target_month or parsed > today:
            continue
        if day_iso in daily:
            daily[day_iso]["eventVenuesReported"] = len(venues)  # 日報が出た会場数（速報で未入力会場の注意に使う）
            daily[day_iso]["eventVenueNames"] = sorted(venues)  # 日報が出た会場名（未入力の会場名を特定するため）
            daily[day_iso]["eventFormTotal"] = sum(venues.values())  # 日報フォームの合計（シートとの突合に使う）
        item = daily.get(day_iso)
        if item and item["eventSales"] > 0:
            continue
        total = sum(venues.values())
        item = daily_row(day_iso)
        item["eventVenuesReported"] = len(venues)
        item["eventVenueNames"] = sorted(venues)
        item["eventSales"] = total
        item["eventRows"] = max(item["eventRows"], 1)
        if "日報フォーム速報" not in item["eventReportStates"]:
            item["eventReportStates"].append("日報フォーム速報")
        zero_rows = [d for d in event_details if d["date"] == day_iso]
        if zero_rows:
            zero_rows[0].update({"sales": total, "profitBeforeFixed": total - zero_rows[0]["staffCost"], "status": "日報フォーム速報"})
        else:
            event_details.append({
                "date": day_iso, "name": "催事（日報フォーム）", "venue": "、".join(sorted(venues)),
                "sales": total, "customers": None, "units": None, "commission": 0, "staffCost": 0,
                "delivery": 0, "material": 0, "packaging": 0, "profitBeforeFixed": total,
                "status": "日報フォーム速報",
            })

    # 催事の販売員費：シートに実額が無い日は、ディースパークの日額（税抜37,000円）×その日の催事数で見積もる。
    # 催事数はアプリの催事カレンダー（同日に複数あれば加算）を基準にし、カレンダーに無い日は
    # 日報フォームの会場数、それも無ければ1とする。富岡八幡宮など店舗同水準の会場は対象外
    # （その日のタイミー実費が店舗人件費に入っているため）。
    import target_settings_layer
    _, calendar_events = target_settings_layer._calendar_schedule()
    calendar_days = {d["date"]: d for d in target_settings_layer._calendar_month(
        today.year, today.month, calendar_events)["daily"]}
    for day_iso, item in daily.items():
        if not item["eventRows"] or item["eventStaff"] > 0:
            continue
        day_details = [d for d in event_details if d["date"] == day_iso]
        if any(d["staffCost"] for d in day_details):
            continue
        cal = calendar_days.get(day_iso) or {}
        staffed = int(cal.get("staffedEventCount") or 0)
        if not staffed:
            staffed = len([v for v in (form_sales.get(day_iso) or {}) if not any(n in v for n in _STORE_LIKE_EVENT_VENUES)])
        if not staffed and not any(_is_store_like_event({"催事名": d["name"], "場所": d["venue"]}) for d in day_details):
            staffed = 1
        amount = EVENT_STAFF_DAILY_ESTIMATE * staffed
        if not amount:
            continue
        item["eventStaff"] = amount
        if day_details:
            day_details[0]["staffCost"] = amount
            day_details[0]["profitBeforeFixed"] -= amount

    # クラウドがAirメイトの催事売上を取れなかった、という断り書きは、予実シート側（日報フォーム・Airメイト）に
    # その日の催事売上がある日は無関係なので出さない（催事の突合は日報フォームで別に行っている）。
    for item in daily.values():
        if item.get("flashNote") and item["eventRows"]:
            kept = " / ".join(p for p in str(item["flashNote"]).split(" / ") if "Airメイト" not in p).strip()
            if kept:
                item["flashNote"] = kept
            else:
                item.pop("flashNote", None)

    records = []
    for day_iso in sorted(daily):
        item = daily[day_iso]
        item["sales"] = item["storeSales"] + item["eventSales"]
        item["material"] = item["storeMaterial"] + item["eventMaterial"]
        item["packaging"] = item["storePackaging"] + item["eventPackaging"]
        item["labor"] = item["storeLabor"] + item["eventStaff"]
        item["knownCost"] = (
            item["material"] + item["packaging"] + item["labor"] +
            item["eventCommission"] + item["delivery"] + item["waste"]
        )
        item["profitBeforeFixed"] = item["sales"] - item["knownCost"]
        item["status"] = "連携速報"
        records.append(item)

    labor_rows = _records(values_by_tab.get(TABS["labor"], []), "月")
    labor_month = next((row for row in reversed(labor_rows) if _month_matches(row.get("月"), today)), None)
    fixed_rows_all = _records(values_by_tab.get(TABS["fixed"], []), "月")
    fixed_rows = [row for row in fixed_rows_all if _month_matches(row.get("月"), today)]
    expense_rows = _records(values_by_tab.get(TABS["expense"], []), "月")
    expense_month = next((row for row in reversed(expense_rows) if _month_matches(row.get("月"), today)), None)
    fixed_details = [{
        "category": str(row.get("費用区分") or row.get("freee科目") or "未分類"),
        "amount": _number(row.get("実績額")),
        "budget": _number(row.get("予算額")),
        "department": str(row.get("部門") or ""),
        "vendor": str(row.get("支払先") or ""),
        "payment": str(row.get("支払方法") or ""),
        "evidence": str(row.get("証憑") or "未確認"),
        "source": str(row.get("連携元") or ""),
    } for row in fixed_rows]
    payment_methods = []
    if expense_month:
        for label in ("Amazon", "ヤフー", "楽天/その他EC", "実店舗クレジット", "振込・引落", "現金", "その他"):
            payment_methods.append({"label": label, "amount": _number(expense_month.get(label)) or 0})

    month_store = sum(row["storeSales"] for row in records)
    month_event = sum(row["eventSales"] for row in records)
    month_cost = sum(row["knownCost"] for row in records)
    fixed_reference = sum(_number(row.get("実績額")) or 0 for row in fixed_rows)
    evidence_pending = sum(1 for row in fixed_rows if str(row.get("証憑") or "").strip() not in ("確認済", "突合済"))
    labor_reference = _number((labor_month or {}).get("人件費合計"))
    expense_reference = _number((expense_month or {}).get("合計"))
    quality = [
        "日次値は運営速報です。月次確定損益はfreee・確定給与・催事精算書との突合後に更新します。",
        "既存シートの月次利益計算は使用せず、確定済みの月次損益を上書きしません。",
        "固定費・会社共通費・決済手数料は日次の固定費前利益に含めていません。",
    ]
    daily_labor = sum(row["labor"] for row in records)
    if labor_reference is not None and labor_reference != daily_labor:
        quality.append("人件費分析の月額と日次合計に差があるため、freee人事労務との月末突合が必要です。")
    if evidence_pending:
        quality.append(f"固定費明細の証憑未突合が{evidence_pending}件あります。")

    return {
        "records": records,
        "latestDate": records[-1]["date"] if records else None,
        "counts": {
            "storeRows": sum(row["storeRows"] for row in records),
            "eventRows": sum(row["eventRows"] for row in records),
            "laborRows": 1 if labor_month else 0,
            "fixedRows": len(fixed_rows),
            "expenseRows": 1 if expense_month else 0,
        },
        "monthSummary": {
            "period": f"{today.year}年{today.month}月",
            "storeSales": month_store,
            "eventSales": month_event,
            "sales": month_store + month_event,
            "knownCost": month_cost,
            "profitBeforeFixed": month_store + month_event - month_cost,
            "laborDaily": daily_labor,
            "laborMonthReference": labor_reference,
            "fixedCostReference": fixed_reference if fixed_rows else None,
            "paymentExpenseReference": expense_reference,
            "fixedEvidencePendingCount": evidence_pending,
            "status": "速報・未突合",
        },
        "qualityChecks": quality,
        "storeDetails": store_details,
        "eventDetails": event_details,
        "productTotals": [{"name": name, "quantity": quantity} for name, quantity in product_totals.items()],
        "laborSummary": {
            "employeeHours": _number((labor_month or {}).get("社員時間")),
            "partTimeHours": _number((labor_month or {}).get("バイト時間")),
            "totalHours": _number((labor_month or {}).get("総人時")),
            "storeLabor": _number((labor_month or {}).get("店舗人件費")),
            "eventStaff": _number((labor_month or {}).get("催事販売員費")),
            "totalLabor": labor_reference,
        },
        "fixedDetails": fixed_details,
        "paymentMethods": payment_methods,
    }


def _read_csv(path):
    # 権限等で読めない環境（Render・サンドボックス）ではファイル無しと同じ扱いにする
    try:
        if not path.is_file():
            return []
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            return list(csv.DictReader(handle))
    except OSError:
        return []


def _read_airmate_file(path, year, month):
    """Airメイトの月次CSVを、店舗・催事を分けた日別値へ変換する。"""
    try:
        if not path.is_file():
            return []
        content = path.read_bytes()
    except OSError:
        return []
    text = None
    for encoding in ("cp932", "shift_jis", "utf-8-sig", "utf-8"):
        try:
            text = content.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        return []

    rows = {}
    for source in csv.DictReader(text.splitlines()):
        day_iso = normalize_date(source.get("日付"), year)
        if not day_iso:
            continue
        parsed = date.fromisoformat(day_iso)
        if (parsed.year, parsed.month) != (year, month):
            continue
        store_name = str(source.get("店舗名") or "")
        channel = "event" if "催事" in store_name else "store"
        item = rows.setdefault(day_iso, {
            "date": day_iso,
            "sales": 0,
            "targetSales": 0,
            "previousYearSales": 0,
            "customers": 0,
            "storeSales": 0,
            "eventSales": 0,
            "storeTargetSales": 0,
            "eventTargetSales": 0,
            "storePreviousYearSales": 0,
            "eventPreviousYearSales": 0,
            "storeCustomers": 0,
            "eventCustomers": 0,
            "storeCustomerRows": 0,
            "eventCustomerRows": 0,
            "storeRows": 0,
            "eventRows": 0,
        })
        sales = _number(source.get("売上")) or 0
        target_sales = _number(source.get("売上目標")) or 0
        previous_sales = _number(source.get("昨年売上")) or 0
        item["sales"] += sales
        item["targetSales"] += target_sales
        item["previousYearSales"] += previous_sales
        customers = _number(source.get("客数"))
        if customers is not None:
            item["customers"] += customers
            item[f"{channel}Customers"] += customers
            item[f"{channel}CustomerRows"] += 1
        item[f"{channel}Rows"] += 1
        item[f"{channel}Sales"] += sales
        item[f"{channel}TargetSales"] += target_sales
        item[f"{channel}PreviousYearSales"] += previous_sales
    result = []
    for key in sorted(rows):
        item = rows[key]
        if not item["storeCustomerRows"]:
            item["storeCustomers"] = None
        if not item["eventCustomerRows"]:
            item["eventCustomers"] = None
        if not item["storeCustomerRows"] and not item["eventCustomerRows"]:
            item["customers"] = None
        result.append(item)
    return result


def _read_airmate_daily_reference(today, data_dir=None):
    """Airメイトの日別目標・前年同月を読み取り専用で集計する。"""
    base = Path(data_dir) if data_dir is not None else LOCAL_DATA_DIR
    path = base / "input" / "airmate" / f"airmate_{today.year}_{today.month:02d}.csv"
    return _read_airmate_file(path, today.year, today.month)


def read_airmate_history(today=None, data_dir=None):
    """保存済みAirメイトCSVを月をまたいで読み、日付範囲分析へ渡す。

    CSVを読めない環境（Render・権限制限）では、リポジトリ同梱の
    スナップショット data/airmate_history_2026.json を代わりに使う。
    """
    target = today or date.today()
    base = Path(data_dir) if data_dir is not None else LOCAL_DATA_DIR
    directory = base / "input" / "airmate"
    rows = []
    try:
        paths = sorted(directory.glob("airmate_????_??.csv"))
    except OSError:
        paths = []
    for path in paths:
        matched = re.fullmatch(r"airmate_(\d{4})_(\d{2})\.csv", path.name)
        if not matched:
            continue
        year, month = map(int, matched.groups())
        if (year, month) > (target.year, target.month):
            continue
        rows.extend(_read_airmate_file(path, year, month))
    if not rows:
        snapshot_path = Path(__file__).resolve().parent / "data" / "airmate_history_2026.json"
        try:
            snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
            rows = snapshot.get("rows") or []
        except (OSError, ValueError):
            rows = []
    return [row for row in rows if row.get("date") and row["date"] <= target.isoformat()]


def _local_sync(today):
    """Googleへ接続できない開発環境では、同じ自動集計の保存結果を読む。"""
    sales_rows = _read_csv(LOCAL_DATA_DIR / "normalized" / "normalized_sales.csv")
    event_rows = _read_csv(LOCAL_DATA_DIR / "normalized" / "normalized_event_report.csv")
    labor_rows = _read_csv(LOCAL_DATA_DIR / "normalized" / "normalized_labor.csv")
    cost_rows = _read_csv(LOCAL_DATA_DIR / "normalized" / "normalized_cost.csv")
    profit_rows = _read_csv(LOCAL_DATA_DIR / "output" / "daily_profit.csv")
    if not any((sales_rows, event_rows, labor_rows, cost_rows, profit_rows)):
        return None

    daily = {}
    store_details = {}
    event_details = []
    product_totals = {}
    staff_totals = {}
    event_profit_costs = {}

    def in_period(row):
        normalized = normalize_date(row.get("date"), today.year)
        if not normalized:
            return None
        parsed = date.fromisoformat(normalized)
        if parsed > today or (parsed.year, parsed.month) != (today.year, today.month):
            return None
        return normalized

    def item(day_iso):
        return daily.setdefault(day_iso, {
            "date": day_iso, "storeSales": 0, "eventSales": 0,
            "storeMaterial": 0, "eventMaterial": 0,
            "storePackaging": 0, "eventPackaging": 0,
            "storeLabor": 0, "eventStaff": 0, "eventCommission": 0,
            "delivery": 0, "waste": 0, "storeRows": 0, "eventRows": 0,
            "eventReportStates": [],
        })

    for row in profit_rows:
        day_iso = normalize_date(row.get("date"), today.year)
        if not day_iso or row.get("business_unit") != "どら山" or row.get("channel") != "event":
            continue
        key = (day_iso, str(row.get("event_name") or ""))
        target = event_profit_costs.setdefault(key, {"material": 0, "packaging": 0, "delivery": 0})
        target["material"] += _number(row.get("material_cost")) or 0
        target["packaging"] += _number(row.get("packaging_cost")) or 0
        target["delivery"] += _number(row.get("delivery_cost")) or 0

    for row in sales_rows:
        day_iso = in_period(row)
        if not day_iso or row.get("business_unit") != "どら山" or row.get("status") != "ok" or row.get("channel") != "store":
            continue
        target = item(day_iso)
        target["storeSales"] += _number(row.get("gross_sales")) or 0
        target["storeRows"] += 1
        product_name = str(row.get("product_name") or "その他")
        product_totals[product_name] = product_totals.get(product_name, 0) + (_number(row.get("quantity")) or 0)
        detail = store_details.setdefault(day_iso, {
            "date": day_iso, "store": str(row.get("store_name") or "どら山"),
            "sales": 0, "customers": 0, "units": 0, "labor": 0,
            "material": 0, "packaging": 0, "quantities": {}, "status": "連携速報",
        })
        detail["sales"] += _number(row.get("gross_sales")) or 0
        detail["customers"] = max(detail["customers"], _number(row.get("customer_count")) or 0)
        detail["units"] += _number(row.get("quantity")) or 0
        detail["quantities"][product_name] = detail["quantities"].get(product_name, 0) + (_number(row.get("quantity")) or 0)

    for row in event_rows:
        day_iso = in_period(row)
        if not day_iso or row.get("business_unit") != "どら山" or row.get("status") != "ok":
            continue
        event_sales = _number(row.get("sales_amount"))
        if date.fromisoformat(day_iso) == today and event_sales in (None, 0):
            continue
        target = item(day_iso)
        target["eventSales"] += event_sales or 0
        target["eventCommission"] += _number(row.get("commission_amount")) or 0
        target["delivery"] += (_number(row.get("delivery_cost")) or 0) + (_number(row.get("transportation_cost")) or 0)
        target["eventRows"] += 1
        source = str(row.get("source") or "").strip()
        if source and source not in target["eventReportStates"]:
            target["eventReportStates"].append(source)
        costs = event_profit_costs.get((day_iso, str(row.get("event_name") or "")), {})
        event_cost = (
            (_number(row.get("commission_amount")) or 0) + (_number(row.get("labor_cost")) or 0) +
            (_number(row.get("delivery_cost")) or 0) + (_number(row.get("transportation_cost")) or 0) +
            (costs.get("material") or 0) + (costs.get("packaging") or 0)
        )
        event_details.append({
            "date": day_iso, "name": str(row.get("event_name") or "名称未設定"),
            "venue": str(row.get("venue_name") or ""), "sales": event_sales or 0,
            "customers": None, "units": _number(row.get("sold_quantity")),
            "commission": _number(row.get("commission_amount")) or 0,
            "staffCost": _number(row.get("labor_cost")) or 0,
            "delivery": (_number(row.get("delivery_cost")) or 0) + (_number(row.get("transportation_cost")) or 0),
            "material": costs.get("material") or 0, "packaging": costs.get("packaging") or 0,
            "profitBeforeFixed": (event_sales or 0) - event_cost, "status": "AirMate速報",
        })

    for row in labor_rows:
        day_iso = in_period(row)
        if not day_iso or row.get("business_unit") != "どら山" or row.get("status") != "ok":
            continue
        amount = _number(row.get("total_labor_cost")) or _number(row.get("labor_cost")) or 0
        staff_name = str(row.get("staff_name") or "氏名未設定")
        staff_totals[staff_name] = staff_totals.get(staff_name, 0) + amount
        if row.get("channel") == "event":
            item(day_iso)["eventStaff"] += amount
        else:
            item(day_iso)["storeLabor"] += amount
            if day_iso in store_details:
                store_details[day_iso]["labor"] += amount

    for row in profit_rows:
        day_iso = in_period(row)
        if not day_iso or row.get("business_unit") != "どら山":
            continue
        target = item(day_iso)
        if row.get("channel") == "event":
            target["eventMaterial"] += _number(row.get("material_cost")) or 0
            target["eventPackaging"] += _number(row.get("packaging_cost")) or 0
        else:
            target["storeMaterial"] += _number(row.get("material_cost")) or 0
            target["storePackaging"] += _number(row.get("packaging_cost")) or 0
            if day_iso in store_details:
                store_details[day_iso]["material"] += _number(row.get("material_cost")) or 0
                store_details[day_iso]["packaging"] += _number(row.get("packaging_cost")) or 0

    records = []
    for day_iso in sorted(daily):
        target = daily[day_iso]
        target["sales"] = target["storeSales"] + target["eventSales"]
        target["material"] = target["storeMaterial"] + target["eventMaterial"]
        target["packaging"] = target["storePackaging"] + target["eventPackaging"]
        target["labor"] = target["storeLabor"] + target["eventStaff"]
        target["knownCost"] = target["material"] + target["packaging"] + target["labor"] + target["eventCommission"] + target["delivery"]
        target["profitBeforeFixed"] = target["sales"] - target["knownCost"]
        target["status"] = "連携速報"
        records.append(target)

    valid_cost_rows = [row for row in cost_rows if in_period(row) and row.get("business_unit") == "どら山" and row.get("status") == "ok"]
    expense_reference = sum(_number(row.get("amount")) or 0 for row in valid_cost_rows)
    fixed_items = {"地代家賃", "賃借料", "水道光熱費", "通信費", "保険料", "減価償却費"}
    fixed_reference = sum(_number(row.get("amount")) or 0 for row in valid_cost_rows if row.get("account_item") in fixed_items)
    fixed_details = [{
        "category": str(row.get("account_item") or "未分類"), "amount": _number(row.get("amount")),
        "budget": None, "department": "どら山", "vendor": str(row.get("vendor_name") or ""),
        "payment": str(row.get("payment_method") or ""), "evidence": "freee速報",
        "source": str(row.get("source") or "freee"),
    } for row in valid_cost_rows if row.get("account_item") in fixed_items]
    payment_group = {}
    for row in valid_cost_rows:
        label = str(row.get("payment_method") or "その他")
        payment_group[label] = payment_group.get(label, 0) + (_number(row.get("amount")) or 0)
    month_sales = sum(row["sales"] for row in records)
    month_cost = sum(row["knownCost"] for row in records)
    labor_total = sum(row["labor"] for row in records)
    return {
        "records": records,
        "latestDate": records[-1]["date"] if records else None,
        "counts": {
            "storeRows": sum(row["storeRows"] for row in records),
            "eventRows": sum(row["eventRows"] for row in records),
            "laborRows": sum(1 for row in labor_rows if in_period(row) and row.get("business_unit") == "どら山" and row.get("status") == "ok"),
            "fixedRows": sum(1 for row in valid_cost_rows if row.get("account_item") in fixed_items),
            "expenseRows": len(valid_cost_rows),
        },
        "monthSummary": {
            "period": f"{today.year}年{today.month}月", "storeSales": sum(row["storeSales"] for row in records),
            "eventSales": sum(row["eventSales"] for row in records), "sales": month_sales,
            "knownCost": month_cost, "profitBeforeFixed": month_sales - month_cost,
            "laborDaily": labor_total, "laborMonthReference": labor_total,
            "fixedCostReference": fixed_reference or None, "paymentExpenseReference": expense_reference or None,
            "fixedEvidencePendingCount": len(valid_cost_rows), "status": "速報・未突合",
        },
        "qualityChecks": [
            "日次値は運営速報です。月次確定損益はfreee・確定給与・催事精算書との突合後に更新します。",
            "Google接続が使えない環境では、同じ自動集計が保存した最新データを読み取ります。元データへの書き込みはありません。",
            "freee経費は重複・配賦・証憑確認前のため、固定費前利益へ加えていません。",
        ],
        "storeDetails": [
            {**row, "unitPrice": round(row["sales"] / row["customers"]) if row.get("customers") else None}
            for row in store_details.values()
        ],
        "eventDetails": event_details,
        "productTotals": [{"name": name, "quantity": quantity} for name, quantity in product_totals.items()],
        "laborSummary": {
            "employeeHours": None, "partTimeHours": None,
            "totalHours": sum(float(row.get("work_hours") or 0) for row in labor_rows if in_period(row) and row.get("business_unit") == "どら山" and row.get("status") == "ok"),
            "storeLabor": sum(row["storeLabor"] for row in records),
            "eventStaff": sum(row["eventStaff"] for row in records),
            "totalLabor": labor_total,
            "staff": [{"name": name, "amount": amount} for name, amount in sorted(staff_totals.items(), key=lambda item: item[1], reverse=True)],
        },
        "fixedDetails": fixed_details,
        "paymentMethods": [{"label": label, "amount": amount} for label, amount in payment_group.items()],
        "connected": bool(records), "partial": True, "errors": [], "mode": "read-only",
        "source": "automation-local-cache",
    }


# === 読み取り削減（Google Sheets APIは1分あたりの読み取り回数に上限があり、超えると429で数字が崩れる） ===
# 以前は「タブごと＋日付ごと」にシートを読み直していた（1回の同期で十数回）。
# 今は ①全タブを1回のまとめ読みで取得 ②日付が違っても同じ生データを使い回す ③読めなければ直前の成功値を
# 一定時間まで使う、の3点で、通常は2分に1〜2回の読み取りで済ませる。
CALENDAR_TAB = "_event_calendar"
MANUAL_REPORT_TAB = "_manual_event_reports"  # LINE画像など、日報フォームに入らなかった日報の手入力欄
_RAW = {"at": 0.0, "values": {}, "failed": set()}
_RAW_TTL = 120.0         # 全タブ成功時にまとめ読みの結果を使い回す秒数
_RAW_RETRY_TTL = 20.0    # 失敗があるときは短い間隔で再試行（ただし読み取りを連打しない）
_STALE_MAX = 1800.0      # 読めない間、直前の成功値を使ってよい最大秒数（超えたら欠損として扱う）
_LAST_GOOD = {}          # {タブ名: (取得時刻, 値)}
_TITLES = {"at": 0.0, "names": set()}
_RAW_LOCK = None


def _known_titles():
    return list(TABS.values()) + [FLASH_TAB, CALENDAR_TAB, MANUAL_REPORT_TAB]


def _pad_rows(rows):
    """get_all_valuesと同じく、行の長さを揃える（末尾の空セルをAPIは省略するため）。"""
    width = max((len(r) for r in rows), default=0)
    return [list(r) + [""] * (width - len(r)) for r in rows]


def _book():
    global _SHEET
    if _SHEET is None:
        _SHEET = data_layer._client().open_by_key(SHEET_ID)
    return _SHEET


def _sheet_names(book):
    if _TITLES["names"] and time.time() - _TITLES["at"] < 600:
        return _TITLES["names"]
    names = {ws.title for ws in book.worksheets()}
    _TITLES.update({"at": time.time(), "names": names})
    return names


def _batch_read():
    """既知のタブをまとめて1回で読む。{タブ名: 値} を返す（存在しないタブは含めない）。"""
    book = _book()
    names = _sheet_names(book)
    wanted = [t for t in _known_titles() if t in names]
    ranges = ["'" + t.replace("'", "''") + "'" for t in wanted]
    response = book.values_batch_get(ranges)
    out = {}
    for title, value_range in zip(wanted, response.get("valueRanges", [])):
        out[title] = _pad_rows(value_range.get("values", []))
    return out


def _raw_tabs():
    """全タブの生データ（キャッシュ付き）。(値の辞書, 読めなかったタブの集合) を返す。"""
    global _SHEET, _RAW_LOCK
    import threading
    if _RAW_LOCK is None:
        _RAW_LOCK = threading.Lock()
    ttl = _RAW_RETRY_TTL if _RAW["failed"] else _RAW_TTL
    if _RAW["values"] and time.time() - _RAW["at"] < ttl:
        return _RAW["values"], _RAW["failed"]
    with _RAW_LOCK:
        ttl = _RAW_RETRY_TTL if _RAW["failed"] else _RAW_TTL
        if _RAW["values"] and time.time() - _RAW["at"] < ttl:  # 待っている間に他のスレッドが読んだ
            return _RAW["values"], _RAW["failed"]
        fresh = None
        for attempt in range(2):
            try:
                fresh = _batch_read()
                break
            except Exception:  # noqa: BLE001 - 429(読み取り上限)・503など。直前の成功値で凌ぐ
                _SHEET = None
                _TITLES["at"] = 0.0
                time.sleep(3 * (attempt + 1))
        now = time.time()
        values, failed = {}, set()
        for title in _known_titles():
            if fresh is not None and title in fresh:
                values[title] = fresh[title]
                _LAST_GOOD[title] = (now, fresh[title])
            elif title in _LAST_GOOD and now - _LAST_GOOD[title][0] < _STALE_MAX:
                values[title] = _LAST_GOOD[title][1]  # 読めなかったので直前の値を使う
                if fresh is None:
                    failed.add("__stale__")
            else:
                values[title] = []
                if title in TABS.values() or fresh is None:  # 補助タブが無いだけなら失敗扱いにしない
                    failed.add(title)
        failed.discard("__stale__")
        _RAW.update({"at": now, "values": values, "failed": failed})
        return values, failed


def _tab_values(title):
    """タブの全値を返す。既知のタブは全タブまとめ読みのキャッシュから（読めなければ例外）。"""
    if title in _known_titles():
        values, failed = _raw_tabs()
        if title in failed:
            raise RuntimeError(f"タブ「{title}」を読めませんでした")
        return values.get(title, [])
    last_error = None
    for attempt in range(2):
        try:
            return _book().worksheet(title).get_all_values()
        except Exception as error:  # noqa: BLE001
            last_error = error
            time.sleep(2 * (attempt + 1))
    raise last_error


def _form_rows():
    """催事日報フォームの回答を読む。同期タスクが遅れた朝でも催事売上を出すための直接取得。
    読めない場合は空を返し、既存の動きを壊さない。"""
    global _FORM_CACHE, _FORM_WS
    ttl = 120 if _FORM_CACHE["rows"] else 20
    if time.time() - _FORM_CACHE["at"] < ttl and _FORM_CACHE["rows"] is not None:
        return _FORM_CACHE["rows"]
    try:
        if _FORM_WS is None:  # シートの場所は最初の1回だけ調べる（毎回調べると読み取り回数が3倍になる）
            book = data_layer._client().open_by_key(FORM_SHEET_ID)
            _FORM_WS = next((w for w in book.worksheets() if w.id == FORM_GID), book.sheet1)
        rows = _FORM_WS.get_all_values()
        _FORM_CACHE = {"at": time.time(), "rows": rows, "good_at": time.time()}
    except Exception:  # noqa: BLE001 - 日報が読めなくても本体の取り込みは続ける
        _FORM_WS = None
        stale = _FORM_CACHE["rows"] if time.time() - _FORM_CACHE.get("good_at", 0) < _STALE_MAX else None
        _FORM_CACHE = {"at": time.time(), "rows": stale or [], "good_at": _FORM_CACHE.get("good_at", 0)}
    return _FORM_CACHE["rows"]


def _form_int(text):
    """フォームの数値欄を整数にする。補足文字・記号・全角数字が付いていても先頭の数字を読む。空欄は0。"""
    s = str(text or "").strip().replace(",", "").replace("，", "").replace("¥", "").replace("￥", "")
    s = s.translate(str.maketrans("０１２３４５６７８９", "0123456789"))
    if not s:
        return 0
    match = re.match(r"\d+", s)
    if not match:
        raise ValueError(f"数値を読み取れません: {text!r}")
    return int(match.group())


def _merge_manual_reports(sales, rows):
    """補完タブ「_manual_event_reports」(日付｜会場｜税込売上…)の行を、日報フォームの会場別売上へ足す。
    同じ日・同じ会場がフォームにもあれば、手入力側を優先する（紙・LINE画像の確認済み値）。"""
    for r in rows[1:]:
        if len(r) < 3 or not str(r[0]).strip() or not str(r[1]).strip():
            continue
        day = normalize_date(str(r[0]).strip().replace("-", "/"), 2026)
        try:
            amount = _form_int(r[2])
        except ValueError:
            continue
        if day and amount > 0:
            sales.setdefault(day, {})[str(r[1]).strip()] = amount


def _form_scan(rows):
    """フォーム回答を読み、({日付: {会場名: 税込売上}}, {日付: [読めなかった会場の説明]}) を返す。
    同会場は後勝ち、テスト入力は除外。売上が読める行は客数が空でも採用する（客数は参考値）。
    売上欄に何か書いてあるのに読めない行は、黙って捨てずに「読めなかった行」として必ず返す。"""
    sales, dropped = {}, {}
    for r in rows[1:]:
        if len(r) <= FORM_COL_SALES_INCL:
            continue
        store = (r[FORM_COL_STORE] or "").strip()
        if any(word in store.lower() for word in ("テスト", "確認用", "test", "dummy")):
            continue
        day = ""
        for col in (FORM_COL_DATE, FORM_COL_DATE_MANUAL):
            if col < len(r) and r[col].strip():
                day = normalize_date(r[col].strip().replace("-", "/"), 2026) or ""
                if day:
                    break
        raw_incl = (r[FORM_COL_SALES_INCL] or "").strip()
        try:
            sales_incl = _form_int(raw_incl)
            excl = _form_int(r[3])  # 税抜売上。無いのに税込だけある行は入力ミスの疑い
        except ValueError:
            if day and raw_incl:
                dropped.setdefault(day, []).append(f"{store}（売上欄「{raw_incl[:20]}」が数字として読めません）")
            continue
        if not day:
            if raw_incl and sales_incl > 0:
                dropped.setdefault("日付不明", []).append(f"{store}（日付が読めません）")
            continue
        if sales_incl <= 0:
            continue  # 売上0・空欄は「その日は報告なし」として扱う
        if excl <= 0:
            dropped.setdefault(day, []).append(f"{store}（税抜売上が空欄・0です）")
            continue
        sales.setdefault(day, {})[store] = sales_incl
    return sales, dropped


def _form_sales_by_day(rows):
    return _form_scan(rows)[0]


def get_management_sync(force=False, today=None):
    """既存シートを読み取る。失敗しても確定損益側へ影響させない。"""
    now = time.time()
    target = today or date.today()
    if not force and _CACHE["date"] == target and _CACHE["value"] is not None and now - _CACHE["at"] < _CACHE_TTL:
        return _CACHE["value"]
    if force and _RAW["failed"]:
        _RAW["at"] = 0.0  # 読めなかったタブがあるときだけ、再取得を促す（成功時は読み直さない）
    errors = []
    values = {}
    for title in TABS.values():
        try:
            values[title] = _tab_values(title)
        except Exception:
            values[title] = []
            errors.append(title)
    try:  # 代役のタブ。読めなくても本体の取り込みは失敗扱いにしない
        values[FLASH_TAB] = _tab_values(FLASH_TAB)
    except Exception:
        values[FLASH_TAB] = []
    values[FORM_KEY] = _form_rows()  # 催事日報フォームの回答（読めなければ空）
    parsed = parse_management_values(values, target)
    fallback = None
    if not parsed["records"] and not parsed["counts"]["expenseRows"] and errors:
        fallback = _local_sync(target)
    if fallback:
        parsed = fallback
    airmate_daily = _read_airmate_daily_reference(target)
    airmate_by_date = {row["date"]: row for row in airmate_daily}
    for record in parsed.get("records") or []:
        reference = airmate_by_date.get(record.get("date"))
        if reference:
            record["dailyTarget"] = reference["targetSales"]
            record["previousYearSales"] = reference["previousYearSales"]
            record["airmateSales"] = reference["sales"]
    parsed["airmateDaily"] = airmate_daily
    parsed.update({
        "connected": bool(parsed["records"] or parsed["counts"]["expenseRows"] or parsed["counts"]["fixedRows"]),
        "partial": bool(errors) or bool(parsed.get("partial")),
        "errors": errors,
        "sheetId": SHEET_ID,
        "mode": "read-only",
        "sourceTimezone": "日付セルを日付として処理（時刻変換なし）",
        "updatedAt": datetime.now().astimezone().isoformat(timespec="minutes"),
    })
    _CACHE.update({"at": now, "date": target, "value": parsed})
    return parsed
