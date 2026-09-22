#!/usr/bin/env python3
"""どら山社員グループLINE向けの「昨日の速報」を、経営分析データから短い文章にまとめる（読み取り専用）。

画面の「今日の速報」と同じ考え方の抜粋版。
- 売上：店舗・催事それぞれの、その日の予算との差
- 人件費：店舗の打刻人件費＋催事の販売員（ディースパーク日割り）を合算し、その日の製造実績（作った数×売価）と比べる
- 今月ここまで：売上の目標累積との差、人件費率
数字がそろっていない項目は、0円や「収まっている」と見せず「入力待ち／取得待ち」と書く。
"""
from datetime import date, timedelta

APP_URL = "https://dorayama-seizo-app-1.onrender.com/store-manager"
WEEKDAYS = "月火水木金土日"


def yen(value):
    return f"{round(value):,}円"


def signed(value):
    return ("＋" if value >= 0 else "▲") + f"{abs(round(value)):,}円"


# 数字を普段扱わないスタッフにも一目で伝わるように、判定を絵文字＋短い言葉にする（長文は避け、一言で）。
_LABOR_TIER_LABEL = {"good": "ちょうどよい", "mid": "やや多め", "bad": "かなり多め"}


def _labor_tier(diff_pt):
    """人件費率が目標より何ポイント多いかで、3段階の判定にする。"""
    if diff_pt <= 0:
        return "😊", "good"
    if diff_pt <= 5:
        return "🙂", "mid"
    return "😥", "bad"


def _headline(day_achieve, labor_diff_pt):
    """その日全体の一言まとめ。売上の達成率と人件費の状態、両方をざっくり見て決める。"""
    if day_achieve is None and labor_diff_pt is None:
        return None
    bad = (day_achieve is not None and day_achieve < 60) or (labor_diff_pt is not None and labor_diff_pt > 10)
    good = (day_achieve is None or day_achieve >= 100) and (labor_diff_pt is None or labor_diff_pt <= 0)
    if bad:
        return "😥 踏ん張りどころ"
    if good:
        return "😊 順調"
    return "🙂 まずまず"


def _distribute(total, weights):
    safe = [max(float(w or 0), 0) for w in weights]
    if not sum(safe):
        safe = [1] * len(safe)
    base = sum(safe) or 1
    values = [round(float(total or 0) * w / base) for w in safe]
    if values:
        values[-1] += round(float(total or 0)) - sum(values)
    return values


def daily_targets(analysis, month_key):
    """月の目標を日ごとに割った予算（画面の日別目標と同じ配り方）。{日付: (店舗, 催事)}"""
    goal = next((m for m in analysis["goalSettings"]["months"] if m["yearMonth"] == month_key), None)
    if not goal:
        return {}
    history = [r for r in (analysis.get("weekdayTimeHistory") or {}).get("daily", [])
               if str(r.get("date", "")).startswith(month_key)] or (analysis.get("airmateDaily") or [])
    by_date = {r["date"]: r for r in history}
    known = [float(r.get("storeTargetSales") or 0) for r in history if float(r.get("storeTargetSales") or 0) > 0]
    fallback = sum(known) / len(known) if known else 1
    dates = [r["date"] for r in goal.get("daily", [])]
    store_weights = [float((by_date.get(d) or {}).get("storeTargetSales") or fallback) for d in dates]
    event_weights = [float(r.get("targetSales") or 0) for r in goal.get("daily", [])]
    store = _distribute(goal.get("storeTarget"), store_weights)
    event = _distribute(goal.get("eventTarget"), event_weights) if any(event_weights) else [0] * len(dates)
    return {d: (store[i], event[i]) for i, d in enumerate(dates)}


