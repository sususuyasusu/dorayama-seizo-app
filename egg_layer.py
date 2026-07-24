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


def _kaiten_row(vals):
    """A列から「回転数（切上げ）」ラベルの行(1始まり)を探す。
    週タブは催事ブロックの増減で集計行がズレる(例: 0727は39→50行)ため、行番号決め打ちは禁止。"""
    for r, row in enumerate(vals):
        if row and str(row[0]).strip().startswith("回転数（切上げ）"):
            return r + 1
    return 39


def _next_week_kaiten(cur_tab):
    """cur_tab（例: '0622'）の翌週タブから行39の回転数を予定・実績、および現在の発注済(届く)を返す。
    見つからなければ (None, None, None)。
    スプレッドシートのバッチ発注数式は row39_予定 − 繰り越し在庫 で計算している。
    Python側で row39_実績 に差し替えるため両方を返す。3つ目は便別の発注済(届く)回転。"""
    from datetime import date, timedelta
    m = _re.match(r'^(\d{1,2})(\d{2})$', str(cur_tab))
    if not m:
        return None, None, None
    mon_month, mon_day = int(m.group(1)), int(m.group(2))
    today = date.today()
    for year in [today.year, today.year - 1]:
        try:
            cur_monday = date(year, mon_month, mon_day)
            break
        except ValueError:
            continue
    else:
        return None, None, None
    next_mon = cur_monday + timedelta(days=7)
    cands = [f"{next_mon.month:02d}{next_mon.day:02d}", f"{next_mon.month}{next_mon.day:02d}"]
    sh = data_layer._spreadsheet()
    tab_map = {w.title: w for w in sh.worksheets()}
    next_ws = next((tab_map[c] for c in cands if c in tab_map), None)
    if not next_ws:
        return None, None, None
    vals = data_layer.cached_values(next_ws)

    def gv(r, c):
        try:
            return float(str(vals[r - 1][c]).replace(",", "").strip())
        except Exception:
            return 0.0

    krow = _kaiten_row(vals)                    # 集計行はタブごとにズレるためラベルで特定
    kp = [gv(krow, c) for c in range(1, 8)]     # 予定 B-H → 月火水木金土日
    ka = [gv(krow, c) for c in range(21, 28)]   # 実績 V-AB → 月火水木金土日
    # 発注済(届く) AU=index46(卵黄)/AV=index47(卵白)。火=row7/木=row9/土=row11。
    incoming = {"火": (gv(7, 46), gv(7, 47)), "木": (gv(9, 46), gv(9, 47)), "土": (gv(11, 46), gv(11, 47))}
    return kp, ka, incoming


def _batch_day_indices(name):
    """'木曜便 (金・土)' → [4, 5] のように配送便の担当曜日インデックスを返す"""
    m = _re.search(r'\(([^)]+)\)', name)
    if not m:
        return []
    return [_DAY_IDX[d] for d in m.group(1).split("・") if d in _DAY_IDX]


def _apply_actual_kaiten(batches, cur_tab):
    """翌週正味発注数の kg・袋数を丸めルールで再計算する。
    シートの回転数は 2026-07-08 に数式を予定(B-H)→実績(V-AB)へ是正済みなので、
    ここでの差分補正(旧: +実績合計−予定合計)は廃止。旧補正は土曜便で誤った曜日
    (翌週の月火。正しくは翌々週の月火)を使い、土曜便だけ大きくズレる原因だった。"""

    def _n(s):
        try:
            return float(str(s).replace(",", "").strip())
        except Exception:
            return 0.0

    def _fmt(v):
        return str(int(v)) if v == int(v) else f"{v:.1f}"

    for b in batches:
        yk = max(0.0, _n(b["yolkKai"]))
        wk = max(0.0, _n(b["whiteKai"]))
        ykg = yk * 0.4
        wkg = wk * 0.75
        b["yolkKai"] = _fmt(yk)
        b["yolkKg"] = _fmt(ykg)
        b["yolkBags"] = str(_bags(ykg, 0.4, YOLK_BAG_THR))    # 端数7回転以上で+1袋
        b["whiteKai"] = _fmt(wk)
        b["whiteKg"] = _fmt(wkg)
        b["whiteBags"] = str(_bags(wkg, 0.75, WHITE_BAG_THR))  # 端数4回転以上で+1袋

