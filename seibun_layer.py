#!/usr/bin/env python3
"""どら山 成分ノート — パッケージ写真の読み取り層（標準ライブラリのみ）。

環境変数:
  ANTHROPIC_API_KEY : Claude API キー（未設定なら読み取り機能だけ止まる）
  ACCESS_CODE       : どら山スタッフ用の合言葉（未設定なら読み取りを通さない＝開けっ放し防止）
"""
import datetime
import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

API_URL = "https://api.anthropic.com/v1/messages"
MODEL = "claude-opus-5"          # 長辺2576pxまで原寸で読める高解像度ビジョン
MAX_TOKENS = 8000                # 「思考＋回答」の合計上限。小さいと途中で切れる
USAGE_LOG = Path(__file__).parent / "data" / "seibun_usage.json"

# 1枚あたりのおおよその費用（入力約4,800トークン＋出力少々）。月1,000円の目安管理用。
YEN_PER_SCAN = 6.6

PROMPT = """あなたは日本の加工食品表示の入力補助担当です。
添付された商品パッケージ裏面を読み取り、原材料マスタへ登録する候補を作ってください。
栄養成分が画像にない、または判読不能な場合だけ、メーカー名「{maker}」と商品名「{product}」でメーカー公式情報を検索してください。
検索結果はメーカー公式ページを最優先し、販売店やブログだけなら未確認にしてください。
推測で数字を埋めないでください。画像に書かれた原材料名は、順番と括弧内を保って文字起こししてください。
文字起こしの注意:
- 小さい文字・かすれた文字も、画像を細部まで見て一字ずつ読み取ること。似た字（ソ/ン、シ/ツ、ー/一、0/O）を取り違えないこと。
- 数値は単位（kcal・g・mg）と桁を必ず画像どおりに写すこと。mgをgに直したりしない。
- どうしても読めない箇所は、そこだけ空欄にして warnings に「どこが読めなかったか」を書くこと。全体を推測で埋めない。
- 100g当たりか1個(1食)当たりかは、画像の見出しどおりに basis へ入れること。
- 🔴 calories・protein・fat・carbs・salt・servingGrams は **単位を付けず数字だけ** で入れること（正: 217 / 4.8 / 0.20　誤: "217kcal" / "4.8g"）。
回答は説明を付けず、次のJSONだけにしてください。
{{"productName":"","manufacturer":"","basis":"per100g|perServing|unknown","servingGrams":null,"calories":null,"protein":null,"fat":null,"carbs":null,"salt":null,"ingredientsText":"","allergens":[],"sourceType":"package|manufacturer|retailer|unknown","sourceUrl":"","confidence":"high|medium|low","warnings":[]}}"""


def _bump_usage():
    """読み取り回数と推定費用を控える（失敗しても本処理は止めない）。"""
    try:
        USAGE_LOG.parent.mkdir(parents=True, exist_ok=True)
        cur = {"count": 0, "yen": 0.0, "since": time.strftime("%Y-%m-%d")}
        if USAGE_LOG.exists():
            cur.update(json.loads(USAGE_LOG.read_text(encoding="utf-8")))
        cur["count"] = int(cur.get("count", 0)) + 1
        cur["yen"] = round(float(cur.get("yen", 0)) + YEN_PER_SCAN, 1)
        cur["last"] = time.strftime("%Y-%m-%d %H:%M")
        USAGE_LOG.write_text(json.dumps(cur, ensure_ascii=False), encoding="utf-8")
        return cur
    except Exception:
        return {}


def usage():
    try:
        return json.loads(USAGE_LOG.read_text(encoding="utf-8"))
    except Exception:
        return {"count": 0, "yen": 0.0}


# 旬どらレシピ_2026 の「共通_皮レシピ」タブ。ここを直せばアプリの皮レシピも変わる。
RECIPE_KEY = "190G3d8IZSGd-rNF28guhirjUwtVBgbZyA2lmBpZiqKE"
RECIPE_TAB = "共通_皮レシピ"
_kawa_cache = {"at": 0, "data": None}


