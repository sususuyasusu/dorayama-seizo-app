#!/usr/bin/env python3
"""製造表の週タブを作る（既定は下見。RUN=1 で実行）。年またぎ対応版（2026-11-25）。

例（来年5月末まで）:
  python3 tools/build_week_tabs.py --start 2026-12-21 --end 2027-05-31 --source 1214 \
      --fix-from 1123 --event "上野=2026-10-01:2027-03-31" \
      --place "伊勢丹新宿=2027-01-20:2027-01-26"
  RUN=1 を付けると実行。

やること
 1) --source のタブを複製して新しい週タブ(MMDD)を作り、時系列の位置に置く
    （--adopt "古い名前=YYYY-MM-DD" は、手作業で複製済みのタブを名前と日付だけ直して使う）
 2) 週の月曜日(B4)と卵ナビの日付(AO6)、実績側の日付(V4)を設定
 3) 在庫の手入力(AQ/AR)と実発注の記録(配送便別合算 W/Y)を空にする
 4) --event の販売期間から「製造日＝販売日の前日」で、期間外の日を0にする
    --place の催事は、空いている2番目の枠に会場名と仮の数量を入れる
 5) --fix-from 以降の週で、週をまたぐ参照を正しい週・正しい行に付け替える
 6) #REF! が残ったセルは同じ式を入れ直して再計算させる
 7) 卵ナビの整形(egg_normalize)を --fix-from 以降の各週に流す

年の扱い: タブ名(MMDD)には年が無い。日付は必ず年つきで指定し、既存タブの年は
ブックの並び順から決める(week_tabs.assign_dates)。同じ名前で別の年のタブがあれば止まる。

⚠️ 催事が3つ以上重なる週（4ブロック）はこのツールでは作れない。
   その場合は A〜AM列だけ部分的に行を挿入する手順が要る（Vault dorayama-seizou-calendar-sync.md）。
"""
import argparse
import datetime
import os
import re
import sys
import time

import gspread
from gspread.utils import rowcol_to_a1
from google.oauth2.service_account import Credentials

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import week_tabs  # noqa: E402

SHEET_ID = "1PRDhGP_4xiO_ZjJP3NB9Id3PmaPa5W7hNyrqFQ5EyqM"
CRED = ("/Users/suzuki3/Library/CloudStorage/Dropbox-Detale/D& W/どら山/過去/"
        "dw_budget_profit_sheets_automation/config/google_credentials.json")
REF = re.compile(r"'(\d{3,4})'!(\$?)([A-Z]{1,3})(\$?)(\d+)")
KROWS = {39, 41, 50, 52}          # 回転数（切上げ）が入りうる行
RUN = os.environ.get("RUN") == "1"
EPOCH = datetime.date(1899, 12, 30)

# --place で入れる仮の数量（百貨店催事の標準＝川越東武・日本橋高島屋と同じ。木金に作る日は多め）
NAMES = ["黒どら", "あんバター", "白どら", "旬どら", "抹茶", "生", "皮4枚セット"]
BASE = {"黒どら": 200, "あんバター": 200, "白どら": 60, "旬どら": 60, "抹茶": "", "生": 60, "皮4枚セット": 15}
PEAK = {"黒どら": 230, "あんバター": 230, "白どら": 80, "旬どら": 80, "抹茶": "", "生": 80, "皮4枚セット": 15}

ap = argparse.ArgumentParser()
ap.add_argument("--start", required=True, help="最初に作る週の月曜日 YYYY-MM-DD")
ap.add_argument("--end", required=True, help="最後に作る週の月曜日 YYYY-MM-DD")
ap.add_argument("--source", required=True, help="複製元の週タブ(MMDD)")
ap.add_argument("--fix-from", default=None, help="参照の付け替えと整形を始める週タブ（既定は複製元）")
ap.add_argument("--adopt", default="", help="手作りタブを使う '古い名前=YYYY-MM-DD' をカンマ区切り")
ap.add_argument("--event", action="append", default=[],
                help="既存の枠の販売期間 '名前=YYYY-MM-DD:YYYY-MM-DD'（製造は前日・期間外は0）。複数可")
