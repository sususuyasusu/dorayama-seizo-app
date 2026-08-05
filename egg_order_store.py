#!/usr/bin/env python3
"""「発注済みにする」で押した袋数を、端末ではなく製造表に記録する。

これまで発注済はブラウザのlocalStorageにしか残らず、他のスタッフの画面には
まったく反映されなかった（2026-08-05 に土便で発覚）。

記録は2か所に書く。
  ① 配送便合算セクション W列(卵黄g)/Y列(卵白g) 行69=火/70=木/71=土
     … LINEの卵発注Botと同じ場所。AU/AV(届く回転)はシート側の数式が自動計算する。
  ② _app_egg_ordered タブ … 「いつ・誰の操作で発注済にしたか」の控え。
     ①だけだと「発注済にした」のか「前の週の発注が残っている」のか区別できない。
"""
import datetime
import re
import time

import data_layer

TAB = "_app_egg_ordered"
HEAD = ["キー（週タブ＋便）", "配達日", "卵黄(袋)", "卵白(袋)", "記録日時"]
# 火曜便=行69 / 木曜便=行70 / 土曜便=行71（月=0 … 日=6）
BIN_ROW = {1: 69, 3: 70, 5: 71}
BAG_G = 5000                     # 1袋 = 5kg
_cache = {"t": 0.0, "map": None}
_TTL = 15.0


def _ws():
    sh = data_layer._spreadsheet()
    try:
        return sh.worksheet(TAB)
    except Exception:
        ws = sh.add_worksheet(title=TAB, rows=400, cols=len(HEAD))
        ws.update(range_name="A1", values=[HEAD], value_input_option="RAW")
        data_layer._spreadsheet(refresh=True)
        return sh.worksheet(TAB)


def _parse_date(s):
    m = re.match(r"^(\d{4})[-/](\d{1,2})[-/](\d{1,2})$", str(s or "").strip())
    if not m:
        return None
    try:
        return datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def _tab_for(d):
    monday = d - datetime.timedelta(days=d.weekday())
    return [f"{monday.month:02d}{monday.day:02d}", f"{monday.month}{monday.day:02d}"]


def all_ordered():
    """{キー: {date, y, w, at}}。画面の「✅発注済」表示に使う。"""
    now = time.time()
    if _cache["map"] is not None and now - _cache["t"] < _TTL:
        return _cache["map"]
    m = {}
    try:
        for row in _ws().get_all_values()[1:]:
            if not (row and str(row[0]).strip()):
                continue
            row = list(row) + [""] * (5 - len(row))
            try:
                m[str(row[0]).strip()] = {
                    "date": row[1], "y": float(row[2] or 0),
                    "w": float(row[3] or 0), "at": row[4]}
            except ValueError:
                continue
    except Exception:
        return _cache["map"] or {}
    _cache.update({"t": now, "map": m})
    return m


def set_ordered(key, date_str, yolk_bags, white_bags):
    """押した袋数を製造表に書く。配達日から週タブと行を決める。"""
    key = (key or "").strip()
    d = _parse_date(date_str)
    if not key:
        return {"ok": False, "msg": "便が特定できません。"}
    if not d:
        return {"ok": False, "msg": f"配達日が読み取れません（{date_str}）。"}
    row = BIN_ROW.get(d.weekday())
    if row is None:
        return {"ok": False, "msg": f"{d.month}/{d.day} は火・木・土便のいずれでもありません。"}
    try:
        y = max(0, int(round(float(yolk_bags))))
        w = max(0, int(round(float(white_bags))))
    except (TypeError, ValueError):
        return {"ok": False, "msg": "袋数が数字ではありません。"}

    sh = data_layer._spreadsheet()
    tab_map = {t.title: t for t in sh.worksheets()}
    cands = _tab_for(d)
    ws = next((tab_map[c] for c in cands if c in tab_map), None)
    if ws is None:
        return {"ok": False, "msg": f"配達日の週タブが見つかりません（候補 {cands}）。"}

    ws.batch_update([{"range": f"W{row}", "values": [[y * BAG_G]]},
                     {"range": f"Y{row}", "values": [[w * BAG_G]]}],
                    value_input_option="USER_ENTERED")
    data_layer.invalidate(ws.title)

    stamp = datetime.datetime.now(
        datetime.timezone(datetime.timedelta(hours=9))).strftime("%Y/%m/%d %H:%M")
    body = [key, d.isoformat(), y, w, stamp]
    tw = _ws()
    rows = tw.get_all_values()
    hit = next((i + 1 for i, r in enumerate(rows)
                if r and str(r[0]).strip() == key), None)
    if hit:
        tw.update(range_name=f"A{hit}:E{hit}", values=[body],
                  value_input_option="USER_ENTERED")
    else:
        tw.append_row(body, value_input_option="USER_ENTERED")
    _cache["map"] = None
    return {"ok": True, "msg": f"{d.month}/{d.day} 卵黄{y}袋・卵白{w}袋で記録しました",
            "tab": ws.title, "row": row}


def clear_ordered(key):
    """発注済を取り消す。控えの行だけ消し、シートの発注数はそのまま残す
    （すでに先方へ伝えた数を勝手に戻すと事故になるため）。"""
    key = (key or "").strip()
    tw = _ws()
    for i, r in enumerate(tw.get_all_values()):
        if r and str(r[0]).strip() == key:
            tw.delete_rows(i + 1)
            _cache["map"] = None
            return {"ok": True, "msg": "発注済の印を外しました（発注数はそのままです）"}
    _cache["map"] = None
    return {"ok": True, "msg": "記録がありませんでした"}
