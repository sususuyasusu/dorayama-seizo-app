#!/usr/bin/env python3
"""AirメイトCSVを読めない環境（Render等）向けの履歴スナップショットを作る。

このMacのDropbox配下にある保存済みAirメイト日次CSVを読み取り専用で集計し、
data/airmate_history_2026.json へ書き出す。元CSV・Airメイト本体には書き込まない。
使い方: python3 tools/build_airmate_history_snapshot.py
"""
import json
import sys
from datetime import date, datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

import management_sync_layer  # noqa: E402


def main():
    rows = management_sync_layer.read_airmate_history(today=date.today())
    if not rows:
        print("CSVから0件。スナップショットは更新しません（既存を保持）")
        return 1

    out = BASE / "data" / "airmate_history_2026.json"
    try:
        existing = json.loads(out.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        existing = None
    if existing is not None and existing.get("rows") == rows:
        first = min(row["date"] for row in rows)
        last = max(row["date"] for row in rows)
        print(f"実データに変化なし（{len(rows)}日分・{first}〜{last}）。書き換えはスキップ")
        return 2

    payload = {
        "schemaVersion": 1,
        "generatedAt": datetime.now().isoformat(timespec="minutes"),
        "source": "dw_budget_profit_sheets_automation/data/input/airmate の保存済み日次CSV（読み取り専用）",
        "note": "CSVを直接読めない環境でのフォールバック。再生成はこのスクリプトを実行",
        "rows": rows,
    }
    out.write_text(json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")
    first = min(row["date"] for row in rows)
    last = max(row["date"] for row in rows)
    print(f"書き出し完了: {out.name}　{len(rows)}日分（{first}〜{last}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
