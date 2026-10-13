#!/usr/bin/env python3
"""製造表の週タブから、2026年2月以降の月ごとの製造実績（作った数×売価）を、行き先（店舗用／催事の会場）別に集計して保存する。
管理会計PLの「店舗／催事の月次PL」で、工房の人件費を製造額の割合で店舗と催事に分けるのに使う（2026-10-13 本人決定）。
読めなかった週は missingWeeks に残し、0円として扱わない。
使い方: python3 tools/build_production_history_snapshot.py
"""
import json
import sys
import time
from datetime import date
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

import production_layer  # noqa: E402
import data_layer  # noqa: E402

OUT = BASE / "data" / "production_monthly_2026.json"


def load_week(monday):
    for _ in range(3):
        try:
            return data_layer.get_week_blocks(today=monday)
        except Exception:  # 一過性の読み取り失敗は少し待って再試行
            time.sleep(2)
    return None


def main():
    today = date.today()
    history = json.loads((BASE / "data" / "product_analysis_history_2026.json").read_text(encoding="utf-8"))
    prices = production_layer.unit_prices(history)
    months = []
    y, m = 2026, 2
    while (y, m) <= (today.year, today.month):
        res = production_layer.month_production(y, m, prices, today, load_week)
        blocks = {}
        for row in res["daily"]:
            for name, item in row["blocks"].items():
                b = blocks.setdefault(name, {"value": 0, "pieces": 0})
                b["value"] += item["value"]
                b["pieces"] += item["pieces"]
        total = sum(b["value"] for b in blocks.values())
        store = blocks.get("店舗用", {}).get("value", 0)
        months.append({
            "month": f"{y}-{m:02d}", "total": total, "store": store, "event": total - store,
            "storeShare": round(store / total, 4) if total else None,
            "blocks": blocks, "days": len(res["daily"]), "missingWeeks": res["missingWeeks"],
        })
        print(f"{y}-{m:02d} 製造額 {total:,}円（店舗用 {store:,}円・{(store / total * 100) if total else 0:.1f}%）"
              f" 日数 {len(res['daily'])} 読めない週 {len(res['missingWeeks'])}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    OUT.write_text(json.dumps({"generatedAt": today.isoformat(), "source": production_layer.SOURCE_NOTE,
                               "months": months}, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"書き出し: {OUT.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
