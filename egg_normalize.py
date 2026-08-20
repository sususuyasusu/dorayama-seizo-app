#!/usr/bin/env python3
"""卵発注ナビ 正規化フィクサー（恒久・再実行可能）。

製造表の発注ナビを「再生成」すると、以下2つの調整が旧仕様に戻ってしまう。
このスクリプトを実行すると、当週＋未来週の全タブに両方を再適用する（冪等）。

  A) 見通し(BA/BB)の起点を実績ベースに: $AO$r<TODAY() → $AO$r<=TODAY()
     （当日の実在庫を使い、未来日の仮入力は無視）
  B) 必要在庫(AS/AT)を発注非依存の理想値に: 便日の「発注g÷400/750」流用を撤去し、
     全日「次便までの実績回転(row39 V-AB)の合計」に統一（黄白共通）。
  C) 見通し(BA/BB)・発注チェック(AY/AZ)の製造消費を「予定(標準値B〜H)」→「実績(作る数V〜AB)」に統一。

使い方:  python3 egg_normalize.py            # 当週＋未来週を自動検出
         python3 egg_normalize.py 0706 0713  # タブ指定
過去週には触れない（営業終了済みの履歴を変えない）。
"""
import sys, re, datetime
import data_layer

# C) 見通し(BA/BB)・発注チェック(AY/AZ) が製造消費に「予定(標準値 B〜H列)」を
#    引いていたのを「実績(作る数 V〜AB列)」に統一する。行番号は保持（週タブごとに集計行が
#    ズレるため。例: 通常39行、0727は50行）。$X$39形式のみ一致（$AB$39等は不一致）。
_PLAN2ACT = {"B": "V", "C": "W", "D": "X", "E": "Y", "F": "Z", "G": "AA", "H": "AB"}
_PLAN_PAT = re.compile(r"\$([BCDEFGH])\$(\d+)")
def _plan_to_act(f):
    if not isinstance(f, str):
        return f
    return _PLAN_PAT.sub(lambda m: "$" + _PLAN2ACT[m.group(1)] + "$" + m.group(2), f)


def _label_row(ws, label):
    """A列から指定ラベルの行(1始まり)を探す。集計行のズレ対応。見つからなければNone。"""
    colA = ws.get("A1:A60")
    for i, row in enumerate(colA):
        if row and str(row[0]).strip().startswith(label):
            return i + 1
    return None


def _parse(d):
    for fmt in ("%Y/%m/%d", "%Y-%m-%d"):
        try:
            return datetime.datetime.strptime(str(d).strip(), fmt).date()
        except ValueError:
            continue
    return None