def kawa_recipe():
    """共通_皮レシピをアプリの行の形にして返す。60秒はキャッシュする。"""
    if _kawa_cache["data"] and time.time() - _kawa_cache["at"] < 60:
        return _kawa_cache["data"]
    try:
        import data_layer
        ws = data_layer._client().open_by_key(RECIPE_KEY).worksheet(RECIPE_TAB)
        vals = ws.get_all_values()
    except Exception as e:
        return {"error": f"シートを読めませんでした（{e}）", "rows": []}

    def n(v):
        try:
            return float(str(v).replace(",", ""))
        except Exception:
            return 0

    rows, total, count = [], 0, 0
    for r in vals[2:]:
        r = r + [""] * (14 - len(r))
        kubun, name = r[0].strip(), r[1].strip()
        if kubun == "合計" or not name:
            continue
        w = n(r[2])
        if w <= 0:
            continue
        total += w
        count = int(n(r[3])) or count
        rows.append({
            "name": name,
            "disp": r[11].strip() or name,
            "sec": kubun or "皮",
            "w": w,
            "k": n(r[5]), "p": n(r[6]), "f": n(r[7]), "c": n(r[8]), "s": n(r[9]),
            "src": r[10].strip(),
            # M列のアレルゲン。原材料名の最後の一括表示に使う。
            "alg": r[12].strip(),
            # 水は原材料名に書かないのが通例なので、初期は裏面表示から外す
            "show": ("水" not in name) or name == "水あめ",
        })
    out = {"rows": rows, "total": total, "count": count,
           "tab": RECIPE_TAB,
           "url": f"https://docs.google.com/spreadsheets/d/{RECIPE_KEY}/edit#gid={ws.id}"}
    _kawa_cache.update({"at": time.time(), "data": out})
    return out


# ---- 保存先のタブ（旬どらレシピ_2026 の中） ----
# 月別管理表 … 旬どら12か月の枠。月を選ぶとその行を上書きする
# 商品マスタ … 商品ごとの台帳。旬どら以外の新規商品はここに行を足す
# 計算記録   … 計算するたびに1行ずつ積む履歴。消さない
TAB_MONTH = "月別管理表"
TAB_MASTER = "商品マスタ"
TAB_LOG = "計算記録"
MONTH_FIRST_ROW = 5          # 5行目が1月、16行目が12月
LOG_DATA_COL = 19            # S列。復元用の内部データを入れる（人は見なくてよい）


def _sheet():
    import data_layer
    return data_layer._client().open_by_key(RECIPE_KEY)


def _today():
    return datetime.date.today().isoformat()


def products():
    """保存先の選択肢を作る。月別管理表の12か月と、商品マスタの登録済み商品。"""
    try:
        sh = _sheet()
        mv = sh.worksheet(TAB_MONTH).get_all_values()
        pv = sh.worksheet(TAB_MASTER).get_all_values()
    except Exception as e:
        return {"error": f"シートを読めませんでした（{e}）", "months": [], "items": []}

    months = []
    for i in range(12):
        r = mv[MONTH_FIRST_ROW - 1 + i] if len(mv) > MONTH_FIRST_ROW - 1 + i else []
        r = list(r) + [""] * (13 - len(r))
        months.append({"row": MONTH_FIRST_ROW + i, "month": r[0].strip(),
                       "seed": r[1].strip(), "name": r[2].strip(),
                       "kcal": r[6].strip()})

    items = []
    for i, r in enumerate(pv[1:], start=2):
        r = list(r) + [""] * (14 - len(r))
        if not r[1].strip():
            continue
        items.append({"row": i, "pid": r[0].strip(), "name": r[1].strip(),
                      "kind": r[2].strip(), "kcal": r[3].strip()})
    return {"months": months, "items": items,
            "url": f"https://docs.google.com/spreadsheets/d/{RECIPE_KEY}/edit"}


TAB_ING = "原材料マスタ_AI"
ING_FIRST_ROW = 3            # 1行目は見出し、2行目が列名、3行目からデータ
_pre_cache = {"at": 0, "data": None}


