#!/usr/bin/env python3
"""公開経営画面が最新のAirレジ月次CSVと一致するか確認する。"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo


JST = ZoneInfo("Asia/Tokyo")
DEFAULT_URL = "https://dorayama-seizo-app-1.onrender.com/api/management/analysis"
DEFAULT_SOURCE_ROOT = Path(
    "/Users/suzuki3/Library/CloudStorage/Dropbox-Detale/D& W/どら山/過去/"
    "dw_budget_profit_sheets_automation/data/input/airregi"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--source-root", type=Path, default=DEFAULT_SOURCE_ROOT)
    parser.add_argument("--today", help="検証用の基準日 YYYY-MM-DD")
    parser.add_argument("--timeout", type=int, default=90)
    return parser.parse_args()


def source_summary(path: Path) -> tuple[date, int, int]:
    latest = None
    total = 0
    rows = 0
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            day_text = (row.get("日付") or "").strip()
            amount_text = (row.get("売上金額") or "0").replace(",", "").strip()
            if not day_text:
                continue
            day = date.fromisoformat(day_text)
            latest = max(latest, day) if latest else day
            total += int(float(amount_text or 0))
            rows += 1
    if latest is None or rows == 0:
        raise ValueError("AirレジCSVに売上行がありません")
    return latest, total, rows


def fetch_public(url: str, timeout: int) -> dict:
    separator = "&" if "?" in url else "?"
    request = Request(
        f"{url}{separator}checked_at={datetime.now(JST).strftime('%Y%m%d%H%M%S')}",
        headers={"Cache-Control": "no-cache", "User-Agent": "dorayama-daily-freshness-check/1.0"},
    )
    with urlopen(request, timeout=timeout) as response:
        if response.status != 200:
            raise RuntimeError(f"公開API HTTP {response.status}")
        return json.load(response)


def main() -> int:
    args = parse_args()
    today = date.fromisoformat(args.today) if args.today else datetime.now(JST).date()
    completed_day = today - timedelta(days=1)
    source_path = args.source_root / f"airregi_{completed_day.year}_{completed_day.month:02d}.csv"
    if not source_path.exists():
        print(f"NG: 前日分を含むAirレジCSVがありません ({source_path.name})")
        return 1

    try:
        source_latest, source_total, source_rows = source_summary(source_path)
        public = fetch_public(args.url, args.timeout)
    except Exception as error:
        print(f"NG: 最新化確認に失敗しました ({error})")
        return 1

    problems = []
    current = public.get("current") or {}
    expected_label = f"{today.month}月"
    if public.get("currentMonthLabel") != expected_label:
        problems.append(f"表示月が{public.get('currentMonthLabel')}です（期待値 {expected_label}）")
    if not current.get("connected"):
        problems.append("公開画面がGoogleシートへ接続できていません")
    if source_latest < completed_day:
        problems.append(
            f"Airレジの最新日が{source_latest.isoformat()}です（前日 {completed_day.isoformat()} 未反映）"
        )

    if completed_day.month == today.month:
        public_store_sales = current.get("storeSales")
        if public_store_sales is None:
            problems.append("公開画面の店舗売上が未取得です")
        elif round(float(public_store_sales)) != source_total:
            problems.append(
                f"店舗売上が不一致です（Airレジ {source_total:,}円 / 公開画面 {round(float(public_store_sales)):,}円）"
            )

    if problems:
        print("NG: " + " / ".join(problems))
        return 1

    sales = current.get("sales")
    sales_text = f"{round(float(sales)):,}円" if sales is not None else "未取得"
    print(
        f"OK: 前日 {completed_day.isoformat()} まで反映、"
        f"今月売上 {sales_text}、Airレジ {source_rows}行を照合済み"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
