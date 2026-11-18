#!/usr/bin/env python3
"""毎日入力された卵の在庫（実測）の推移を見る（読み取り専用）。

使い方: python3 tools/egg_stock_history.py [さかのぼる週数(既定10)]

在庫は「その日の営業終了後の残り」。
 ・最小値が0に近い日があれば、発注が足りていなかった恐れ
 ・最小値がいつも大きければ、発注が多すぎて在庫が寝ている
 ・1回転＝卵黄400g・卵白750g。5kg袋だと卵黄12.5回転・卵白6.7回転ぶん
"""
import datetime
import os
import re
import sys
import time

import gspread
from google.oauth2.service_account import Credentials

SHEET_ID = "1PRDhGP_4xiO_ZjJP3NB9Id3PmaPa5W7hNyrqFQ5EyqM"
CRED = ("/Users/suzuki3/Library/CloudStorage/Dropbox-Detale/D& W/どら山/過去/"
        "dw_budget_profit_sheets_automation/config/google_credentials.json")
JST = datetime.timezone(datetime.timedelta(hours=9))
BACK = int(sys.argv[1]) if len(sys.argv) > 1 else 10
WD = "月火水木金土日"


def retry(fn):
    for i in range(6):
        try:
            return fn()
        except Exception as e:
            if "429" in str(e) and i < 5:
                time.sleep(65)
                continue
            raise


gc = gspread.authorize(Credentials.from_service_account_file(
    CRED, scopes=["https://www.googleapis.com/auth/spreadsheets"]))
sh = retry(lambda: gc.open_by_key(SHEET_ID))
titles = [w.title for w in retry(lambda: sh.worksheets())]
today = datetime.datetime.now(JST).date()
monday = today - datetime.timedelta(days=today.weekday())
cur = "%02d%02d" % (monday.month, monday.day)
weeks = [t for t in titles[max(0, titles.index(cur) - BACK): titles.index(cur) + 1]
         if re.fullmatch(r"\d{4}", t)]
print(f"今日 {today} / 在庫の実測（営業終了後の残り・回転）  {weeks[0]}〜{weeks[-1]}\n")
print("週     月  火  水  木  金  土  日   ｜ 最小 平均（卵黄 / 卵白）")

ys, wsall, low = [], [], []
for t in weeks:
    ws = sh.worksheet(t)
    v = retry(lambda: ws.get("AQ6:AR12"))

    def val(i, k):
        row = v[i] if i < len(v) else []
        s = (str(row[k]) if k < len(row) else "").strip().replace(",", "")
        try:
            return float(s)
        except Exception:
            return None
    y = [val(i, 0) for i in range(7)]
    w = [val(i, 1) for i in range(7)]
    yy = [x for x in y if x is not None]
    ww = [x for x in w if x is not None]
    ys += yy
    wsall += ww
    for i in range(7):
        if y[i] is not None and y[i] <= 5:
            low.append(f"{t}{WD[i]} 卵黄{y[i]:g}")
        if w[i] is not None and w[i] <= 5:
            low.append(f"{t}{WD[i]} 卵白{w[i]:g}")
    cells = " ".join(f"{(f'{y[i]:.0f}/{w[i]:.0f}' if y[i] is not None and w[i] is not None else '—'):>7}"
                     for i in range(7))
    s_min = f"{min(yy):.0f}/{min(ww):.0f}" if yy and ww else "—"
    s_avg = f"{sum(yy)/len(yy):.0f}/{sum(ww)/len(ww):.0f}" if yy and ww else "—"
    print(f"{t} {cells} ｜ 最小 {s_min}  平均 {s_avg}")

if ys and wsall:
    print(f"\n全期間: 卵黄 最小{min(ys):.0f}・平均{sum(ys)/len(ys):.0f}回転"
          f"（{sum(ys)/len(ys)*0.4/5:.1f}袋ぶん）")
    print(f"        卵白 最小{min(wsall):.0f}・平均{sum(wsall)/len(wsall):.0f}回転"
          f"（{sum(wsall)/len(wsall)*0.75/5:.1f}袋ぶん）")
    print(f"        入力のあった日数: 卵黄{len(ys)}日 / 卵白{len(wsall)}日")
print("\n在庫が5回転以下だった日:", "なし" if not low else " , ".join(low))
