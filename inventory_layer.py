#!/usr/bin/env python3
"""在庫一覧を「どら山_在庫管理_新」(AppSheet在庫管理の元データ)から読む。
材料配送タブに在庫と要発注を表示するための読み取り専用。
在庫の編集は既存のAppSheet在庫管理アプリ側で行う（二重書き込みを避ける）。
合計在庫(倉庫数量＋店舗在庫)が最低在庫を割ったら「要発注」。60秒キャッシュ。"""
import time
import data_layer

INV_SHEET_ID = "14gFJiuVpuGT-GwDhGw-plVqbX5ulKY31g7fvRtwGJ8k"
TAB = "商品マスタ"
# 列: 0商品ID 1商品名 2カテゴリー 3発注先 4発注頻度 5倉庫数量 6店舗在庫 7最低在庫 8納期 9発注ロット 10備考 11参考URL
C_ID, C_NAME, C_CAT, C_SUP, C_WH, C_STORE, C_MIN, C_LEAD = 0, 1, 2, 3, 5, 6, 7, 8

_cache = {"t": 0.0, "data": None}
_TTL = 60.0


def _num(s):
    s = str(s).replace(",", "").strip()
    try:
        return float(s) if s not in ("", "-") else None
    except ValueError:
        return None


def get_inventory():
    now = time.time()
    if _cache["data"] is not None and now - _cache["t"] < _TTL:
        return _cache["data"]
    try:
        gc = data_layer._client()
        ws = gc.open_by_key(INV_SHEET_ID).worksheet(TAB)
        rows = ws.get_all_values()
    except Exception as e:
        return _cache["data"] or {"items": [], "needCount": 0, "error": str(e)}

    def g(r, c):
        return r[c] if c < len(r) else ""

    items = []
    for r in rows[1:]:
        name = g(r, C_NAME).strip()
        if not name:
            continue
        wh = _num(g(r, C_WH)) or 0
        store = _num(g(r, C_STORE)) or 0
        mn = _num(g(r, C_MIN))
        total = wh + store
        need = (mn is not None and total < mn)
        items.append({
            "id": g(r, C_ID).strip(),
            "name": name,
            "category": g(r, C_CAT).strip(),
            "supplier": g(r, C_SUP).strip(),
            "warehouse": wh, "store": store, "total": total,
            "min": mn, "need": need,
            "lead": g(r, C_LEAD).strip(),
            "url": g(r, 11).strip(),
        })

    # 要発注を先頭に、その後カテゴリー→名前で並べる
    items.sort(key=lambda x: (not x["need"], x["category"], x["name"]))
    out = {"items": items, "needCount": sum(1 for x in items if x["need"])}
    _cache["t"] = now
    _cache["data"] = out
    return out
