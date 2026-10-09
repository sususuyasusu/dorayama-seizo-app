#!/usr/bin/env python3
"""卵発注の「計算式」を1本ずつ検査する（読み取り専用）。

使い方: python3 tools/egg_formula_audit.py

見るところ（今週〜3週先）
 1) 翌週正味発注数(AP16:AU18) … 便ごとに参照先が合っているか
      火便=翌週の水木 / 木便=翌週の金土 / 土便=翌週の日＋翌々週の月火
      繰り越し控除 = その便が届くまでに翌週で使う曜日の合計
 2) 回転→kg→袋の換算 … 卵黄0.4kg・卵白0.75kg/回転、1袋5kg
 3) 必要在庫(AS6:AT12) … 曜日ごとに「次の便までの製造」を見ているか
 4) 見通し(BA/BB)・発注チェック(AY/AZ) … 予定ではなく実績(V〜AB)基準か、起点が当日か
 5) 材料の原単位 … 卵黄400g/卵白750g・1回転の基準個数60
 6) 届く(AU/AV) … 実発注(配送便別合算W/Y)を見ているか
 7) シートの袋数とアプリの袋数の食い違い（丸めルールの違い）
"""
import datetime
import json
import re
import time
import urllib.request

import gspread
from google.oauth2.service_account import Credentials

BASE = "https://dorayama-seizo-app-1.onrender.com"
SHEET_ID = "1PRDhGP_4xiO_ZjJP3NB9Id3PmaPa5W7hNyrqFQ5EyqM"
CRED = ("/Users/suzuki3/Library/CloudStorage/Dropbox-Detale/D& W/どら山/過去/"
        "dw_budget_profit_sheets_automation/config/google_credentials.json")
JST = datetime.timezone(datetime.timedelta(hours=9))
COLS = ["V", "W", "X", "Y", "Z", "AA", "AB"]          # 実績側 月〜日
WD = "月火水木金土日"
ng = []


def retry(fn):
    for i in range(6):
        try:
            return fn()
        except Exception as e:
            if "429" in str(e) and i < 5:
                time.sleep(65)
                continue
            raise


def refs(formula):
    """数式が参照している「'週'!列行」を集合で返す。"""
    return {(m[0], m[1], m[2]) for m in re.findall(r"'(\d{3,4})'!\$?([A-Z]{1,3})\$?(\d+)", str(formula))}


today = datetime.datetime.now(JST).date()
monday = today - datetime.timedelta(days=today.weekday())
tabs = ["%02d%02d" % ((monday + datetime.timedelta(days=7 * k)).month,
                      (monday + datetime.timedelta(days=7 * k)).day) for k in range(4)]
sh = gspread.authorize(Credentials.from_service_account_file(
    CRED, scopes=["https://www.googleapis.com/auth/spreadsheets"])).open_by_key(SHEET_ID)
names = [w.title for w in retry(lambda: sh.worksheets())]
kr = {}
for t in set(tabs) | {t for t in names if re.fullmatch(r"\d{4}", t)}:
    pass


def kaiten_row(tab):
    if tab in kr:
        return kr[tab]
    col = retry(lambda: sh.worksheet(tab).get("A1:A70"))
    for i, row in enumerate(col):
        if row and str(row[0]).strip().startswith("回転数（切上げ）"):
            kr[tab] = i + 1
            return kr[tab]
    kr[tab] = None
    return None


