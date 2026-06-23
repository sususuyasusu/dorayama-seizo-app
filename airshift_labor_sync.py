#!/usr/bin/env python3
"""エアシフトのシフト表CSVから日別の人件費・交通費を集計し製造表の _app_labor に書き出す。
CSV列: 0氏名 1日付 ... 12通勤手当 14合計。月別ファイルが同じ人日を重複保持するため
(氏名,日付) で一意化してから日別合計（しないと約6倍に膨らむ）。"""
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
    ppd = {}
    for path in glob.glob(CSV_GLOB):
        with open(path, encoding="utf-8-sig") as f:
            rows = list(csv.reader(f))
        for r in rows[1:]:
            if len(r) < 15:
                continue
            nm = r[0].strip()
            d = r[1].strip()
            if not nm or "/" not in d:
                continue
            ppd[(nm, d)] = (_num(r[14]), _num(r[12]))
    daily = {}
    for (nm, d), (tot, trn) in ppd.items():
        cur = daily.setdefault(d, [0.0, 0.0])
        cur[0] += tot
        cur[1] += trn
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
    print("_app_labor " + str(n) + " days")
    for d in sorted(daily)[-5:]:
        print("  " + d + " " + format(round(daily[d][0]), ",") + " (kotsu " + format(round(daily[d][1]), ",") + ")")