ap.add_argument("--place", action="append", default=[],
                help="空き枠に入れる催事 '名前=YYYY-MM-DD:YYYY-MM-DD'（仮の数量つき）。複数可")
ap.add_argument("--skip-normalize", action="store_true")
args = ap.parse_args()


def span(text):
    name, sp = text.split("=", 1)
    s, t = sp.split(":", 1)
    return name, (datetime.date.fromisoformat(s), datetime.date.fromisoformat(t))


events = dict(span(e) for e in args.event)
places = dict(span(e) for e in args.place)
adopt = {}
for a in [x for x in args.adopt.split(",") if x.strip()]:
    old, d = a.split("=", 1)
    adopt[datetime.date.fromisoformat(d.strip())] = old.strip()
d_start, d_end = datetime.date.fromisoformat(args.start), datetime.date.fromisoformat(args.end)
assert d_start.weekday() == 0 and d_end.weekday() == 0, "--start/--end は月曜日で指定する"
mondays = []
d = d_start
while d <= d_end:
    mondays.append(d)
    d += datetime.timedelta(days=7)
fix_from = args.fix_from or args.source

gc = gspread.authorize(Credentials.from_service_account_file(
    CRED, scopes=["https://www.googleapis.com/auth/spreadsheets"]))
sh = gc.open_by_key(SHEET_ID)


def retry(fn):
    for i in range(7):
        try:
            return fn()
        except Exception as ex:
            if ("429" in str(ex) or "503" in str(ex) or "500" in str(ex)) and i < 6:
                time.sleep(65)
                continue
            raise


def prod_days(d0, s, t):
    """その週(月曜d0)のうち、製造日(販売日の前日)にあたる曜日の番号。"""
    return [i for i in range(7)
            if s - datetime.timedelta(days=1) <= d0 + datetime.timedelta(days=i) <= t - datetime.timedelta(days=1)]


def label_row(grid, label):
    for i, row in enumerate(grid):
        if row and str(row[0]).strip().startswith(label):
            return i + 1
    return None


today = datetime.date.today()
sheets = retry(lambda: sh.worksheets())
titles = [w.title for w in sheets]
by_title = {w.title: w for w in sheets}
dates = week_tabs.assign_dates(titles, today)
assert args.source in titles, f"複製元 {args.source} が無い"
src = by_title[args.source]
src_date = dates[args.source]
assert d_start > src_date, "作る週は複製元より後にする"
print(f"複製元 {args.source}({src_date}) / 作る週 {len(mondays)}枚 {mondays[0]}〜{mondays[-1]} / 付け替え開始 {fix_from}")

