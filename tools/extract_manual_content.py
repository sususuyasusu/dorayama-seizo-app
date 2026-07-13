#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""印刷版マニュアルの生成スクリプト(_build_station_manual.py)から、
アプリ用のマニュアルデータ(data/manual_seed.json)を機械的に抽出する。
生成スクリプトの内容は全てリテラル引数なのでAST解析で安全に取り出せる。
使い方: python3 tools/extract_manual_content.py
"""
import ast
import json
from pathlib import Path

SRC = Path("/Users/suzuki3/Library/CloudStorage/Dropbox-Detale/D& W/どら山/マニュアル/_build_station_manual.py")
OUT = Path(__file__).resolve().parent.parent / "data" / "manual_seed.json"

NOTE_KIND = {"warn": "注意", "star": "ポイント", "info": "メモ"}


def lit(node):
    """リテラルノード→Python値。"""
    return ast.literal_eval(node)


def photo_band(call):
    """photos() の引数 full()/half()/trio() を写真タプルのリストへ。"""
    name = call.func.id if isinstance(call.func, ast.Name) else ""
    args = [lit(a) for a in call.args]
    if name == "full":
        fn, cap = args
        return [(fn, cap)]
    if name == "half":
        f1, c1, f2, c2 = args
        return [(f1, c1), (f2, c2)]
    if name == "trio":
        return [(fn, cap) for fn, cap in args[0]]
    return []


def main():
    tree = ast.parse(SRC.read_text(encoding="utf-8"))
    cats = []          # [{name, accent, light, title, src, rows:[...]}]
    cur = None
    for node in tree.body:
        # s = Sheet(wb, "名前", "色", "薄色")
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            f = node.value.func
            if isinstance(f, ast.Name) and f.id == "Sheet":
                a = node.value.args
                cur = {"name": lit(a[1]), "accent": lit(a[2]), "light": lit(a[3]),
                       "title": "", "src": "", "rows": []}
                cats.append(cur)
                continue
        if cur is None or not isinstance(node, ast.Expr) or not isinstance(node.value, ast.Call):
            continue
        call = node.value
        f = call.func
        if not (isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) and f.value.id == "s"):
            continue
        m = f.attr
        R = cur["rows"]
        if m == "title":
            cur["title"] = lit(call.args[0])
            cur["src"] = lit(call.args[1]).replace("\n", "")
        elif m == "bar":
            R.append({"type": "見出し", "label": "", "text": lit(call.args[0]).lstrip("■ ").strip()})
        elif m == "rows":
            for label, desc in lit(call.args[0]):
                R.append({"type": "項目", "label": label, "text": desc})
        elif m == "bullets":
            for it in lit(call.args[0]):
                R.append({"type": "箇条書き", "label": "", "text": it})
        elif m == "note":
            kind = lit(call.args[1]) if len(call.args) > 1 else "info"
            R.append({"type": NOTE_KIND.get(kind, "メモ"), "label": "", "text": lit(call.args[0])})
        elif m == "redmark":
            for it in lit(call.args[0]):
                R.append({"type": "禁止", "label": "", "text": it})
        elif m == "quad":
            cells = lit(call.args[0])
            R.append({"type": "カード", "label": "|".join(h for h, _ in cells),
                      "text": "|".join(d for _, d in cells)})
        elif m == "flow":
            R.append({"type": "流れ", "label": "", "text": " → ".join(lit(call.args[0]))})
        elif m == "fifo":
            R.append({"type": "冷蔵庫", "label": "", "text": ""})
        elif m == "photos":
            for band in call.args[0].elts:      # list of full()/half()/trio() calls
                for fn, cap in photo_band(band):
                    R.append({"type": "写真", "label": cap, "text": "", "photo": fn})
        # gap/footer/finish は無視
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(cats, ensure_ascii=False, indent=1), encoding="utf-8")
    total = sum(len(c["rows"]) for c in cats)
    print(f"OK: {len(cats)}カテゴリ / {total}行 -> {OUT}")
    for c in cats:
        photos = sum(1 for r in c["rows"] if r["type"] == "写真")
        print(f"  {c['name']}: {len(c['rows'])}行 (写真{photos})")


if __name__ == "__main__":
    main()
