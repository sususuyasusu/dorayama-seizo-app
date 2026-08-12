"""店長向け「予算対比サマリー」。製造表(週次)とは別の『どら山 予実管理』
スプレッドシートから、月次の 売上・原価・粗利益・人件費・固定費 を読み、
会社の管理会計基準(粗利益中心)で1画面にまとめる。

対象外(このサマリーには含まない):
  正社員給与・役員報酬・法定福利費(本社/デザイン按分含む会社全体の人件費)。
  ここで言う「人件費」は、どら山の店舗運営(Airシフト実績・時給ベース)と
  催事の外注(派遣・タイミー実額)のみ。全社の人件費はVault
  dw-management-accounting-gross-profit.md / dw_ledger を参照。
"""
import time
import json
import os
from pathlib import Path

import gspread
from google.oauth2.service_account import Credentials

YOSAN_SHEET_ID = "1PxLrwb2x2ZDs0DaWgmGuwW-6IzRvqXYJhywsGzyLftY"
CRED = os.environ.get(
    "DORAYAMA_SA_CRED",
    "/Users/suzuki3/Library/CloudStorage/Dropbox-Detale/D& W/どら山/過去/dw_budget_profit_sheets_automation/config/google_credentials.json",
)

_cache = {"t": 0.0, "sh": None}
_TTL = 60.0

FIXED_COST_CATS = [
    "家賃", "水道光熱費", "通信/システム費", "広告宣伝費",
    "消耗品費", "修繕費", "会計/税理士/社労士", "雑費", "支払利息",
]  # 「外注費」は派遣・タイミー(人件費)と非人件費が混在するため固定費合計には含めない


def _spreadsheet():
    now = time.time()
    if _cache["sh"] and now - _cache["t"] < _TTL:
        return _cache["sh"]
    raw = os.environ.get("DORAYAMA_SA_CRED_JSON")
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    if raw:
        creds = Credentials.from_service_account_info(json.loads(raw), scopes=scopes)
    else:
        creds = Credentials.from_service_account_file(CRED, scopes=scopes)
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(YOSAN_SHEET_ID)
    _cache["sh"] = sh
    _cache["t"] = now
    return sh


def _num(s):
    s = str(s).replace(",", "").replace("¥", "").replace("%", "").strip()
    try:
        return float(s) if s not in ("", "-") else 0.0
    except ValueError:
        return 0.0


def _find_row(values, month_label):
    for i, row in enumerate(values):
        if row and row[0].strip() == month_label:
            return i
    return None


def get_summary(ym: str = None):
    """ym: "2026-08" 形式。省略時は当月。"""
    from datetime import date
    if ym is None:
        today = date.today()
        ym = f"{today.year}-{today.month:02d}"
    yr, mo = ym.split("-")
    month_label = f"{int(yr)}/{int(mo)}"

    sh = _spreadsheet()

    # ── 06_月次予実分析: 売上・原価 ──────────────────────────
    ws1 = sh.worksheet("06_月次予実分析")
    v1 = ws1.get_values("A4:U400")
    r1 = _find_row(v1, month_label)
    sales_budget = sales_actual = material_cost = 0.0
    store_sales = event_sales = 0.0
    if r1 is not None:
        row = v1[r1]
        sales_budget = _num(row[1])
        sales_actual = _num(row[2])
        material_cost = _num(row[18]) if len(row) > 18 else 0.0
        store_sales = _num(row[16]) if len(row) > 16 else 0.0
        event_sales = _num(row[17]) if len(row) > 17 else 0.0

    # ── 10_人件費分析: 店舗人件費・催事外注人件費 ──────────────
    ws2 = sh.worksheet("10_人件費分析")
    v2 = ws2.get_values("A4:I400")
    r2 = _find_row(v2, month_label)
    store_labor = event_labor = 0.0
    if r2 is not None:
        row = v2[r2]
        store_labor = _num(row[4]) if len(row) > 4 else 0.0
        event_labor = _num(row[5]) if len(row) > 5 else 0.0
    labor_total = store_labor + event_labor

    # ── 11_固定費明細: 費目ごとの予算額・実績額 ─────────────────
    ws3 = sh.worksheet("11_固定費明細")
    v3 = ws3.get_values("A4:D400")
    fixed_budget = fixed_actual = 0.0
    fixed_breakdown = []
    for row in v3:
        if len(row) < 4 or row[0].strip() != month_label:
            continue
        cat = row[1].strip()
        if cat not in FIXED_COST_CATS:
            continue
        b, a = _num(row[2]), _num(row[3])
        fixed_budget += b
        fixed_actual += a
        fixed_breakdown.append({"cat": cat, "budget": round(b), "actual": round(a)})

    gross_profit = sales_actual - material_cost
    operating_profit = gross_profit - labor_total - fixed_actual

    def pct(a, b):
        return round(a / b * 100, 1) if b else None

    return {
        "ym": ym,
        "monthLabel": month_label,
        "sales": {"budget": round(sales_budget), "actual": round(sales_actual),
                   "store": round(store_sales), "event": round(event_sales),
                   "achieveRate": pct(sales_actual, sales_budget)},
        "materialCost": {"actual": round(material_cost),
                          "rate": pct(material_cost, sales_actual)},
        "grossProfit": {"actual": round(gross_profit),
                         "rate": pct(gross_profit, sales_actual)},
        "labor": {"store": round(store_labor), "event": round(event_labor),
                  "total": round(labor_total),
                  "rateOfGP": pct(labor_total, gross_profit)},
        "fixedCost": {"budget": round(fixed_budget), "actual": round(fixed_actual),
                      "breakdown": fixed_breakdown,
                      "rateOfGP": pct(fixed_actual, gross_profit)},
        "operatingProfit": {"actual": round(operating_profit),
                             "rateOfGP": pct(operating_profit, gross_profit)},
        "note": "人件費は店舗(Airシフト実績)＋催事外注(派遣・タイミー実額)のみ。"
                "正社員給与・役員報酬・法定福利費は含みません。",
    }
