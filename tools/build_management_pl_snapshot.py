#!/usr/bin/env python3
"""管理会計PL Excelを読めない環境（Render等）向けのスナップショットを作る。

このMacの原本 【第10期 どら山】管理会計PL.xlsx を読み取り専用で解析し、
data/management_pl_workbook_snapshot.json へ書き出す。原本には書き込まない。
原本を更新したら、このスクリプトを再実行してからデプロイする。
使い方: python3 tools/build_management_pl_snapshot.py
"""
import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

import management_pl_workbook_layer as layer  # noqa: E402


def main():
    data = layer.read_workbook(layer.WORKBOOK_PATH)
    if not data.get("available"):
        print("Excel原本を読めませんでした。スナップショットは更新しません")
        return 1
    out = BASE / "data" / "management_pl_workbook_snapshot.json"
    out.write_text(json.dumps(data, ensure_ascii=False) + "\n", encoding="utf-8")
    summary = data.get("summary") or {}
    print(f"書き出し完了: {out.name}")
    print(f"  元Excel更新: {data.get('updatedAt')}　科目行: {len(data.get('lines') or [])}行")
    print(f"  期間 {summary.get('period')}　売上 {summary.get('sales'):,}円　経常利益 {summary.get('profit'):,}円")
    return 0


if __name__ == "__main__":
    sys.exit(main())
