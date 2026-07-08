#!/usr/bin/env python3
"""あん(粒あん=あんこ／上白あん=白あん)の月曜締め発注計算。
現在庫で翌月曜まで足りるかを確認し、月曜通常発注は翌火曜〜翌々月曜分を計算する。

レシピ(製造表の数式に準拠):
 - 粒あん  = 35g × (黒どら + あんバター) の個数
 - 上白あん = 35g × 白どら の個数  ＋  (旬どら原単位/100) × 旬どら の個数
   ※旬どらの白あん消費は製造表に無いので、係数を _app_config に入力して加算する。
個数は各週タブの実績側製造表(V〜AB, 3ブロック分 行5-34, I列『はい』のみ)を合計。
現在庫は翌月曜までの消費に引き当てる。翌月曜時点で余る分だけ、通常発注から差し引く。"""
import datetime
import math

import data_layer
import inventory_layer
import config_store

G_PER = 35           # 1個あたりのあん(g)。製造表の数式と同じ
BAG_G = 5000         # 粒あん・上白あん とも 5kg/袋
BUSINESS_CLOSE_HOUR = 17
JUN_KEY = "旬どら_白あん_g_per_100個"   # 旬どら100個あたりの白あん(g)
TSUBU_PRODUCTS = ("黒どら", "あんバター")
SHIRO_PRODUCTS = ("白どら",)
JUN_PRODUCTS = ("旬どら",)


def _num(s):
    try:
        return float(str(s).replace(",", "").replace("¥", "").strip())
    except (ValueError, AttributeError):
        return 0.0


def _daily_counts(tab):
    """その週タブの実績側製造数を商品・曜日別に合計（3ブロック・I列はい のみ）。"""
    ws = data_layer.open_ws(tab)
    V = data_layer.cached_values(ws)

    def g(r, c):
        return V[r - 1][c] if r - 1 < len(V) and c < len(V[r - 1]) else ""

    s = {name: [0.0] * 7 for name in ("黒どら", "あんバター", "白どら", "旬どら")}
    for r in range(5, 35):
        nm = str(g(r, 0)).strip()
        if nm in s and str(g(r, 8)).strip() == "はい":
            for i, c in enumerate(range(21, 28)):  # V..AB 実績側製造表
                s[nm][i] += _num(g(r, c))
    return ws.title, s


def _tab_after(cur, days):
    try:
        mm, dd = int(cur[:2]), int(cur[2:])
        base = datetime.date(datetime.date.today().year, mm, dd)
        nd = base + datetime.timedelta(days=days)
        name = "%02d%02d" % (nd.month, nd.day)
        return name if name in data_layer.list_tabs() else None
    except Exception:
        return None


def _next_tab(cur):
    return _tab_after(cur, 7)


def _sum_days(counts, indexes):
    return {name: sum(values[i] for i in indexes) for name, values in counts.items()}


def _demand(counts, jun_rate):
    tsubu = G_PER * (counts["黒どら"] + counts["あんバター"])
    shiro_dora = G_PER * counts["白どら"]
    shun_dora = (jun_rate / 100.0) * counts["旬どら"]
    return {"tsubu": tsubu, "shiroDora": shiro_dora,
            "shunDora": shun_dora, "shiro": shiro_dora + shun_dora}


def _tab_monday(tab):
    """MMDDタブを、そのタブが表す月曜日の日付に直す（年またぎ対応）。"""
    mm, dd = int(tab[:2]), int(tab[2:])
    today = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))).date()
    candidates = [datetime.date(today.year + y, mm, dd) for y in (-1, 0, 1)]
    return min(candidates, key=lambda d: abs((d - today).days))


def _alert(tab):
    now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9)))
    order_day = _tab_monday(tab)   # 発注日＝月曜
    if now.date() == order_day:
        if now.hour >= BUSINESS_CLOSE_HOUR:
            return {"level": "confirm", "text": "🔴 月曜営業終了後です。在庫数を確定して、あんを発注してください。"}
        return {"level": "today", "text": "🟡 本日17:00の営業終了後、在庫数を確定して発注してください。"}
    if now.date() == order_day + datetime.timedelta(days=1):
        return {"level": "late", "text": "⚠️ 昨日が発注日です。未発注なら在庫数を確認して、すぐ発注してください。"}
    days = (order_day - now.date()).days
    if days < 0:
        order_day += datetime.timedelta(days=7)
    return {"level": "next", "text": f"次回発注：{order_day.month}/{order_day.day}（月）営業終了後"}