# ---- 1〜4) 作成と初期設定 ----
prev_title = args.source
for d0 in mondays:
    tab = week_tabs.name_for(d0)
    made = False
    if tab in titles:
        have = dates.get(tab)
        if have != d0:
            raise SystemExit(f"[{tab}] 同じ名前で別の年のタブがある（{have}）→ 中止。名前の付け方を決め直す必要あり")
        print(f"[{tab}] {d0} 既にあり→作成スキップ")
        prev_title = tab
        continue
    idx = titles.index(prev_title) + 1
    if d0 in adopt:
        old = adopt[d0]
        assert old in titles, f"--adopt の {old} が無い"
        print(f"[{tab}] {d0}（手作りの {old} を改名して使う）")
        if RUN:
            ws = by_title[old]
            retry(lambda: ws.update_title(tab))
            pos = titles.index(old)
            titles[pos] = tab
            if pos != idx:
                # 並びがずれていたら直す
                titles.pop(pos)
                idx = titles.index(prev_title) + 1
                titles.insert(idx, tab)
                retry(lambda: sh.batch_update({"requests": [{"updateSheetProperties": {
                    "properties": {"sheetId": ws.id, "index": idx}, "fields": "index"}}]}))
            made = True
        else:
            titles[titles.index(old)] = tab
    else:
        print(f"[{tab}] {d0}（{prev_title}の直後に作成）")
        if RUN:
            ws = retry(lambda: sh.duplicate_sheet(src.id, insert_sheet_index=idx, new_sheet_name=tab))
            made = True
        titles.insert(idx, tab)
    dates[tab] = d0
    prev_title = tab
    if not made:
        # 下見: 催事の入り方だけ表示
        for name, (s, t) in list(events.items()) + list(places.items()):
            pd = prod_days(d0, s, t)
            if name in places and pd:
                print(f"     ・{name}: 製造 {[ (d0 + datetime.timedelta(days=i)).strftime('%m/%d') for i in pd ]}")
            if name in events and len(pd) < 7:
                print(f"     ・{name}: 製造日 {len(pd)}日（残り{7 - len(pd)}日は0）")
        continue
    by_title[tab] = ws
    serial = (d0 - EPOCH).days
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

    def c(r, k):
        row = grid[r - 1] if r - 1 < len(grid) else []
        return (str(row[k]) if k < len(row) else "").strip()
    heads = []
    for r in range(1, 60):
        if c(r, 0).startswith("【日別】"):
            break
        if c(r, 18) == "カテゴリー":
            heads.append(r)
    # 既存の枠: 期間外を0に
    for h in heads:
        cur = c(h, 0)
        if cur not in events:
            continue
        s, t = events[cur]
        pd = prod_days(d0, s, t)
        n = 0
        for r in range(h + 1, h + 10):
            if c(r, 18) == "カテゴリー" or c(r, 10) == "日別合計金額":
                break
            if c(r, 8) in ("はい", "いいえ") and c(r, 0):
                for i in range(7):
                    if i not in pd:
                        ups += [{"range": rowcol_to_a1(r, 2 + i), "values": [[0]]},
                                {"range": rowcol_to_a1(r, 22 + i), "values": [[0]]}]
                        n += 1
        if n:
            print(f"     ・{cur}: 期間外 {n}マスを0に（製造日 {len(pd)}日）")
    # 空き枠: 催事を入れる
    todo = [(name, prod_days(d0, s, t)) for name, (s, t) in places.items() if prod_days(d0, s, t)]
    free = [h for h in heads if not c(h, 0)]
    if todo:
        if len(todo) > len(free):
            raise SystemExit(f"[{tab}] 催事 {[n for n, _ in todo]} を入れる空き枠が足りない（4ブロック週は別手順）")
        for (name, pd), h in zip(todo, free):
            ups += [{"range": f"A{h}", "values": [[name]]}, {"range": f"U{h}", "values": [[name]]}]
            for k, prod in enumerate(NAMES):
                r = h + 1 + k
                assert c(r, 8) in ("はい", "いいえ"), f"[{tab}] 空き枠の行{r}が商品行でない"
                ups += [{"range": f"A{r}", "values": [[prod]]}, {"range": f"U{r}", "values": [[prod]]}]
                if prod == "抹茶":
                    ups += [{"range": f"J{r}", "values": [[450]]}, {"range": f"AD{r}", "values": [[450]]}]
                for i in range(7):
                    wd = (d0 + datetime.timedelta(days=i)).weekday()
                    v = (PEAK if wd in (3, 4) else BASE)[prod] if i in pd else (0 if prod != "抹茶" else "")
                    ups += [{"range": rowcol_to_a1(r, 2 + i), "values": [[v]]},
                            {"range": rowcol_to_a1(r, 22 + i), "values": [[v]]}]
            print(f"     ・{name}: 行{h}の空き枠に入れた（製造 {len(pd)}日・数量は仮）")
    retry(lambda: ws.batch_update(ups, value_input_option="USER_ENTERED"))
    time.sleep(2.5)

