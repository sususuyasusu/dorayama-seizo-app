#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""公開アプリに出る _manual_content から鍵の暗証番号を伏せ字にする（応急処置）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import data_layer
import manual_layer

OLD = "（暗証番号 0210）"
NEW = "（暗証番号は店内掲示・スタッフ間で共有。ここには載せない）"

ws = manual_layer._ws(manual_layer.CONTENT_TAB, manual_layer.CONTENT_HEADER)
vals = ws.get_all_values()
n = 0
for i, row in enumerate(vals, start=1):
    for j, cell in enumerate(row, start=1):
        if OLD in str(cell):
            ws.update_cell(i, j, str(cell).replace(OLD, NEW))
            n += 1
print(f"伏せ字化: {n}セル")
data_layer.invalidate()
# 検証
rows = manual_layer._rows(manual_layer.CONTENT_TAB, manual_layer.CONTENT_HEADER)
leak = [r for r in rows if any("0210" in str(c) for c in r)]
print(f"残存0210: {len(leak)}件")
