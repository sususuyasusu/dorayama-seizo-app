#!/usr/bin/env python3
"""卵発注ナビの読み取り層（アプリ用に項目を解釈して構造化）。
卵の情報を1か所に集約しつつ、表の丸写しではなくカード表示できる形で返す：
 - days: 日別（卵黄/卵白それぞれ 在庫・必要・届く・在庫の見通し）
 - batches: 翌週の正味発注数（配送便別 卵黄/卵白の回転・kg・袋）
 - rules: 見方・ルール（締切ルール、記号の意味、LINE報告フォーマット 等）
週ごとに列構成が違っても、見出し語からマッピングして拾う。"""
import datetime
import math as _math
import re as _re

import data_layer

# 曜日文字→回転数配列インデックス（月=0…日=6）
_DAY_IDX = {"月": 0, "火": 1, "水": 2, "木": 3, "金": 4, "土": 5, "日": 6}


def _next_week_kaiten(cur_tab):
    """cur_tab（例: '0622'）の翌週タブから行39の回転数を予定・実績で返す。
    見つからなければ (None, None)。
    スプレッドシートのバッチ発注数式は row39_予定 − 繰り越し在庫 で計算している。
    Python側で row39_実績 に差し替えるため、両方を返す。"""
    from datetime import date, timedelta
    m = _re.match(r'^(\d{1,2})(\d{2})$', str(cur_tab))
    if not m:
        return None, None
    mon_month, mon_day = int(m.group(1)), int(m.group(2))
    today = date.today()
    for year in [today.year, today.year - 1]:
        try:
            cur_monday = date(year, mon_month, mon_day)
            break
        except ValueError:
            continue
    else:
        return None, None
    next_mon = cur_monday + timedelta(days=7)
    cands = [f"{next_mon.month:02d}{next_mon.day:02d}", f"{next_mon.month}{next_mon.day:02d}"]
    sh = data_layer._spreadsheet()
    tab_map = {w.title: w for w in sh.worksheets()}
    next_ws = next((tab_map[c] for c in cands if c in tab_map), None)
    if not next_ws:
        return None, None
    vals = data_layer.cached_values(next_ws)

    def gv(r, c):
        try:
            return float(str(vals[r - 1][c]).replace(",", "").strip())
        except Exception:
            return 0.0

    kp = [gv(39, c) for c in range(1, 8)]    # 予定 B-H → 月火水木金土日
    ka = [gv(39, c) for c in range(21, 28)]   # 実績 V-AB → 月火水木金土日
    return kp, ka


def _batch_day_indices(name):
    """'木曜便 (金・土)' → [4, 5] のように配送便の担当曜日インデックスを返す"""
    m = _re.search(r'\(([^)]+)\)', name)
    if not m:
        return []
    return [_DAY_IDX[d] for d in m.group(1).split("・") if d in _DAY_IDX]


