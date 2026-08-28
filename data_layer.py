#!/usr/bin/env python3
"""どら山 製造表アプリ — データ層（読み取り）
サービスアカウントで「月～日製造表」から今週の店舗用データ（予定/実績/回転数）を取得する。
裏の計算はスプレッドシートに任せ、アプリはここ経由で読み書きする。"""
import os
import json
import re
import time
from datetime import date, datetime, timedelta, timezone
import gspread
from google.oauth2.service_account import Credentials

SHEET_ID = "1PRDhGP_4xiO_ZjJP3NB9Id3PmaPa5W7hNyrqFQ5EyqM"
CRED = os.environ.get(
    "DORAYAMA_SA_CRED",
    "/Users/suzuki3/Library/CloudStorage/Dropbox-Detale/D& W/どら山/過去/dw_budget_profit_sheets_automation/config/google_credentials.json",
)

# 店舗用ブロックの商品行。行番号は商品追加でズレるため既定値であり、実際は
# store_product_rows() が「店舗用」見出しの下から商品名で探して使う。
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
_VC_TTL = 45.0        # 秒。同一画面の複数取得や連打・週の行き来はこの間1回の読み取りを共有。
                      # 自分の編集は set_cell/made が invalidate するので即反映される（鮮度は保たれる）。
# タブ一覧のキャッシュ。gspreadは worksheet(名前) を呼ぶたびシート全体のメタ情報を
# 取りに行く。週タブが50枚あるので、これが表示の遅さと 429（読み取り上限）の主因だった。
_WS = {"t": 0.0, "map": None}
_WS_TTL = 300.0       # 秒。週タブが増えるのは週1回なので5分で十分。


def _spreadsheet(refresh=False):
    global _SH
    if _SH is None or refresh:
        _SH = _client().open_by_key(SHEET_ID)
    return _SH


def worksheets(refresh=False):
    """全タブを1回だけ取ってきて使い回す（メタ情報の取り直しをやめる）。"""
    now = time.time()
    if not refresh and _WS["map"] is not None and now - _WS["t"] < _WS_TTL:
        return _WS["map"]
    m = {w.title: w for w in _spreadsheet(refresh=refresh).worksheets()}
    _WS.update({"t": now, "map": m})
    return m


def get_ws(title, refresh=False):
    """タブ名からワークシートを取る。見つからなければ1度だけ取り直す。"""
    m = worksheets(refresh)
    if title in m:
        return m[title]
    if not refresh:
        return get_ws(title, refresh=True)
    return None


def store_product_rows(vals):
    """「店舗用」ブロックの商品名→行番号を、シートを読んで作る。
    商品追加(例: 抹茶)で行がズレても正しく追従するため、行番号は決め打ちしない。
    見つからない場合のみ既定値 STORE_PRODUCTS を返す。"""
    start = None
    for r, row in enumerate(vals, start=1):
        a = str(row[0]).strip() if row else ""
        if a == "店舗用":
            start = r
            break
    if start is None:
        return dict(STORE_PRODUCTS)
    found = {}
    for r in range(start + 1, min(start + 15, len(vals) + 1)):
        row = vals[r - 1] if r - 1 < len(vals) else []
        a = str(row[0]).strip() if row else ""
        if not a:
            continue
        if a.startswith(("【", "どら焼き合計", "回転数", "1回転", "合計回転")):
            break          # 集計ブロックに入ったら終了
        found[a] = r
    return found or dict(STORE_PRODUCTS)


def kaiten_row(vals, label="回転数（実数）"):
    """『回転数』行はタブごとに位置が違う（催事ブロックの増減でズレる）。
    古い週では33〜46行目に散らばっていたので、行番号の決め打ちは禁止。A列のラベルで探す。"""
    for r, row in enumerate(vals, start=1):
        a = str(row[0]).strip() if row else ""
        if a.startswith(label) or a.startswith("合計" + label):
            return r
    return 38


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
    # 日本時間の今日を使う（サーバー=UTCのdate.today()だと日本の早朝に前日=前週扱いになる）
    today = today or datetime.now(timezone(timedelta(hours=9))).date()
    monday = today - timedelta(days=today.weekday())
    cands = [f"{monday.month:02d}{monday.day:02d}", f"{monday.month}{monday.day:02d}",
             f"{monday.month:02d}{monday.day}"]
    return monday, cands


