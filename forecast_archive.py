#!/usr/bin/env python3
"""製造数予測のアーカイブ（学習データの置き場）。製造表スプレッドシートの裏タブに溜める。

  _fc_daily   … 会場×日付ごとの実績（販売数・夜の在庫・売上・客数・味別内訳・天気）。毎日増える。
  _fc_log     … その日に出した予測の控え（あとで「予測 対 実績」を採点するため）。毎日増える。
  _fc_events  … 特別日（催し・除外する日）。人が書き足す。

実績の元は売上日報フォーム。ここへは「取り込み（sync）」で写す:
  - フォームに新しい日があれば追記、内容が変わっていれば上書き
  - ただし「固定」列に何か書いてある行は上書きしない（会場POSや手修正を守る）
  - 天気が空の過去日には、その会場の天気の実績を埋める

フォームは読むだけ（書き込まない）。製造表の既存タブには触れない。
"""
import re
import threading
import time
from datetime import date, datetime, timedelta, timezone

import data_layer
import forecast_calendar as cal
import forecast_engine as fe
import forecast_weather as fw

JST = timezone(timedelta(hours=9))
FORM_SHEET_ID = "1v0w_oAmbTmw3t9oOhCFVWv87ysm2gpOnNQbGAlALpIc"
FORM_GID = 816782526
TAB_DAILY, TAB_LOG, TAB_EVENTS = "_fc_daily", "_fc_log", "_fc_events"

_QTY_HEADERS = []
for _p in fe.PRODUCTS:
    _QTY_HEADERS += [f"{_p} 販売", f"{_p} 夜在庫"]
DAILY_HEADERS = (["日付", "会場", "曜日", "売上(税抜)", "売上(税込)", "客数"] + _QTY_HEADERS +
                 ["味別内訳", "天気", "最高気温", "最低気温", "雨量mm(営業時間)", "雨の時間数", "雲量%",
                  "暦", "データ元", "固定", "注意", "所感", "日報の提出日時", "更新日時"])
LOG_HEADERS = (["予測した日", "会場", "対象日", "何日先", "天気の前提", "最高気温", "雨量mm"] +
               [f"{p} 予測" for p in fe.PRODUCTS] + ["売上 予測", "客数 予測"] +
               [f"{p} 納品" for p in fe.PRODUCTS] + ["記録日時"])
EVENT_HEADERS = ["日付", "会場", "名前", "種類", "倍率(任意)", "メモ", "登録日時"]
COL = {h: i for i, h in enumerate(DAILY_HEADERS)}

_lock = threading.Lock()
_cache = {}
status = {"lastSync": None, "lastResult": None, "lastError": None}


def _now():
    return datetime.now(JST)


def _retry(fn, tries=4, wait=20):
    """Sheets APIの分間上限(429)や一時的な失敗は、待って再試行する。"""
    for i in range(tries):
        try:
            return fn()
        except Exception as e:
            msg = str(e)
            transient = any(s in msg for s in ("429", "500", "502", "503", "timed out", "Connection"))
            if transient and i < tries - 1:
                time.sleep(wait * (i + 1))
                continue
            raise


def _tab(name, headers, rows=2000):
    """裏タブを取る。無ければ見出しつきで作る。見出しが足りなければ右に足す。"""
    ws = data_layer.get_ws(name)
    if ws is None:
        sh = data_layer._spreadsheet()
        ws = _retry(lambda: sh.add_worksheet(title=name, rows=rows, cols=len(headers)))
        _retry(lambda: ws.update(range_name="A1", values=[headers], value_input_option="RAW"))
        try:
            ws.freeze(rows=1)
        except Exception:
            pass
        data_layer.worksheets(refresh=True)
        return ws
    return ws


def _values(name, headers, ttl=20.0):
    key = ("vals", name)
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    ws = _tab(name, headers)
    vals = _retry(lambda: ws.get_all_values())
    if not vals or [str(x).strip() for x in vals[0][:len(headers)]] != headers:
        if vals and any(str(x).strip() for x in vals[0]):
            raise RuntimeError(f"タブ {name} の見出しが想定と違います（手で列を変えた可能性）。"
                               f"1行目を確認してください")
        _retry(lambda: ws.update(range_name="A1", values=[headers], value_input_option="RAW"))
        vals = [headers] + (vals[1:] if vals else [])
    _cache[key] = (time.time(), vals)
    return vals


def _invalidate(name):
    _cache.pop(("vals", name), None)


# ───────────────────────── 日報フォーム（読むだけ） ─────────────────────────