def _apply_actual_kaiten(batches, cur_tab):
    """バッチリストの回転・kg・袋を翌週実績回転数ベースに補正する。
    計算式: 実績ベース発注 = スプレッドシート値 + (実績合計 − 予定合計)
    これにより繰り越し在庫はスプレッドシートの計算をそのまま継承する。"""
    kp, ka = _next_week_kaiten(cur_tab)
    if not kp or not ka:
        return  # 翌週タブがなければ補正しない

    def _n(s):
        try:
            return float(str(s).replace(",", "").strip())
        except Exception:
            return 0.0

    def _fmt(v):
        return str(int(v)) if v == int(v) else f"{v:.1f}"

    def _bags(kg, per_rot, thr):
        """5kg袋に丸める。満杯ぶんは切り捨て、端数は閾値回転(卵黄7/卵白4)以上で初めて+1袋。
        端数が閾値未満＝『発注しない』ルールに一致（例: 卵黄6.4kg=1袋, 2.4kg=0袋）。"""
        if kg <= 0:
            return 0
        full = int(kg // 5)
        rem_rot = (kg - full * 5) / per_rot
        return full + (1 if rem_rot >= thr - 1e-9 else 0)

    for b in batches:
        idxs = _batch_day_indices(b["name"])
        if not idxs:
            continue
        plan_sum = sum(kp[i] for i in idxs)
        act_sum = sum(ka[i] for i in idxs)
        if act_sum == 0:
            continue  # 実績未入力の便はそのまま（予定ベースを維持）
        delta = act_sum - plan_sum

        yk = max(0.0, _n(b["yolkKai"]) + delta)
        wk = max(0.0, _n(b["whiteKai"]) + delta)
        ykg = yk * 0.4
        wkg = wk * 0.75
        b["yolkKai"] = _fmt(yk)
        b["yolkKg"] = _fmt(ykg)
        b["yolkBags"] = str(_bags(ykg, 0.4, 7))   # 卵黄: 端数7回転(2.8kg)以上で+1袋
        b["whiteKai"] = _fmt(wk)
        b["whiteKg"] = _fmt(wkg)
        b["whiteBags"] = str(_bags(wkg, 0.75, 4))  # 卵白: 端数4回転(3.0kg)以上で+1袋

# 過不足の判断を表す行頭記号（🔴追加/🔵減量/🟢/✅変更不要/🛒新規/🚨⚠️在庫切れ警告）
_ACTION_PREFIXES = ("🔴", "🔵", "🟢", "🚨", "⚠️", "✅", "🛒")


def _parse_date(s):
    """'2026/06/24' などを date に。失敗時 None。"""
    for fmt in ("%Y/%m/%d", "%Y-%m-%d"):
        try:
            return datetime.datetime.strptime(str(s).strip(), fmt).date()
        except ValueError:
            continue
    return None


def _future_safe_todo(todo, day_date, today):
    """まだ来ていない日（未来日）のやることは、過不足の指示（🔴+N回転 等）を伏せ、
    締切などの案内行だけ残す。当日・過去日はそのまま。
    在庫はその日を迎えないと確定しないため、未来日に断定的な発注数を出さない。"""
    if not todo or day_date is None or day_date <= today:
        return todo
    kept, dropped = [], False
    for ln in todo.split("\n"):
        if ln.strip().startswith(_ACTION_PREFIXES):
            dropped = True
            continue
        kept.append(ln)
    if not dropped:
        return todo  # 案内のみ → そのまま
    kept = [k for k in kept if k.strip()]
    kept.append("🗓 具体的な数量は当日に表示されます")
    return "\n".join(kept)


def _sections(cells):
    """テキスト群を「■/【/📖 見出し」で章立てに整理。各章 {header, lines}。"""
    secs = []
    cur = None
    for t in cells:
        for raw in str(t).split("\n"):
            ln = raw.strip()
            if not ln:
                continue
            if ln[0] in "■【" or ln.startswith("📖"):
                cur = {"header": ln, "lines": []}
                secs.append(cur)
            else:
                if cur is None:
                    cur = {"header": "", "lines": []}
                    secs.append(cur)
                cur["lines"].append(ln)
    return secs


def get_egg_nav(tab=None):
    ws = data_layer.open_ws(tab)
    vals = data_layer.cached_values(ws)

    def g(r, c):
        return vals[r - 1][c] if r - 1 < len(vals) and c < len(vals[r - 1]) else ""

    title = str(g(1, 40)).strip()
    note = str(g(2, 40)).strip()

    # 日別テーブルの見出し行（AO列に「日付」）
    hrow = None
    for r in range(3, 10):
        if "日付" in g(r, 40):
            hrow = r
            break

    col = {}
    if hrow:
        for c in range(40, 56):
            h = g(hrow, c).replace("\n", "").strip()
            if not h:
                continue
            yolk = "卵黄" in h
            white = "卵白" in h
            if h == "日付" and "date" not in col:
                col["date"] = c
            elif h == "曜日" and "wd" not in col:
                col["wd"] = c
            elif "在庫" in h and "見通し" not in h and yolk and "sy" not in col:
                col["sy"] = c
            elif "在庫" in h and "見通し" not in h and white and "sw" not in col:
                col["sw"] = c
            elif "必要" in h and yolk and "ny" not in col:
                col["ny"] = c
            elif "必要" in h and white and "nw" not in col:
                col["nw"] = c
            elif "届く" in h and yolk and "iy" not in col:
                col["iy"] = c
            elif "届く" in h and white and "iw" not in col:
                col["iw"] = c
            elif "見通し" in h and yolk and "oy" not in col:
                col["oy"] = c
            elif "見通し" in h and white and "ow" not in col:
                col["ow"] = c
            elif "やること" in h and yolk and "ty" not in col:
                col["ty"] = c
            elif "やること" in h and white and "tw" not in col:
                col["tw"] = c

    days = []
    today = datetime.date.today()
    if hrow and "date" in col:
        for r in range(hrow + 1, hrow + 9):
            dt = g(r, col["date"]).strip()
            if not dt:
                break

            def v(k):
                return g(r, col[k]).strip() if k in col else ""

            d_date = _parse_date(dt)
            yt = _future_safe_todo(v("ty"), d_date, today)
            wt = _future_safe_todo(v("tw"), d_date, today)
            # その日の製造回転（実績）= row39 の 実績側 V-AB（月〜日）。卵の必要量の元。
            _pcol = {"月": 21, "火": 22, "水": 23, "木": 24, "金": 25, "土": 26, "日": 27}.get(v("wd"))
            prod = g(39, _pcol).strip() if _pcol is not None else ""
            days.append({
                "row": r, "date": dt, "wd": v("wd"), "prod": prod,
                "yolk": {"stock": v("sy"), "need": v("ny"), "incoming": v("iy"), "outlook": v("oy"), "todo": yt},
                "white": {"stock": v("sw"), "need": v("nw"), "incoming": v("iw"), "outlook": v("ow"), "todo": wt},
            })

    syCol = (col["sy"] + 1) if "sy" in col else 0   # 在庫卵黄 列（1始まり・編集用）
    swCol = (col["sw"] + 1) if "sw" in col else 0

    # 翌週の正味発注数（配送便別）
    batches = []
    brow = None
    for r in range((hrow or 5), 24):
        if "配送便" in g(r, 40):
            brow = r
            break
    if brow:
        for r in range(brow + 1, brow + 6):
            nm = g(r, 40).strip()
            if not nm:
                break
            if "配送便" in nm:
                continue
            if not any(g(r, c).strip() for c in range(41, 47)):
                break
            batches.append({
                "name": nm,
                "yolkKai": g(r, 41), "yolkKg": g(r, 42), "yolkBags": g(r, 43),
                "whiteKai": g(r, 44), "whiteKg": g(r, 45), "whiteBags": g(r, 46),
            })

    # スプレッドシートのバッチ数式は予定ベース → 実績回転数で上書き補正
    _apply_actual_kaiten(batches, ws.title)

    # この表の見方（AW/AX列）= 各行 AW→AX の順に読むと表示順になる
    guide_cells = []
    for r in range(14, 35):
        guide_cells.append(g(r, 48))  # AW
        guide_cells.append(g(r, 49))  # AX
    guide = _sections(guide_cells)
    guide = [s for s in guide if not (s["header"].startswith("📖") and not s["lines"])]

    # 卵発注ルール（AO列・行20以降）
    rules = _sections([g(r, 40) for r in range(20, 35)])

    ruleY = g(hrow, col["ty"]).replace("\n", " ").strip() if (hrow and "ty" in col) else ""
    ruleW = g(hrow, col["tw"]).replace("\n", " ").strip() if (hrow and "tw" in col) else ""

    return {"tab": ws.title, "title": title, "note": note,
            "syCol": syCol, "swCol": swCol, "ruleY": ruleY, "ruleW": ruleW,
            "days": days, "batches": batches, "guide": guide, "rules": rules}
