#!/usr/bin/env python3
"""どら山 製造表アプリ — データ層（読み取り）
サービスアカウントで「月～日製造表」から今週の店舗用データ（予定/実績/回転数）を取得する。
裏の計算はスプレッドシートに任せ、アプリはここ経由で読み書きする。"""
import os
import json
from datetime import date, timedelta
import gspread
from google.oauth2.service_account import Credentials

SHEET_ID = "1PRDhGP_4xiO_ZjJP3NB9Id3PmaPa5W7hNyrqFQ5EyqM"
CRED = os.environ.get(
    "DORAYAMA_SA_CRED",
    "/Users/suzuki3/Library/CloudStorage/Dropbox-Detale/D& W/どら山/過去/dw_budget_profit_sheets_automation/config/google_credentials.json",
)

STORE_PRODUCTS = {"黒どら": 27, "あんバター": 28, "白どら": 29, "旬どら": 30, "生": 31, "皮4枚セット": 32}
PLAN_COLS = list(range(1, 8))      # B..H = 予定 月～日
ACT_COLS = list(range(21, 28))     # V..AB = 実績 月～日
WEEKDAYS = ["月", "火", "水", "木", "金", "土", "日"]


_GC = None


def _client():
    """サービスアカウントのgspreadクライアント（プロセス内で1回だけ作成）。
    Render等では鍵ファイルを置けないので、環境変数 DORAYAMA_SA_CRED_JSON に
    鍵JSONそのものを入れておけばそちらを優先して使う。無ければローカルの鍵ファイル。"""
    global _GC
    if _GC is not None:
        return _GC
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    raw = os.environ.get("DORAYAMA_SA_CRED_JSON")
    if raw:
        creds = Credentials.from_service_account_info(json.loads(raw), scopes=scopes)
    else:
        creds = Credentials.from_service_account_file(CRED, scopes=scopes)
    _GC = gspread.authorize(creds)
    return _GC


def current_week_tab(today=None):
    today = today or date.today()
    monday = today - timedelta(days=today.weekday())
    cands = [f"{monday.month:02d}{monday.day:02d}", f"{monday.month}{monday.day:02d}",
             f"{monday.month:02d}{monday.day}"]
    return monday, cands


def get_week_store_data(today=None):
    gc = _client()
    sh = gc.open_by_key(SHEET_ID)
    monday, cands = current_week_tab(today)
    ws = None
    for c in cands:
        try:
            ws = sh.worksheet(c); break
        except gspread.WorksheetNotFound:
            continue
    if ws is None:
        raise RuntimeError(f"今週タブが見つからない（候補 {cands}）")
    vals = ws.get_all_values()

    def cell(r, c):
        return vals[r - 1][c] if r - 1 < len(vals) and c < len(vals[r - 1]) else ""

    def num(s):
        s = str(s).replace(",", "").strip()
        try:
            return float(s) if s not in ("", "-") else None
        except ValueError:
            return None

    days = []
    for i in range(7):
        d = monday + timedelta(days=i)
        days.append({"label": f"{WEEKDAYS[i]}{d.month}/{d.day}", "date": d.isoformat()})

    products = []
    for name, row in STORE_PRODUCTS.items():
        plan = [num(cell(row, c)) for c in PLAN_COLS]
        act = [num(cell(row, c)) for c in ACT_COLS]
        products.append({"name": name, "plan": plan, "actual": act})

    kaiten = [num(cell(38, c)) for c in ACT_COLS]   # 回転数（実数）実績側

    return {"tab": ws.title, "monday": monday.isoformat(), "days": days,
            "products": products, "kaiten": kaiten}


if __name__ == "__main__":
    d = get_week_store_data()
    print(f"今週タブ: {d['tab']}（{d['monday']} 週）")
    head = "商品        " + " ".join(f"{x['label']:>6}" for x in d["days"])
    print(head)
    for p in d["products"]:
        cells = " ".join(f"{('' if v is None else int(v)):>6}" for v in p["actual"])
        print(f"{p['name']:<10}{cells}")
    print("回転数(実)  " + " ".join(f"{('' if v is None else v):>6}" for v in d["kaiten"]))
    print("\n読み取りOK: サービスアカウント経由で今週分を取得できました。")
