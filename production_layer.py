#!/usr/bin/env python3
"""製造実績（作った数）を日別・ブロック別（店舗用／各催事）に集計する（読み取り専用）。

製造表の週タブ「実績」列（V〜AB）は、その日に作った数。催事分は製造実績Botが当日列へ書き込む。
店舗の打刻人件費には、翌日以降の催事用商品をつくる人員が含まれるため、
人件費と比べる相手は「その日の売上」ではなく「その日の製造実績」とする。
金額は「作った数 × 商品ごとの平均売価（Airメイトの商品別実績の販売額÷個数・税込）」で換算する。
"""
import time
from datetime import date, timedelta

# 製造表の商品名 → 売価を求める商品名（Airメイトの商品分析側の名前）
PRICE_NAME = {
    "黒どら": "黒どら", "あんバター": "あんバター", "白どら": "白どら", "旬どら": "旬どら",
    "抹茶": "旬どら", "生": "生どら", "生どら": "生どら",
    "皮4枚セット": "皮4枚セット", "皮だけ（パック）": "皮4枚セット",
}
SOURCE_NOTE = "製造表の週タブ「実績」列（作った数）× 商品ごとの平均売価（Airメイト商品別実績・税込）"

_CACHE = {}
_TTL = 300.0


def unit_prices(product_history):
    """商品分析の履歴から、商品ごとの平均売価（販売額÷個数）を求める。"""
    sales, quantity = {}, {}
    for month in (product_history or {}).get("months", []):
        for item in month.get("items", []):
            q = item.get("quantity") or 0
            if q > 0 and (item.get("sales") or 0) > 0:
                sales[item["name"]] = sales.get(item["name"], 0) + item["sales"]
                quantity[item["name"]] = quantity.get(item["name"], 0) + q
    return {name: sales[name] / quantity[name] for name in sales}


def aggregate_week(payload, monday, prices, first_day, last_day):
    """1週分の get_week_blocks の結果を、日付ごとの製造実績に直す。"""
    rows = {}
    for index in range(7):
        day = monday + timedelta(days=index)
        if day < first_day or day > last_day:
            continue
        blocks, pieces, value = {}, 0, 0.0
        for block in payload.get("blocks", []):
            block_pieces, block_value = 0, 0.0
            for product in block.get("products", []):
                made = (product.get("actual") or [None] * 7)[index]
                if not made:
                    continue
                price = prices.get(PRICE_NAME.get(product["name"], product["name"]), 0)
                block_pieces += made
                block_value += made * price
            if block_pieces:
                label = "店舗用" if block.get("category") == "店舗用" else block["name"]
                blocks[label] = {"pieces": int(block_pieces), "value": round(block_value)}
                pieces += block_pieces
                value += block_value
        rows[day.isoformat()] = {
            "date": day.isoformat(), "pieces": int(pieces), "value": round(value), "blocks": blocks,
        }
    return rows


def month_production(year, month, prices, today, loader):
    """指定月の日別製造実績。loader(monday) が1週分のシート内容を返す。今日より先の日は含めない。"""
    first = date(year, month, 1)
    last = min(today, (date(year + (month == 12), month % 12 + 1, 1) - timedelta(days=1)))
    monday = first - timedelta(days=first.weekday())
    days, missing_weeks = {}, []
    while monday <= last:
        payload = loader(monday)
        if payload is None:
            missing_weeks.append(monday.isoformat())
        else:
            days.update(aggregate_week(payload, monday, prices, first, last))
        monday += timedelta(days=7)
    rows = [days[key] for key in sorted(days)]
    return {
        "source": SOURCE_NOTE,
        "missingWeeks": missing_weeks,
        "daily": rows,
        "blocks": sorted({name for row in rows for name in row["blocks"]}, key=lambda name: (name != "店舗用", name)),
        "unitPrices": {name: round(price) for name, price in prices.items()},
    }


_LAST_GOOD = {}
_FAILED_AT = {}
_RETRY_AFTER = 30.0
_STALE_LIMIT = 3600.0


def get_production(product_history, today):
    """製造実績（今月）。シートの一部が読めない（一時的な失敗）ときは、読めた最後の結果を古い旨つきで返す。
    どの週も読めなかったときは None。読めなかった週があるのに「0円」と見せることはしない。"""
    key = (today.year, today.month)
    now = time.time()
    cached = _CACHE.get(key)
    if cached and now - cached[0] < _TTL:
        return cached[1]
    good = _LAST_GOOD.get(key)
    if now - _FAILED_AT.get(key, 0) < _RETRY_AFTER:  # 直前に失敗したばかりなら、連打せずに待つ
        return good[1] if good and now - good[0] < _STALE_LIMIT else None
    try:
        import data_layer

        def load_week(monday):
            for attempt in range(2):
                try:
                    return data_layer.get_week_blocks(today=monday)
                except Exception:  # 一過性の読み取り失敗は少し待って再試行
                    time.sleep(1.5)
            return None

        result = month_production(today.year, today.month, unit_prices(product_history), today, load_week)
    except Exception:
        result = None
    if result is None or result["missingWeeks"] or not result["daily"]:
        _FAILED_AT[key] = now
        if good and now - good[0] < _STALE_LIMIT:
            return {**good[1], "stale": True}
        return result if result and result["daily"] else None
    _CACHE[key] = (now, result)
    _LAST_GOOD[key] = (now, result)
    return result
