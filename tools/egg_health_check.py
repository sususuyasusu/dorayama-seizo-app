#!/usr/bin/env python3
"""卵発注まわりの健康診断（読み取り専用・書き込みなし）。

使い方: python3 tools/egg_health_check.py

アプリ側（本番API）
  - 今週・来週の卵ナビ: 7日そろっているか / 日付が連番か / エラー表記が無いか / 翌週分の発注数
  - 毎日の自己修復と在庫同期の最終実行
シート側（今週〜4週先。シートの数式に頼らず生データから計算し直して照合）
  - どら焼き合計・回転数（切上げ）が一致するか
  - 実績側の日付(V4)が予定側(B4)と一致するか / 卵ナビの日付が連番か
  - 翌々週以降に「幽霊の発注記録」が残っていないか / 今週・来週の発注記録（LINE照合用）
  - 土便の式に +0+0（翌々週タブ待ちの暫定）が残っていないか / #REF! 等のエラー
  - 週タブがどこまで作られているか（翌々週タブが無いと土便は日曜分のみの暫定になる）
"""
import datetime
import json
import math
import re
import time
import urllib.request

import gspread
from google.oauth2.service_account import Credentials

BASE = "https://dorayama-seizo-app-1.onrender.com"
SHEET_ID = "1PRDhGP_4xiO_ZjJP3NB9Id3PmaPa5W7hNyrqFQ5EyqM"
CRED = ("/Users/suzuki3/Library/CloudStorage/Dropbox-Detale/D& W/どら山/過去/"
        "dw_budget_profit_sheets_automation/config/google_credentials.json")
BAD = ("#REF", "#N/A", "#VALUE", "#DIV", "#ERROR", "#NAME")
JST = datetime.timezone(datetime.timedelta(hours=9))
issues = []


def api(path):
    return json.loads(urllib.request.urlopen(BASE + path, timeout=60).read())


def retry(fn):
    for i in range(6):
        try:
            return fn()
        except Exception as e:
            if "429" in str(e) and i < 5:
                time.sleep(65)
                continue
            raise


today = datetime.datetime.now(JST).date()
monday = today - datetime.timedelta(days=today.weekday())
tabs = ["%02d%02d" % ((monday + datetime.timedelta(days=7 * k)).month,
                      (monday + datetime.timedelta(days=7 * k)).day) for k in range(5)]
print(f"今日 {today}（今週タブ {tabs[0]}）")

# ---------------- アプリ側 ----------------
print("\n■ アプリ（本番）")
for k in (0, 1):
    E = api(f"/api/eggs?tab={tabs[k]}")
    days = E.get("days", [])
    try:
        ds = [datetime.datetime.strptime(d["date"], "%Y/%m/%d").date() for d in days]
        seq = len(ds) == 7 and all((ds[i] - ds[i - 1]).days == 1 for i in range(1, 7))
    except Exception:
        seq = False
    blob = json.dumps(E, ensure_ascii=False)
    bad = [b for b in BAD + ("undefined", "NaN") if b in blob]
    print(f"  [{tabs[k]}] 日数{len(days)} 日付連番{'OK' if seq else 'NG'} エラー表記{bad or 'なし'}"
          f" 作る数{[d.get('prod') for d in days]}")
    for d in days:
        y, w = d["yolk"], d["white"]
        if y.get("stock") or w.get("stock") or y.get("incoming") or w.get("incoming"):
            print(f"     {d['wd']} {d['date'][5:]}: 在庫 黄{y.get('stock') or '-'}/白{w.get('stock') or '-'}"
                  f" 届く 黄{y.get('incoming') or '-'}/白{w.get('incoming') or '-'}")
    print("     翌週分の発注推奨: " + " | ".join(
        f"{b['name'][:3]} 黄{b['yolkBags']}袋 白{b['whiteBags']}袋" for b in E.get("batches", [])))
    if len(days) != 7 or not seq or bad:
        issues.append(f"アプリ{tabs[k]}")
for path, label in (("/api/eggheal", "自己修復"), ("/api/eggsync", "在庫同期")):
    try:
        h = api(path)
        res = str(h.get("lastResult") or "")
        err = "error" in res.lower()
        print(f"  {label}: 最終 {h.get('lastRun')} {'⚠️エラーあり' if err else 'エラーなし'}")
        if err:
            issues.append(label)
            print("     " + res[:300])
    except Exception as e:
        print(f"  {label}: 取得失敗 {str(e)[:60]}")
        issues.append(label)

