#!/usr/bin/env python3
"""freeeの固定費5科目を、公開画面用の安全な月別明細へ変換する。

取引ID・口座番号・freee認証情報は出力しない。既定は確認のみで、--write 時だけ
data/fixed_cost_details_2026.json を更新する。freee帳簿には一切書き込まない。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


BASE = Path(__file__).resolve().parents[1]
FREEE_ROOT = Path(
    "/Users/suzuki3/Library/CloudStorage/Dropbox-Detale/D& W/どら山/"
    "dw_freee_accounting_automation"
)
OUTPUT = BASE / "data" / "fixed_cost_details_2026.json"
FIXED_CATEGORIES = {"地代家賃", "賃借料", "水道光熱費", "通信費", "保険料"}
JST = ZoneInfo("Asia/Tokyo")

sys.path.insert(0, str(FREEE_ROOT))
from dotenv import load_dotenv  # noqa: E402

load_dotenv(FREEE_ROOT / ".env")
from src.freee_client import FreeeClient  # noqa: E402
from src.token_manager import build_token_manager  # noqa: E402


def page(client, path, params, key, limit=100):
    rows, offset = [], 0
    while True:
        request = dict(params)
        request.update({"limit": limit, "offset": offset})
        got = client._get(path, request).get(key) or []
        rows.extend(got)
        if len(got) < limit:
            return rows
        offset += limit
        if offset > 200000:
            raise RuntimeError("freee取得件数が上限を超えました")


def compact_text(value):
    return " ".join(str(value or "").replace("\u3000", " ").split())[:80]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--dorayama-only", action="store_true", help="検証用。本店部門だけを集計")
    parser.add_argument("--all-sections", action="store_true", help="検証用。全事業の部門を集計")
    parser.add_argument("--start", default="2026-02-01")
    parser.add_argument("--end", default="2027-01-31")
    args = parser.parse_args()

    company_id = int(os.environ.get("FREEE_COMPANY_ID", "800646"))
    client = FreeeClient(build_token_manager(), company_id)
    accounts = {
        row["id"]: row.get("name", "")
        for row in client._get("/api/1/account_items", {"company_id": company_id}).get("account_items", [])
    }
    sections = {
        row["id"]: row.get("name", "")
        for row in client._get("/api/1/sections", {"company_id": company_id}).get("sections", [])
    }
    partners = {
        row["id"]: row.get("name", "")
        for row in page(client, "/api/1/partners", {"company_id": company_id}, "partners")
    }
    walletables = {
        row["id"]: row.get("name", "")
        for row in client._get("/api/1/walletables", {"company_id": company_id}).get("walletables", [])
    }
    deals = page(
        client,
        "/api/1/deals",
        {
            "company_id": company_id,
            "type": "expense",
            "start_issue_date": args.start,
            "end_issue_date": args.end,
            "accruals": "with",
        },
        "deals",
    )

    months = {}
    for deal in deals:
        issue_date = str(deal.get("issue_date") or "")
        if len(issue_date) < 7:
            continue
        payments = deal.get("payments") or []
        payment_names = sorted({walletables.get(row.get("from_walletable_id"), "") for row in payments})
        payment = "・".join(name for name in payment_names if name) or (
            "決済待ち" if deal.get("status") != "settled" else "決済済み"
        )
        for detail in deal.get("details") or []:
            category = accounts.get(detail.get("account_item_id"), "")
            if category not in FIXED_CATEGORIES:
                continue
            section = sections.get(detail.get("section_id"), "")
            if args.dorayama_only and "どら山" not in section:
                continue
            if not args.dorayama_only and not args.all_sections and not (
                "どら山" in section or "会社共通" in section
            ):
                continue
            amount = int(detail.get("amount") or 0)
            if amount == 0:
                continue
            month = issue_date[:7]
            vendor = compact_text(partners.get(deal.get("partner_id"))) or "取引先未設定"
            description = compact_text(detail.get("description"))
            receipt_count = len(deal.get("receipt_ids") or deal.get("receipts") or [])
            months.setdefault(month, []).append({
                "date": issue_date,
                "category": category,
                "vendor": vendor,
                "description": description,
                "amount": amount,
                "payment": payment,
                "department": section or "部門未設定",
                "evidence": "証憑あり" if receipt_count else "証憑未確認",
                "source": "freee取引明細",
            })

    # 8月固定費締めで確認済みの発生主義調整。元のfreee取引とは分けて表示する。
    months.setdefault("2026-08", []).append({
        "date": "2026-08-31",
        "category": "水道光熱費",
        "vendor": "月次調整",
        "description": "上下水道の8月未計上分",
        "amount": 1958,
        "payment": "未払調整",
        "department": "管理会計調整",
        "evidence": "8月固定費締め済み",
        "source": "管理会計PL月次調整",
    })

    month_rows = []
    for month, rows in sorted(months.items()):
        rows.sort(key=lambda row: (row["date"], row["category"], row["vendor"], row["amount"]))
        month_rows.append({
            "month": month,
            "count": len(rows),
            "total": sum(row["amount"] for row in rows),
            "transactions": rows,
        })
    payload = {
        "schemaVersion": 1,
        "generatedAt": datetime.now(JST).isoformat(timespec="minutes"),
        "source": "freee会計のどら山・会社共通取引明細＋確定済み月次調整（読み取り専用）",
        "privacy": "取引ID・口座番号・認証情報は保存しない",
        "months": month_rows,
    }
    print("固定費明細の確認結果")
    for month in month_rows:
        print(f"- {month['month']}: {month['count']}件／{month['total']:,}円")
    if args.write:
        if OUTPUT.is_file():
            try:
                current = json.loads(OUTPUT.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                current = {}
            comparable_keys = ("schemaVersion", "source", "privacy", "months")
            if all(current.get(key) == payload.get(key) for key in comparable_keys):
                print("実データに変化がないため、ファイルは書き換えません。")
                return 2
        OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"保存しました: {OUTPUT.name}")
    else:
        print("確認のみ。ファイルは変更していません。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
