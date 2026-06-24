#!/usr/bin/env python3
"""製造表の材料（必要量・推奨発注量）と、在庫管理アプリの商品マスタ（在庫・要発注・発注先）を
材料ごとに突き合わせて1つに合体する。読み取り専用。
在庫アプリの商品名はパックサイズ付きで製造表の材料名と一致しないため、
キーワード(部分一致)で対応付ける。マッピングは下の MATERIAL_MAP（ユーザー確認済 2026-06-24）。"""
import material_layer
import inventory_layer

# 製造表の材料名 -> 在庫アプリ商品名に含まれるキーワード（部分一致で1件特定）
MATERIAL_MAP = {
    "砂糖": "てんさい上白糖",
    "黒糖": "黒砂糖",
    "重曹": "重曹",
    "スーパーバイオレット（薄力粉）": "スーパーバイオレット",
    "中力粉": "中力粉",
    "みりん": "みりん",
    "水あめ": "水飴",
    "あんこ": "粒あん",
    "白あん": "上白あん",
    "バター": "バター450g",
}


def _keyword_for(name):
    n = (name or "").strip()
    if n in MATERIAL_MAP:
        return MATERIAL_MAP[n]
    # 軽い揺れ吸収: マップのキーが材料名に含まれる/その逆
    for k, v in MATERIAL_MAP.items():
        if k in n or n in k:
            return v
    return None


def _num(s):
    try:
        return float(str(s).replace(",", "").strip())
    except (ValueError, AttributeError):
        return None


def _unit_word(inv_name):
    """在庫商品名から発注単位の語を拾う。なければ『袋』。"""
    n = inv_name or ""
    for w in ("ケース", "缶", "束", "本", "箱", "袋"):
        if w in n:
            return w
    return "袋"


def get_material_inventory(tab=None):
    mats = material_layer.get_materials(tab)
    inv = inventory_layer.get_inventory()
    items = inv.get("items", [])
    used = set()
    rows = []
    for m in mats["materials"]:
        kw = _keyword_for(m["name"])
        match = None
        if kw:
            for it in items:
                if kw in it["name"]:
                    match = it
                    break
        row = dict(m)  # name, unit, order, arrive, deliverBy, needUnits
        if match:
            used.add(match["name"])
            row.update({
                "invName": match["name"],
                "stock": match["total"],
                "min": match["min"],
                "supplier": match["supplier"],
                "url": match["url"],
                "need": match["need"],
                "linked": True,
            })
        else:
            row.update({"invName": None, "linked": False, "need": False})
        # 推奨発注量(g) ÷ 発注単位(g) = 発注袋数（例: 砂糖 60000g ÷ 30000g = 2袋）
        og = _num(m.get("order"))
        ou = _num(m.get("orderUnit"))
        if og is not None and ou and ou > 0:
            b = og / ou
            row["orderBags"] = int(round(b)) if abs(b - round(b)) < 0.05 else round(b, 1)
            row["orderUnitWord"] = _unit_word(match["name"] if match else "")
        else:
            row["orderBags"] = None
        rows.append(row)

    others = [it for it in items if it["name"] not in used]
    return {
        "tab": mats["tab"],
        "rows": rows,
        "others": others,
        "linkedNeed": sum(1 for r in rows if r.get("need")),
        "othersNeed": sum(1 for it in others if it.get("need")),
        "unlinked": sum(1 for r in rows if not r.get("linked")),
        "error": inv.get("error"),
    }
