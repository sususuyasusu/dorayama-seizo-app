#!/usr/bin/env python3
"""速報（運営ベース）が、過去の確定値とどれくらいずれたかを検証して保存する。

確定月ごとに、その月末時点の日次台帳から速報のコスト見込・損益を組み立て、
管理会計PLの確定値と比べる。帳簿・シート・本番への書き込みはしない（data/flash_accuracy_2026.json のみ更新）。
催事の販売員費率は「その月を除いた直近の確定月」で求め、答え合わせに使った月を混ぜない。
"""

from __future__ import annotations

import json
import sys
import warnings
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

warnings.filterwarnings("ignore")
BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))

import management_analysis_layer as layer  # noqa: E402

OUTPUT = layer.FLASH_ACCURACY_PATH
JST = ZoneInfo("Asia/Tokyo")


def main() -> int:
    analysis = layer.get_management_analysis()
    monthly = analysis["monthly"]
    fixed = {row["key"]: row for row in analysis["fixed"]["history"]}
    ops_by_month = layer._monthly_daily_sales_totals(analysis["weekdayTimeHistory"]["daily"])

    rows = []
    for index, row in enumerate(monthly):
        month_number = index + 1
        if month_number == 1 or row.get("phase") != "確定":
            continue
        ym = f"2026-{month_number:02d}"
        complete = layer.complete_sync(layer._month_end(2026, month_number))
        summary = (complete or {}).get("monthSummary") or {}
        if complete is None or not summary.get("sales") or not summary.get("knownCost"):
            # 一過性の取得失敗で空の月ができても、そのまま保存しない（検証結果が静かに狂うのを防ぐ）
            print(f"{row['month']}の日次台帳を取得できませんでした。保存せずに終了します")
            return 1
        ops_event = summary.get("eventSales") or 0
        confirmed_event = row.get("eventSales") or 0
        # 催事の運営データが会計上の催事売上の半分にも満たない月は、比較の対象外にする
        comparable = confirmed_event > 0 and ops_event >= confirmed_event * 0.5

        others = [
            (other["eventStaffing"], (ops_by_month.get(f"2026-{i + 1:02d}") or {}).get("eventSales") or 0)
            for i, other in enumerate(monthly)
            if i + 1 != month_number and i > 0 and other.get("phase") == "確定"
            and other.get("eventStaffing") is not None
            and ((ops_by_month.get(f"2026-{i + 1:02d}") or {}).get("eventSales") or 0) > 0
        ][-layer.FLASH_STAFFING_RATE_MONTHS:]
        denominator = sum(item[1] for item in others)
        rate = sum(item[0] for item in others) / denominator if denominator else 0
        fixed_total = (fixed.get(row["month"]) or {}).get("total")
        flash = layer.flash_components(
            summary, fixed_total, "確定", round(ops_event * rate), "他の確定月の平均率で仮置き"
        )
        confirmed_cost = row.get("costTotal")
        cost_diff = flash["costEstimate"] - confirmed_cost if confirmed_cost else None
        profit_diff = flash["opsProfit"] - row["profit"] if row.get("profit") is not None else None
        rows.append({
            "month": row["month"],
            "comparable": comparable,
            "opsSales": flash["opsSales"],
            "accountingSales": row.get("sales"),
            "costEstimate": flash["costEstimate"],
            "confirmedCost": confirmed_cost,
            "costDiff": cost_diff,
            "costDiffPct": round(cost_diff / confirmed_cost * 100, 1) if cost_diff is not None and confirmed_cost else None,
            "opsProfit": flash["opsProfit"],
            "confirmedProfit": row.get("profit"),
            "profitDiff": profit_diff,
        })

    comparable_rows = [row for row in rows if row["comparable"] and row["costDiffPct"] is not None]
    summary = {
        "comparableMonths": [row["month"] for row in comparable_rows],
        "excludedMonths": [row["month"] for row in rows if not row["comparable"]],
        "costAvgAbsPct": round(sum(abs(row["costDiffPct"]) for row in comparable_rows) / len(comparable_rows), 1) if comparable_rows else None,
        "costMaxAbsPct": max((abs(row["costDiffPct"]) for row in comparable_rows), default=None),
        "profitDiffMin": min((row["profitDiff"] for row in comparable_rows), default=None),
        "profitDiffMax": max((row["profitDiff"] for row in comparable_rows), default=None),
        "note": (
            "コスト見込は確定コストと近い一方、損益は催事売上の会計上の計上時期でずれます。"
            "催事の運営データが会計上の売上の半分に満たない月（2〜3月）は比較から除いています。"
        ),
    }
    payload = {
        "schemaVersion": 1,
        "generatedAt": datetime.now(JST).isoformat(timespec="minutes"),
        "method": "月末時点の日次台帳の把握費用＋固定費（確定）＋催事販売員費（他の確定月の率で仮置き）",
        "months": rows,
        "summary": summary,
    }
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("速報の精度検証（確定月との比較）")
    for row in rows:
        mark = "" if row["comparable"] else "（比較対象外）"
        print(
            f"- {row['month']}: コスト見込{row['costEstimate']:,}円／確定{row['confirmedCost']:,}円"
            f"（差{row['costDiffPct']:+.1f}%）、損益差{row['profitDiff']:+,}円{mark}"
        )
    print(f"コスト見込の平均ずれ {summary['costAvgAbsPct']}%・最大 {summary['costMaxAbsPct']}%、"
          f"損益のずれ {summary['profitDiffMin']:+,}〜{summary['profitDiffMax']:+,}円")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
