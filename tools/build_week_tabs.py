#!/usr/bin/env python3
"""製造表の週タブを作る（既定は下見。RUN=1 で実行）。

例:
  python3 tools/build_week_tabs.py --weeks 1026,1102 --source 1019 \
      --event "上野=2026-07-21:2026-11-30"
  RUN=1 python3 tools/build_week_tabs.py --weeks 1026,1102 --source 1019 \
      --event "上野=2026-07-21:2026-11-30"

やること
 1) --source のタブを複製して新しい週タブを作り、時系列の位置に置く
 2) 週の月曜日(B4)と卵ナビの日付(AO6)、実績側の日付(V4)を設定
 3) 在庫の手入力(AQ/AR)と実発注の記録(配送便別合算 W/Y)を空にする
 4) --event の販売期間から「製造日＝販売日の前日」で、期間外の日を0にする
 5) 週をまたぐ参照を正しい週・正しい行に付け替える（後ろの週を指すものは行だけ補正）
 6) #REF! が残ったセルは同じ式を入れ直して再計算させる

⚠️ 催事が3つ以上重なる週（4ブロック）はこのツールでは作れない。
   その場合は A〜AM列だけ部分的に行を挿入する手順が要る（Vault dorayama-seizou-calendar-sync.md）。
"""
import argparse
import datetime
import os
import re
import time

import gspread
from gspread.utils import rowcol_to_a1
from google.oauth2.service_account import Credentials

SHEET_ID = "1PRDhGP_4xiO_ZjJP3NB9Id3PmaPa5W7hNyrqFQ5EyqM"
CRED = ("/Users/suzuki3/Library/CloudStorage/Dropbox-Detale/D& W/どら山/過去/"
        "dw_budget_profit_sheets_automation/config/google_credentials.json")
REF = re.compile(r"'(\d{3,4})'!(\$?)([A-Z]{1,3})(\$?)(\d+)")
KROWS = {39, 41, 50, 52}          # 回転数（切上げ）が入りうる行
RUN = os.environ.get("RUN") == "1"

ap = argparse.ArgumentParser()
ap.add_argument("--weeks", required=True, help="作る週タブ(MMDD)をカンマ区切りで。例 1026,1102")
ap.add_argument("--source", required=True, help="複製元の週タブ(MMDD)")
ap.add_argument("--year", type=int, default=datetime.date.today().year)
ap.add_argument("--event", action="append", default=[],
                help="催事の販売期間 '名前=YYYY-MM-DD:YYYY-MM-DD'（製造は前日）。複数可")
args = ap.parse_args()

events = {}
for e in args.event:
    name, span = e.split("=", 1)
    s, t = span.split(":", 1)
    events[name] = (datetime.date.fromisoformat(s), datetime.date.fromisoformat(t))

gc = gspread.authorize(Credentials.from_service_account_file(
    CRED, scopes=["https://www.googleapis.com/auth/spreadsheets"]))
sh = gc.open_by_key(SHEET_ID)


def retry(fn):
    for i in range(6):
        try:
            return fn()
        except Exception as ex:
            if "429" in str(ex) and i < 5:
                time.sleep(65)
                continue
            raise


def monday_of(tab, year):
    return datetime.date(year, int(tab[:2]), int(tab[2:]))


def label_row(grid, label):
    for i, row in enumerate(grid):
        if row and str(row[0]).strip().startswith(label):
            return i + 1
    return None


titles = [w.title for w in retry(lambda: sh.worksheets())]
weeks_all = [t for t in titles if re.fullmatch(r"\d{4}", t)]
new = [w.strip() for w in args.weeks.split(",") if w.strip()]
src = sh.worksheet(args.source)
print(f"複製元 {args.source} / 作る週 {new}")