def read_form_rows(ttl=300.0):
    hit = _cache.get("form")
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    ws = _cache.get("formWs")
    if ws is None:
        ss = _retry(lambda: data_layer._client().open_by_key(FORM_SHEET_ID))
        ws = next((w for w in _retry(lambda: ss.worksheets()) if w.id == FORM_GID), None)
        if ws is None:
            raise RuntimeError("売上日報フォームの回答タブが見つかりません")
        _cache["formWs"] = ws
    rows = _retry(lambda: ws.get_all_values())
    if len(rows) < 2:
        raise RuntimeError("売上日報フォームが空で返ってきました（取得失敗の疑い）")
    head = "".join(rows[0])
    if "店舗名" not in head or "黒どら" not in head:
        raise RuntimeError("売上日報フォームの見出しが想定と違います（列が変わった可能性）")
    _cache["form"] = (time.time(), rows)
    return rows


# ───────────────────────── 行 ⇄ 記録 ─────────────────────────

def flavors_to_text(flavors):
    parts = []
    for p in fe.PRODUCTS:
        fl = (flavors or {}).get(p)
        if fl:
            parts.append(f"{p}[" + "・".join(f"{k}{int(v)}" for k, v in fl.items()) + "]")
    return " ".join(parts)


def text_to_flavors(text):
    out = {}
    for prod, body in re.findall(r"(\S+?)\[(.*?)\]", str(text or "")):
        fl = {}
        for part in body.split("・"):
            m = re.match(r"^(.+?)(\d+)$", part.strip())
            if m:
                fl[m.group(1)] = int(m.group(2))
        if fl:
            out[prod] = fl
    return out


def _calendar_text(d):
    c = cal.describe(d)
    return " / ".join(x for x in (c["holiday"], c["period"], c["vacation"]) if x)


def _cell(v):
    return "" if v is None else v


def record_to_row(rec, wx=None, flags=None, locked="", now=None):
    d = rec["date"]
    row = [d.isoformat(), rec["venue"], cal.WD_JA[d.weekday()],
           _cell(rec.get("salesExcl")), _cell(rec.get("salesIncl")), _cell(rec.get("customers"))]
    for p in fe.PRODUCTS:
        row += [_cell(rec["qty"].get(p)), _cell(rec["stock"].get(p))]
    row.append(flavors_to_text(rec.get("flavors")))
    w = wx or {}
    row += [w.get("label", ""), _cell(w.get("tmax")), _cell(w.get("tmin")), _cell(w.get("rain")),
            _cell(w.get("rainHours")) if w else "", _cell(w.get("cloud"))]
    row += [_calendar_text(d), rec.get("source", "日報フォーム"), locked,
            " / ".join(flags or []), (rec.get("comment") or "")[:500],
            rec.get("submittedAt", ""), (now or _now()).strftime("%Y-%m-%d %H:%M")]
    return row


def row_to_record(row):
    row = list(row) + [""] * (len(DAILY_HEADERS) - len(row))
    d = fe.parse_date(row[COL["日付"]])
    if d is None or not str(row[COL["会場"]]).strip():
        return None
    rec = {"date": d, "venue": str(row[COL["会場"]]).strip(),
           "salesExcl": fe.to_number(row[COL["売上(税抜)"]]), "salesIncl": fe.to_number(row[COL["売上(税込)"]]),
           "customers": fe.to_number(row[COL["客数"]]), "qty": {}, "stock": {},
           "flavors": text_to_flavors(row[COL["味別内訳"]]), "stockFlavors": {},
           "comment": row[COL["所感"]], "source": row[COL["データ元"]] or "日報フォーム",
           "locked": bool(str(row[COL["固定"]]).strip()), "flagsText": row[COL["注意"]],
           "submittedAt": row[COL["日報の提出日時"]], "updatedAt": str(row[COL["更新日時"]]).strip()}
    for p in fe.PRODUCTS:
        rec["qty"][p] = fe.to_number(row[COL[f"{p} 販売"]])
        rec["stock"][p] = fe.to_number(row[COL[f"{p} 夜在庫"]])
    tmax = fe.to_number(row[COL["最高気温"]])
    rec["weather"] = None
    if tmax is not None:
        label = row[COL["天気"]]
        rec["weather"] = {"label": label, "emoji": fw.EMOJI.get(label, ""), "tmax": tmax,
                          "tmin": fe.to_number(row[COL["最低気温"]]),
                          "rain": fe.to_number(row[COL["雨量mm(営業時間)"]]) or 0.0,
                          "rainHours": fe.to_number(row[COL["雨の時間数"]]) or 0,
                          "cloud": fe.to_number(row[COL["雲量%"]])}
    return rec


