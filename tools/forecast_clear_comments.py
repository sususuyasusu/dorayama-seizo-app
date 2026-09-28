#!/usr/bin/env python3
"""一度きりの片付け（2026-09-28）: アーカイブ _fc_daily に写していた日報の「所感」を消し、
列の見出しを「メモ」（人が書き足す欄）に変える。

理由: 製造表スプレッドシートは「リンクを知っている人は閲覧可」の共有になっているため、
スタッフの自由記入の文章は持ち込まないことにした。販売数・在庫・天気などの数字は残す。

  python3 tools/forecast_clear_comments.py          … 何件消すかの確認だけ
  python3 tools/forecast_clear_comments.py --apply  … 実行
"""
import os
import sys
import warnings

warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import data_layer
import forecast_archive as archive

ws = data_layer.get_ws(archive.TAB_DAILY)
if ws is None:
    sys.exit("アーカイブのタブがありません")
vals = ws.get_all_values()
head = vals[0]
col = None
for name in ("所感", "メモ"):
    if name in head:
        col = head.index(name)
        break
if col is None:
    sys.exit(f"所感／メモの列が見つかりません: {head}")
filled = sum(1 for r in vals[1:] if len(r) > col and str(r[col]).strip())
letter = data_layer.gspread.utils.rowcol_to_a1(1, col + 1).rstrip("1")
print(f"列 {letter}（見出し「{head[col]}」）: 文章が入っている行 {filled}件／全{len(vals) - 1}行")
if "--apply" not in sys.argv:
    print("確認だけで終了（実行するには --apply）")
    sys.exit(0)
body = [["メモ"]] + [[""] for _ in vals[1:]]
ws.update(range_name=f"{letter}1:{letter}{len(vals)}", values=body, value_input_option="RAW")
after = ws.get_all_values()
left = sum(1 for r in after[1:] if len(r) > col and str(r[col]).strip())
print(f"実行後: 見出し「{after[0][col]}」・文章が残っている行 {left}件")
if left or after[0][col] != "メモ":
    sys.exit("消し残りがあります。シートを確認してください")
