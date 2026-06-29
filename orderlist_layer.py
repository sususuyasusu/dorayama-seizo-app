#!/usr/bin/env python3
"""在庫管理の商品マスタと発注履歴から、今発注すべき品だけを作る。
在庫不足でも直近発注が未受領なら除外し、発注作業の重複を避ける。"""
import re
from datetime import datetime, timedelta

import data_layer
import inventory_layer

HISTORY_TAB = "発注履歴"
OPEN_ORDER_DAYS = 7
DELIVERY_GRACE_DAYS = 7


def _num(s):
    s = str(s or "").replace(",", "").strip()
    if s in ("", "-"):
        return None
    try:
        return float(s)
    except ValueError:
        m = re.search(r"-?\d+(?:\.\d+)?", s)
        if m:
            return float(m.group(0))
        return None


def _dt(s):
    s = str(s or "").strip()
    if not s:
        return None
    for fmt in ("%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            pass
    return None


def _days_left(due):
    if not due:
        return None
    return (due.date() - datetime.now().date()).days


def _status_text(last_order, lead):
    if not last_order:
        return ""
    lead_num = _num(lead) or 0
    due = last_order + timedelta(days=lead_num)
    md = f"{due.month}/{due.day}"
    left = _days_left(due)
    if left is None:
        return f"発注済・納品予定 {md}"
    if left < 0:
        return f"発注済・納品予定 {md}（{abs(left)}日超過）"
    if left == 0:
        return f"発注済・本日納品予定 {md}"
    return f"発注済・納品予定 {md}"


def _pending_until(last_order, lead):
    if not last_order:
        return None
    lead_num = _num(lead)
    if lead_num and lead_num > 0:
        return last_order + timedelta(days=lead_num + DELIVERY_GRACE_DAYS)
    return last_order + timedelta(days=OPEN_ORDER_DAYS)


def _history():
    try:
        sh = data_layer._client().open_by_key(inventory_layer.INV_SHEET_ID)
        rows = sh.worksheet(HISTORY_TAB).get_all_values()
    except Exception:
        return {}
    out = {}
    for r in rows[1:]:
        if len(r) < 4:
            continue
        product_id = str(r[2]).strip()
        kind = str(r[3]).strip()
        when = _dt(r[1] if len(r) > 1 else "")
        if not product_id or not when:
            continue
        h = out.setdefault(product_id, {"order": None, "receive": None})
        if kind == "発注" and (h["order"] is None or when > h["order"]):
            h["order"] = when
        if kind == "入庫" and (h["receive"] is None or when > h["receive"]):
            h["receive"] = when
    return out


def _source_rows():
    sh = data_layer._client().open_by_key(inventory_layer.INV_SHEET_ID)
    rows = sh.worksheet(inventory_layer.TAB).get_all_values()
    header = rows[0] if rows else []
    idx = {name: i for i, name in enumerate(header)}

    def g(row, name):
        i = idx.get(name)
        return row[i] if i is not None and i < len(row) else ""

    for row in rows[1:]:
        product_id = g(row, "商品ID").strip()
        name = g(row, "商品名").strip()
        if not product_id or not name:
            continue
        wh = _num(g(row, "倉庫数量")) or 0
        store = _num(g(row, "店舗在庫")) or 0
        min_stock = _num(g(row, "最低在庫"))
        total = wh + store
        yield {
            "id": product_id,
            "name": name,
            "category": g(row, "カテゴリー").strip(),
            "supplier": g(row, "発注先").strip(),
            "frequency": g(row, "発注頻度").strip(),
            "warehouse": wh,
            "store": store,
            "total": total,
            "min": min_stock,
            "shortage": None if min_stock is None else round(min_stock - total, 2),
            "lead": g(row, "納期").strip(),
            "lot": g(row, "発注ロット").strip(),
            "note": g(row, "備考").strip(),
            "url": g(row, "参考URL・連絡先").strip(),
            "lastReceived": _dt(g(row, "LastReceived")),
            "need": min_stock is not None and total < min_stock,
        }


def get_orderlist():
    hist = _history()
    orderable = []
    pending = []
    for item in _source_rows():
        if not item["need"]:
            continue
        h = hist.get(item["id"], {})
        last_order = h.get("order")
        last_receive = h.get("receive")
        last_received = item.get("lastReceived")
        closed_by_receive = last_receive and last_order and last_receive >= last_order
        closed_by_stock_edit = last_received and last_order and last_received >= last_order
        pending_until = _pending_until(last_order, item["lead"])
        still_recent = pending_until and pending_until >= datetime.now()
        is_pending = bool(last_order and still_recent and not closed_by_receive and not closed_by_stock_edit)
        row = dict(item)
        row.update({
            "lastOrder": last_order.strftime("%Y/%m/%d %H:%M") if last_order else "",
            "lastReceive": last_receive.strftime("%Y/%m/%d %H:%M") if last_receive else "",
            "pending": is_pending,
            "pendingUntil": pending_until.strftime("%Y/%m/%d") if pending_until else "",
            "status": _status_text(last_order, row["lead"]) if is_pending else "未発注",
        })
        row["lastReceived"] = last_received.strftime("%Y/%m/%d %H:%M") if last_received else ""
        (pending if is_pending else orderable).append(row)

    key = lambda x: (x["supplier"], x["category"], x["name"])
    orderable.sort(key=key)
    pending.sort(key=key)
    suppliers = {}
    for item in orderable:
        suppliers[item["supplier"] or "未設定"] = suppliers.get(item["supplier"] or "未設定", 0) + 1
    return {
        "items": orderable,
        "pending": pending,
        "orderCount": len(orderable),
        "pendingCount": len(pending),
        "suppliers": suppliers,
        "openOrderDays": OPEN_ORDER_DAYS,
        "deliveryGraceDays": DELIVERY_GRACE_DAYS,
    }


def pending_map():
    """商品ID → 発注済みステータス文字列。最近発注して未入荷・未消込のものだけ。
    『その他の在庫』一覧に発注済み表示を出すための判定（要発注かどうかに関係なく見る）。
    判定は発注リストの『発注済み待ち』と同じロジックを使う。"""
    hist = _history()
    out = {}
    for item in _source_rows():
        last_order = hist.get(item["id"], {}).get("order")
        if not last_order:
            continue
        last_receive = hist.get(item["id"], {}).get("receive")
        last_received = item.get("lastReceived")
        closed_by_receive = last_receive and last_receive >= last_order
        closed_by_stock_edit = last_received and last_received >= last_order
        pending_until = _pending_until(last_order, item["lead"])
        still_recent = pending_until and pending_until >= datetime.now()
        if still_recent and not closed_by_receive and not closed_by_stock_edit:
            out[item["id"]] = _status_text(last_order, item["lead"])
    return out