print(f"今日 {today}（今週タブ {tabs[0]}）")
for t in tabs:
    if t not in names:
        print(f"\n===== {t}: タブなし =====")
        continue
    nxt = None
    nxt2 = None
    d = monday + datetime.timedelta(days=7 * tabs.index(t))
    for k, store in ((7, "nxt"), (14, "nxt2")):
        nm = "%02d%02d" % ((d + datetime.timedelta(days=k)).month, (d + datetime.timedelta(days=k)).day)
        if nm in names:
            if store == "nxt":
                nxt = nm
            else:
                nxt2 = nm
    ws = sh.worksheet(t)
    f = retry(lambda: ws.get("A1:BE130", value_render_option="FORMULA"))
    v = retry(lambda: ws.get("A1:BE130"))

    def F(r, c):
        row = f[r - 1] if r - 1 < len(f) else []
        return str(row[c] if c < len(row) else "")

    def V(r, c):
        row = v[r - 1] if r - 1 < len(v) else []
        return str(row[c] if c < len(row) else "").strip()

    def num(s):
        try:
            return float(str(s).replace(",", ""))
        except Exception:
            return 0.0

    kr_n = kaiten_row(nxt) if nxt else None
    kr_n2 = kaiten_row(nxt2) if nxt2 else None
    print(f"\n===== {t}（翌週 {nxt or 'なし'} / 翌々週 {nxt2 or 'なし'}）=====")

    # 1) 便ごとの参照先
    want = {
        16: (("火曜便", "翌週の水木"), {(nxt, "X", kr_n), (nxt, "Y", kr_n)}, {(nxt, "V", kr_n), (nxt, "W", kr_n)}),
        17: (("木曜便", "翌週の金土"), {(nxt, "Z", kr_n), (nxt, "AA", kr_n)},
             {(nxt, c, kr_n) for c in ("V", "W", "X", "Y")}),
        18: (("土曜便", "翌週の日＋翌々週の月火"),
             {(nxt, "AB", kr_n)} | ({(nxt2, "V", kr_n2), (nxt2, "W", kr_n2)} if nxt2 else set()),
             {(nxt, c, kr_n) for c in ("V", "W", "X", "Y", "Z", "AA")}),
    }
    for r, ((label, desc), cover, carry) in want.items():
        if not nxt:
            print(f"  {label}: 翌週タブが無いので判定不可")
            continue
        got = {(s, c, int(rw)) for s, c, rw in refs(F(r, 41))}
        exp = {(s, c, int(rw)) for s, c, rw in (cover | carry) if s}
        miss = exp - got
        extra = got - exp
        mark = "OK" if not miss and not extra else "⚠️"
        if mark == "⚠️":
            ng.append(f"{t} {label}の参照")
        print(f"  {label}（{desc}）: {mark}" + ("" if mark == "OK" else f" 不足{sorted(miss)} 余分{sorted(extra)}"))
        if not nxt2 and r == 18:
            print("     ※翌々週タブが無いため日曜分のみの暫定（タブ作成後に自動で組み直し）")

    # 2) 換算（回転→kg→袋）
    conv_ng = []
    for r in (16, 17, 18):
        for c, label, expect in ((42, "卵黄kg", f"=ROUND($AP${r}*0.4,1)"), (43, "卵黄袋", f"=ROUND($AP${r}*0.4/5,0)"),
                                 (45, "卵白kg", f"=ROUND($AS${r}*0.75,1)"), (46, "卵白袋", f"=ROUND($AS${r}*0.75/5,0)")):
            if F(r, c).replace(" ", "") != expect:
                conv_ng.append(f"行{r}{label}: {F(r, c)[:40]}")
    print(f"  回転→kg→袋の換算（卵黄0.4kg・卵白0.75kg/回転、5kg/袋）: {'OK' if not conv_ng else '⚠️ ' + ' / '.join(conv_ng)}")
    if conv_ng:
        ng.append(f"{t} 換算式")

    # 3) 必要在庫（曜日ごとに次の便までの製造を見ているか）
    need = F(6, 44)
    ok_need = need.startswith("=CHOOSE(WEEKDAY(") and "$W$" in need and (not nxt or nxt in need)
    print(f"  必要在庫(AS6)の作り: {'OK' if ok_need else '⚠️ ' + need[:70]}")
    if not ok_need:
        ng.append(f"{t} 必要在庫")

    # 4) 見通し・発注チェックが実績基準か／起点が当日か
    plan_cols = re.compile(r"\$[B-H]\$?\d")
    bad_plan = []
    for r in range(6, 13):
        for c, lab in ((52, "見通し卵黄"), (53, "見通し卵白"), (50, "発注チェック卵黄"), (51, "発注チェック卵白")):
            fx = F(r, c)
            if fx.startswith("=") and plan_cols.search(fx.replace("$BA$", "").replace("$BB$", "")):
                bad_plan.append(f"{lab}行{r}")
    today_ok = "<=TODAY()" in F(6, 52) and "<=TODAY()" in F(6, 53)
    print(f"  見通し・発注チェックが実績(V〜AB)基準: {'OK' if not bad_plan else '⚠️ ' + ','.join(bad_plan[:4])}"
          f" / 起点が当日(<=TODAY): {'OK' if today_ok else '⚠️'}")
    if bad_plan or not today_ok:
        ng.append(f"{t} 見通し")

    # 5) 材料の原単位
    rows = {}
    for r in range(1, 70):
        a = V(r, 0)
        if a in ("卵黄", "卵白") and V(r, 1) == "g":
            rows[a] = r
        elif a.startswith("1回転の基準個数"):
            rows["std"] = r
    base = num(V(rows["std"], 1)) if "std" in rows else 0
    y = num(V(rows.get("卵黄", 0), 2)) if "卵黄" in rows else 0
    w = num(V(rows.get("卵白", 0), 2)) if "卵白" in rows else 0
    ok_unit = (base == 60 and y == 400 and w == 750)
    print(f"  原単位: 1回転={base:g}個 / 卵黄{y:g}g / 卵白{w:g}g {'OK' if ok_unit else '⚠️'}")
    if not ok_unit:
        ng.append(f"{t} 原単位")

    # 6) 届く＝実発注(W/Y)を見ているか
    bins = [r for r in range(55, 100) if V(r, 0).startswith(("火曜便", "木曜便", "土曜便"))]
    arrive = [F(r, 46) for r in range(6, 13)]           # AU列(届く卵黄)
    ok_arr = all((not a.startswith("=")) or any(f"${chr(87)}${b}" in a or f"W{b}" in a for b in bins) for a in arrive if a)
    print(f"  「届く」が実発注(配送便別合算)参照: {'OK' if ok_arr else '⚠️ 要確認'}（合算の行 {bins}）")

    # 7) 手計算で発注数を出し直してシート・アプリと比べる
    if nxt and kr_n:
        nv = retry(lambda: sh.worksheet(nxt).get(f"{COLS[0]}{kr_n}:{COLS[6]}{kr_n}"))
        nrot = [num(x) for x in (nv[0] if nv else [])] + [0] * 7
        n2rot = [0] * 7
        if nxt2 and kr_n2:
            n2v = retry(lambda: sh.worksheet(nxt2).get(f"{COLS[0]}{kr_n2}:{COLS[6]}{kr_n2}"))
            n2rot = [num(x) for x in (n2v[0] if n2v else [])] + [0] * 7
        ba, bb = num(V(12, 52)), num(V(12, 53))
        plan = {16: (nrot[2] + nrot[3], nrot[0] + nrot[1]),
                17: (nrot[4] + nrot[5], sum(nrot[0:4])),
                18: (nrot[6] + n2rot[0] + n2rot[1], sum(nrot[0:6]))}
        print("  手計算との照合（回転）:")
        for r, (cover, carry) in plan.items():
            my_y = round(max(0, cover - max(0, ba - carry)))
            my_w = round(max(0, cover - max(0, bb - carry)))
            sy, sw = num(V(r, 41)), num(V(r, 44))
            mark = "OK" if (abs(my_y - sy) < 1.01 and abs(my_w - sw) < 1.01) else "⚠️"
            if mark == "⚠️":
                ng.append(f"{t} 行{r}の値")
            print(f"    {V(r, 40)[:3]}: 手計算 黄{my_y}/白{my_w}  シート 黄{sy:g}/白{sw:g}  {mark}"
                  f"（対象{cover:g}回転・繰越控除前{carry:g}回転・日曜繰越 黄{ba:g}/白{bb:g}）")

    # 8) シートの袋数 vs アプリの袋数
    try:
        E = json.loads(urllib.request.urlopen(f"{BASE}/api/eggs?tab={t}", timeout=60).read())
        for i, r in enumerate((16, 17, 18)):
            b = E.get("batches", [])[i] if i < len(E.get("batches", [])) else None
            if not b:
                continue
            sy, sw = int(num(V(r, 43))), int(num(V(r, 46)))
            if (sy, sw) != (b["yolkBags"], b["whiteBags"]):
                print(f"  袋数の違い {b['name'][:3]}: シート 黄{sy}/白{sw} ・ アプリ 黄{b['yolkBags']}/白{b['whiteBags']}"
                      f"（シート=四捨五入、アプリ=卵黄7回転・卵白4回転の切上げルール）")
    except Exception as e:
        print("  アプリとの比較: 取得失敗", str(e)[:50])
    time.sleep(4)

print("\n総合:", "計算式に問題なし" if not ng else "要確認 → " + " / ".join(ng))