def open_ws(tab=None, today=None):
    """指定タブ（週）を開く。未指定なら今週タブ。Spreadsheet objectは使い回す。"""
    cands = [tab] if tab else current_week_tab(today)[1]
    for refresh in (False, True):
        m = worksheets(refresh)
        for c in cands:
            if c in m:
                return m[c]
    raise RuntimeError(f"タブが見つからない（候補 {cands}）")


def list_tabs():
    """週タブ名の一覧（古い順）。アプリ用の内部タブ(_app_made/_app_labor/_app_config 等)は除く。"""
    return [t for t in worksheets() if not t.startswith("_")]


def list_gids():
    """週タブ名→シート内部番号(gid) の一覧。全データ画面の週切替で使う。"""
    return [[t, w.id] for t, w in worksheets().items() if not t.startswith("_")]


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
    monday, cands = current_week_tab(today)
    m = worksheets()
    ws = next((m[c] for c in cands if c in m), None)
    if ws is None:
        m = worksheets(refresh=True)
        ws = next((m[c] for c in cands if c in m), None)
    if ws is None:
        raise RuntimeError(f"今週タブが見つからない（候補 {cands}）")
    vals = cached_values(ws)   # 同じ週タブを何度も読み直さない

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
    for name, row in store_product_rows(vals).items():
        plan = [num(cell(row, c)) for c in PLAN_COLS]
        act = [num(cell(row, c)) for c in ACT_COLS]
        products.append({"name": name, "plan": plan, "actual": act})

    kaiten = [num(cell(kaiten_row(vals), c)) for c in ACT_COLS]  # 回転数（実数）実績側。行はラベルで探す

    return {"tab": ws.title, "monday": monday.isoformat(), "days": days,
            "products": products, "kaiten": kaiten}


PRODUCTS_SET = ["黒どら", "あんバター", "白どら", "旬どら", "抹茶", "生", "生どら",
                "皮4枚セット", "皮だけ（パック）"]
# 数字が未入力でも画面に必ず出す商品（入力欄が無いと入力できないため）
ALWAYS_SHOW = {"抹茶"}


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
    for r in range(1, 45):        # 商品追加で行が増えるため余裕をもって走査
        a = cell(r, 0).strip()
        s = cell(r, 18).strip()
        if s == "カテゴリー" and a:
            if daydates is None:
                daydates = [_md(cell(r, c)) for c in PLAN_COLS]
            cur = {"name": a, "category": "", "products": []}
            blocks.append(cur)
            continue
        if cur is not None and a in PRODUCTS_SET:
            plan = [num(cell(r, c)) for c in PLAN_COLS]
            actual = [num(cell(r, c)) for c in ACT_COLS]
            # 予算が全て空/0 かつ 実績が全て空の行は、空の重複テンプレ行なので取り込まない。
            # ただし ALWAYS_SHOW の商品は数字が未入力でも入力欄として必ず出す（新商品対応）。
            empty_plan = all(v is None or v == 0 for v in plan)
            empty_actual = all(v is None for v in actual)
            existing = {p["name"] for p in cur["products"]}
            if a in existing:
                continue
            if (empty_plan and empty_actual) and a not in ALWAYS_SHOW:
                continue
            if not cur["category"]:
                cur["category"] = "店舗用" if cur["name"] == "店舗用" else "催事用"
            cur["products"].append({"name": a, "row": r, "plan": plan, "actual": actual})
    blocks = [b for b in blocks if b["products"]]

    days = [{"label": (f"{WEEKDAYS[i]}{daydates[i]}" if daydates and daydates[i] else WEEKDAYS[i]),
             "date": (daydates[i] if daydates else "")} for i in range(7)]
    kaiten = [num(cell(kaiten_row(vals), c)) for c in ACT_COLS]  # 行はラベルで探す
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
