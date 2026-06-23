#!/usr/bin/env python3
"""エアシフト由来の店舗日次人件費を「予実シート」から読む（製造実績タブ用）。

製造表(1PRDhGP…)の手入力人件費ではなく、毎朝のAirレジ/Airシフト自動同期が書き込む
予実シート 03_実績_店舗日次 の「人件費合計」(社員＋バイト)を日付ごとに拾う。
製造表とは別シートだが同じサービスアカウント(dorayama-sheets-bot)で読める。
同期が滞れば古い日までしか入らない（その日は None＝画面では「–」表示）。"""
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
    parts = str(d).strip().replace("-", "/").split("/")
    if len(parts) >= 3:
        try:
            return (int(parts[1]), int(parts[2]))
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
        lab = _to_int(row[LABOR_COL])
        if lab is not None and lab > 0:
            m[md] = lab
    _cache["t"] = now
    _cache["map"] = m
    return m
