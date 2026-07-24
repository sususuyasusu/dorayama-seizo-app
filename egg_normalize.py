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
    return f"[{tab}] A:{nA}/14 置換  B:{b}  C:見通し/発注チェックを実績化(冪等)"


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
