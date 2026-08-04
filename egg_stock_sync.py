#!/usr/bin/env python3
"""AppSheet在庫 → 製造表 卵在庫欄(AQ/AR) の自動同期（アプリ内蔵版）。

従来はGoogle Apps Scriptの10分トリガ(syncEggStockFromAppSheet)が担当していたが、
2026-08-05にトリガ停止で在庫欄が数日空になる事故が起きたため、常時稼働している
このアプリ自身が同じ同期を行う。Apps Script側が生きていても同じ値を書くだけで無害。

仕様（Apps Script版と同じ）:
 - AppSheet正本シート「商品マスタ」の P027(卵黄)/P028(卵白) G列(店舗在庫) を読む
 - 今週タブの今日の行(月=6..日=12)の AQ(卵黄)/AR(卵白) に書き込む
 - AppSheetが正＝手入力より優先（既存ルール踏襲）。10分ごと。
"""
import threading
import time
import datetime
import data_layer

SRC_SHEET_ID = "14gFJiuVpuGT-GwDhGw-plVqbX5ulKY31g7fvRtwGJ8k"
SRC_TAB = "商品マスタ"
YOLK_PID, WHITE_PID = "P027", "P028"
INTERVAL_SEC = 600

status = {"lastRun": None, "lastResult": None, "yolk": None, "white": None}


def _sync_once():
    gc = data_layer._client()
    src = gc.open_by_key(SRC_SHEET_ID).worksheet(SRC_TAB)
    y = w = None
    for r in src.get_all_values():
        if not r:
            continue
        if r[0] == YOLK_PID and len(r) > 6:
            y = str(r[6]).strip()
        elif r[0] == WHITE_PID and len(r) > 6:
            w = str(r[6]).strip()
    if not y and not w:
        return "AppSheetに在庫値なし（何も書かない）"
    ws = data_layer.open_ws(None)  # 今週タブ
    row = 6 + datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))).weekday()
    ws.batch_update([{"range": f"AQ{row}:AR{row}", "values": [[y or "", w or ""]]}],
                    value_input_option="USER_ENTERED")
    try:
        data_layer.invalidate(ws.title)
    except Exception:
        pass
    status["yolk"], status["white"] = y, w
    return f"{ws.title} 行{row} ← 卵黄{y}/卵白{w}"


def _loop():
    while True:
        try:
            res = _sync_once()
        except Exception as e:
            res = f"error: {type(e).__name__}: {str(e)[:200]}"
        status["lastRun"] = datetime.datetime.now(
            datetime.timezone(datetime.timedelta(hours=9))).strftime("%Y-%m-%d %H:%M:%S")
        status["lastResult"] = res
        time.sleep(INTERVAL_SEC)


def start():
    t = threading.Thread(target=_loop, daemon=True, name="egg-stock-sync")
    t.start()
