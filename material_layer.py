#!/usr/bin/env python3
"""材料の推奨発注量・入荷予定日と、配送便別の卵まとめ（製造表 計算済み）を読む。"""
import data_layer


def _ws():
    gc = data_layer._client()
    sh = gc.open_by_key(data_layer.SHEET_ID)
    _, cands = data_layer.current_week_tab()
    for c in cands:
        try:
            return sh.worksheet(c)
        except Exception:
            continue
    raise RuntimeError("今週タブが見つからない")


def get_materials():
    ws = _ws()
    v = ws.get_all_values()

    def g(r, c):
        return v[r - 1][c] if r - 1 < len(v) and c < len(v[r - 1]) else ""

    mats = []
    for r in range(47, 59):  # 卵黄..バター（実績側 U..AD）
        name = g(r, 20)
        if not name.strip():
            continue
        mats.append({
            "name": name, "unit": g(r, 21),
            "order": g(r, 26),       # AA 推奨発注量
            "arrive": g(r, 27),      # AB 入荷予定日
            "deliverBy": g(r, 29),   # AD 推奨納品日
            "needUnits": g(r, 28),   # AC 発注必要数
        })
    deliv = []
    for r in range(69, 72):  # 火/木/土便（実績側）
        name = g(r, 20)
        if not name.strip():
            continue
        deliv.append({
            "name": name,
            "yolkG": g(r, 22), "yolkKai": g(r, 26),
            "whiteG": g(r, 24), "whiteKai": g(r, 27),
        })
    return {"tab": ws.title, "materials": mats, "delivery": deliv}