def presets(force=False):
    """プリセット（原材料の候補）を原材料マスタ_AIから読む。60秒はキャッシュする。"""
    if not force and _pre_cache["data"] and time.time() - _pre_cache["at"] < 60:
        return _pre_cache["data"]
    try:
        vals = _sheet().worksheet(TAB_ING).get_all_values()
    except Exception as e:
        return {"error": f"シートを読めませんでした（{e}）", "items": []}

    def n(v):
        try:
            return float(str(v).replace(",", ""))
        except Exception:
            return ""

    items = []
    for i, r in enumerate(vals[ING_FIRST_ROW - 1:], start=ING_FIRST_ROW):
        r = list(r) + [""] * (19 - len(r))
        if not r[1].strip():
            continue
        items.append({
            "row": i, "iid": r[0].strip(), "n": r[1].strip(),
            "disp": r[2].strip() or r[1].strip(),
            "sec": "皮" if r[3].strip() == "皮" else "餡・仕上げ",
            "k": n(r[4]), "p": n(r[5]), "f": n(r[6]), "c": n(r[7]), "s": n(r[8]),
            "txt": r[11].strip(), "alg": r[12].strip(),
            "src": r[13].strip(), "url": r[14].strip(),
        })
    out = {"items": items, "tab": TAB_ING}
    _pre_cache.update({"at": time.time(), "data": out})
    return out


def add_preset(d):
    """原材料をプリセットに登録する。同じ材料名があればその行を上書きする。"""
    name = (d.get("n") or "").strip()
    if not name:
        return {"ok": False, "msg": "原材料名が空です。"}
    ws = _sheet().worksheet(TAB_ING)
    vals = ws.get_all_values()

    def num(v):
        try:
            return round(float(v), 3)
        except Exception:
            return ""

    body = [name, (d.get("disp") or name).strip(),
            "皮" if d.get("sec") == "皮" else "餡",
            num(d.get("k")), num(d.get("p")), num(d.get("f")),
            num(d.get("c")), num(d.get("s"))]
    extra = [(d.get("txt") or "").strip(), (d.get("alg") or "").strip(),
             (d.get("src") or "アプリ登録").strip(), (d.get("url") or "").strip()]

    target = None
    used = []
    for i, r in enumerate(vals[ING_FIRST_ROW - 1:], start=ING_FIRST_ROW):
        if len(r) > 1 and r[1].strip():
            used.append(r[0].strip())
            if r[1].strip() == name:
                target = i

    if target:
        ws.update(range_name=f"B{target}:I{target}", values=[body],
                  value_input_option="USER_ENTERED")
        ws.update(range_name=f"L{target}:O{target}", values=[extra],
                  value_input_option="USER_ENTERED")
        ws.update(range_name=f"Q{target}:R{target}", values=[[_today(), "アプリ登録"]],
                  value_input_option="USER_ENTERED")
        msg = f"「{name}」を上書きしました（{target}行目）"
    else:
        mx = 0
        for u in used:
            digits = "".join(ch for ch in u if ch.isdigit())
            if u.startswith("I") and digits:
                mx = max(mx, int(digits))
        iid = f"I{mx + 1:03d}"
        row = [iid] + body + ["", ""] + extra + ["", _today(), "アプリ登録", ""]
        ws.append_row(row, value_input_option="USER_ENTERED")
        msg = f"「{name}」をプリセットに追加しました（{iid}）"

    _pre_cache.update({"at": 0, "data": None})
    return {"ok": True, "msg": msg}


def history(limit=10):
    """計算記録タブの新しい順。端末が変わっても同じものが見える。"""
    try:
        vals = _sheet().worksheet(TAB_LOG).get_all_values()
    except Exception as e:
        return {"error": f"シートを読めませんでした（{e}）", "items": []}
    out = []
    for i in range(len(vals) - 1, 0, -1):
        r = list(vals[i]) + [""] * (LOG_DATA_COL - len(vals[i]))
        if not r[1].strip():
            continue
        try:
            data = json.loads(r[LOG_DATA_COL - 1] or "null")
        except Exception:
            data = None
        out.append({"row": i + 1, "at": r[0], "name": r[1], "kcal": r[2], "data": data})
        if len(out) >= limit:
            break
    return {"items": out}


