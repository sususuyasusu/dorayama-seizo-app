#!/usr/bin/env python3
"""公開経営画面だけを使って、前月の月次確定準備を安全に判定する。

帳簿・Googleシート・公開アプリへの書き込みは行わない。
通常の未確定状態は異常終了にせず、機械判定結果を日本語とJSONで保存する。
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path
from urllib.request import Request, urlopen


DEFAULT_URL = "https://dorayama-seizo-app-1.onrender.com/api/management/analysis"
DEFAULT_OUTPUT = Path(__file__).resolve().parents[1] / "data" / "month_close_readiness.json"

KNOWN_BLOCKERS = {
    "2026-08": [
        "上野・川越・立川の催事会場別精算書",
        "TakeEatsの8月売上・手数料・キャンセル明細",
        "8月分の催事販売員請求書",
        "社会保険の会社負担分と配賦基準",
        "未解消経費と減価償却の確認",
        "Airシフトの時給未設定4勤務・16.87時間と退勤未打刻1勤務",
    ]
}


def previous_month(today: date) -> str:
    year, month = today.year, today.month - 1
    if month == 0:
        year, month = year - 1, 12
    return f"{year:04d}-{month:02d}"


def month_key(target: str) -> str:
    return f"{int(target[5:7])}月"


def fetch_json(url: str) -> dict:
    request = Request(url, headers={"User-Agent": "dorayama-month-close-readiness/1.0"})
    with urlopen(request, timeout=30) as response:
        if response.status != 200:
            raise RuntimeError(f"公開経営画面の取得に失敗しました（HTTP {response.status}）")
        return json.load(response)


def load_input(path: str | None, url: str) -> dict:
    if path:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    return fetch_json(url)


def evaluate(data: dict, target: str) -> dict:
    key = month_key(target)
    fixed_rows = (data.get("fixed") or {}).get("history") or []
    fixed = next((row for row in fixed_rows if row.get("key") == key), None)
    monthly = next((row for row in data.get("monthly") or [] if row.get("month") == key), None)
    cost_months = (data.get("costAnalysis") or {}).get("months") or []
    cost_month = next((row for row in cost_months if row.get("key") == key), None)

    fixed_ready = bool(
        fixed
        and fixed.get("status") == "確定"
        and fixed.get("total") is not None
        and not (fixed.get("missingCategories") or [])
    )
    labor_ready = bool(
        monthly
        and monthly.get("internalLabor") is not None
        and not monthly.get("laborReconciliation")
    )
    profit_ready = bool(
        monthly
        and monthly.get("profit") is not None
        and cost_month
        and cost_month.get("status") == "確定"
    )

    checks = [
        {
            "id": "fixed_cost",
            "label": "固定費",
            "ready": fixed_ready,
            "amount": fixed.get("total") if fixed else None,
            "status": fixed.get("status") if fixed else "未取得",
        },
        {
            "id": "internal_labor",
            "label": "内部人件費",
            "ready": labor_ready,
            "amount": monthly.get("internalLabor") if monthly else None,
            "status": (
                (monthly.get("laborReconciliation") or {}).get("status")
                if monthly
                else "未取得"
            ) or (monthly.get("dataStatus") if monthly else "未取得"),
        },
        {
            "id": "ordinary_profit",
            "label": "経常利益",
            "ready": profit_ready,
            "amount": monthly.get("profit") if monthly else None,
            "status": cost_month.get("status") if cost_month else "未取得",
        },
    ]
    ready = all(item["ready"] for item in checks)
    blockers = [] if ready else KNOWN_BLOCKERS.get(
        target,
        ["催事精算・給与・決済手数料・未処理経費・減価償却の月次資料"],
    )
    return {
        "targetMonth": target,
        "ready": ready,
        "decision": "確定可能" if ready else "未確定を維持",
        "checkedAtSource": data.get("updatedAt"),
        "checks": checks,
        "blockersToSearch": blockers,
        "safety": "未取得を0円扱いせず、未確定のまま維持します。",
    }


def print_summary(result: dict) -> None:
    print(f"月次確定準備 {result['targetMonth']}: {result['decision']}")
    for item in result["checks"]:
        amount = "未取得" if item["amount"] is None else f"{item['amount']:,}円"
        mark = "準備済み" if item["ready"] else "未確定"
        print(f"- {item['label']}: {amount}／{mark}（{item['status']}）")
    if result["blockersToSearch"]:
        print("- 自動探索を続ける資料: " + "、".join(result["blockersToSearch"]))
    print("- " + result["safety"])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--month", default=previous_month(date.today()))
    parser.add_argument("--input", help="検証用の公開API保存JSON")
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--require-ready", action="store_true")
    args = parser.parse_args()

    if len(args.month) != 7 or args.month[4] != "-":
        raise SystemExit("対象月はYYYY-MM形式で指定してください")
    result = evaluate(load_input(args.input, args.url), args.month)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print_summary(result)
    return 2 if args.require_ready and not result["ready"] else 0


if __name__ == "__main__":
    sys.exit(main())
