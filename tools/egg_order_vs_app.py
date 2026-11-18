#!/usr/bin/env python3
"""手計算の実発注と、アプリの推奨と、実績から見た必要量を並べて比べる（読み取り専用）。

使い方: python3 tools/egg_order_vs_app.py [さかのぼる週数(既定10)]

並べる3つ
  A 実発注   … 配送便別合算(W/Y)に記録された、実際に発注した袋数
  B アプリ推奨 … 前の週タブの「翌週正味発注数」。袋数はアプリと同じ丸め
                 （卵黄は端数7回転以上・卵白は4回転以上で1袋足す）
  C 必要量   … その便がカバーした日の製造実績から計算した、繰越を考えない必要袋数

見方
  A−B が「人とアプリの食い違い」。A−C が「結果として多かった／少なかった」。
  B は今のシート（製造実績が入ったあと）で計算し直した値なので、
  発注した当時にアプリが出していた数とは違うことがある（当時は予定、いまは実績）。
"""
import datetime
import math
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import gspread
from google.oauth2.service_account import Credentials

SHEET_ID = "1PRDhGP_4xiO_ZjJP3NB9Id3PmaPa5W7hNyrqFQ5EyqM"
CRED = ("/Users/suzuki3/Library/CloudStorage/Dropbox-Detale/D& W/どら山/過去/"
        "dw_budget_profit_sheets_automation/config/google_credentials.json")
YOLK_G, WHITE_G, BAG_G = 400, 750, 5000      # 1回転あたり / 1袋
YOLK_THR, WHITE_THR = 7, 4                   # 端数がこの回転数以上なら1袋足す
JST = datetime.timezone(datetime.timedelta(hours=9))
BACK = int(sys.argv[1]) if len(sys.argv) > 1 else 10


def bags(rot, per_rot, thr):
    """アプリと同じ丸め: 端数が thr 回転以上なら1袋足す、未満は切り捨て。"""
    if rot <= 0:
        return 0
    per_bag = BAG_G / per_rot                 # 1袋で何回転ぶんか
    whole = int(rot // per_bag)
    rest = rot - whole * per_bag
    return whole + (1 if rest >= thr else 0)


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
if cur not in titles:
    raise SystemExit(f"今週タブ {cur} が見つかりません")
weeks = [t for t in titles[max(0, titles.index(cur) - BACK): titles.index(cur) + 1]
         if re.fullmatch(r"\d{4}", t)]
print(f"今日 {today} / 調べる週: {weeks[0]}〜{weeks[-1]}（{len(weeks)}週）\n")

data = {}
for t in weeks:
    ws = sh.worksheet(t)
    g = retry(lambda: ws.get("A1:BE100"))

    def c(r, k):
        row = g[r - 1] if r - 1 < len(g) else []
        return (str(row[k]) if k < len(row) else "").strip()

    def num(s):
        try:
            return float(str(s).replace(",", ""))
        except Exception:
            return 0.0

    kc = next((r for r in range(1, 70) if c(r, 0).startswith("回転数（切上げ）")), None)
    rot = [num(c(kc, 21 + i)) for i in range(7)] if kc else [0] * 7
    bins = {}
    for r in range(50, 100):
        nm = c(r, 0)[:3]
        if nm in ("火曜便", "木曜便", "土曜便"):
            bins[nm] = (num(c(r, 22)) / BAG_G, num(c(r, 24)) / BAG_G)
    plan = {r: (num(c(r, 41)), num(c(r, 44))) for r in (16, 17, 18)}   # 翌週正味発注数(回転)
    data[t] = {"rot": rot, "bins": bins, "plan": plan}
    time.sleep(5)

rows = []
for i, t in enumerate(weeks):
    if i == 0:
        continue
    prev, d, dp = weeks[i - 1], data[weeks[i]], data[weeks[i - 1]]
    nxt = data[weeks[i + 1]]["rot"] if i + 1 < len(weeks) else [0] * 7
    # その便がカバーした日の製造実績（繰越を考えない必要量）
    need_rot = {"火曜便": d["rot"][2] + d["rot"][3],
                "木曜便": d["rot"][4] + d["rot"][5],
                "土曜便": d["rot"][6] + nxt[0] + nxt[1]}
    for key, prow in (("火曜便", 16), ("木曜便", 17), ("土曜便", 18)):
        a_y, a_w = d["bins"].get(key, (0, 0))
        p_y, p_w = dp["plan"][prow]
        b_y, b_w = bags(p_y, YOLK_G, YOLK_THR), bags(p_w, WHITE_G, WHITE_THR)
        n = need_rot[key]
        c_y, c_w = bags(n, YOLK_G, YOLK_THR), bags(n, WHITE_G, WHITE_THR)
        rows.append((t, key, a_y, a_w, b_y, b_w, c_y, c_w))

print("週     便      実発注(黄/白)  アプリ推奨  必要量   実−推奨   実−必要")
tot = [0, 0, 0, 0, 0, 0]
for t, k, ay, aw, by, bw, cy, cw in rows:
    if ay == 0 and aw == 0:
        continue                                   # まだ発注していない週は飛ばす
    tot[0] += ay; tot[1] += aw; tot[2] += by; tot[3] += bw; tot[4] += cy; tot[5] += cw
    print(f"{t}  {k}   {ay:4.0f}/{aw:4.0f}     {by:4d}/{bw:4d}   {cy:4d}/{cw:4d}"
          f"   {ay - by:+4.0f}/{aw - bw:+4.0f}  {ay - cy:+4.0f}/{aw - cw:+4.0f}")

print(f"\n合計   実発注 卵黄{tot[0]:.0f}袋・卵白{tot[1]:.0f}袋")
print(f"       アプリ 卵黄{tot[2]}袋・卵白{tot[3]}袋  （実−推奨 卵黄{tot[0]-tot[2]:+.0f}・卵白{tot[1]-tot[3]:+.0f}）")
print(f"       必要量 卵黄{tot[4]}袋・卵白{tot[5]}袋  （実−必要 卵黄{tot[0]-tot[4]:+.0f}・卵白{tot[1]-tot[5]:+.0f}）")
if tot[2]:
    print(f"\nアプリ推奨に対する実発注の比率: 卵黄 {tot[0]/tot[2]*100:.0f}% / 卵白 {tot[1]/tot[3]*100:.0f}%")
if tot[4]:
    print(f"必要量に対する実発注の比率:     卵黄 {tot[0]/tot[4]*100:.0f}% / 卵白 {tot[1]/tot[5]*100:.0f}%")