def normalize(tab, ws, tabset):
    g = ws.get_all_values()
    def cell(r, c0): return g[r-1][c0] if r-1 < len(g) and c0 < len(g[r-1]) else ""
    as6 = ws.acell("AS6", value_render_option="FORMULA").value or ""
    ba6 = ws.acell("BA6", value_render_option="FORMULA").value or ""
    if "CHOOSE" not in as6 or "TODAY" not in ba6:
        return f"[{tab}] 発注ナビ無し→スキップ"

    # A) BA6:BB12 を <=TODAY に ＋ C) 製造消費を予定→実績に
    cur = ws.get("BA6:BB12", value_render_option="FORMULA")
    newA, nA, changed = [], 0, False
    for i in range(7):
        row = cur[i] if i < len(cur) else []
        out = []
        for j in range(2):
            c = row[j] if j < len(row) else ""
            nc = c
            if isinstance(c, str) and "<TODAY()" in c and "<=TODAY()" not in c:
                nc = nc.replace("<TODAY()", "<=TODAY()"); nA += 1
            nc = _plan_to_act(nc)
            if nc != c:
                changed = True
            out.append(nc)
        newA.append(out)
    if changed:
        ws.batch_update([{"range": "BA6:BB12", "values": newA}], value_input_option="USER_ENTERED")

    # C) AY6:AZ12 発注チェックの製造消費も予定→実績に
    ay = ws.get("AY6:AZ12", value_render_option="FORMULA")
    newC, cC = [], False
    for i in range(7):
        row = ay[i] if i < len(ay) else []
        out = []
        for j in range(2):
            c = row[j] if j < len(row) else ""
            nc = _plan_to_act(c)
            if nc != c:
                cC = True
            out.append(nc)
        newC.append(out)
    if cC:
        ws.batch_update([{"range": "AY6:AZ12", "values": newC}], value_input_option="USER_ENTERED")

    # C2) 翌週正味発注数(AO16:AU18) の製造消費・繰越控除も予定→実績に
    bt = ws.get("AO16:AU18", value_render_option="FORMULA")
    newD, cD = [], False
    for i in range(3):
        row = bt[i] if i < len(bt) else []
        out = []
        for j in range(7):
            c = row[j] if j < len(row) else ""
            nc = _plan_to_act(c)
            if nc != c:
                cD = True
            out.append(nc)
        newD.append(out)
    if cD:
        ws.batch_update([{"range": "AO16:AU18", "values": newD}], value_input_option="USER_ENTERED")

    # B) AS6:AT12 を製造ベースに（翌週タブが在る場合のみ）
    # 集計行はタブごとにズレる(通常39行、催事ブロック追加週は下にずれる)ためラベルで特定する。
    m = re.search(r"'(\d{4})'!", as6)
    nw = m.group(1) if m else None
    b = "skip(翌週なし)"
    if nw and nw in tabset:
        kr = _label_row(ws, "回転数（切上げ）") or 39
        nws = ws.spreadsheet.worksheet(nw)
        nkr = _label_row(nws, "回転数（切上げ）") or 39
        def f(r):
            return (f"=CHOOSE(WEEKDAY(AO{r},2),$W${kr},$X${kr}+$Y${kr},$Y${kr},"
                    f"$Z${kr}+$AA${kr},$AA${kr},$AB${kr}+'{nw}'!$V${nkr}+'{nw}'!$W${nkr},"
                    f"'{nw}'!$V${nkr}+'{nw}'!$W${nkr})")
        ws.batch_update([{"range": "AS6:AT12", "values": [[f(r), f(r)] for r in range(6, 13)]}],
                        value_input_option="USER_ENTERED")
        b = f"OK(翌週{nw}/集計行{kr}・翌週{nkr})"

    # D) 翌週正味発注数(AO14:AU18) を毎回正しい参照で組み直す（冪等・自己修復）
    #    過去事故: 土便の「翌々週の月・火」が 0 リテラルのまま残り、6週にわたり過少発注表示。
    #    原因は「翌々週タブが未作成の時点で生成し、その 0 が以後コピー継承された」こと。
    #    ここで毎回タブ名を日付から解決して書き直すので、後からタブが増えても自動で埋まる。
    d = _fix_batches(ws, tab, tabset)

    # E) まだ発注していない先の週に残る「幽霊の発注記録」を消す
    #    週タブを複製すると配送便別合算(W/Y)の手入力値がコピーされ、未発注の週にも
    #    発注済みの数字が居座る。それが在庫予測を膨らませ「翌週の発注は0でよい」と
    #    誤った推奨を生む（2026-08-13 と 08-20 に実害）。
    #    発注は木曜に翌週分をまとめて出すので、翌々週以降は必ず未発注 → 空にする。
    e = _clear_ghost_orders(ws, tab)
    return (f"[{tab}] A:{nA}/14 置換  B:{b}  C:見通し/発注チェックを実績化(冪等)  "
            f"D:{d}  E:{e}  F:{_fix_actual_dates(ws)}  G:{_fix_week_total(ws)}")


def _fix_actual_dates(ws):
    """実績側の日付起点(V4)が予定側(B4)とズレていたら直す。
    2026-08-20発覚: 0907〜0928の4週で実績側が「7月20日」のまま固定値で残り、
    予定側(9月)と一致していなかった。V4を =B4 にすれば以降の曜日は自動で連なる。"""
    try:
        b = ws.get("B4:B4", value_render_option="UNFORMATTED_VALUE")
        v = ws.get("V4:V4", value_render_option="UNFORMATTED_VALUE")
        bv = b[0][0] if b and b[0] else None
        vv = v[0][0] if v and v[0] else None
    except Exception:
        return "skip(読取不可)"
    if bv is None:
        return "skip(予定側なし)"
    if bv == vv:
        return "OK"
    ws.batch_update([{"range": "V4", "values": [["=B4"]]}], value_input_option="USER_ENTERED")
    return "実績側の日付を予定に合わせた"


