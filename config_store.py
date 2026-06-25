#!/usr/bin/env python3
"""アプリの設定値(週に依らない定数)を製造表の _app_config タブ(key/value)に保存する。
旬どらの白あん原単位など、ユーザーが入力する係数を置く。30秒キャッシュ。"""
import time
import data_layer

TAB = "_app_config"
_cache = {"t": 0.0, "map": None}
_TTL = 30.0


def _ws():
    sh = data_layer._spreadsheet()
    try:
        return sh.worksheet(TAB)
    except Exception:
        ws = sh.add_worksheet(title=TAB, rows=50, cols=2)
        ws.update(range_name="A1", values=[["key", "value"]], value_input_option="RAW")
        data_layer._spreadsheet(refresh=True)
        return sh.worksheet(TAB)


def _all():
    now = time.time()
    if _cache["map"] is not None and now - _cache["t"] < _TTL:
        return _cache["map"]
    m = {}
    try:
        for row in _ws().get_all_values()[1:]:
            if row and str(row[0]).strip():
                m[str(row[0]).strip()] = row[1] if len(row) > 1 else ""
    except Exception:
        return _cache["map"] or {}
    _cache["t"] = now
    _cache["map"] = m
    return m


def get_config(key, default=None):
    v = _all().get(key)
    return v if v not in (None, "") else default


def set_config(key, value):
    ws = _ws()
    rows = ws.get_all_values()
    for i, row in enumerate(rows):
        if row and str(row[0]).strip() == key:
            ws.update(range_name="B" + str(i + 1), values=[[str(value)]], value_input_option="RAW")
            _cache["map"] = None
            return
    ws.append_row([key, str(value)], value_input_option="RAW")
    _cache["map"] = None