def _stock_bags():
    yt = wt = None
    for it in inventory_layer.get_inventory().get("items", []):
        if "粒あん" in it["name"]:
            yt = it
        elif "上白あん" in it["name"]:
            wt = it
    return yt, wt


def get_anko_order(tab=None):
    jun_rate = _num(config_store.get_config(JUN_KEY, 0))
    cur_title, cur_daily = _daily_counts(tab)
    nxt = _next_tab(cur_title)
    nxt2 = _tab_after(cur_title, 14)
    empty = {name: [0.0] * 7 for name in ("黒どら", "あんバター", "白どら", "旬どら")}
    nxt_title, nxt_daily = _daily_counts(nxt) if nxt else (None, empty)
    nxt2_title, nxt2_daily = _daily_counts(nxt2) if nxt2 else (None, empty)

    # 現在庫でカバーする期間=火水木金土日（当週）＋翌週月。
    cover_cur = _sum_days(cur_daily, range(1, 7))
    cover_next = _sum_days(nxt_daily, range(0, 1))
    cover_counts = {name: cover_cur[name] + cover_next[name] for name in cover_cur}
    d_cover = _demand(cover_counts, jun_rate)

    # 月曜通常発注の対象=翌週火水木金土日＋翌々週月。
    order_next = _sum_days(nxt_daily, range(1, 7))
    order_nxt2 = _sum_days(nxt2_daily, range(0, 1))
    order_counts = {name: order_next[name] + order_nxt2[name] for name in order_next}
    d_order = _demand(order_counts, jun_rate)

    tsubu_inv, shiro_inv = _stock_bags()
    t_stock_bags = (tsubu_inv["total"] if tsubu_inv else 0) or 0
    s_stock_bags = (shiro_inv["total"] if shiro_inv else 0) or 0

    def card(name, inv, key, stock_bags):
        stock_g = stock_bags * BAG_G
        carryover_g = max(0, stock_g - d_cover[key])
        emergency_g = max(0, d_cover[key] - stock_g)
        regular_g = max(0, d_order[key] - carryover_g)
        regular_bags = max(0, math.ceil(regular_g / BAG_G))
        return {
            "name": name,
            "invName": inv["name"] if inv else None,
            "supplier": inv["supplier"] if inv else "",
            "url": inv["url"] if inv else "",
            "bagG": BAG_G,
            "coverPeriodG": round(d_cover[key]), "coverPeriodBags": round(d_cover[key] / BAG_G, 1),
            "stockBags": stock_bags, "stockG": round(stock_g),
            "carryoverG": round(carryover_g), "carryoverBags": round(carryover_g / BAG_G, 1),
            "emergencyG": round(emergency_g),
            "emergencyBags": max(0, math.ceil(emergency_g / BAG_G)),
            "orderPeriodG": round(d_order[key]), "orderPeriodBags": round(d_order[key] / BAG_G, 1),
            "regularOrderG": round(regular_g),
            "regularOrderBags": regular_bags,
            "totalOrderBags": regular_bags,
        }

    return {
        "tab": cur_title, "nextTab": nxt_title, "next2Tab": nxt2_title,
        "coverPeriodLabel": "火曜〜翌月曜",
        "orderPeriodLabel": "翌火曜〜翌々月曜",
        "alert": _alert(cur_title),
        "junRatePer100": jun_rate,
        "junCountCover": round(cover_counts["旬どら"]),
        "junCountPeriod": round(order_counts["旬どら"]),
        "shiroBreakdown": {
            "shiroDoraG": round(d_order["shiroDora"]),
            "shunDoraG": round(d_order["shunDora"]),
        },
        "gPer": G_PER,
        "tsubu": card("あんこ（粒あん）", tsubu_inv, "tsubu", t_stock_bags),
        "shiro": card("白あん（上白あん）", shiro_inv, "shiro", s_stock_bags),
    }


def set_jun_rate(value, tab=None):
    config_store.set_config(JUN_KEY, _num(value))
    return get_anko_order(tab)


def set_anko_config(values, tab=None):
    if "junRatePer100" in values:
        config_store.set_config(JUN_KEY, _num(values.get("junRatePer100")))
    return get_anko_order(tab)
