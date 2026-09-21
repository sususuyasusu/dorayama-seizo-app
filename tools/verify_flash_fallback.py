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

# 6) 催事売上: 正本に催事行が無い日 → クラウドの催事売上で埋まる（件数1以上のときだけ）
EVENT_HEAD = ["日付", "催事名", "売上税込"]


def run_event(event_rows, flash_rows):
    values = {sync.TABS["event"]: [EVENT_HEAD] + event_rows,
              sync.FLASH_TAB: [FLASH_HEAD + ["催事売上", "催事件数"]] + flash_rows}
    records = {r["date"]: r for r in sync.parse_management_values(values, TODAY)["records"]}
    return records.get("2026-09-20")


row = run_event([], [["2026-09-20", "58986", "39", "53438", "5", "x", "取得済み", "194318", "1"]])
assert row["eventSales"] == 194318 and row["eventRows"] == 1, row

# 7) 正本に催事行がある → 正本が優先
row = run_event([["2026/09/20", "上野", "200,000"]], [["2026-09-20", "58986", "39", "53438", "5", "x", "取得済み", "194318", "1"]])
assert row["eventSales"] == 200000, row

# 8) 催事件数0（催事なしの日）・取得失敗 → 催事売上は入れない
row = run_event([], [["2026-09-20", "58986", "39", "53438", "5", "x", "取得済み", "0", "0"]])
assert row is None or not row["eventRows"], row
row = run_event([], [["2026-09-20", "58986", "39", "53438", "5", "x", "一部取得できず", "", ""]])
assert row is None or not row["eventRows"], row

# 9) 退勤の打刻漏れなどの注意書きは、正本を使う日でも記録に残る
row = run([["2026/09/20", "60,000", "50,000", "10"]],
          [["2026-09-20", "58986", "39", "53438", "5", "x", "取得済み（要確認）：退勤の打刻なし（人件費に入っていません）: 塩見かほり", "", ""]])
assert "塩見かほり" in (row.get("flashNote") or ""), row
assert row["storeSales"] == 60000, row
# 注意書きが無ければ付かない
assert not run([["2026/09/20", "60,000", "50,000", "10"]],
               [["2026-09-20", "58986", "39", "53438", "5", "x", "取得済み", "", ""]]).get("flashNote")

# 10) 「タイミー未登録」の恒常的な断り書きは文面に出さない
assert not run([["2026/09/20", "60,000", "50,000", "10"]],
               [["2026-09-20", "58986", "39", "53438", "5", "x",
                 "取得済み（要確認）：タイミーの認証情報が未登録（タイミー勤務がある日は人件費が低く出ます）", "", ""]]).get("flashNote")

print("flash fallback: all ok")
