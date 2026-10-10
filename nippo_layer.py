"""【どら山 日報】（社員LINEに製造担当が毎日投稿する日報）の読み取り・保存・集計。

LINEの文面 → parse_nippo() で項目に分解 → 経営管理シートのタブ「日報台帳」へ1日1行で保存（同じ日の再投稿は上書き）。
画面（/nippo）は、このタブを読んで日別カード・回転数の推移・提出状況を出す。

文面の形（2026-10-10 に始まった運用）:
    【どら山 日報】
    日付：10/10
    名前：タモガミ
    ① 当日の回転数        回転19
    ② 朝イチ製造数        黒：30 / あんバター：30 / 白：20 / 抹茶：20 / 旬：30 / 皮だけ:3 / 生どら：0
    ③ 品切れ              完売
    ④ お客様・商品について  （自由記述）
    ⑤ 明日の準備          砂糖：24 / 小麦：20 / ホイップ：31 / 卵：黄60白40
"""
from __future__ import annotations

import re
import time
import unicodedata
from datetime import date, datetime, timedelta, timezone

JST = timezone(timedelta(hours=9))
TAB = "日報台帳"
HEADER = ["日付", "名前", "回転数", "黒", "あんバター", "白", "抹茶", "旬", "皮だけ", "生どら",
          "品切れ", "お客様・商品について", "砂糖", "小麦", "ホイップ", "卵黄", "卵白", "卵（原文）", "受信時刻", "原文"]
PRODUCTS = ["黒", "あんバター", "白", "抹茶", "旬", "皮だけ", "生どら"]
PRODUCT_ALIASES = {
    "黒": "黒", "黒どら": "黒", "あんバター": "あんバター", "バター": "あんバター", "白": "白", "白どら": "白",
    "抹茶": "抹茶", "旬": "旬", "旬どら": "旬", "皮だけ": "皮だけ", "皮": "皮だけ", "生どら": "生どら", "生": "生どら",
}
MARK = "【どら山日報】"
START_DATE = date(2026, 10, 10)  # 日報の運用開始日（これより前の日は「未提出」とせず「開始前」）


def _norm(text):
    """全角数字・記号を半角に揃える（改行は残す）。"""
    # 見出しの ①〜⑤ は、半角化(NFKC)で ordinary な数字の「1」になって本文の数字と区別できなくなるため、先に § 付きの目印へ置き換える
    text = str(text or "").translate(str.maketrans({"①": "§1 ", "②": "§2 ", "③": "§3 ", "④": "§4 ", "⑤": "§5 "}))
    return unicodedata.normalize("NFKC", text).replace("\r\n", "\n").replace("\r", "\n")


def is_nippo(text):
    compact = re.sub(r"\s+", "", _norm(text))
    return MARK in compact[:30]


def _split_sections(body):
    """①〜⑤の見出しで本文を区切る。{1: 本文, 2: 本文, ...}（見出しの行自体は除く）。"""
    sections, current = {}, None
    for line in body.split("\n"):
        m = re.match(r"^\s*(?:§([1-5])|([1-5])\s*[\)）.．、])\s*(.*)$", line)
        if m:
            current = int(m.group(1) or m.group(2))
            sections[current] = []
            continue
        if current:
            sections[current].append(line)
    return {k: "\n".join(v).strip() for k, v in sections.items()}


def _clean_free(text):
    """自由記述から、記入のしかたの注意書き（※で始まる行）と空行を除く。"""
    lines = [l.strip() for l in text.split("\n") if l.strip() and not l.strip().startswith("※")]
    return "\n".join(lines)


def _int(text):
    m = re.search(r"\d+", text or "")
    return int(m.group()) if m else None


