#!/usr/bin/env python3
"""どら山 製造表アプリ — データ層（読み取り）
サービスアカウントで「月～日製造表」から今週の店舗用データ（予定/実績/回転数）を取得する。
裏の計算はスプレッドシートに任せ、アプリはここ経由で読み書きする。"""
import os
import json
import re
import time
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


# === 読み取り削減キャッシュ（Google Sheets APIの分間上限=429対策） ===
_SH = None            # Spreadsheet object（メタ情報の読み直しを避ける）
_VC = {}              # {タブ名: (取得時刻, 全セル値)}
_VC_TTL = 8.0         # 秒。同一画面の複数取得や連打はこの間1回の読み取りを共有


def _spreadsheet(refresh=False):
    global _SH
    if _SH is None or refresh:
        _SH = _client().open_by_key(SHEET_ID)
    return _SH


def cached_values(ws):
    """ワークシートの全セルを数秒キャッシュ。1画面で複数回・複数エンドポイントが
    同じ週タブを読んでも、APIへの読み取りは1回で済む。"""
    t = ws.title
    now = time.time()
    hit = _VC.get(t)
    if hit and now - hit[0] < _VC_TTL:
        return hit[1]
    vals = ws.get_all_values()
    _VC[t] = (now, vals)
    return vals


def invalidate(tab=None):
    """書き込み後に呼ぶ＝次の読み取りで最新（再計算後）を取り直す。"""
    if tab is None:
        _VC.clear()
    else:
        _VC.pop(tab, None)


def current_week_tab(today=None):
    today = today or date.today()
    monday = today - timedelta(days=today.weekday())
    cands = [f"{monday.month:02d}{monday.day:02d}", f"{monday.month}{monday.day:02d}",
             f"{monday.month:02d}{monday.day}"]
    return monday, cands


def open_ws(tab=None, today=None):
    """指定タブ（週）を開く。未指定なら今週タブ。Spreadsheet objectは使い回す。"""
    cands = [tab] if tab else current_week_tab(today)[1]
    for attempt in (0, 1):
        sh = _spreadsheet(refresh=(attempt == 1))
        for c in cands:
            try:
                return sh.worksheet(c)
            except gspread.WorksheetNotFound:
                continue
    raise RuntimeError(f"タブが見つからない（候補 {cands}）")


def list_tabs():
    """週タブ名の一覧（古い順）。アプリ用の内部タブ _app_made は除く。"""
    return [w.title for w in _spreadsheet().worksheets() if w.title != "_app_made"]


def list_gids():
    """週タブ名→シート内部番号(gid) の一覧。全データ画面の週切替で使う。"""
    return [[w.title, w.id] for w in _spreadsheet().worksheets() if w.title != "_app_made"]


def get_raw(tab=None):
    """指定週タブの全セル（行×列）をそのまま返す＝もれなく全表示用。"""
    ws = open_ws(tab)
    return {"tab": ws.title, "values": cached_values(ws)}


def set_cell(tab, row, col, value):
    """全データ画面の手打ち保存：指定セルにそのまま書き込む（USER_ENTERED＝シートに打つのと同じ挙動）。"""
    ws = open_ws(tab)
    a1 = gspread.utils.rowcol_to_a1(int(row), int(col))
    ws.update(range_name=a1, values=[[value]], value_input_option="USER_ENTERED")
    invalidate(ws.title)  # 次の読み取りで再計算後の最新を取り直す
    return {"ok": True, "tab": ws.title, "a1": a1}


def _md(s):
    m = re.findall(r"\d+", str(s))
    return f"{int(m[0])}/{int(m[1])}" if len(m) >= 2 else ""


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


PRODUCTS_SET = ["黒どら", "あんバター", "白どら", "旬どら", "生", "生どら", "皮4枚セット", "皮だけ（パック）"]


def get_week_blocks(tab=None, today=None):
    """指定週タブの全ブロック（各催事＋店舗用）を、見出し（カテゴリー行）から動的に検出して返す。
    予定(B..H)と実績(V..AB)は同じ行に並ぶので、商品行ごとに両方読む。曜日ラベルはシートの日付から。"""
    ws = open_ws(tab, today)
    vals = cached_values(ws)

    def cell(r, c):
        return vals[r - 1][c] if r - 1 < len(vals) and c < len(vals[r - 1]) else ""

    def num(s):
        s = str(s).replace(",", "").strip()
        try:
            return float(s) if s not in ("", "-") else None
        except ValueError:
            return None

    daydates = None
    blocks = []
    cur = None
    for r in range(1, 35):
        a = cell(r, 0).strip()
        s = cell(r, 18).strip()
        if s == "カテゴリー" and a:
            if daydates is None:
                daydates = [_md(cell(r, c)) for c in PLAN_COLS]
            cur = {"name": a, "category": "", "products": []}
            blocks.append(cur)
            continue
        if cur is not None and a in PRODUCTS_SET:
            if not cur["category"]:
                cur["category"] = "店舗用" if cur["name"] == "店舗用" else "催事用"
            cur["products"].append({
                "name": a,
                "row": r,   # シート上の行番号（予算の書き戻し先）
                "plan": [num(cell(r, c)) for c in PLAN_COLS],
                "actual": [num(cell(r, c)) for c in ACT_COLS],
            })
    blocks = [b for b in blocks if b["products"]]

    days = [{"label": (f"{WEEKDAYS[i]}{daydates[i]}" if daydates and daydates[i] else WEEKDAYS[i]),
             "date": (daydates[i] if daydates else "")} for i in range(7)]
    kaiten = [num(cell(38, c)) for c in ACT_COLS]
    return {"tab": ws.title, "gid": ws.id, "days": days, "blocks": blocks, "kaiten": kaiten}


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
