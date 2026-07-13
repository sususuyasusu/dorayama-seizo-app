#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""動作テストで入れた行を _manual_content / _manual_updates から削除する。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import data_layer
import manual_layer

MARK = "動作テスト"


def cleanup(tab, header, col_idx):
    ws = manual_layer._ws(tab, header)
    vals = ws.get_all_values()
    hit = [i for i, r in enumerate(vals, start=1)
           if len(r) > col_idx and MARK in str(r[col_idx])]
    for i in reversed(hit):
        ws.delete_rows(i)
    print(f"{tab}: {len(hit)}行削除")


cleanup(manual_layer.CONTENT_TAB, manual_layer.CONTENT_HEADER, 3)
cleanup(manual_layer.UPDATES_TAB, manual_layer.UPDATES_HEADER, 2)
data_layer.invalidate()
