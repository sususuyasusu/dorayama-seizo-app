#!/usr/bin/env python3
"""エアシフトのシフト表CSV（毎朝の自動取得物）から日別の人件費と交通費を集計し、
製造表の専用タブ `_app_labor` に書き出す。アプリ(Render)はDropboxのCSVを読めないので、
ここ(Mac)で集計してシートに置き、アプリはそのシートを読む。
CSV列: 0氏名 1日付 ... 12通勤手当 14合計  → 日別: 人件費=Σ合計 / 交通費=Σ通勤手当"""
import csv
import glob
import data_layer

CSV_GLOB = "/Users/suzuki3/Library/CloudStorage/Dropbox-Detale/D& W/どら山/過去/dw_budget_profit_sheets_automation/data/input/airshift_worksheets/airshift_worksheet_*.csv"
TAB = "_app_labor"
HEADER = ["日付", "人件費", "交通費"]


def _num(s):
    s = str(s).replace(",", "").replace("¥", "").strip()
    try:
        return float(s) if s not in ("", "-") else 0.0
    except ValueError:
        return 0.0


def collect():
    daily = {}
    for path in glob.glob(CSV_GLOB):
        with open(path, encoding="utf-8-sig") as f:
            rows = list(csv.reader(f))
        for row in rows[1:]:
            if len(row) < 15:
                continue
            d = row[1].strip()
            if not d or "/" not in d:
                continue
            cur = daily.setdefault(d, [0.0, 0.0])
            cur[0] += _num(row[14])
            cur[1] += _num(row[12])
    return daily


def write_sheet(daily):
    sh = data_layer._spreadsheet()
    try:
        ws = sh.worksheet(TAB)
        ws.clear()
    except Exception:
        ws = sh.add_worksheet(title=TAB, rows=400, cols=4)
        data_layer._spreadsheet(refresh=True)
    out = [HEADER] + [[d, round(daily[d][0]), round(daily[d][1])] for d in sorted(daily)]
    ws.update(range_name="A1", values=out, value_input_option="RAW")
    data_layer.invalidate(TAB)
    return len(out) - 1


if __name__ == "__main__":
    daily = collect()
    n = write_sheet(daily)
    print(f"_app_labor に {n} 日分を書き込みました")
    for d in sorted(daily)[-7:]:
        print(f"  {d}: 人件費 {round(daily[d][0]):,}円（うち交通費 {round(daily[d][1]):,}円）")