def _fix_week_total(ws):
    """週間の合計回転数の実績側が、予定側の合計を参照していたら直す。
    2026-08-20発覚: 実績6,310個の週に予定10,110個由来の168.5回転が表示されていた。"""
    r_tot = _label_row(ws, "どら焼き合計（個）［週間］")
    r_std = _label_row(ws, "1回転の基準個数")
    r_real = _label_row(ws, "合計回転数（実数）")
    r_ceil = _label_row(ws, "合計回転数（切上げ）")
    if not all([r_tot, r_std, r_real, r_ceil]):
        return "skip(行なし)"
    try:
        cur = ws.acell(f"V{r_real}", value_render_option="FORMULA").value or ""
    except Exception:
        return "skip(読取不可)"
    # 分子が予定側の週間合計($B$40等)なら誤り。実績側($V$40)を見ていれば正しい。
    if f"$B${r_tot}" not in cur:
        return "OK"
    ws.batch_update([
        {"range": f"V{r_real}", "values": [[f"=IFERROR($V${r_tot}/$B${r_std},0)"]]},
        {"range": f"V{r_ceil}", "values": [[f"=IFERROR(CEILING($V${r_tot}/$B${r_std},1),0)"]]},
    ], value_input_option="USER_ENTERED")
    return "週間合計を実績基準に修正"


def _weeks_ahead(tab):
    """タブ(MMDD)が当週の何週先かを返す。当週=0, 翌週=1, ... 過去なら負。"""
    try:
        mm, dd = int(tab[:2]), int(tab[2:])
    except ValueError:
        return None
    today = datetime.date.today()
    cands = []
    for y in (today.year - 1, today.year, today.year + 1):
        try:
            cands.append(datetime.date(y, mm, dd))
        except ValueError:
            continue
    if not cands:
        return None
    tab_monday = min(cands, key=lambda d: abs((d - today).days))
    cur_monday = today - datetime.timedelta(days=today.weekday())
    return (tab_monday - cur_monday).days // 7


def _clear_ghost_orders(ws, tab):
    """翌々週以降のタブなら、配送便別合算の発注記録(W/Y)を空にする。
    当週・翌週は実際に発注済みの可能性があるため触らない。"""
    ahead = _weeks_ahead(tab)
    if ahead is None:
        return "skip(週判定不可)"
    if ahead < 2:
        return f"skip(当週から{ahead}週=発注済みの可能性)"
    rows = _bin_rows_by_label(ws)
    if not rows:
        return "skip(配送便行が見つからない)"
    cur = ws.get(f"W{rows[0]}:Y{rows[-1]}")
    has_value = False
    for i, r in enumerate(rows):
        row = cur[i] if i < len(cur) else []
        for ci in (0, 2):                     # W列(卵黄g) と Y列(卵白g)
            v = str(row[ci]).strip() if len(row) > ci else ""
            if v and v not in ("0",):
                has_value = True
    if not has_value:
        return "OK(既に空)"
    ws.batch_update([{"range": f"W{r}", "values": [[""]]} for r in rows]
                    + [{"range": f"Y{r}", "values": [[""]]} for r in rows],
                    value_input_option="USER_ENTERED")
    return f"幽霊記録を消去(行{rows})"


def _bin_rows_by_label(ws):
    """A列のラベルから火/木/土便の行を特定（催事増で行がズレる週に追従）。"""
    try:
        colA = ws.get("A60:A95")
    except Exception:
        return []
    rows = []
    for i, cell in enumerate(colA):
        text = (cell[0] if cell else "").strip()
        if text.startswith(("火曜便", "木曜便", "土曜便")):
            rows.append(60 + i)
    return sorted(rows)[:3]


