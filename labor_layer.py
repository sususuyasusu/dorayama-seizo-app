#!/usr/bin/env python3
"""日別の店舗人件費を予実シート(03_実績_店舗日次)から読む。店舗スタッフのみで
ユーザーの概算規模とほぼ一致する正しい範囲。交通費列は無いため None。60秒キャッシュ。"""
import time
import data_layer

YOSAN_SHEET_ID = "1PxLrwb2x2ZDs0DaWgmGuwW-6IzRvqXYJhywsGzyLftY"
TAB = "03_実績_店舗日次"
DATE_COL = 0
LABOR_COL = 19

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
        gc = data_layer._client()
        ws = gc.open_by_key(YOSAN_SHEET_ID).worksheet(TAB)
        vals = ws.get_all_values()
    except Exception:
        return _cache["map"]
    m = {}
    for row in vals[3:]:
        if len(row) <= LABOR_COL:
            continue
        md = _parse_md(row[DATE_COL])
        if not md:
            continue
        labor = _to_int(row[LABOR_COL])
        if labor is not None and labor > 0:
            m[md] = {"labor": labor, "transport": None}
    _cache["t"] = now
    _cache["map"] = m
    return m
