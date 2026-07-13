#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""スタッフマニュアル層。
本文＝製造表スプレッドシートの `_manual_content` タブ（編集者はシート直編集でもアプリ加筆でもOK）。
更新のお知らせ＝`_manual_updates` タブ。どちらも週セレクタからは自動除外（_始まり）。
列: _manual_content = カテゴリ|種類|ラベル|内容|写真|出典
    _manual_updates = 日付|タイトル|内容|カテゴリ|記入者
"""
from datetime import date
import data_layer
import config_store

CONTENT_TAB = "_manual_content"
UPDATES_TAB = "_manual_updates"
CONTENT_HEADER = ["カテゴリ", "種類", "ラベル", "内容", "写真", "出典"]
UPDATES_HEADER = ["日付", "タイトル", "内容", "カテゴリ", "記入者"]

# 現場カテゴリ（掲示版の色と対応）
CATEGORIES = [
    {"id": "common",  "name": "共通",        "emoji": "⭐", "accent": "595959", "light": "EDEDED", "sub": "全員で守るルール"},
    {"id": "iriguchi","name": "入口",        "emoji": "🚪", "accent": "8B5E3C", "light": "F3E5D8", "sub": "出勤・開店・閉店"},
    {"id": "shikomi", "name": "仕込み台",    "emoji": "🥚", "accent": "C55A11", "light": "FCE4D6", "sub": "たまご管理・仕込み"},
    {"id": "kiji",    "name": "生地作り",    "emoji": "🥣", "accent": "BF8F00", "light": "FBE9C7", "sub": "タネづくり"},
    {"id": "yakidai", "name": "焼台",        "emoji": "🔥", "accent": "9E3D3D", "light": "F2DCDC", "sub": "焼き・銅板清掃"},
    {"id": "seizou",  "name": "製造作業台",  "emoji": "🫘", "accent": "538135", "light": "E2EFDA", "sub": "あんこ・生どら・フルーツ"},
    {"id": "counter", "name": "カウンター",  "emoji": "🛍️", "accent": "2E75B6", "light": "DDEBF7", "sub": "接客・レジ"},
    {"id": "tana",    "name": "材料などの棚","emoji": "📦", "accent": "7030A0", "light": "E6DCF0", "sub": "在庫・発注"},
    {"id": "pc",      "name": "PC・コピー機","emoji": "🖨️", "accent": "305496", "light": "DCE3EE", "sub": "打刻・袋印刷・伝票"},
    {"id": "sealer",  "name": "シーラー・梱包","emoji": "🎁", "accent": "1F7A6B", "light": "D6EAE4", "sub": "箱詰め・袋・発送"},
]
CAT_NAMES = [c["name"] for c in CATEGORIES]

TYPES = ["見出し", "項目", "箇条書き", "注意", "メモ", "ポイント", "禁止", "流れ", "冷蔵庫", "カード", "写真"]


def _ws(tab, header, cols=8):
    sh = data_layer._spreadsheet()
    try:
        return sh.worksheet(tab)
    except Exception:
        ws = sh.add_worksheet(title=tab, rows=400, cols=cols)
        ws.update(range_name="A1", values=[header], value_input_option="RAW")
        data_layer._spreadsheet(refresh=True)
        return sh.worksheet(tab)


def _rows(tab, header):
    ws = _ws(tab, header)
    vals = data_layer.cached_values(ws)
    return vals[1:] if len(vals) > 1 else []


def get_manual():
    """アプリ画面用の全データ。"""
    content = {}
    for r in _rows(CONTENT_TAB, CONTENT_HEADER):
        cat = (r[0] if len(r) > 0 else "").strip()
        if cat not in CAT_NAMES:
            continue
        content.setdefault(cat, []).append({
            "type": (r[1] if len(r) > 1 else "").strip() or "箇条書き",
            "label": r[2] if len(r) > 2 else "",
            "text": r[3] if len(r) > 3 else "",
            "photo": (r[4] if len(r) > 4 else "").strip(),
            "src": r[5] if len(r) > 5 else "",
        })
    updates = []
    for r in _rows(UPDATES_TAB, UPDATES_HEADER):
        if not (r and str(r[0]).strip()):
            continue
        updates.append({
            "date": str(r[0]).strip(),
            "title": r[1] if len(r) > 1 else "",
            "note": r[2] if len(r) > 2 else "",
            "cat": (r[3] if len(r) > 3 else "").strip(),
            "by": r[4] if len(r) > 4 else "",
        })
    updates.reverse()  # 新しい順（追記は下に増えるため）
    return {"categories": CATEGORIES, "content": content, "updates": updates,
            "today": date.today().isoformat()}


def _check_code(code):
    want = str(config_store.get_config("manual_edit_code", "どら") or "").strip()
    return str(code or "").strip() == want


def add_update(title, note="", cat="", by="", when=None):
    ws = _ws(UPDATES_TAB, UPDATES_HEADER)
    d = when or date.today().strftime("%Y/%m/%d")
    ws.append_row([d, title, note, cat, by], value_input_option="RAW")
    data_layer.invalidate(UPDATES_TAB)
    return {"ok": True}


def add_entry(data):
    """アプリからの加筆。カテゴリ末尾（次カテゴリの直前）に1行挿入し、お知らせにも自動掲載。"""
    if not _check_code(data.get("code")):
        return {"ok": False, "error": "合言葉が違います"}
    cat = str(data.get("cat", "")).strip()
    typ = str(data.get("type", "箇条書き")).strip()
    label = str(data.get("label", "")).strip()
    text = str(data.get("text", "")).strip()
    by = str(data.get("by", "")).strip()
    if cat not in CAT_NAMES:
        return {"ok": False, "error": "カテゴリが不正です"}
    if typ not in TYPES:
        typ = "箇条書き"
    if not text and typ != "見出し":
        return {"ok": False, "error": "内容が空です"}
    src = date.today().strftime("%Y/%m/%d") + (f" {by}" if by else "") + " アプリ追記"

    ws = _ws(CONTENT_TAB, CONTENT_HEADER)
    col = ws.col_values(1)  # カテゴリ列
    last = None
    for i, v in enumerate(col, start=1):
        if str(v).strip() == cat:
            last = i
    row = [cat, typ, label, text, "", src]
    if last is None:
        ws.append_row(row, value_input_option="RAW")
    else:
        ws.insert_row(row, index=last + 1, value_input_option="RAW")
    data_layer.invalidate(CONTENT_TAB)

    head = label or text
    if len(head) > 40:
        head = head[:39] + "…"
    add_update(f"{cat}に追記", head, cat, by)
    return {"ok": True}


def announce(data):
    """お知らせだけを投稿（内容変更をシートで直接行ったときの周知用）。"""
    if not _check_code(data.get("code")):
        return {"ok": False, "error": "合言葉が違います"}
    title = str(data.get("title", "")).strip()
    if not title:
        return {"ok": False, "error": "タイトルが空です"}
    return add_update(title, str(data.get("note", "")).strip(),
                      str(data.get("cat", "")).strip(), str(data.get("by", "")).strip())
