#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""data/manual_seed.json を製造表の `_manual_content` タブへ流し込む（全置換）。
`_manual_updates` は無ければ作って初期のお知らせを入れる（既存があれば触らない）。
合言葉 manual_edit_code も未設定なら既定値を入れる。
使い方: python3 tools/seed_manual.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import data_layer
import manual_layer
import config_store

SEED = Path(__file__).resolve().parent.parent / "data" / "manual_seed.json"

INITIAL_UPDATES = [
    ["2026/06/18", "催事の箱詰めルールを掲載", "生どら・箱詰めの見本写真と数の統一", "シーラー・梱包", ""],
    ["2026/06/28", "催事袋（透明袋）とひと仕切り8個に更新", "クラフト紙が入荷不可のため当面は透明袋。箱詰めは1仕切り8個（旧6個から変更）", "シーラー・梱包", ""],
    ["2026/07/11", "あんこ・卵の管理ルールを追加", "あんこ期限5日/ホワイトボード日付記入・卵は毎日2人でダブルチェック", "製造作業台", ""],
    ["2026/07/12", "毎日の在庫確認（iPadアプリ）を開始", "資材/食材の担当分担・商品一覧から店舗在庫を入力・朝のレジ周り補充", "材料などの棚", ""],
    ["2026/07/12", "スタッフマニュアルがアプリになりました", "スマホでいつでも見られます。現場ごとのページ＋検索＋このお知らせ欄。加筆は各ページの✏️から", "", ""],
]


def main():
    cats = json.loads(SEED.read_text(encoding="utf-8"))
    rows = []
    for c in cats:
        for r in c["rows"]:
            rows.append([c["name"], r["type"], r.get("label", ""), r.get("text", ""),
                         r.get("photo", ""), r.get("src", "")])
    sh = data_layer._spreadsheet()

    # _manual_content: 全置換
    ws = manual_layer._ws(manual_layer.CONTENT_TAB, manual_layer.CONTENT_HEADER)
    ws.clear()
    body = [manual_layer.CONTENT_HEADER] + rows
    ws.update(range_name="A1", values=body, value_input_option="RAW")
    print(f"content: {len(rows)}行を書き込み")

    # _manual_updates: 既存が空のときだけ初期投入
    wu = manual_layer._ws(manual_layer.UPDATES_TAB, manual_layer.UPDATES_HEADER)
    existing = wu.get_all_values()
    if len(existing) <= 1:
        wu.update(range_name="A1", values=[manual_layer.UPDATES_HEADER] + INITIAL_UPDATES,
                  value_input_option="RAW")
        print(f"updates: 初期お知らせ{len(INITIAL_UPDATES)}件を書き込み")
    else:
        print(f"updates: 既存{len(existing)-1}件あり（変更なし）")

    if not config_store.get_config("manual_edit_code"):
        config_store.set_config("manual_edit_code", "どら")
        print("合言葉 manual_edit_code=『どら』を設定")

    data_layer.invalidate()
    m = manual_layer.get_manual()
    total = sum(len(v) for v in m["content"].values())
    print(f"検証: カテゴリ{len(m['content'])} / 本文{total}行 / お知らせ{len(m['updates'])}件")


if __name__ == "__main__":
    main()