def parse_nippo(text, now=None):
    """日報の文面を項目に分解する。日報でなければ None。"""
    raw = _norm(text)
    if not is_nippo(raw):
        return None
    now = now or datetime.now(JST)
    m = re.search(r"日付\s*[:：]?\s*(\d{4}[/年-])?\s*(\d{1,2})\s*[/月.-]\s*(\d{1,2})", raw)
    if not m:
        return None
    month, day = int(m.group(2)), int(m.group(3))
    year = int(re.sub(r"\D", "", m.group(1))) if m.group(1) else now.year
    try:
        d = date(year, month, day)
    except ValueError:
        return None
    if not m.group(1) and (d - now.date()).days > 31:  # 年が省略され、未来すぎる日付は前年とみなす
        d = date(year - 1, month, day)
    n = re.search(r"名前\s*[:：]?\s*([^\n]+)", raw)
    name = n.group(1).strip() if n else ""
    sec = _split_sections(raw)

    rec = {"date": d.isoformat(), "name": name, "rotations": _int(sec.get(1, "")), "products": {}, "stockout": "",
           "voice": "", "prep": {}, "egg": "", "raw": text.strip()}
    for line in sec.get(2, "").split("\n"):
        pm = re.match(r"^\s*([^\d:：\s]+)\s*[:：]?\s*(\d+)", line)
        if pm and pm.group(1) in PRODUCT_ALIASES:
            rec["products"][PRODUCT_ALIASES[pm.group(1)]] = int(pm.group(2))
    rec["stockout"] = _clean_free(sec.get(3, ""))
    rec["voice"] = _clean_free(sec.get(4, ""))
    for line in sec.get(5, "").split("\n"):
        line = line.strip()
        if line.startswith("※") or not line:
            continue
        pm = re.match(r"^(砂糖|小麦|ホイップ)\s*[:：]?\s*(\d+)", line)
        if pm:
            rec["prep"][pm.group(1)] = int(pm.group(2))
        elif line.startswith("卵"):
            rec["egg"] = re.sub(r"^卵\s*[:：]?\s*", "", line)
            y, w = re.search(r"黄\s*(\d+)", line), re.search(r"白\s*(\d+)", line)
            rec["prep"]["卵黄"] = int(y.group(1)) if y else None
            rec["prep"]["卵白"] = int(w.group(1)) if w else None
    return rec


def to_row(rec, received=None):
    received = received or datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")
    p, q = rec["products"], rec["prep"]
    return [rec["date"], rec["name"], rec["rotations"] if rec["rotations"] is not None else ""] + \
           [p.get(k, "") for k in PRODUCTS] + [rec["stockout"], rec["voice"],
           q.get("砂糖", ""), q.get("小麦", ""), q.get("ホイップ", ""), q.get("卵黄") or "", q.get("卵白") or "",
           rec["egg"], received, rec["raw"]]


def from_row(row):
    row = list(row) + [""] * (len(HEADER) - len(row))

    def num(v):
        v = str(v).strip().replace(",", "")
        return int(v) if v.lstrip("-").isdigit() else None
    return {
        "date": row[0], "name": row[1], "rotations": num(row[2]),
        "products": {k: num(row[3 + i]) for i, k in enumerate(PRODUCTS) if num(row[3 + i]) is not None},
        "stockout": row[10], "voice": row[11],
        "prep": {"砂糖": num(row[12]), "小麦": num(row[13]), "ホイップ": num(row[14]), "卵黄": num(row[15]), "卵白": num(row[16])},
        "egg": row[17], "received": row[18],
    }


def save_nippo(rec, received=None):
    """日報台帳へ保存（同じ日付の行があれば上書き、なければ追記）。"""
    import data_layer
    import management_sync_layer as sync
    book = data_layer._client().open_by_key(sync.SHEET_ID)
    try:
        ws = book.worksheet(TAB)
    except Exception:  # noqa: BLE001 - 初回はタブを作る
        ws = book.add_worksheet(title=TAB, rows=1000, cols=len(HEADER))
        ws.update(values=[HEADER], range_name="A1")
    values = ws.get_all_values()
    row = to_row(rec, received)
    target = next((i for i, r in enumerate(values[1:], start=2) if r and r[0] == rec["date"]), None)
    if target:
        ws.update(values=[row], range_name=f"A{target}", value_input_option="USER_ENTERED")
        action = "上書き"
    else:
        ws.append_row(row, value_input_option="USER_ENTERED")
        action = "追記"
    sync._RAW["at"] = 0.0  # 画面が次の読み込みで最新を見るように
    return action