# ---------------- シート側 ----------------
print("\n■ シート（自力計算で照合）")
sh = gspread.authorize(Credentials.from_service_account_file(
    CRED, scopes=["https://www.googleapis.com/auth/spreadsheets"])).open_by_key(SHEET_ID)
names = [w.title for w in retry(lambda: sh.worksheets())]
weeks = [t for t in names if re.fullmatch(r"\d{4}", t)]
last = weeks[-1] if weeks else "?"
print(f"  週タブの最終: {last}")

for k, t in enumerate(tabs):
    if t not in names:
        print(f"  [{t}] タブなし")
        if k <= 2:
            issues.append(f"{t}タブなし")
        continue
    ws = sh.worksheet(t)
    g = retry(lambda: ws.get("A1:BE130"))
    f = retry(lambda: ws.get("A1:BE130", value_render_option="FORMULA"))
    u = retry(lambda: ws.get("B4:V4", value_render_option="UNFORMATTED_VALUE"))

    def c(r, j, grid=g):
        row = grid[r - 1] if r - 1 < len(grid) else []
        return (str(row[j]) if j < len(row) else "").strip()

    def num(s):
        try:
            return float(str(s).replace(",", ""))
        except Exception:
            return 0.0

    rows, blocks = {}, []
    for r in range(1, 70):
        a = c(r, 0)
        if c(r, 18) == "カテゴリー" and not rows:
            blocks.append(a or "(空き)")
        if a.startswith("どら焼き合計（個）［日別］"):
            rows["sum"] = r
        elif a.startswith("回転数（切上げ）"):
            rows["ceil"] = r
        elif a.startswith("1回転の基準個数"):
            rows["std"] = r
    std = num(c(rows["std"], 1)) or 60
    mine = [0.0] * 7
    for r in range(1, rows["sum"]):
        if c(r, 8) == "はい":
            for d in range(7):
                mine[d] += num(c(r, 21 + d))
        if c(r, 0) in ("皮だけ（パック）", "皮4枚セット"):
            for d in range(7):
                mine[d] += 2 * num(c(r, 21 + d))
    ok_sum = all(abs(mine[d] - num(c(rows["sum"], 21 + d))) < 0.01 for d in range(7))
    ceil = [int(num(c(rows["ceil"], 21 + d))) for d in range(7)]
    ok_ceil = [math.ceil(v / std) for v in mine] == ceil
    ok_v4 = bool(u and len(u[0]) > 20 and u[0][0] == u[0][20])
    nav = [c(r, 40) for r in range(6, 13)]
    try:
        nd = [datetime.datetime.strptime(x, "%Y/%m/%d").date() for x in nav]
        ok_nav = all((nd[i] - nd[i - 1]).days == 1 for i in range(1, 7))
    except Exception:
        ok_nav = False
    bins = {c(r, 0)[:3]: (num(c(r, 22)) / 5000, num(c(r, 24)) / 5000)
            for r in range(60, 100) if c(r, 0).startswith(("火曜便", "木曜便", "土曜便"))}
    ghost = k >= 2 and any(y or w for y, w in bins.values())
    # 土便は「翌週の日曜＋翌々週の月火」＝後ろの週を2つ参照するのが正常。1つ以下なら暫定。
    ap18 = c(18, 41, f)
    later = {s for s in re.findall(r"'(\d{4})'!", ap18)
             if s in weeks and weeks.index(s) > weeks.index(t)}
    zero = len(later) < 2
    errs = sorted({v for row in g for v in row if str(v).startswith(BAD)})

    flags = [lab for ok, lab in ((ok_sum, "合計"), (ok_ceil, "回転数"), (ok_v4, "実績日付"),
                                 (ok_nav, "ナビ日付"), (not ghost, "幽霊記録"), (not errs, "エラー"))
             if not ok]
    note = "（翌々週タブ待ちの暫定）" if zero and t in weeks[-2:] else ""
    if zero and not note:
        flags.append("土便+0+0")
    issues += [f"{t}:{x}" for x in flags]
    rec = " ".join(f"{n}{y:g}/{w:g}" for n, (y, w) in bins.items())
    print(f"  [{t}] {'OK' if not flags else '⚠️' + '・'.join(flags)}  回転数{ceil}"
          f"  枠{blocks}  土便式{'暫定' + note if zero else 'OK'}")
    if k <= 1:
        print(f"         発注記録(袋 黄/白): {rec}")
    time.sleep(5)

print("\n総合:", "異常なし" if not issues else "要確認 → " + " / ".join(issues))