def load_daily(venue=None):
    """アーカイブの実績を読む。返り値: 記録のリスト（日付順）。"""
    vals = _values(TAB_DAILY, DAILY_HEADERS)
    out = []
    for i, row in enumerate(vals[1:], start=2):
        rec = row_to_record(row)
        if rec is None:
            continue
        rec["sheetRow"] = i
        if venue is None or rec["venue"] == venue:
            out.append(rec)
    out.sort(key=lambda r: (r["venue"], r["date"]))
    return out


# ───────────────────────── 取り込み（フォーム → アーカイブ） ─────────────────────────

_SYNC_COLS = (["売上(税抜)", "売上(税込)", "客数"] + _QTY_HEADERS + ["味別内訳", "所感", "日報の提出日時"])


def _norm(v):
    n = fe.to_number(v)
    return n if n is not None else str("" if v is None else v).strip()


def sync(today=None):
    """フォームの最新をアーカイブへ写し、天気の空欄を埋める。結果の要約を返す。
    フォームが読めない・空のときは例外（アーカイブには一切触れない）。"""
    with _lock:
        today = today or _now().date()
        rows = read_form_rows(ttl=60.0)
        recs, skipped = fe.clean_form_rows(rows)
        if not recs:
            raise RuntimeError("売上日報フォームから1件も読めませんでした（取得失敗の疑い）")
        _invalidate(TAB_DAILY)
        vals = _values(TAB_DAILY, DAILY_HEADERS, ttl=0.0)
        ws = _tab(TAB_DAILY, DAILY_HEADERS)
        existing = {}
        for i, row in enumerate(vals[1:], start=2):
            r = list(row) + [""] * (len(DAILY_HEADERS) - len(row))
            d = fe.parse_date(r[COL["日付"]])
            if d and str(r[COL["会場"]]).strip():
                existing[(str(r[COL["会場"]]).strip(), d)] = (i, r)

        # 個数×単価と売上の比（会場ごとの中央値）を、入力ミス検出の基準にする
        ratio_ref = {}
        by_venue = {}
        for r in recs:
            v = fe.qty_sales_ratio(r)
            if v:
                by_venue.setdefault(r["venue"], []).append(v)
        for v, xs in by_venue.items():
            xs.sort()
            ratio_ref[v] = xs[len(xs) // 2]

        now = _now()
        updates, appends = [], []
        added = changed = kept = 0
        for rec in recs:
            key = (rec["venue"], rec["date"])
            flags = fe.quality_flags(rec, ratio_ref.get(rec["venue"]))
            if key not in existing:
                appends.append((key, record_to_row(rec, None, flags, "", now)))
                added += 1
                continue
            i, old = existing[key]
            if str(old[COL["固定"]]).strip():
                kept += 1
                continue
            new = record_to_row(rec, None, flags, "", now)
            if any(_norm(old[COL[c]]) != _norm(new[COL[c]]) for c in _SYNC_COLS):
                merged = list(old)
                for c in _SYNC_COLS + ["注意", "データ元", "更新日時", "曜日", "暦"]:
                    merged[COL[c]] = new[COL[c]]
                updates.append((i, merged))
                existing[key] = (i, merged)
                changed += 1
        if appends:
            appends.sort(key=lambda x: (x[0][1], x[0][0]))
            _retry(lambda: ws.append_rows([r for _, r in appends], value_input_option="RAW",
                                          insert_data_option="INSERT_ROWS", table_range="A1"))
            # 追記した行が実際に何行目に入ったかは、読み直して確かめる（行番号を決め打ちしない）
            _invalidate(TAB_DAILY)
            vals = _values(TAB_DAILY, DAILY_HEADERS, ttl=0.0)
            pending = dict(updates)
            existing = {}
            for i, row in enumerate(vals[1:], start=2):
                r = list(row) + [""] * (len(DAILY_HEADERS) - len(row))
                d = fe.parse_date(r[COL["日付"]])
                if d and str(r[COL["会場"]]).strip():
                    existing[(str(r[COL["会場"]]).strip(), d)] = (i, pending.get(i, r))
            lost = [k for k, _ in appends if k not in existing]
            if lost:
                raise RuntimeError(f"アーカイブへの追記を確認できません（{len(lost)}件）。シートを確認してください")

        # 天気の空欄を埋める（過去日のみ）
        need = {}
        for (venue, d), (i, r) in existing.items():
            if d < today and not str(r[COL["最高気温"]]).strip() and venue in fw.VENUES:
                need.setdefault(venue, []).append(d)
        filled = 0
        wx_missing = []
        wx_errors = []
        for venue, ds in need.items():
            try:
                wx, miss = fw.actuals(venue, ds)
            except Exception as e:
                wx_errors.append(f"{venue}: {str(e)[:80]}")
                continue
            wx_missing += [f"{venue} {d}" for d in miss]
            for d in ds:
                w = wx.get(d.isoformat())
                if not w:
                    continue
                i, r = existing[(venue, d)]
                r = list(r)
                for h, k in (("天気", "label"), ("最高気温", "tmax"), ("最低気温", "tmin"),
                             ("雨量mm(営業時間)", "rain"), ("雨の時間数", "rainHours"), ("雲量%", "cloud")):
                    r[COL[h]] = _cell(w.get(k))
                existing[(venue, d)] = (i, r)
                updates = [(j, x) for j, x in updates if j != i] + [(i, r)]
                filled += 1
        if updates:
            last_col = data_layer.gspread.utils.rowcol_to_a1(1, len(DAILY_HEADERS)).rstrip("1")
            payload = [{"range": f"A{i}:{last_col}{i}", "values": [[_cell(x) for x in r[:len(DAILY_HEADERS)]]]}
                       for i, r in sorted(updates)]
            for k in range(0, len(payload), 200):
                chunk = payload[k:k + 200]
                _retry(lambda chunk=chunk: ws.batch_update(chunk, value_input_option="RAW"))
        _invalidate(TAB_DAILY)
        no_coords = sorted({v for (v, _d) in existing if v not in fw.VENUES})
        res = {"at": now.strftime("%Y-%m-%d %H:%M"), "formDays": len(recs), "added": added,
               "changed": changed, "keptLocked": kept, "weatherFilled": filled,
               "weatherMissing": wx_missing[:10], "weatherErrors": wx_errors,
               "skippedRows": skipped[:10], "venuesWithoutLocation": no_coords}
        status.update({"lastSync": res["at"], "lastResult": res, "lastError": None})
        return res


def upsert_fixed(records, source, note=""):
    """会場POSや手修正の実績を「固定」で書き込む（フォームの取り込みで上書きされない）。
    既にある日は販売数・売上・味別だけ差し替え、夜の在庫などフォーム由来の値は残す。
    「注意」欄は、確認が必要なときだけ書く（note。通常は空＝正しい数字に置き換わったので注意を消す）。"""
    with _lock:
        _invalidate(TAB_DAILY)
        vals = _values(TAB_DAILY, DAILY_HEADERS, ttl=0.0)
        ws = _tab(TAB_DAILY, DAILY_HEADERS)
        existing = {}
        for i, row in enumerate(vals[1:], start=2):
            r = list(row) + [""] * (len(DAILY_HEADERS) - len(row))
            d = fe.parse_date(r[COL["日付"]])
            if d:
                existing[(str(r[COL["会場"]]).strip(), d)] = (i, r)
        now = _now()
        updates, appends = [], []
        for rec in records:
            key = (rec["venue"], rec["date"])
            if key in existing:
                i, r = existing[key]
                r = list(r)
                if rec.get("salesExcl") is not None:
                    r[COL["売上(税抜)"]] = rec["salesExcl"]
                for p in fe.PRODUCTS:
                    if rec["qty"].get(p) is not None:
                        r[COL[f"{p} 販売"]] = rec["qty"][p]
                if rec.get("flavors"):
                    r[COL["味別内訳"]] = flavors_to_text(rec["flavors"])
                r[COL["データ元"]] = source
                r[COL["固定"]] = "固定"
                r[COL["注意"]] = note
                r[COL["更新日時"]] = now.strftime("%Y-%m-%d %H:%M")
                updates.append((i, r))
            else:
                full = {"salesIncl": None, "customers": None, "stock": {}, "flavors": {}, "comment": ""}
                full.update(rec)
                full["source"] = source
                appends.append(record_to_row(full, None, [note] if note else [], "固定", now))
        if updates:
            last_col = data_layer.gspread.utils.rowcol_to_a1(1, len(DAILY_HEADERS)).rstrip("1")
            payload = [{"range": f"A{i}:{last_col}{i}", "values": [[_cell(x) for x in r[:len(DAILY_HEADERS)]]]}
                       for i, r in updates]
            _retry(lambda: ws.batch_update(payload, value_input_option="RAW"))
        if appends:
            _retry(lambda: ws.append_rows(appends, value_input_option="RAW",
                                          insert_data_option="INSERT_ROWS", table_range="A1"))
        _invalidate(TAB_DAILY)
        return {"updated": len(updates), "added": len(appends)}


# ───────────────────────── 特別日 ─────────────────────────

def load_events(venue):
    """返り値: {date: [{name, kind, factor, memo}]}（その会場ぶん＋会場が空欄の共通ぶん）"""
    vals = _values(TAB_EVENTS, EVENT_HEADERS, ttl=60.0)
    out = {}
    for row in vals[1:]:
        row = list(row) + [""] * (len(EVENT_HEADERS) - len(row))
        d = fe.parse_date(row[0])
        v = str(row[1]).strip()
        if d is None or (v and v != venue):
            continue
        name = str(row[2]).strip()
        if not name:
            continue
        kind = "除外" if "除外" in str(row[3]) else "特別日"
        factor = fe.to_number(row[4])
        if factor is not None and not (0.2 <= factor <= 5.0):
            factor = None                      # 倍率として不自然な値は使わない
        out.setdefault(d, []).append({"name": name, "kind": kind, "factor": factor, "memo": row[5]})
    return out


def add_events(rows):
    """特別日を追記。rows: [[日付, 会場, 名前, 種類, 倍率, メモ]]。同じ日付・会場・名前は足さない。"""
    with _lock:
        _invalidate(TAB_EVENTS)
        vals = _values(TAB_EVENTS, EVENT_HEADERS, ttl=0.0)
        have = {(str(r[0]).strip(), str(r[1]).strip(), str(r[2]).strip()) for r in vals[1:] if len(r) >= 3}
        now = _now().strftime("%Y-%m-%d %H:%M")
        new = [list(r) + [now] for r in rows
               if (str(r[0]).strip(), str(r[1]).strip(), str(r[2]).strip()) not in have]
        if new:
            ws = _tab(TAB_EVENTS, EVENT_HEADERS)
            _retry(lambda: ws.append_rows(new, value_input_option="RAW",
                                          insert_data_option="INSERT_ROWS", table_range="A1"))
            _invalidate(TAB_EVENTS)
        return len(new)


# ───────────────────────── 予測の控え ─────────────────────────

def load_log(venue):
    vals = _values(TAB_LOG, LOG_HEADERS, ttl=120.0)
    out = []
    for row in vals[1:]:
        row = list(row) + [""] * (len(LOG_HEADERS) - len(row))
        made, target = fe.parse_date(row[0]), fe.parse_date(row[2])
        if not made or not target or str(row[1]).strip() != venue:
            continue
        rec = {"made": made, "target": target, "horizon": fe.to_number(row[3]),
               "weather": row[4], "pred": {}, "delivery": {}}
        for k, p in enumerate(fe.PRODUCTS):
            rec["pred"][p] = fe.to_number(row[7 + k])
            rec["delivery"][p] = fe.to_number(row[7 + len(fe.PRODUCTS) + 2 + k])
        rec["pred"]["売上"] = fe.to_number(row[7 + len(fe.PRODUCTS)])
        rec["pred"]["客数"] = fe.to_number(row[7 + len(fe.PRODUCTS) + 1])
        out.append(rec)
    return out


def append_log(venue, made, rows):
    """その日の予測を控える。同じ「予測した日×会場」が既にあれば何もしない（1日1回）。"""
    with _lock:
        _invalidate(TAB_LOG)
        vals = _values(TAB_LOG, LOG_HEADERS, ttl=0.0)
        key = made.isoformat()
        if any(len(r) >= 2 and str(r[0]).strip() == key and str(r[1]).strip() == venue for r in vals[1:]):
            return 0
        ws = _tab(TAB_LOG, LOG_HEADERS, rows=5000)
        _retry(lambda: ws.append_rows(rows, value_input_option="RAW",
                                      insert_data_option="INSERT_ROWS", table_range="A1"))
        _invalidate(TAB_LOG)
        return len(rows)


def sheet_links():
    """アーカイブの各タブを開くURL。"""
    out = {}
    for name in (TAB_DAILY, TAB_LOG, TAB_EVENTS):
        ws = data_layer.get_ws(name)
        if ws is not None:
            out[name] = f"https://docs.google.com/spreadsheets/d/{data_layer.SHEET_ID}/edit#gid={ws.id}"
    return out