# ---- 1〜4) 作成と初期設定 ----
for tab in new:
    d0 = monday_of(tab, args.year)
    if tab in titles:
        print(f"[{tab}] 既にあり→作成スキップ")
        continue
    # 並び順は「複製元より後ろにあるタブ」だけで判断する。
    # 去年のタブ(1020/1027など)が同じMMDD形式で前方にあり、年を取り違えると挿入位置が狂う。
    recent = [t for t in titles[titles.index(args.source):] if re.fullmatch(r"\d{4}", t)]
    before = [t for t in recent if monday_of(t, args.year) < d0]
    prev = before[-1] if before else args.source
    idx = titles.index(prev) + 1
    print(f"[{tab}] {d0}（{prev}の直後に作成）")
    if not RUN:
        titles.insert(idx, tab)
        weeks_all.append(tab)
        continue
    ws = retry(lambda: sh.duplicate_sheet(src.id, insert_sheet_index=idx, new_sheet_name=tab))
    serial = (d0 - datetime.date(1899, 12, 30)).days
    grid = retry(lambda: ws.get("A1:AB100"))
    bins = [r for r in range(50, 100)
            if (grid[r - 1][0].strip() if r - 1 < len(grid) and grid[r - 1] else "")
            .startswith(("火曜便", "木曜便", "土曜便"))]
    ups = [{"range": "B4", "values": [[serial]]},
           {"range": "V4", "values": [["=B4"]]},
           {"range": "AO6", "values": [[f"=DATE({d0.year},{d0.month},{d0.day})"]]},
           {"range": "AQ6:AR12", "values": [["", ""] for _ in range(7)]}]
    for r in bins:
        ups += [{"range": f"W{r}", "values": [[""]]}, {"range": f"Y{r}", "values": [[""]]}]
    retry(lambda: ws.batch_update(ups, value_input_option="USER_ENTERED"))
    titles.insert(idx, tab)
    weeks_all.append(tab)
    time.sleep(3)

    # 催事の期間外を0にする（製造日＝販売日の前日）
    if events:
        grid = retry(lambda: ws.get("A1:AB100"))

        def c(r, k):
            row = grid[r - 1] if r - 1 < len(grid) else []
            return (str(row[k]) if k < len(row) else "").strip()
        cur, zeros = None, []
        for r in range(1, 60):
            if c(r, 0).startswith("【日別】"):
                break
            if c(r, 18) == "カテゴリー":
                cur = c(r, 0)
                continue
            if cur in events and c(r, 8) in ("はい", "いいえ") and c(r, 0):
                s, t = events[cur]
                for i in range(7):
                    day = d0 + datetime.timedelta(days=i)
                    if not (s - datetime.timedelta(days=1) <= day <= t - datetime.timedelta(days=1)):
                        zeros += [{"range": rowcol_to_a1(r, 2 + i), "values": [[0]]},
                                  {"range": rowcol_to_a1(r, 22 + i), "values": [[0]]}]
        if zeros:
            retry(lambda: ws.batch_update(zeros, value_input_option="USER_ENTERED"))
            print(f"   催事の期間外 {len(zeros) // 2}マスを0に")
        time.sleep(2)

if not RUN:
    print("（下見のみ・書き込みなし）")
    raise SystemExit

# ---- 5) 週をまたぐ参照の付け替え ----
all_titles = [w.title for w in retry(lambda: sh.worksheets())]
# 複製元より後ろにある週タブだけを対象にする（去年の同名タブを巻き込まない）
weeks_all = [t for t in all_titles[all_titles.index(args.source):] if re.fullmatch(r"\d{4}", t)]
kr = {}
for t in weeks_all:
    kr[t] = label_row(retry(lambda: sh.worksheet(t).get("A1:A70")), "回転数（切上げ）") or 41
    time.sleep(1)
order = {t: i for i, t in enumerate(weeks_all)}
print("回転数（切上げ）の行:", kr)

for t in weeks_all:
    ws = sh.worksheet(t)
    f = retry(lambda: ws.get("A1:BE130", value_render_option="FORMULA"))
    i = order[t]
    nxt = weeks_all[i + 1] if i + 1 < len(weeks_all) else None
    prv = weeks_all[i - 1] if i > 0 else None
    ups = []
    for r, row in enumerate(f, start=1):
        for cix, v in enumerate(row, start=1):
            if not (isinstance(v, str) and v.startswith("=") and "'!" in v):
                continue

            def rep(m):
                s, d1, cl, d2, rw = m.groups()
                if cl in ("BA", "BB") and rw == "12" and prv:
                    return f"'{prv}'!{d1}{cl}{d2}{rw}"
                if int(rw) not in KROWS:
                    return m.group(0)
                later = s in order and order[s] > i
                tgt = s if later else (nxt or t)
                return f"'{tgt}'!{d1}{cl}{d2}{kr.get(tgt, rw)}"
            nv = REF.sub(rep, v)
            if nv != v:
                ups.append({"range": rowcol_to_a1(r, cix), "values": [[nv]]})
    print(f"[{t}] 参照の付け替え {len(ups)}セル（翌週={nxt or '自タブ'}）")
    if ups:
        retry(lambda: ws.batch_update(ups, value_input_option="USER_ENTERED"))
    time.sleep(3)

# ---- 6) #REF! の焼き付きを解消 ----
for t in weeks_all:
    ws = sh.worksheet(t)
    vals = retry(lambda: ws.get("A1:BE130"))
    f = retry(lambda: ws.get("A1:BE130", value_render_option="FORMULA"))
    bad = [{"range": rowcol_to_a1(r, cix), "values": [[f[r - 1][cix - 1]]]}
           for r, row in enumerate(vals, start=1) for cix, v in enumerate(row, start=1)
           if str(v).startswith("#REF") and r - 1 < len(f) and cix - 1 < len(f[r - 1])]
    if bad:
        retry(lambda: ws.batch_update(bad, value_input_option="USER_ENTERED"))
    print(f"[{t}] #REF! {len(bad)}セル{'→入れ直し' if bad else ''}")
    time.sleep(3)
print("完了（このあと egg_normalize.py を各週に流すこと）")