def build_brief(analysis, target=None):
    """target（日付）の速報を作る。返り値: {"text": 文章, "date": 日付, "complete": 数字がそろっているか}"""
    updated = str(analysis.get("updatedAt") or "")[:10]
    target = target or (date.fromisoformat(updated) - timedelta(days=1)).isoformat()
    day = date.fromisoformat(target)
    month_key = target[:7]
    board = analysis.get("todayBoard") or {}
    rate = float(board.get("laborRateTarget", 25))
    staff_daily = float(board.get("eventStaffDailyRate", 0))
    rows = {r["date"]: r for r in analysis.get("daily", [])}
    row = rows.get(target)
    goal = next((m for m in analysis["goalSettings"]["months"] if m["yearMonth"] == month_key), {}) or {}
    goal_days = {r["date"]: r for r in goal.get("daily", [])}
    targets = daily_targets(analysis, month_key)
    production = analysis.get("production") or {}
    prod_by_date = {r["date"]: r for r in production.get("daily", [])}

    head = f"【どら山 速報】{day.month}/{day.day}（{WEEKDAYS[day.weekday()]}）"
    if not row:
        return {"text": head + "\n売上データがまだ取得できていません。アプリで確認してください。\n" + APP_URL,
                "date": target, "complete": False, "data": None}

    complete = True
    lines = [head]
    store_target, event_target = targets.get(target, (0, 0))
    venues = int((goal_days.get(target) or {}).get("eventCount") or 0)
    store_sales = row.get("storeSales") or 0
    event_sales = row.get("eventSales") or 0
    event_pending = venues > 0 and not row.get("eventRows")
    store_diff = store_sales - store_target
    store_part = f"{'✅' if store_diff >= 0 else '⚠️'} 店舗 {yen(store_sales)}（{signed(store_diff)}）"
    if venues == 0:
        event_part = "催事なし"
    elif event_pending:
        event_part = "⏳ 催事 入力待ち"
        complete = False
    else:
        event_diff = event_sales - event_target
        event_part = f"{'✅' if event_diff >= 0 else '⚠️'} 催事 {yen(event_sales)}（{signed(event_diff)}）"

    store_labor = row.get("storeLabor") or 0
    event_labor = venues * staff_daily
    labor = store_labor + event_labor
    prod = prod_by_date.get(target)
    labor_diff_pt = None
    labor_part = f"人件費 {yen(labor)}"
    if not store_labor:
        complete = False
    if not analysis.get("production"):
        labor_part += "　製造実績を読めず判定なし"
        complete = False
    elif not prod or not prod.get("value"):
        labor_part += "　製造実績待ちで判定なし"
        complete = False
    else:
        ratio = labor / prod["value"] * 100
        labor_diff_pt = ratio - rate
        emoji, tier = _labor_tier(labor_diff_pt)
        labor_part += f"　{emoji}{_LABOR_TIER_LABEL[tier]}（{ratio:.1f}%／目標{rate:.0f}%）"

    day_target_total = store_target + event_target
    day_actual_total = store_sales + event_sales
    day_achieve = None if event_pending else (day_actual_total / day_target_total * 100 if day_target_total > 0 else None)
    headline = _headline(day_achieve, labor_diff_pt)
    if headline:
        lines.append(headline)
    lines += ["", f"{store_part}　{event_part}", labor_part]
    if not store_labor:
        lines.append("※店舗の打刻未取得")
    if row.get("flashNote"):
        lines.append(f"※{row['flashNote']}")

    # 今月ここまで（昨日まで）
    month_dates = sorted(d for d in rows if d.startswith(month_key) and d <= target)
    if month_dates:
        sales_total = sum((rows[d].get("storeSales") or 0) + (rows[d].get("eventSales") or 0) for d in month_dates)
        target_total = sum(sum(targets.get(d, (0, 0))) for d in month_dates)
        month_labor = sum((rows[d].get("storeLabor") or 0) + int((goal_days.get(d) or {}).get("eventCount") or 0) * staff_daily
                          for d in month_dates if (prod_by_date.get(d) or {}).get("value"))
        month_prod = sum((prod_by_date.get(d) or {}).get("value", 0) for d in month_dates)
        month_line = f"{day.month}月ここまで"
        if target_total:
            month_line += f" 売上{yen(sales_total)}（{signed(sales_total - target_total)}）"
        if month_prod:
            month_ratio = month_labor / month_prod * 100
            m_emoji, m_tier = _labor_tier(month_ratio - rate)
            month_line += f"　{m_emoji}人件費{_LABOR_TIER_LABEL[m_tier]}（{month_ratio:.1f}%）"
        lines += ["", month_line]

    lines += ["", "くわしい図は👇", APP_URL]

    # 画面・PDF（デザイン版）用の構造化データ。文面と同じ数字から作る。
    if venues == 0:
        event_state = "none"
    elif not row.get("eventRows"):
        event_state = "pending"
    else:
        event_state = "ok"
    if not analysis.get("production"):
        prod_state = "unreadable"
    elif not prod or not prod.get("value"):
        prod_state = "pending"
    else:
        prod_state = "ok"
    blocks = []
    if prod_state == "ok":
        total_value = sum(item["value"] for item in prod.get("blocks", {}).values()) or prod["value"]
        blocks = [{"name": name, "value": item["value"], "share": item["value"] / total_value * 100}
                  for name, item in sorted(prod.get("blocks", {}).items(), key=lambda kv: -kv[1]["value"])]
    rate_value = labor / prod["value"] * 100 if prod_state == "ok" else None
    allowed_value = prod["value"] * rate / 100 if prod_state == "ok" else None
    month = None
    if month_dates:
        month = {
            "label": f"{day.month}月ここまで", "sales": sales_total, "target": target_total,
            "diff": sales_total - target_total if target_total else None,
            "rate": (month_labor / month_prod * 100) if month_prod else None, "rateTarget": rate,
        }
    # 小さなグラフ用の累積系列（月初〜昨日）
    import calendar
    days_in_month = calendar.monthrange(day.year, day.month)[1]
    sales_actual, sales_target, sales_target_cum = [], [], 0
    for d in range(1, days_in_month + 1):
        key = f"{month_key}-{d:02d}"
        sales_target_cum += sum(targets.get(key, (0, 0)))
        sales_target.append(sales_target_cum)
    running = 0
    for d in range(1, day.day + 1):
        r = rows.get(f"{month_key}-{d:02d}") or {}
        running += (r.get("storeSales") or 0) + (r.get("eventSales") or 0)
        sales_actual.append(running)
    labor_store, labor_total, labor_allowed = [], [], []
    store_run = total_run = allowed_run = 0
    for d in range(1, day.day + 1):
        key = f"{month_key}-{d:02d}"
        value = (prod_by_date.get(key) or {}).get("value", 0)
        if value:  # 製造実績が入っている日だけ数える（入力待ちの日で率が跳ねないように）
            store_day = (rows.get(key) or {}).get("storeLabor") or 0
            event_day = int((goal_days.get(key) or {}).get("eventCount") or 0) * staff_daily
            store_run += store_day
            total_run += store_day + event_day
            allowed_run += value * rate / 100
        labor_store.append(store_run)
        labor_total.append(total_run)
        labor_allowed.append(allowed_run)
    charts = {
        "days": days_in_month,
        "sales": {"actual": sales_actual, "target": sales_target},
        "labor": {"store": labor_store, "total": labor_total, "allowed": labor_allowed},
    }
    data = {
        "date": target, "label": f"{day.month}/{day.day}（{WEEKDAYS[day.weekday()]}）",
        "store": {"sales": store_sales, "target": store_target, "diff": store_sales - store_target,
                  "achieve": store_sales / store_target * 100 if store_target else None},
        "event": {"state": event_state, "sales": event_sales, "target": event_target, "venues": venues,
                  "diff": event_sales - event_target if event_state == "ok" else None,
                  "achieve": event_sales / event_target * 100 if event_state == "ok" and event_target else None},
        "labor": {"total": labor, "store": store_labor, "event": event_labor, "storeMissing": not store_labor},
        "production": {"state": prod_state, "value": prod["value"] if prod_state == "ok" else 0,
                       "pieces": prod.get("pieces", 0) if prod_state == "ok" else 0, "blocks": blocks},
        "rate": {"value": rate_value, "target": rate, "allowed": allowed_value,
                 "gap": allowed_value - labor if prod_state == "ok" else None,
                 "verdict": "unknown" if prod_state != "ok" else ("ok" if labor <= allowed_value else "over")},
        "month": month,
        "charts": charts,
    }
    return {"text": "\n".join(lines), "date": target, "complete": complete, "data": data}
