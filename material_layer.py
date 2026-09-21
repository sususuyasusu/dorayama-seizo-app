#!/usr/bin/env python3
"""材料の推奨発注量・入荷予定日と、配送便別の卵まとめ（製造表 計算済み）を読む。"""
import data_layer

BIN_LABELS = ("火曜便", "木曜便", "土曜便")


def _ws(tab=None):
    return data_layer.open_ws(tab)


def get_materials(tab=None):
    ws = _ws(tab)
    v = data_layer.cached_values(ws)

    def g(r, c):
        return v[r - 1][c] if r - 1 < len(v) and c < len(v[r - 1]) else ""

    # 材料表と配送便別の位置は週ごとに変わる（商品行や催事ブロックの増減でずれる。抹茶行の追加後は
    # 材料=49行目〜・便=71行目〜、4会場の週はさらに下）。行番号は決め打ちせず、実績側(U列)の見出しで探す。
    mat_head = deliv_head = None
    for r in range(1, len(v) + 1):
        u = g(r, 20).strip()
        if mat_head is None and u == "材料名":
            mat_head = r
        elif u.startswith("【配送便別"):
            deliv_head = r
            break

    mats = []
    if mat_head is not None:
        for r in range(mat_head + 1, mat_head + 30):  # 卵黄..バター（実績側 U..AD）
            name = g(r, 20)
            if not name.strip() or name.strip().startswith("【"):
                break
            if name.strip() in ("卵黄", "卵白"):
                continue  # 卵は「卵発注」タブに集約
            mats.append({
                "name": name, "unit": g(r, 21),
                "order": g(r, 26),       # AA 推奨発注量
                "orderUnit": g(r, 24),   # Y  発注単位(g/ml) … 推奨発注量÷発注単位=発注袋数
                "arrive": g(r, 27),      # AB 入荷予定日
                "deliverBy": g(r, 29),   # AD 推奨納品日
                "needUnits": g(r, 28),   # AC 発注必要数
            })
    deliv = []
    if deliv_head is not None:
        for r in range(deliv_head + 1, deliv_head + 9):  # 火/木/土便（実績側）
            name = g(r, 20)
            if not name.strip().startswith(BIN_LABELS):
                continue
            deliv.append({
                "name": name,
                "yolkG": g(r, 22), "yolkKai": g(r, 26),
                "whiteG": g(r, 24), "whiteKai": g(r, 27),
            })
    return {"tab": ws.title, "materials": mats, "delivery": deliv}
