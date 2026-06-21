#!/usr/bin/env python3
"""「作った数」の保存層（フェーズ4：クラウド保存）。

製造表シート内の専用タブ `_app_made` に保存する。既存の予定・実績・回転数・
卵発注などの計算には一切触れない（別タブなので数式に影響なし）。
ローカルファイルに依存しないため、Render等のネット配信でも値が消えない。
"""
import data_layer

TAB = "_app_made"
HEADER = ["週", "商品", "月", "火", "水", "木", "金", "土", "日"]


def _ws():
    gc = data_layer._client()
    sh = gc.open_by_key(data_layer.SHEET_ID)
    try:
        return sh.worksheet(TAB)
    except Exception:
        ws = sh.add_worksheet(title=TAB, rows=300, cols=10)
        ws.update(range_name="A1", values=[HEADER])
        return ws


def _to_int(s):
    s = str(s).strip()
    return int(s) if s.lstrip("-").isdigit() else None


def get_made(tab):
    ws = _ws()
    out = {}
    for r in ws.get_all_values()[1:]:
        if len(r) >= 2 and r[0] == tab:
            out[r[1]] = [(_to_int(r[i]) if i < len(r) else None) for i in range(2, 9)]
    return out


def set_made(tab, product, day_index, value):
    ws = _ws()
    rows = ws.get_all_values()
    di = int(day_index)
    v = "" if value in ("", None) else int(value)
    target = None
    for idx, r in enumerate(rows[1:], start=2):
        if len(r) >= 2 and r[0] == tab and r[1] == product:
            target = idx
            break
    if target is None:
        row = [tab, product] + [""] * 7
        row[2 + di] = v
        ws.append_row(row, value_input_option="RAW")
    else:
        ws.update_cell(target, 3 + di, v)
    return get_made(tab)


def seed(tab, products):
    ws = _ws()
    rows = ws.get_all_values()
    if any(len(r) >= 1 and r[0] == tab for r in rows[1:]):
        return
    new = []
    for pr in products:
        vals = [("" if v is None else int(v)) for v in pr["actual"]]
        vals = (vals + [""] * 7)[:7]
        new.append([tab, pr["name"]] + vals)
    if new:
        ws.append_rows(new, value_input_option="RAW")
