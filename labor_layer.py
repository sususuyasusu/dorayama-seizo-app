#!/usr/bin/env python3
"""日別の人件費・交通費を製造表の _app_labor タブから読む。60秒キャッシュ。"""
import time
import data_layer

TAB = "_app_labor"
_cache = {"t": 0.0, "map": {}}
_TTL = 60.0


def _to_int(s):
    s = str(s).replace("¥", "").replace(",", "").strip()
    try:
        return int(round(float(s))) if s not in ("", "-") else None
    except ValueError:
        return None


def _parse_md(d):
    p = str(d).strip().replace("-", "/").split("/")
    if len(p) >= 3:
        try:
            return (int(p[1]), int(p[2]))
        except ValueError:
            return None
    return None


def daily_labor_map():
    now = time.time()
    if _cache["map"] and now - _cache["t"] < _TTL:
        return _cache["map"]
    try:
        ws = data_layer._spreadsheet().worksheet(TAB)
        vals = data_layer.cached_values(ws)
    except Exception:
        return _cache["map"]
    m = {}
    for row in vals[1:]:
        if len(row) < 2:
            continue
        md = _parse_md(row[0])
        if not md:
            continue
        labor = _to_int(row[1])
        trans = _to_int(row[2]) if len(row) > 2 else None
        if labor is not None and labor > 0:
            m[md] = {"labor": labor, "transport": trans or 0}
    _cache["t"] = now
    _cache["map"] = m
    return m