if not RUN:
    print("（下見のみ・書き込みなし。参照の付け替えは既存タブ分だけ下に下見を出す）")

# ---- 5) 週をまたぐ参照の付け替え ----
if RUN:
    sheets = retry(lambda: sh.worksheets())
    titles = [w.title for w in sheets]
    by_title = {w.title: w for w in sheets}
    dates = week_tabs.assign_dates(titles, today)
assert fix_from in titles, f"--fix-from {fix_from} が無い"
weeks_all = [t for t in titles[titles.index(fix_from):] if week_tabs.is_week_tab(t) and (RUN or t in by_title)]
# 並び順と日付が7日刻みでそろっているか（そろっていなければ止める）
seq = [dates.get(t) for t in weeks_all]
gaps = [(weeks_all[i], seq[i], weeks_all[i + 1], seq[i + 1]) for i in range(len(seq) - 1)
        if not (seq[i] and seq[i + 1] and (seq[i + 1] - seq[i]).days == 7)]
if RUN and gaps:
    raise SystemExit(f"週の並びが7日刻みでない: {gaps}")
kr = {}
for t in weeks_all:
    kr[t] = label_row(retry(lambda: by_title[t].get("A1:A70")), "回転数（切上げ）") or 41
    time.sleep(1.1)
order = {t: i for i, t in enumerate(weeks_all)}
print("回転数（切上げ）の行:", sorted(set(kr.values())), "／対象", len(weeks_all), "週")

for t in weeks_all:
    ws = by_title[t]
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
                ups.append({"range": rowcol_to_a1(r, cix), "values": [[nv]], "_old": v})
    print(f"[{t}] 参照の付け替え {len(ups)}セル（翌週={nxt or '自タブ'}・前週={prv or '—'}）")
    if not RUN and ups:
        for u in ups[:3]:
            print(f"      例 {u['range']}: {u['_old'][:70]} → {u['values'][0][0][:70]}")
    if RUN and ups:
        body = [{"range": u["range"], "values": u["values"]} for u in ups]
        retry(lambda: ws.batch_update(body, value_input_option="USER_ENTERED"))
    time.sleep(2.5)

if not RUN:
    raise SystemExit

# ---- 6) #REF! の焼き付きを解消 ----
for t in weeks_all:
    ws = by_title[t]
    vals = retry(lambda: ws.get("A1:BE130"))
    f = retry(lambda: ws.get("A1:BE130", value_render_option="FORMULA"))
    bad = [{"range": rowcol_to_a1(r, cix), "values": [[f[r - 1][cix - 1]]]}
           for r, row in enumerate(vals, start=1) for cix, v in enumerate(row, start=1)
           if str(v).startswith("#REF") and r - 1 < len(f) and cix - 1 < len(f[r - 1])]
    if bad:
        retry(lambda: ws.batch_update(bad, value_input_option="USER_ENTERED"))
    print(f"[{t}] #REF! {len(bad)}セル{'→入れ直し' if bad else ''}")
    time.sleep(2.5)

# ---- 7) 卵ナビの整形 ----
if args.skip_normalize:
    print("完了（egg_normalize は未実行）")
    raise SystemExit
os.environ["DORAYAMA_DISABLE_BACKGROUND"] = "1"
if not os.environ.get("DORAYAMA_SA_CRED_JSON"):
    os.environ["DORAYAMA_SA_CRED_JSON"] = open(CRED).read()
import egg_normalize  # noqa: E402
egg_normalize.set_tab_order(titles)
tabset = set(titles)
for t in weeks_all:
    try:
        print(retry(lambda: egg_normalize.normalize(t, by_title[t], tabset)))
    except Exception as ex:
        print(f"[{t}] 整形でエラー: {type(ex).__name__}: {str(ex)[:160]}")
    time.sleep(14)
print("完了")
