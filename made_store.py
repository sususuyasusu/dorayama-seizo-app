#!/usr/bin/env python3
"""「作った数」の保存層（クラウド保存・ブロック対応）。

製造表シート内の専用タブ `_app_made` に保存する。既存の予定・実績・回転数・
卵発注などの計算には一切触れない（別タブなので数式に影響なし）。
同じ商品名でも店舗用と各催事で別管理するため、ブロック名をキーに含める。
"""
import data_layer

TAB = "_app_made"
HEADER = ["週", "ブロック", "商品", "月", "火", "水", "木", "金", "土", "日"]


def _ws():
    sh = data_layer._spreadsheet()
    try:
        return sh.worksheet(TAB)
    except Exception:
        ws = sh.add_worksheet(title=TAB, rows=400, cols=12)
        ws.update(range_name="A1", values=[HEADER])
        data_layer._spreadsheet(refresh=True)  # メタ情報に新タブを反映
        return ws


def _to_int(s):
    s = str(s).strip()
    return int(s) if s.lstrip("-").isdigit() else None


def get_made(tab):
    """{ブロック名: {商品名: [7日分]}} を返す。"""
    ws = _ws()
    out = {}
    for r in data_layer.cached_values(ws)[1:]:
        if len(r) >= 3 and r[0] == tab:
            block, product = r[1], r[2]
            vals = [(_to_int(r[i]) if i < len(r) else None) for i in range(3, 10)]
            out.setdefault(block, {})[product] = vals
    return out


def set_made(tab, block, product, day_index, value):
    ws = _ws()
    rows = ws.get_all_values()
    di = int(day_index)
    v = "" if value in ("", None) else int(value)
    target = None
    for idx, r in enumerate(rows[1:], start=2):
        if len(r) >= 3 and r[0] == tab and r[1] == block and r[2] == product:
            target = idx
            break
    if target is None:
        row = [tab, block, product] + [""] * 7
        row[3 + di] = v
        ws.append_row(row, value_input_option="RAW")
    else:
        ws.update_cell(target, 4 + di, v)  # D列=月 が 4列目
    data_layer.invalidate(TAB)  # 書込を反映するためキャッシュ破棄
    return get_made(tab)


def seed(tab, blocks):
    """今週タブの行がまだ無ければ、各ブロック×商品を実績値で初期登録する。"""
    ws = _ws()
    rows = data_layer.cached_values(ws)
    if any(len(r) >= 1 and r[0] == tab for r in rows[1:]):
        return
    new = []
    for b in blocks:
        for p in b["products"]:
            vals = [("" if v is None else int(v)) for v in p["actual"]]
            vals = (vals + [""] * 7)[:7]
            new.append([tab, b["name"], p["name"]] + vals)
    if new:
        ws.append_rows(new, value_input_option="RAW")
        data_layer.invalidate(TAB)
