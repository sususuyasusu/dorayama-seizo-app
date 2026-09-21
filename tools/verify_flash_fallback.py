#!/usr/bin/env python3
"""Mac停止日の代役（クラウドが書く _flash_daily）が正しく速報に入るかを、外部接続なしで検証する。"""
import sys
import warnings
from datetime import date
from pathlib import Path

warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import management_sync_layer as sync  # noqa: E402

TODAY = date(2026, 9, 21)
STORE_HEAD = ["日付", "Airレジ売上税込", "人件費合計", "客数"]
FLASH_HEAD = ["日付", "Airレジ売上税込", "客数", "人件費合計", "人数", "取得時刻", "状態"]


def run(store_rows, flash_rows):
    values = {sync.TABS["store"]: [STORE_HEAD] + store_rows, sync.FLASH_TAB: [FLASH_HEAD] + flash_rows}
    records = {r["date"]: r for r in sync.parse_management_values(values, TODAY)["records"]}
    return records.get("2026-09-20")


# 1) 正本に9/20の行が無い → クラウドの値で埋まる
row = run([], [["2026-09-20", "58986", "39", "53438", "5", "x", "取得済み"]])
assert row and row["storeSales"] == 58986 and row["storeLabor"] == 53438, row

# 2) 正本に数字がある → 正本が優先（二重に足さない）
row = run([["2026/09/20", "60,000", "50,000", "10"]], [["2026-09-20", "58986", "39", "53438", "5", "x", "取得済み"]])
assert row["storeSales"] == 60000 and row["storeLabor"] == 50000, row

# 3) 正本の行はあるが中身が0 → クラウドの値で埋まる（二重計上しない）
row = run([["2026/09/20", "0", "0", "0"]], [["2026-09-20", "58986", "39", "53438", "5", "x", "取得済み"]])
assert row["storeSales"] == 58986 and row["storeLabor"] == 53438, row

# 4) クラウド側も売上が取れていない → 入力待ちのまま（0円と見せない）
assert run([], [["2026-09-20", "", "", "53438", "5", "x", "一部取得できず"]]) is None

# 5) タブが無い/空でも従来どおり動く
assert run([], []) is None

print("flash fallback: all ok")