# 袋数の丸めルール（画面ヘッダーの表記と一致）:
#  端数（5kg袋に満たない残り）が、卵黄は7回転以上・卵白は4回転以上で初めて+1袋。
#  卵黄6回転以下・卵白3回転以下の端数は切り捨て（発注しない）。1回転=卵黄400g/卵白750g。
YOLK_BAG_THR = 7    # 卵黄: 端数7回転(2.8kg)以上で+1袋
WHITE_BAG_THR = 4   # 卵白: 端数4回転(3.0kg)以上で+1袋


def _bags(kg, per_rot, thr):
    """kg を 5kg袋に丸める。満杯ぶんは切り捨て、端数は閾値回転(thr)以上で初めて+1袋。"""
    if kg <= 0:
        return 0
    full = int(kg // 5)
    rem_rot = (kg - full * 5) / per_rot
    return full + (1 if rem_rot >= thr - 1e-9 else 0)


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


# 締切日 → その締切で動かす便と、便がカバーする曜日
#  月: 木便(金土) / 水: 土便(日 + 翌週月火) / 土: 翌火便(翌週水木)
_DEADLINE_COVER = {
    "月": {"bin": "木便", "this": ["金", "土"], "next": [], "arrive": "木"},
    "水": {"bin": "土便", "this": ["日"], "next": [0, 1], "arrive": "土"},
    "土": {"bin": "翌火便", "this": [], "next": [2, 3], "arrive": None},
}


def _num(s):
    try:
        return float(str(s).replace(",", "").strip())
    except Exception:
        return 0.0


def _white_bags_up(rot):
    """卵白の回転数 → 5kg袋（1回転=750g、5kg袋に切り上げ）。おすすめ量に使う。"""
    return int(_math.ceil(rot * 0.75 / 5 - 1e-9))


def _white_bags_round(rot):
    """既存発注（届く回転）→ 何袋ぶんか（袋単位なので四捨五入）。"""
    return int(round(rot * 0.75 / 5))


def _white_coverage_line(wd, prod_by_wd, next_act, incoming_by_wd):
    """締切日の卵白おすすめ文を『便がカバーする製造回転を5kg袋に切り上げ』で作る。
    安全在庫の上乗せはしない。既存の発注(届く)が分かる便は差分を、
    分からない便(翌火便)は新規発注として返す。算出できなければ None。"""
    cfg = _DEADLINE_COVER.get(wd)
    if not cfg:
        return None
    rot = sum(prod_by_wd.get(d, 0.0) for d in cfg["this"])
    if cfg["next"]:
        if not next_act:
            return None  # 翌週タブが無ければ算出しない（従来表示のまま）
        rot += sum(next_act[i] for i in cfg["next"])
    if rot <= 0:
        return None
    target_bags = _bags(rot * 0.75, 0.75, WHITE_BAG_THR)   # 端数4回転以上で+1袋
    kg = target_bags * 5
    arrive = cfg["arrive"]
    existing = incoming_by_wd.get(arrive) if arrive else None
    if existing is None:
        return f"🛒 卵白 {target_bags}袋（{kg}kg）を発注"
    diff = target_bags - _white_bags_round(existing)
    if diff == 0:
        return f"✅ 卵白は {target_bags}袋（{kg}kg）でOK・変更不要"
    if diff > 0:
        return f"🔴 卵白を ＋{diff}袋 追加 → 合計{target_bags}袋（{kg}kg）"
    return f"🔵 卵白を {abs(diff)}袋 減らせる → 合計{target_bags}袋（{kg}kg）"


def _yolk_bags_up(kg):
    """卵黄のkg → 5kg袋（切り上げ）。"""
    return int(_math.ceil(kg / 5 - 1e-9))


def _yolk_bags_round(rot):
    """既存発注（届く回転）→ 何袋ぶんか（1回転=400g、四捨五入）。"""
    return int(round(rot * 0.4 / 5))


def _yolk_coverage_line(wd, prod_by_wd, next_act, incoming_by_wd, stock_by_wd):
    """締切日の卵黄おすすめ文を『便のカバー製造必要 −（その日の手持ち在庫）→5kg袋に切り上げ』で作る。
    卵黄は在庫が多いため在庫を差し引く（卵白との違い）。算出できなければ None。"""
    cfg = _DEADLINE_COVER.get(wd)
    if not cfg:
        return None
    rot = sum(prod_by_wd.get(d, 0.0) for d in cfg["this"])
    if cfg["next"]:
        if not next_act:
            return None
        rot += sum(next_act[i] for i in cfg["next"])
    if rot <= 0:
        return None
    need_kg = rot * 0.4
    stock_kg = stock_by_wd.get(wd, 0.0) * 0.4
    net_kg = max(0.0, need_kg - stock_kg)
    target_bags = _bags(net_kg, 0.4, YOLK_BAG_THR)   # 端数7回転以上で+1袋
    kg = target_bags * 5
    arrive = cfg["arrive"]
    existing = incoming_by_wd.get(arrive) if arrive else None
    if target_bags == 0 and (existing is None or existing <= 0):
        return "✅ 卵黄は在庫で足りる・発注不要"
    if existing is None:
        return f"🛒 卵黄 {target_bags}袋（{kg}kg）を発注"
    diff = target_bags - _yolk_bags_round(existing)
    if diff == 0:
        return f"✅ 卵黄は {target_bags}袋（{kg}kg）でOK・変更不要"
    if diff > 0:
        return f"🔴 卵黄を ＋{diff}袋 追加 → 合計{target_bags}袋（{kg}kg）"
    return f"🔵 卵黄を {abs(diff)}袋 減らせる → 合計{target_bags}袋（{kg}kg）"


def _rewrite_white_todo(todo, line):
    """やること文の過不足/警告行（🔴🔵🚨等）を、算出した1行に差し替える。卵黄・卵白共用。
    📦締切案内などの非・判断行は残す。"""
    keep = [l for l in todo.split("\n") if not l.strip().startswith(_ACTION_PREFIXES)]
    keep = [k for k in keep if k.strip()]
    keep.append(line)
    return "\n".join(keep)


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
    krow = _kaiten_row(vals)   # 「回転数（切上げ）」の行。タブごとにズレるためラベルで特定
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
            # その日の製造回転（実績）= 回転数（切上げ）行の実績側 V-AB（月〜日）。卵の必要量の元。
            _pcol = {"月": 21, "火": 22, "水": 23, "木": 24, "金": 25, "土": 26, "日": 27}.get(v("wd"))
            prod = g(krow, _pcol).strip() if _pcol is not None else ""
            days.append({
                "row": r, "date": dt, "wd": v("wd"), "prod": prod,
                "yolk": {"stock": v("sy"), "need": v("ny"), "incoming": v("iy"), "outlook": v("oy"), "todo": yt},
                "white": {"stock": v("sw"), "need": v("nw"), "incoming": v("iw"), "outlook": v("ow"), "todo": wt},
            })

    # 締切日（当日・過去）の卵白おすすめを「便のカバー製造回転→5kg袋に切り上げ」で算出し直す。
    # シートの予測ベース(+N回転)が過大に出る問題への対応。卵白のみ・未来日は従来どおり。
    if days:
        prod_by_wd = {d["wd"]: _num(d["prod"]) for d in days}
        inc_w = {d["wd"]: _num(d["white"]["incoming"]) for d in days}
        inc_y = {d["wd"]: _num(d["yolk"]["incoming"]) for d in days}
        stock_y = {d["wd"]: _num(d["yolk"]["stock"]) for d in days}
        _kp, next_act, _next_inc = _next_week_kaiten(ws.title)
        for d in days:
            d_date = _parse_date(d["date"])
            if d_date is None or d_date > today:
                continue   # 未来日は当日まで断定数量を出さない（従来方針）
            # 卵白: 便のカバー必要を5kg袋に切り上げ（在庫は引かない＝常にギリギリ運用）
            wtodo = d["white"]["todo"]
            if "📦" in wtodo:
                wl = _white_coverage_line(d["wd"], prod_by_wd, next_act, inc_w)
                if wl:
                    d["white"]["todo"] = _rewrite_white_todo(wtodo, wl)
            # 卵黄: カバー必要 − その日の在庫 を5kg袋に切り上げ（在庫が多いので差し引く）
            ytodo = d["yolk"]["todo"]
            if "📦" in ytodo:
                yl = _yolk_coverage_line(d["wd"], prod_by_wd, next_act, inc_y, stock_y)
                if yl:
                    d["yolk"]["todo"] = _rewrite_white_todo(ytodo, yl)

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

    # 各便の「発注済(届く)」= 翌週タブの届く回転を袋換算して付与（もともと発注した数の表示用）
    _kp2, _ka2, next_inc = _next_week_kaiten(ws.title)
    if next_inc:
        for b in batches:
            key = "火" if b["name"].startswith("火曜便") else ("木" if b["name"].startswith("木曜便") else ("土" if b["name"].startswith("土曜便") else None))
            if key and key in next_inc:
                yk, wk = next_inc[key]
                b["orderedYolkBags"] = _yolk_bags_round(yk)
                b["orderedWhiteBags"] = _white_bags_round(wk)

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