def _tab_after(tab, days, tabset):
    """MMDDタブの days 日後のタブ名（存在するものだけ返す）。年またぎは近い方を採用。"""
    try:
        mm, dd = int(tab[:2]), int(tab[2:])
    except ValueError:
        return None
    today = datetime.date.today()
    cands = []
    for y in (today.year - 1, today.year, today.year + 1):
        try:
            cands.append(datetime.date(y, mm, dd))
        except ValueError:
            continue
    if not cands:
        return None
    base = min(cands, key=lambda d: abs((d - today).days))
    nd = base + datetime.timedelta(days=days)
    name = f"{nd.month:02d}{nd.day:02d}"
    return name if name in tabset else None


def _fix_batches(ws, tab, tabset):
    """翌週正味発注数の3便(火/木/土)を、翌週・翌々週の実績回転を参照する式に組み直す。
    カバー範囲: 火便=翌週の水木 / 木便=翌週の金土 / 土便=翌週の日＋翌々週の月火。
    繰越控除: その便の到着までに翌週で消費する曜日の合計。"""
    nw = _tab_after(tab, 7, tabset)
    if not nw:
        return "skip(翌週タブなし)"
    nw2 = _tab_after(tab, 14, tabset)
    nws = ws.spreadsheet.worksheet(nw)
    r1 = _label_row(nws, "回転数（切上げ）") or 39          # 翌週の集計行
    r2 = None
    if nw2:
        r2 = _label_row(ws.spreadsheet.worksheet(nw2), "回転数（切上げ）") or 39

    C = ["V", "W", "X", "Y", "Z", "AA", "AB"]               # 実績側 月〜日
    def nwc(i):
        return f"'{nw}'!${C[i]}${r1}"

    if nw2 and r2:
        sat_cover = f"{nwc(6)}+'{nw2}'!$V${r2}+'{nw2}'!$W${r2}"
        sat_note = ""
    else:
        sat_cover = nwc(6)                                   # 翌々週未作成時は日曜のみ（後日自動で埋まる）
        sat_note = "・土便は翌々週タブ待ち"

    rows = {
        16: (f"{nwc(2)}+{nwc(3)}", [0, 1]),                  # 火便=水木 / 繰越控除=月火
        17: (f"{nwc(4)}+{nwc(5)}", [0, 1, 2, 3]),            # 木便=金土 / 〜木
        18: (sat_cover, [0, 1, 2, 3, 4, 5]),                 # 土便=日+翌々月火 / 〜土
    }
    updates = [{"range": "AO14", "values": [[f"翌週({nw}) 正味発注数 — 繰り越し在庫を引いた数 / 木曜まで確定"]]}]
    for r, (cover, carry_idx) in rows.items():
        carry = "+".join(nwc(i) for i in carry_idx)
        ap = f"=ROUND(MAX(0,({cover})-MAX(0,N($BA$12)-({carry}))),0)"
        asf = f"=ROUND(MAX(0,({cover})-MAX(0,N($BB$12)-({carry}))),0)"
        updates.append({"range": f"AP{r}:AU{r}", "values": [[
            ap, f"=ROUND($AP${r}*0.4,1)", f"=ROUND($AP${r}*0.4/5,0)",
            asf, f"=ROUND($AS${r}*0.75,1)", f"=ROUND($AS${r}*0.75/5,0)"]]})
    ws.batch_update(updates, value_input_option="USER_ENTERED")
    return f"OK(翌週{nw}/翌々週{nw2 or '無'}){sat_note}"


def main():
    ws0 = data_layer.open_ws("0622")
    sh = ws0.spreadsheet
    sheets = sh.worksheets()
    tabset = {w.title for w in sheets}
    today = datetime.date.today()
    monday = today - datetime.timedelta(days=today.weekday())
    targets = sys.argv[1:]
    for w in sheets:
        t = w.title
        if targets and t not in targets:
            continue
        if not targets:  # 自動: 当週以降のみ
            ao6 = (w.get_all_values()[5][40] if len(w.get_all_values()) > 5 else "")
            d = _parse(ao6)
            if d is None or d < monday:
                continue
        print(normalize(t, w, tabset))


if __name__ == "__main__":
    main()
