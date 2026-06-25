#!/usr/bin/env python3
"""あん(粒あん=あんこ／上白あん=白あん)の発注計算。卵発注ナビと同じ考え方で
『今週必要・在庫(在庫アプリ)・来週必要・来週の発注袋数』を出す。読み取り＋係数のみ書込。

レシピ(製造表の数式に準拠):
 - 粒あん  = 35g × (黒どら + あんバター) の個数
 - 上白あん = 35g × 白どら の個数  ＋  (旬どら原単位/100) × 旬どら の個数
   ※旬どらの白あん消費は製造表に無いので、係数を _app_config に入力して加算する。
個数は各週タブの予算(B〜H, 3ブロック分 行5-34, I列『はい』のみ)を合計。来週=日付+7のタブ。"""
import datetime
import math

import data_layer
import inventory_layer
import config_store

G_PER = 35           # 1個あたりのあん(g)。製造表の数式と同じ
BAG_G = 5000         # 粒あん・上白あん とも 5kg/袋
JUN_KEY = "旬どら_白あん_g_per_100個"   # 旬どら100個あたりの白あん(g)
TSUBU_PRODUCTS = ("黒どら", "あんバター")
SHIRO_PRODUCTS = ("白どら",)
JUN_PRODUCTS = ("旬どら",)


def _num(s):
    try:
        return float(str(s).replace(",", "").replace("¥", "").strip())
    except (ValueError, AttributeError):
        return 0.0


def _counts(tab):
    """その週タブの予算個数を商品別に合計（3ブロック・I列はい のみ）。"""
    ws = data_layer.open_ws(tab)
    V = data_layer.cached_values(ws)

    def g(r, c):
        return V[r - 1][c] if r - 1 < len(V) and c < len(V[r - 1]) else ""

    s = {"黒どら": 0.0, "あんバター": 0.0, "白どら": 0.0, "旬どら": 0.0}
    for r in range(5, 35):
        nm = str(g(r, 0)).strip()
        if nm in s and str(g(r, 8)).strip() == "はい":
            s[nm] += sum(_num(g(r, c)) for c in range(1, 8))  # B..H 予算
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


def _demand(counts, jun_rate):
    tsubu = G_PER * (counts["黒どら"] + counts["あんバター"])
    shiro = G_PER * counts["白どら"] + (jun_rate / 100.0) * counts["旬どら"]
    return tsubu, shiro


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
    cur_title, cur_counts = _counts(tab)
    nxt = _next_tab(cur_title)
    nxt_title, nxt_counts = _counts(nxt) if nxt else (None, {"黒どら": 0, "あんバター": 0, "白どら": 0, "旬どら": 0})

    t_now, s_now = _demand(cur_counts, jun_rate)
    t_next, s_next = _demand(nxt_counts, jun_rate)

    tsubu_inv, shiro_inv = _stock_bags()
    t_stock_bags = (tsubu_inv["total"] if tsubu_inv else 0) or 0
    s_stock_bags = (shiro_inv["total"] if shiro_inv else 0) or 0

    def order_bags(need_g, stock_bags):
        return max(0, math.ceil((need_g - stock_bags * BAG_G) / BAG_G))

    def card(name, inv, now_g, next_g, stock_bags):
        return {
            "name": name,
            "invName": inv["name"] if inv else None,
            "supplier": inv["supplier"] if inv else "",
            "url": inv["url"] if inv else "",
            "bagG": BAG_G,
            "thisG": round(now_g), "thisBags": round(now_g / BAG_G, 1),
            "nextG": round(next_g), "nextBags": round(next_g / BAG_G, 1),
            "stockBags": stock_bags, "stockG": round(stock_bags * BAG_G),
            "orderBags": order_bags(next_g, stock_bags),
        }

    return {
        "tab": cur_title, "nextTab": nxt_title,
        "junRatePer100": jun_rate,
        "junCountThis": round(cur_counts["旬どら"]), "junCountNext": round(nxt_counts["旬どら"]),
        "gPer": G_PER,
        "tsubu": card("あんこ（粒あん）", tsubu_inv, t_now, t_next, t_stock_bags),
        "shiro": card("白あん（上白あん）", shiro_inv, s_now, s_next, s_stock_bags),
    }


def set_jun_rate(value, tab=None):
    config_store.set_config(JUN_KEY, _num(value))
    return get_anko_order(tab)