def read_nippo(days=45, today=None):
    """直近days日ぶんの日報（新しい順）。読めなければ空。"""
    import management_sync_layer as sync
    today = today or datetime.now(JST).date()
    try:
        values = sync._tab_values(TAB)
    except Exception as error:  # noqa: BLE001 - 読めないときは空で返すが、黙って捨てず必ず記録する
        print(f"[nippo] 日報台帳を読めませんでした: {error}", flush=True)
        return []
    out = []
    for r in values[1:]:
        if not r or not str(r[0]).strip():
            continue
        try:
            d = date.fromisoformat(str(r[0]).strip().replace("/", "-"))
        except ValueError:
            continue
        if (today - d).days <= days:
            rec = from_row(r)
            rec["date"] = d.isoformat()
            out.append(rec)
    out.sort(key=lambda x: x["date"], reverse=True)
    return out


def submission_status(records, today=None, days=14, deadline_hour=20):
    """直近days日の提出状況。今日は20時までは「これから」。"""
    now = datetime.now(JST)
    today = today or now.date()
    have = {r["date"] for r in records}
    out = []
    for i in range(days - 1, -1, -1):
        d = today - timedelta(days=i)
        key = d.isoformat()
        if key in have:
            state = "submitted"
        elif d < START_DATE:
            state = "before"
        elif d == today and now.hour < deadline_hour:
            state = "pending"
        elif d > today:
            state = "future"
        else:
            state = "missing"
        out.append({"date": key, "state": state})
    return out


def get_nippo_view(days=45):
    """画面(/nippo)用: 日報の一覧・提出状況・集計・売上と人件費（同じ日のもの）をまとめて返す。"""
    now = datetime.now(JST)
    records = read_nippo(days=days, today=now.date())
    # 同じ日の売上・人件費（今月ぶん。日次の予実シートから）
    context = {}
    try:
        import management_sync_layer as sync
        for r in (sync.get_management_sync().get("records") or []):
            context[r["date"]] = {
                "storeSales": r.get("storeSales") or 0, "eventSales": r.get("eventSales") or 0,
                "storeLabor": r.get("storeLabor") or 0,
            }
    except Exception:  # noqa: BLE001 - 売上が読めなくても日報だけは出す
        context = {}
    for rec in records:
        c = context.get(rec["date"])
        if c and rec["date"] >= now.date().isoformat():
            c = None  # 当日は売上・人件費が途中の数字なので、日報の横には出さない
        if c and rec.get("rotations"):
            total = c["storeSales"] + c["eventSales"]
            rec["context"] = {**c, "salesPerRotation": round(total / rec["rotations"]) if total else None}
        elif c:
            rec["context"] = c
    rotations = [r for r in records if r.get("rotations")]
    avg7 = None
    last7 = [r["rotations"] for r in rotations if (now.date() - date.fromisoformat(r["date"])).days < 7]
    if last7:
        avg7 = round(sum(last7) / len(last7), 1)
    # 現場の声: よく出る言葉（簡易の言葉集計）
    keywords = {"行列・混雑": ["列", "行列", "混雑", "混んで", "並"], "品切れ・完売": ["品切れ", "売り切れ", "完売", "なくなり"],
                "まとめ買い・お土産": ["まとめ", "お土産", "手土産", "箱"], "声かけ": ["声かけ", "声掛け"],
                "クレーム・ご指摘": ["クレーム", "ご指摘", "お叱り", "苦情"]}
    counts = {}
    for rec in records:
        if (now.date() - date.fromisoformat(rec["date"])).days >= 14:
            continue
        text = (rec.get("voice") or "") + " " + (rec.get("stockout") or "")
        for label, words in keywords.items():
            if any(w in text for w in words):
                counts[label] = counts.get(label, 0) + 1
    return {
        "updatedAt": now.isoformat(timespec="minutes"),
        "records": records,
        "status": submission_status(records, today=now.date()),
        "rotationAvg7": avg7,
        "voiceKeywords": [{"label": k, "count": v} for k, v in sorted(counts.items(), key=lambda kv: -kv[1])],
        "products": PRODUCTS,
    }