def _new_pid(pv, kind):
    """商品IDの自動採番。旬どら=D、生どら=N、それ以外=P。"""
    head = {"旬どら": "D", "生どら": "N"}.get(kind, "P")
    used = [r[0].strip() for r in pv[1:] if r and r[0].strip().startswith(head)]
    mx = 0
    for u in used:
        digits = "".join(ch for ch in u if ch.isdigit())
        if digits:
            mx = max(mx, int(digits))
    return f"{head}{mx + 1:03d}"


def save_result(d):
    """計算結果をシートに保存する。計算記録には必ず1行積み、
    選ばれた保存先（月別管理表の月／商品マスタの既存行／新規行）を上書きする。"""
    name = (d.get("name") or "").strip()
    if not name:
        return {"ok": False, "msg": "商品名が空です。"}
    p100 = d.get("per100") or {}
    p1 = d.get("per1") or {}

    def v(src, key, digits):
        try:
            return round(float(src.get(key) or 0), digits)
        except Exception:
            return 0

    n100 = [v(p100, k, dg) for k, dg in
            (("k", 0), ("p", 1), ("f", 1), ("c", 1), ("s", 2))]
    now = datetime.datetime.now().strftime("%Y/%m/%d %H:%M")
    sh = _sheet()
    done = []

    # ① 計算記録に1行積む（これが履歴の本体。上書きはしない）
    log = sh.worksheet(TAB_LOG)
    if log.col_count < LOG_DATA_COL:      # 復元用の列がまだ無ければ1列足す
        log.add_cols(LOG_DATA_COL - log.col_count)
    if len(log.row_values(1) or []) < LOG_DATA_COL:
        log.update_cell(1, LOG_DATA_COL, "アプリ内部データ（復元用・編集不要）")
    log.append_row([
        now, name, v(p1, "k", 0), v(p1, "p", 1), v(p1, "f", 1), v(p1, "c", 1),
        v(p1, "s", 2), 60, round(float(d.get("oneWeight") or 0) * 60, 1),
        d.get("ingredients") or "", round(float(d.get("matSum") or 0), 1),
        n100[0], "", "", "アプリ計算", "", _today(),
        d.get("allergen") or "",
        json.dumps({"kw": d.get("kawaW") or 60, "rows": d.get("rows") or []},
                   ensure_ascii=False),
    ], value_input_option="USER_ENTERED")
    done.append("計算記録に1行追加")

    mode = d.get("mode")
    if mode == "shun":
        row = int(d.get("row") or 0)
        if not MONTH_FIRST_ROW <= row <= MONTH_FIRST_ROW + 11:
            return {"ok": False, "msg": "月の指定が正しくありません。"}
        ws = sh.worksheet(TAB_MONTH)
        ws.update(range_name=f"C{row}", values=[[name]], value_input_option="USER_ENTERED")
        ws.update(range_name=f"F{row}:K{row}",
                  values=[[d.get("allergenPlain") or ""] + n100],
                  value_input_option="USER_ENTERED")
        ws.update(range_name=f"M{row}", values=[[_today()]], value_input_option="USER_ENTERED")
        done.append(f"月別管理表 {row - MONTH_FIRST_ROW + 1}月の行を更新")
        mode = "master_by_name"

    mst = sh.worksheet(TAB_MASTER)
    pv = mst.get_all_values()
    target = None
    if mode == "existing":
        target = int(d.get("row") or 0) or None
    elif mode == "master_by_name":
        for i, r in enumerate(pv[1:], start=2):
            if len(r) > 1 and r[1].strip() == name:
                target = i
                break

    if target:
        mst.update(range_name=f"D{target}:H{target}", values=[n100],
                   value_input_option="USER_ENTERED")
        mst.update(range_name=f"I{target}:K{target}",
                   values=[["アプリ計算", _today(), "成分ノート"]],
                   value_input_option="USER_ENTERED")
        done.append(f"商品マスタ {target}行目を更新")
    elif mode in ("new", "master_by_name"):
        kind = (d.get("kind") or "その他").strip()
        pid = _new_pid(pv, kind)
        mst.append_row([pid, name, kind] + n100 +
                       ["アプリ計算", _today(), "成分ノート", "", "要根拠確認",
                        d.get("allergenPlain") or ""],
                       value_input_option="USER_ENTERED")
        done.append(f"商品マスタに新規追加（{pid}）")

    return {"ok": True, "msg": "／".join(done)}


