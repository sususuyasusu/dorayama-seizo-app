#!/usr/bin/env python3
"""卵発注ナビ 正規化フィクサー（恒久・再実行可能）。

製造表の発注ナビを「再生成」すると、以下2つの調整が旧仕様に戻ってしまう。
このスクリプトを実行すると、当週＋未来週の全タブに両方を再適用する（冪等）。

  A) 見通し(BA/BB)の起点を実績ベースに: $AO$r<TODAY() → $AO$r<=TODAY()
     （当日の実在庫を使い、未来日の仮入力は無視）
  B) 必要在庫(AS/AT)を発注非依存の理想値に: 便日の「発注g÷400/750」流用を撤去し、
     全日「次便までの実績回転(row39 V-AB)の合計」に統一（黄白共通）。

使い方:  python3 egg_normalize.py            # 当週＋未来週を自動検出
         python3 egg_normalize.py 0706 0713  # タブ指定
過去週には触れない（営業終了済みの履歴を変えない）。
"""
import sys, re, datetime
import data_layer


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

    # A) BA6:BB12 を <=TODAY に
    cur = ws.get("BA6:BB12", value_render_option="FORMULA")
    newA, nA = [], 0
    for i in range(7):
        row = cur[i] if i < len(cur) else []
        out = []
        for j in range(2):
            c = row[j] if j < len(row) else ""
            if isinstance(c, str) and "<TODAY()" in c and "<=TODAY()" not in c:
                out.append(c.replace("<TODAY()", "<=TODAY()")); nA += 1
            else:
                out.append(c)
        newA.append(out)
    if nA:
        ws.batch_update([{"range": "BA6:BB12", "values": newA}], value_input_option="USER_ENTERED")

    # B) AS6:AT12 を製造ベースに（翌週タブが在る場合のみ）
    m = re.search(r"'(\d{4})'!", as6)
    nw = m.group(1) if m else None
    b = "skip(翌週なし)"
    if nw and nw in tabset:
        def f(r):
            return (f"=CHOOSE(WEEKDAY(AO{r},2),$W$39,$X$39+$Y$39,$Y$39,"
                    f"$Z$39+$AA$39,$AA$39,$AB$39+'{nw}'!$V$39+'{nw}'!$W$39,"
                    f"'{nw}'!$V$39+'{nw}'!$W$39)")
        ws.batch_update([{"range": "AS6:AT12", "values": [[f(r), f(r)] for r in range(6, 13)]}],
                        value_input_option="USER_ENTERED")
        b = f"OK(翌週{nw})"
    return f"[{tab}] A:{nA}/14 置換  B:{b}"


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
