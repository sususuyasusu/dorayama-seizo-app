#!/usr/bin/env python3
"""どら山 成分ノート — パッケージ写真の読み取り層（標準ライブラリのみ）。

環境変数:
  ANTHROPIC_API_KEY : Claude API キー（未設定なら読み取り機能だけ止まる）
  ACCESS_CODE       : どら山スタッフ用の合言葉（未設定なら読み取りを通さない＝開けっ放し防止）
"""
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
