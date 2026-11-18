#!/usr/bin/env python3
"""「アプリの推奨どおりに発注していたら在庫は足りたか」を再現する（読み取り専用）。

使い方: python3 tools/egg_what_if_app_orders.py [さかのぼる週数(既定10)]

やりかた
  その週の月曜の実測在庫を出発点に、毎日「前日の残り ＋ 届く − その日の製造」で追う。
  ① 実発注どおり（配送便別合算の記録）→ 実測の在庫とどれだけ合うかで、追い方が正しいか確かめる
  ② アプリの推奨どおり（前週タブの翌週正味発注数・アプリと同じ丸め）
  ②で在庫がマイナスになる日があれば「アプリでは足りなかった」。
  1袋＝卵黄12.5回転ぶん・卵白6.7回転ぶん。
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
YOLK_G, WHITE_G, BAG_G = 400, 750, 5000
YOLK_THR, WHITE_THR = 7, 4
JST = datetime.timezone(datetime.timedelta(hours=9))
BACK = int(sys.argv[1]) if len(sys.argv) > 1 else 10
ARRIVE = {"火曜便": 1, "木曜便": 3, "土曜便": 5}      # 月=0


def bags(rot, per_rot, thr):
    if rot <= 0:
        return 0
    per_bag = BAG_G / per_rot
    whole = int(rot // per_bag)
    return whole + (1 if rot - whole * per_bag >= thr else 0)


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
sheets = {w.title: w for w in retry(lambda: sh.worksheets())}   # 1回だけ取って使い回す
titles = list(sheets)
today = datetime.datetime.now(JST).date()
monday = today - datetime.timedelta(days=today.weekday())
cur = "%02d%02d" % (monday.month, monday.day)
weeks = [t for t in titles[max(0, titles.index(cur) - BACK): titles.index(cur) + 1]
         if re.fullmatch(r"\d{4}", t)]

raw = {}
for t in weeks:
    ws = sheets[t]
    g = retry(lambda: ws.get("A1:BE100"))

    def c(r, k):
        row = g[r - 1] if r - 1 < len(g) else []
        return (str(row[k]) if k < len(row) else "").strip()

    def num(s, d=0.0):
        try:
            return float(str(s).replace(",", ""))
        except Exception:
            return d
    kc = next((r for r in range(1, 70) if c(r, 0).startswith("回転数（切上げ）")), None)
    stock = []
    for i in range(7):
        y = c(6 + i, 42)
        w = c(6 + i, 43)
        stock.append((num(y, None) if y else None, num(w, None) if w else None))
    bins = {}
    for r in range(50, 100):
        nm = c(r, 0)[:3]
        if nm in ARRIVE:
            bins[nm] = (num(c(r, 22)) / BAG_G, num(c(r, 24)) / BAG_G)
    raw[t] = {"rot": [num(c(kc, 21 + i)) for i in range(7)] if kc else [0] * 7,
              "stock": stock, "bins": bins,
              "plan": {r: (num(c(r, 41)), num(c(r, 44))) for r in (16, 17, 18)}}
    time.sleep(5)

print(f"今日 {today} / 「アプリ推奨どおりに発注していたら」の再現  {weeks[0]}〜{weeks[-1]}\n")
print("週     発注の置きかえ   日曜終わりの在庫(黄/白)   週内の最小在庫   足りたか")
short_app = []
for i, t in enumerate(weeks):
    d = raw[t]
    if i == 0 or not d["bins"] or d["stock"][0][0] is None:
        continue
    prev = raw[weeks[i - 1]]
    plans = {"火曜便": prev["plan"][16], "木曜便": prev["plan"][17], "土曜便": prev["plan"][18]}
    out = {}
    for label, use_app in (("実発注", False), ("アプリ推奨", True)):
        y, w = d["stock"][0]
        miny, minw = y, w
        for day in range(1, 7):
            inc_y = inc_w = 0.0
            for k, ad in ARRIVE.items():
                if ad != day:
                    continue
                if use_app:
                    by = bags(plans[k][0], YOLK_G, YOLK_THR)
                    bw = bags(plans[k][1], WHITE_G, WHITE_THR)
                else:
                    by, bw = d["bins"].get(k, (0, 0))
                inc_y += by * BAG_G / YOLK_G
                inc_w += bw * BAG_G / WHITE_G
            y = y + inc_y - d["rot"][day]
            w = w + inc_w - d["rot"][day]
            miny, minw = min(miny, y), min(minw, w)
        out[label] = (y, w, miny, minw)
    for label in ("実発注", "アプリ推奨"):
        y, w, my, mw = out[label]
        ok = "✅足りた" if my >= 0 and mw >= 0 else "⚠️不足"
        if label == "アプリ推奨" and (my < 0 or mw < 0):
            short_app.append(f"{t}(黄{my:.0f}/白{mw:.0f})")
        print(f"{t} {label:9s}   {y:7.0f}/{w:5.0f}        {my:5.0f}/{mw:5.0f}     {ok}")
    act = raw[t]["stock"][6]
    if act[0] is not None:
        print(f"       （実測の日曜在庫 {act[0]:.0f}/{act[1]:.0f}  ＝実発注の再現との差 "
              f"{out['実発注'][0]-act[0]:+.0f}/{out['実発注'][1]-act[1]:+.0f}）")
print("\nアプリ推奨で在庫がマイナスになった週:", "なし" if not short_app else " , ".join(short_app))