def check_access(code):
    """合言葉の照合。(通ってよいか, エラー文, HTTPコード) を返す。"""
    expected = (os.environ.get("ACCESS_CODE") or "").strip()
    if not expected:
        return False, "スタッフ用の合言葉が未設定です。Renderの環境変数 ACCESS_CODE を登録してください。", 503
    if (code or "").strip() != expected:
        return False, "合言葉が違います。", 401
    return True, "", 200


def analyze(image_b64, media_type="image/jpeg", manufacturer="", product_name=""):
    """パッケージ写真を読み取って辞書で返す。エラー時は {'error':..., 'status':...}。"""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return {"error": "画像認識を使うためのClaude接続設定がまだありません。", "status": 503}
    if not image_b64:
        return {"error": "画像が選択されていません。", "status": 400}

    body = json.dumps({
        "model": MODEL,
        "max_tokens": MAX_TOKENS,
        "output_config": {"effort": "high"},
        "tools": [{"type": "web_search_20260209", "name": "web_search", "max_uses": 3}],
        "messages": [{
            "role": "user",
            "content": [
                {"type": "image",
                 "source": {"type": "base64", "media_type": media_type or "image/jpeg",
                            "data": image_b64}},
                {"type": "text",
                 "text": PROMPT.format(maker=manufacturer or "不明", product=product_name or "不明")},
            ],
        }],
    }).encode("utf-8")

    req = urllib.request.Request(API_URL, data=body, headers={
        "content-type": "application/json",
        "x-api-key": key,
        "anthropic-version": "2023-06-01",
    })
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            result = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read().decode("utf-8")).get("error", {}).get("message", "")
        except Exception:
            detail = ""
        return {"error": detail or "Claudeで画像を読み取れませんでした。", "status": e.code}
    except Exception:
        return {"error": "画像を読み取れませんでした。写真を撮り直すか手入力してください。", "status": 500}

    stop = result.get("stop_reason")
    if stop == "refusal":
        return {"error": "この画像は読み取り対象外と判定されました。別の写真で試してください。", "status": 422}
    if stop == "max_tokens":
        return {"error": "読み取り結果が長すぎて途中で切れました。写真を分けて撮り直してください。", "status": 422}

    text = "\n".join(b.get("text", "") for b in result.get("content", []) if b.get("type") == "text")
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        return {"error": "画像の結果を整理できませんでした。", "status": 422}
    try:
        parsed = json.loads(m.group(0))
    except Exception:
        return {"error": "画像の結果を整理できませんでした。", "status": 422}

    # 「217kcal」「0.20g」のように単位付きで返ってくることがあるので、数値に直す。
    # これをしないとアプリ側で数字として扱えず、成分がすべて0になる。
    for key in ("calories", "protein", "fat", "carbs", "salt", "servingGrams"):
        parsed[key] = _to_number(parsed.get(key))

    parsed["_usage"] = _bump_usage()
    return parsed


def _to_number(v):
    """「217kcal」「約4.8 g」「0.20g」→ 217 / 4.8 / 0.2。取れなければ None。"""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return v
    m = re.search(r"-?\d+(?:\.\d+)?", str(v).replace(",", ""))
    if not m:
        return None
    n = float(m.group(0))
    return int(n) if n == int(n) else n
