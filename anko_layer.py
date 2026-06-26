#!/usr/bin/env python3
"""あん(粒あん=あんこ／上白あん=白あん)の木曜締め発注計算。
木曜営業終了後の在庫を基準に、金曜〜翌木曜の7日分を一括発注する。

レシピ(製造表の数式に準拠):
 - 粒あん  = 35g × (黒どら + あんバター) の個数
 - 上白あん = 35g × 白どら の個数  ＋  (旬どら原単位/100) × 旬どら の個数
   ※旬どらの白あん消費は製造表に無いので、係数を _app_config に入力して加算する。
個数は各週タブの予算(B〜H, 3ブロック分 行5-34, I列『はい』のみ)を合計。
発注対象は選択週の金土日＋翌週の月火水木。必要袋数に予備1袋を加える。"""
import datetime
import math

import data_layer
import inventory_layer
import config_store

G_PER = 35           # 1個あたりのあん(g)。製造表の数式と同じ
BAG_G = 5000         # 粒あん・上白あん とも 5kg/袋
RESERVE_BAGS = 1      # 毎回、必要数に加えて予備を1袋発注
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
    """その週タブの予算個数を商品・曜日別に合計（3ブロック・I列はい のみ）。"""
    ws = data_layer.open_ws(tab)
    V = data_layer.cached_values(ws)

    def g(r, c):
        return V[r - 1][c] if r - 1 < len(V) and c < len(V[r - 1]) else ""

    s = {name: [0.0] * 7 for name in ("黒どら", "あんバター", "白どら", "旬どら")}
    for r in range(5, 35):
        nm = str(g(r, 0)).strip()
        if nm in s and str(g(r, 8)).strip() == "はい":
            for i, c in enumerate(range(1, 8)):  # B..H 予算
                s[nm][i] += _num(g(r, c))
    return ws.title, s


def _next_tab(cur):
    try:
        mm, dd = int(cur[:2]), int(cur[2:])
        base = datetime.date(datetime.date.today().year, mm, dd)
        nd = base + datetime.timedelta(days=7)
        name = "%02d%02d" % (nd.month, nd.day)
        return name if name in data_layer.list_tabs() else None
    except Exception:
        return None


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
    monday = _tab_monday(tab)
    thursday = monday + datetime.timedelta(days=3)
    if now.date() == thursday:
        if now.hour >= BUSINESS_CLOSE_HOUR:
            return {"level": "confirm", "text": "🔴 木曜営業終了後です。在庫数を確定して、あんを発注してください。"}
        return {"level": "today", "text": "🟡 本日17:00の営業終了後、在庫数を確定して発注してください。"}
    if now.date() == thursday + datetime.timedelta(days=1):
        return {"level": "late", "text": "⚠️ 昨日が発注日です。未発注なら在庫数を確認して、すぐ発注してください。"}
    days = (thursday - now.date()).days
    if days < 0:
        thursday += datetime.timedelta(days=7)
    return {"level": "next", "text": f"次回発注：{thursday.month}/{thursday.day}（木）営業終了後"}


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
    empty = {name: [0.0] * 7 for name in ("黒どら", "あんバター", "白どら", "旬どら")}
    nxt_title, nxt_daily = _daily_counts(nxt) if nxt else (None, empty)

    # 木曜営業終了後に見る量。今週残り=金土日、次週前半=月火水木。
    this_remain = _sum_days(cur_daily, range(4, 7))
    next_first = _sum_days(nxt_daily, range(0, 4))
    order_counts = {name: this_remain[name] + next_first[name] for name in this_remain}
    d_remain = _demand(this_remain, jun_rate)
    d_next = _demand(next_first, jun_rate)
    d_order = _demand(order_counts, jun_rate)

    tsubu_inv, shiro_inv = _stock_bags()
    t_stock_bags = (tsubu_inv["total"] if tsubu_inv else 0) or 0
    s_stock_bags = (shiro_inv["total"] if shiro_inv else 0) or 0

    def required_bags(need_g, stock_bags):
        return max(0, math.ceil((need_g - stock_bags * BAG_G) / BAG_G))

    def card(name, inv, key, stock_bags):
        base = required_bags(d_order[key], stock_bags)
        return {
            "name": name,
            "invName": inv["name"] if inv else None,
            "supplier": inv["supplier"] if inv else "",
            "url": inv["url"] if inv else "",
            "bagG": BAG_G,
            "remainG": round(d_remain[key]), "remainBags": round(d_remain[key] / BAG_G, 1),
            "nextFirstG": round(d_next[key]), "nextFirstBags": round(d_next[key] / BAG_G, 1),
            "orderPeriodG": round(d_order[key]), "orderPeriodBags": round(d_order[key] / BAG_G, 1),
            "stockBags": stock_bags, "stockG": round(stock_bags * BAG_G),
            "requiredOrderBags": base,
            "reserveBags": RESERVE_BAGS,
            "totalOrderBags": base + RESERVE_BAGS,
        }

    return {
        "tab": cur_title, "nextTab": nxt_title,
        "periodLabel": "金曜〜翌木曜",
        "alert": _alert(cur_title),
        "junRatePer100": jun_rate,
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
