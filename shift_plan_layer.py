#!/usr/bin/env python3
"""当日の時間割アプリへ渡すシフト予定（2026-11-02 本人指示「人はエアシフトと連動して」）。

元は非公開シートの「シフト予定」タブ（Mac mini が Airシフトから定期的に書く）。
給与は含まない。合言葉は本人指示で廃止（2026-11-02）。呼び出し元は app.py で時間割アプリの出どころに限る。
"""
import json
import re
import time
from datetime import datetime

import data_layer
import manager_hr_layer as hr


def get_day(date):
    rows = hr._values("シフト予定")
    updated = rows[0][6] if rows and len(rows[0]) > 6 else ""
    people = [{"name": r[1], "start": r[2], "end": r[3], "status": r[4] if len(r) > 4 else ""}
              for r in rows[1:] if len(r) >= 4 and r[0] == date]
    return {"date": date, "updated": updated, "people": people}


PRINT_TAB = "印刷依頼"
PRINT_HEAD = ["受付番号", "日付", "受付時刻", "内容", "状態", "メモ"]


def _print_ws():
    sh = data_layer._client().open_by_key(hr.HR_SHEET_ID)
    try:
        return sh.worksheet(PRINT_TAB)
    except Exception:  # noqa: BLE001
        ws = sh.add_worksheet(title=PRINT_TAB, rows=300, cols=6)
        ws.append_row(PRINT_HEAD)
        return ws


def add_print(date, body):
    """画面の「A3で印刷」の依頼を受け付ける。実際の印刷は Mac mini の見張りが行う。"""
    text = json.dumps(body, ensure_ascii=False)
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(date or "")) or len(text) > 40000:
        raise ValueError("印刷の依頼内容が正しくありません。")
    pid = str(int(time.time() * 1000))
    _print_ws().append_row([pid, date, datetime.now(hr.JST).strftime("%Y-%m-%d %H:%M:%S"), text, "待ち", ""],
                           value_input_option="RAW")
    return pid


def print_status(pid):
    for r in _print_ws().get_all_values()[1:]:
        if r and r[0] == str(pid):
            return {"status": r[4] if len(r) > 4 else "", "note": r[5] if len(r) > 5 else ""}
    return {"status": "不明", "note": ""}
