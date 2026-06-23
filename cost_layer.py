#!/usr/bin/env python3
"""売上（予定/実績・店舗/催事別）・回転・人件費を製造表から読む。
各ブロックの「日別合計金額」行を拾い、カテゴリー（店舗用/催事用）で店舗・催事に振り分ける。
予定=L..R列、実績=AF..AL列。"""
import data_layer
import labor_layer

DAYS = ["月", "火", "水", "木", "金", "土", "日"]


def _ws(tab=None):
    return data_layer.open_ws(tab)


def get_cost(tab=None):
    ws = _ws(tab)
    v = data_layer.cached_values(ws)

    def g(r, c):
        return v[r - 1][c] if r - 1 < len(v) and c < len(v[r - 1]) else ""

    def m(s):
        s = str(s).replace("¥", "").replace(",", "").strip()
        try:
            return float(s) if s not in ("", "-") else 0.0
        except ValueError:
            return 0.0

    store = {"plan": [0.0] * 7, "actual": [0.0] * 7}
    event = {"plan": [0.0] * 7, "actual": [0.0] * 7}
    daymd = [None] * 7   # 各曜日の (月, 日) ＝エアシフト人件費の照合キー
    cat = None
    for r in range(1, 40):
        a = g(r, 0).strip()
        s = g(r, 18).strip()
        if s == "カテゴリー" and a:
            cat = "store" if a == "店舗用" else "event"
            if daymd[0] is None:
                for i in range(7):
                    md = data_layer._md(g(r, 1 + i))
                    if "/" in md:
                        p = md.split("/")
                        try:
                            daymd[i] = (int(p[0]), int(p[1]))
                        except ValueError:
                            pass
        if "日別合計金額" in str(g(r, 10)) and cat:
            tgt = store if cat == "store" else event
            for i in range(7):
                tgt["plan"][i] += m(g(r, 11 + i))    # L..R 予定金額
                tgt["actual"][i] += m(g(r, 31 + i))  # AF..AL 実績金額

    lmap = labor_layer.daily_labor_map()   # エアシフト由来の日次人件費 {(月,日):合計}
    days = []
    for i in range(7):
        sp, sa = store["plan"][i], store["actual"][i]
        ep, ea = event["plan"][i], event["actual"][i]
        days.append({
            "d": DAYS[i],
            "storePlan": round(sp), "storeActual": round(sa),
            "eventPlan": round(ep), "eventActual": round(ea),
            "totalPlan": round(sp + ep), "totalActual": round(sa + ea),
            "kaiten": g(38, 21 + i),     # 回転数（実数）
            "labor": g(36, 31 + i),      # 製造人件費（製造表の手入力）
            "laborAir": lmap.get(daymd[i]),  # エアシフト由来の人件費合計（無い日はNone）
        })

    def tot(d):
        return {"plan": round(sum(d["plan"])), "actual": round(sum(d["actual"]))}

    st, ev = tot(store), tot(event)
    return {
        "tab": ws.title,
        "store": st, "event": ev,
        "total": {"plan": st["plan"] + ev["plan"], "actual": st["actual"] + ev["actual"]},
        "days": days,
    }
