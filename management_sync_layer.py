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
_CACHE = {"at": 0.0, "date": None, "value": None}
_CACHE_TTL = 90.0
_SHEET = None
LOCAL_DATA_DIR = Path(os.environ.get(
    "DORAYAMA_MANAGEMENT_DATA_DIR",
    "/Users/suzuki3/Library/CloudStorage/Dropbox-Detale/D& W/どら山/過去/dw_budget_profit_sheets_automation/data",
))


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
        # 過去のAirメイト0円は欠損ではなく有効な実績として保持する。
        item["eventSales"] += event_sales or 0
        item["eventMaterial"] += _number(row.get("原材料費")) or 0
        item["eventPackaging"] += _number(row.get("包材費")) or 0
        item["eventStaff"] += _number(row.get("販売員費")) or 0
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
            (_number(row.get("販売員費")) or 0) + (_number(row.get("会場手数料")) or 0) +
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
            "staffCost": _number(row.get("販売員費")) or 0,
            "delivery": _number(row.get("配送費")) or 0,
            "material": _number(row.get("原材料費")) or 0,
            "packaging": _number(row.get("包材費")) or 0,
            "profitBeforeFixed": (event_sales or 0) - event_cost,
            "status": state or "連携速報",
        })

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


def _tab_values(title):
    """タブの全値を読む。Sheets APIの一過性エラー(503等)は短いリトライで吸収する。"""
    global _SHEET
    last_error = None
    for attempt in range(3):
        try:
            if _SHEET is None:
                _SHEET = data_layer._client().open_by_key(SHEET_ID)
            return _SHEET.worksheet(title).get_all_values()
        except Exception as error:  # noqa: BLE001 - 呼び出し元がタブ単位で失敗を記録する
            last_error = error
            _SHEET = None
            time.sleep(2 * (attempt + 1))
    raise last_error


def get_management_sync(force=False, today=None):
    """既存シートを読み取る。失敗しても確定損益側へ影響させない。"""
    now = time.time()
    target = today or date.today()
    if not force and _CACHE["date"] == target and _CACHE["value"] is not None and now - _CACHE["at"] < _CACHE_TTL:
        return _CACHE["value"]
    errors = []
    values = {}
    for title in TABS.values():
        try:
            values[title] = _tab_values(title)
        except Exception:
            values[title] = []
            errors.append(title)
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
