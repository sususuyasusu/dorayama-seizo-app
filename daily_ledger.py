#!/usr/bin/env python3
"""どら山の日次経営台帳（読み取り専用）。

既存の製造表・在庫表・会計データへは書き込まず、各情報源を同じ形へ
そろえて画面へ返す。外部連携が未設定の項目はゼロにせず None とし、
未確定理由を残す。
"""
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from zoneinfo import ZoneInfo

import cost_layer
import data_layer
import inventory_layer
import management_sync_layer


JST = ZoneInfo("Asia/Tokyo")
STATUS_PRIORITY = {"速報": 1, "確定": 2, "突合済": 3}
SOURCE_PRIORITY = {
    "freee会計": 100,
    "freee人事労務": 100,
    "カード明細": 80,
    "銀行明細": 70,
    "Amazon": 50,
    "Yahooショッピング": 50,
    "Gmail": 40,
}


def _money(value):
    """金額を安全に整数円へそろえる。空欄・不正値は None。"""
    if value in (None, "", "-"):
        return None
    try:
        return int(Decimal(str(value).replace(",", "")).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    except (InvalidOperation, ValueError):
        return None


def _dedupe_key(event):
    """明示IDがある場合だけ重複候補にする。同額・同日だけでは消さない。"""
    for field in ("paymentGroup", "orderId"):
        value = str(event.get(field) or "").strip()
        if value:
            return field + ":" + value
    external_id = str(event.get("externalId") or "").strip()
    source = str(event.get("source") or "").strip()
    if external_id and source:
        return "externalId:" + source + ":" + external_id
    return None


def deduplicate(events):
    """注文メール→カード→銀行→freeeを1支払グループとして1回だけ計上する。"""
    groups = {}
    singles = []
    for raw in events or []:
        event = dict(raw)
        key = _dedupe_key(event)
        if not key:
            event["sources"] = [event.get("source")] if event.get("source") else []
            event["evidence"] = list(event.get("evidence") or [])
            singles.append(event)
            continue
        groups.setdefault(key, []).append(event)

    out = list(singles)
    for key, members in groups.items():
        chosen = max(
            members,
            key=lambda x: (
                STATUS_PRIORITY.get(x.get("status"), 0),
                SOURCE_PRIORITY.get(x.get("source"), 0),
            ),
        )
        merged = dict(chosen)
        merged["dedupeKey"] = key
        merged["sources"] = sorted({x.get("source") for x in members if x.get("source")})
        evidence = []
        for item in members:
            for value in item.get("evidence") or []:
                if value not in evidence:
                    evidence.append(value)
        merged["evidence"] = evidence
        merged["duplicateCount"] = max(0, len(members) - 1)
        out.append(merged)
    return out


def moving_average_usage_cost(opening_qty, opening_value, movements):
    """在庫入庫と使用量から移動平均原価を計算。ゼロ除算と負在庫を防ぐ。"""
    qty = Decimal(str(opening_qty or 0))
    value = Decimal(str(opening_value or 0))
    usage_cost = Decimal("0")
    rows = []
    for movement in movements or []:
        kind = movement.get("kind")
        move_qty = Decimal(str(movement.get("quantity") or 0))
        move_value = Decimal(str(movement.get("amount") or 0))
        if kind == "receipt" and move_qty > 0:
            qty += move_qty
            value += move_value
        elif kind == "usage" and move_qty > 0:
            usable = min(move_qty, max(qty, Decimal("0")))
            unit = value / qty if qty > 0 else Decimal("0")
            cost = (unit * usable).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
            qty -= usable
            value -= cost
            usage_cost += cost
        rows.append({"date": movement.get("date"), "quantity": float(qty), "value": _money(value) or 0})
    return {"closingQuantity": float(qty), "closingValue": _money(value) or 0,
            "usageCost": _money(usage_cost) or 0, "rows": rows}


def _source(source_id, label, state, status, detail, updated=None):
    return {"id": source_id, "label": label, "state": state, "status": status,
            "detail": detail, "updatedAt": updated}


def _day_iso(value, now_date):
    normalized = management_sync_layer.normalize_date(value, now_date.year)
    if not normalized:
        return None
    parsed = date.fromisoformat(normalized)
    if parsed - now_date > timedelta(days=180):
        parsed = parsed.replace(year=parsed.year - 1)
    elif now_date - parsed > timedelta(days=180):
        parsed = parsed.replace(year=parsed.year + 1)
    return parsed.isoformat()


def _empty_week(now_date):
    monday = now_date - timedelta(days=now_date.weekday())
    weekdays = "月火水木金土日"
    return [{
        "date": (monday + timedelta(days=index)).strftime("%-m/%-d"),
        "dateIso": (monday + timedelta(days=index)).isoformat(),
        "label": f"{weekdays[index]}{(monday + timedelta(days=index)).month}/{(monday + timedelta(days=index)).day}",
        "salesPlan": None,
        "salesPreliminary": None,
        "storeSales": None,
        "eventSales": None,
        "materialEstimate": None,
        "packagingEstimate": None,
        "laborEstimate": None,
        "eventFeeEstimate": None,
        "eventStaffDeliveryEstimate": None,
        "knownCost": None,
        "profitBeforeFixed": None,
        "eventCount": 0,
        "status": "未入力",
        "unconfirmedReason": "実績入力前",
    } for index in range(7)]


def _safe_operations(tab=None, now_date=None):
    try:
        cost = cost_layer.get_cost(tab)
        week = data_layer.get_week_blocks(tab)
    except Exception:
        return None

    days = []
    blocks = week.get("blocks") or []
    labels = week.get("days") or []
    cost_days = cost.get("days") or []
    for index in range(7):
        values = cost_days[index] if index < len(cost_days) else {}
        actual_cells = []
        event_count = 0
        for block in blocks:
            product_values = [p.get("actual", [None] * 7)[index] for p in block.get("products") or []]
            actual_cells.extend(product_values)
            if block.get("category") == "催事用" and any(v is not None for v in product_values):
                event_count += 1
        has_actual = any(v is not None for v in actual_cells)
        sales = _money(values.get("totalActual")) if has_actual else None
        store_sales = _money(values.get("storeActual")) if has_actual else None
        event_sales = _money(values.get("eventActual")) if has_actual else None
        labor = _money(values.get("laborAir"))
        material = _money(Decimal(sales) * Decimal("0.25")) if sales is not None else None
        event_fee = _money(Decimal(event_sales or 0) * Decimal("0.20")) if sales is not None else None
        event_staff_delivery = event_count * (45000 + 7150) if has_actual else None
        known_parts = [material, event_fee, event_staff_delivery, labor]
        known_cost = sum(v for v in known_parts if v is not None) if sales is not None else None
        day = labels[index] if index < len(labels) else {}
        days.append({
            "date": day.get("date") or "",
            "dateIso": _day_iso(day.get("date"), now_date or datetime.now(JST).date()),
            "label": day.get("label") or values.get("d") or "",
            "salesPlan": _money(values.get("totalPlan")),
            "salesPreliminary": sales,
            "storeSales": store_sales,
            "eventSales": event_sales,
            "materialEstimate": material,
            "packagingEstimate": None,
            "laborEstimate": labor,
            "eventFeeEstimate": event_fee,
            "eventStaffDeliveryEstimate": event_staff_delivery,
            "knownCost": known_cost,
            "profitBeforeFixed": (sales - known_cost) if sales is not None and known_cost is not None else None,
            "eventCount": event_count,
            "status": "速報" if has_actual else "未入力",
            "unconfirmedReason": "固定費・請求書・決済手数料・確定給与は月次突合前" if has_actual else "実績入力前",
        })
    return {"tab": cost.get("tab") or week.get("tab"), "days": days,
            "store": cost.get("store") or {}, "event": cost.get("event") or {},
            "total": cost.get("total") or {}}


def _merge_sync(days, sync):
    by_date = {row.get("date"): row for row in sync.get("records") or [] if row.get("date")}
    for day in days:
        record = by_date.get(day.get("dateIso"))
        if not record:
            continue
        day.update({
            "salesPreliminary": record["sales"],
            "storeSales": record["storeSales"],
            "eventSales": record["eventSales"],
            "materialEstimate": record["material"],
            "packagingEstimate": record["packaging"],
            "laborEstimate": record["labor"],
            "eventFeeEstimate": record["eventCommission"],
            "eventStaffDeliveryEstimate": record["delivery"] + record["waste"],
            "knownCost": record["knownCost"],
            "profitBeforeFixed": record["profitBeforeFixed"],
            "eventCount": record["eventRows"],
            "status": "連携速報",
            "sourceDetail": "Airレジ・Airメイト・Airシフト等の既存集計シート",
            "unconfirmedReason": "固定費・会社共通費・決済手数料・確定給与・催事精算書は月次突合前",
        })
    return days


def _safe_inventory():
    try:
        data = inventory_layer.get_inventory()
        if data.get("error"):
            return None
        return {"itemCount": len(data.get("items") or []), "needCount": data.get("needCount", 0)}
    except Exception:
        return None


def get_daily_ledger(tab=None):
    now = datetime.now(JST)
    updated = now.isoformat(timespec="minutes")
    operations = _safe_operations(tab, now.date())
    inventory = _safe_inventory()
    try:
        sync = management_sync_layer.get_management_sync(today=now.date())
    except Exception as exc:
        sync = {"connected": False, "partial": True, "errors": [str(exc)], "records": [],
                "counts": {}, "monthSummary": None, "qualityChecks": []}
    days = operations.get("days") if operations else _empty_week(now.date())
    days = _merge_sync(days, sync)
    entered = [row for row in days if row.get("salesPreliminary") is not None]
    week_sales = sum(row["salesPreliminary"] for row in entered) if entered else None
    known_cost = sum(row["knownCost"] for row in entered if row.get("knownCost") is not None) if entered else None
    contribution = week_sales - known_cost if week_sales is not None and known_cost is not None else None

    counts = sync.get("counts") or {}
    sync_updated = sync.get("updatedAt")
    has_sales_sync = bool(counts.get("storeRows") or counts.get("eventRows"))
    has_expense_sync = bool(counts.get("expenseRows") or counts.get("fixedRows"))
    sources = [
        _source("production", "製造実績・販売実績", "connected" if operations else "error",
                "速報" if operations else "取得待ち", "製造表スプレッドシートを読み取り", updated if operations else None),
        _source("inventory", "在庫管理", "connected" if inventory else "error",
                "速報" if inventory else "取得待ち", "AppSheet元データを読み取り", updated if inventory else None),
        _source("airshift", "Airシフト・タイミー", "connected" if counts.get("laborRows") else "pending",
                "連携速報" if counts.get("laborRows") else "取得待ち",
                "既存集計シート経由。月末にfreee人事労務の確定給与と差額調整", sync_updated),
        _source("airmate", "Airメイト・Airレジ", "connected" if has_sales_sync else "pending",
                "連携速報" if has_sales_sync else "取得待ち",
                f"店舗{counts.get('storeRows', 0)}行・催事{counts.get('eventRows', 0)}行を日別に読取", sync_updated),
        _source("freee", "freee会計", "snapshot" if has_expense_sync else "pending",
                "月次速報" if has_expense_sync else "取得待ち",
                "経費内訳と固定費明細を読取。証憑突合前は経常利益へ断定反映しない", sync_updated),
        _source("freee_hr", "freee人事労務", "pending", "月末確定待ち", "日次はシフト速報。確定給与は月末に差額調整"),
        _source("event_form", "催事Googleフォーム", "connected" if counts.get("eventRows") else "pending",
                "Airメイト優先" if counts.get("eventRows") else "取得待ち",
                "フォーム値よりAirメイトを優先し、過去の0円実績も有効値として保持", sync_updated),
        _source("gmail", "Amazon・Yahoo・カード・振込", "snapshot" if counts.get("expenseRows") else "pending",
                "月次速報" if counts.get("expenseRows") else "取得待ち",
                "既存のどら山経費内訳を読取。証憑とfreee取引の突合前", sync_updated),
        _source("settlement", "催事精算書", "pending", "書類待ち", "最終入金額を受領後に確定"),
    ]
    return {
        "schemaVersion": 2,
        "phase": 2,
        "mode": "read-only",
        "updatedAt": updated,
        "updateRule": "既存の日次集計を読み取り専用で反映。月次確定値は上書きせず、未突合項目を分離",
        "summary": {
            "tab": operations.get("tab") if operations else None,
            "weekSalesPreliminary": week_sales,
            "weekKnownCost": known_cost,
            "weekProfitBeforeFixed": contribution,
            "storeSalesPreliminary": _money((operations or {}).get("store", {}).get("actual")),
            "eventSalesPreliminary": _money((operations or {}).get("event", {}).get("actual")),
            "inventoryNeedCount": inventory.get("needCount") if inventory else None,
            "syncConnected": bool(sync.get("connected")),
            "syncLatestDate": sync.get("latestDate"),
            "syncRecordCount": len(sync.get("records") or []),
            "monthPreview": sync.get("monthSummary"),
            "fixedCostStatus": "未突合",
            "connectedCount": sum(1 for item in sources if item["state"] in ("connected", "snapshot")),
            "pendingCount": sum(1 for item in sources if item["state"] not in ("connected", "snapshot")),
        },
        "days": days,
        "sources": sources,
        "qualityChecks": sync.get("qualityChecks") or [],
        "syncErrors": sync.get("errors") or [],
        "rules": [
            "同額・同日だけでは重複削除せず、注文番号・元ID・支払グループが一致した場合だけ1件化",
            "購入は在庫入庫、製造で使った数量を移動平均単価で日次原価化",
            "日次人件費はAirシフト速報、月次はfreee人事労務の確定給与へ差額調整",
            "未接続・未確定の金額はゼロ扱いせず、利益から分けて表示",
        ],
    }
