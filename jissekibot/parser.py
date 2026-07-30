"""LINE「実績」報告メッセージから {シート商品名: 個数} を抽出。

期待フォーマット:
    実績
    黒280
    あんバター280
    白100
    抹茶110
    皮15
    バナナ10
    オレンジ30
    マスカット29

- 先頭行が「実績/製造実績/作成個数/生産個数」で始まる本文のみ処理。
- 果物どら（バナナ/オレンジ/マスカット/いちご/キウイ/みかん等）は「生」に合算。
- 旬/抹茶 → 旬どら、皮 → 皮4枚セット 等のゆれを吸収。
"""
from __future__ import annotations

import re
import unicodedata

# 報告語 → シートの6商品。果物系はすべて「生」に寄せて合算する。
ALIASES = {
    "黒": "黒どら", "黒どら": "黒どら",
    "あんバター": "あんバター", "バター": "あんバター", "あん": "あんバター",
    "白": "白どら", "白どら": "白どら",
    "旬": "旬どら", "抹茶": "旬どら", "旬どら": "旬どら", "めっちゃ抹茶": "旬どら",
    "皮": "皮4枚セット", "皮だけ": "皮4枚セット", "皮4枚": "皮4枚セット", "皮4枚セット": "皮4枚セット",
    "生": "生", "生どら": "生", "バナナ": "生", "オレンジ": "生", "マスカット": "生",
    "いちご": "生", "苺": "生", "キウイ": "生", "みかん": "生", "ぶどう": "生", "果物": "生",
}

_TRIGGER = re.compile(r"^(実績|製造実績|作成個数|本日の作成個数|生産個数|作成数)")
_LINE = re.compile(r"^\s*(?P<name>[^\d\s][^\d]*?)\s*[:：]?\s*(?P<n>\d+)\s*(?:個|枚|パック)?\s*$")
_EXCLUDE = ("ビニール", "ネット", "紙", "袋", "箱", "以上", "合計", "お疲れ", "本日は", "報告")


def _norm(s: str) -> str:
    return unicodedata.normalize("NFKC", s.replace("　", " ")).strip()


def starts_with_jisseki(text: str) -> bool:
    for raw in (text or "").splitlines():
        s = _norm(raw)
        if not s:
            continue
        return bool(_TRIGGER.match(s))
    return False


def _to_product(name: str):
    if name in ALIASES:
        return ALIASES[name]
    for k, v in ALIASES.items():
        if k in name or name in k:
            return v
    return None


def parse(text: str) -> dict:
    """{シート商品名: 個数}。先頭が実績系トリガでなければ {}。果物は生に合算。"""
    lines = [_norm(x) for x in (text or "").splitlines()]
    i = 0
    while i < len(lines) and not lines[i]:
        i += 1
    if i >= len(lines) or not _TRIGGER.match(lines[i]):
        return {}

    out: dict = {}
    for ln in lines[i + 1:]:
        if not ln:
            continue
        m = _LINE.match(ln)
        if not m:
            continue
        name = m["name"].strip()
        if any(k in name for k in _EXCLUDE):
            continue
        prod = _to_product(name)
        if prod is None:
            continue
        out[prod] = out.get(prod, 0) + int(m["n"])
    return out
