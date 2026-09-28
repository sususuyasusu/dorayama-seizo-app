#!/usr/bin/env python3
"""会場から届く「日別単品別売上」（Excel）を、製造数予測のアーカイブへ「固定」で取り込む。

会場のレジの数字は日報フォームより正確（味別の個数も入っている）ので、届いたら取り込む。
取り込んだ日は「固定」になり、その後の日報フォームの取り込みで上書きされない。

使い方:
  python3 tools/forecast_import_pos.py <Excelファイル> [会場名]        … 中身の確認だけ（書き込まない）
  python3 tools/forecast_import_pos.py <Excelファイル> [会場名] --apply … アーカイブへ書き込む

対応する形式: エキュート上野の「■どら山さま_日別単品別売上.xlsx」
  （シートごとに、2行目に日付、3行目に「売上実績／数量実績」、4行目から商品）
知らない商品名があったら止まる（勝手に捨てない）。下の PRODUCT_MAP に足してから再実行。
"""
import os
import sys
import warnings
from datetime import date

warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import openpyxl

import forecast_engine as fe

# 会場レジの商品名 → (予測アプリの商品, 味)
PRODUCT_MAP = {
    "黒どら": ("黒どら", None), "白どら": ("白どら", None),
    "あんバタどら": ("あんバター", None), "あんバターどら": ("あんバター", None),
    "皮だけ": ("皮だけ", None),
}
SKIP = ("常温計", "冷蔵計", "常温構成比", "冷蔵構成比")


def classify(name):
    if name in PRODUCT_MAP:
        return PRODUCT_MAP[name]
    if "生どら" in name:
        return "生どら", fe.flavor_name(name.replace("生どら", ""))
    if name.endswith("どら") and len(name) > 2:      # 「めっちゃ抹茶どら」「レモンどら」など＝旬どら
        return "旬どら", fe.flavor_name(name[:-2])
    return None


def read(path, venue):
    wb = openpyxl.load_workbook(path, data_only=True)
    days = {}
    unknown = set()
    for ws in wb.worksheets:
        rows = list(ws.iter_rows(values_only=True))
        if len(rows) < 4:
            continue
        cols = {}
        for c, v in enumerate(rows[1]):
            if hasattr(v, "year"):
                cols[v.date() if hasattr(v, "date") else v] = c
        if not cols:
            continue
        for r in rows[3:]:
            name = str(r[0] or "").strip()
            if name in ("常温計", "冷蔵計"):             # ファイル自身の小計（個数）を検算用に控える
                for d, c in cols.items():
                    rec = days.setdefault(d, {"qty": {}, "flavors": {}})
                    rec["subtotal"] = rec.get("subtotal", 0) + int(r[c + 1] or 0)
                continue
            if name in SKIP:
                continue
            if name in ("売上", ""):
                if any(r[c] for c in cols.values()):
                    for d, c in cols.items():        # 日別の売上合計（税抜）
                        days.setdefault(d, {"qty": {}, "flavors": {}})["salesExcl"] = int(r[c] or 0)
                continue
            hit = classify(name)
            if hit is None:
                unknown.add(name)
                continue
            prod, flavor = hit
            for d, c in cols.items():
                q = int(r[c + 1] or 0)               # 売上実績の右隣が数量実績
                rec = days.setdefault(d, {"qty": {}, "flavors": {}})
                rec["qty"][prod] = rec["qty"].get(prod, 0) + q
                if flavor and q:
                    fl = rec["flavors"].setdefault(prod, {})
                    fl[flavor] = fl.get(flavor, 0) + q
    if unknown:
        sys.exit(f"知らない商品名があります: {sorted(unknown)} → PRODUCT_MAP に足してください（何も書き込んでいません）")
    out = []
    for d in sorted(days):
        v = days[d]
        if not v.get("salesExcl"):
            continue                                 # 売上が0の日（まだ営業していない日）は取り込まない
        out.append({"date": d, "venue": venue, "salesExcl": v["salesExcl"],
                    "qty": {p: v["qty"].get(p) for p in fe.PRODUCTS}, "flavors": v["flavors"],
                    "sum": sum(v["qty"].values()), "subtotal": v.get("subtotal")})
    return out


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        sys.exit(__doc__)
    path = args[0]
    venue = args[1] if len(args) > 1 else "エキュート上野"
    recs = read(path, venue)
    if not recs:
        sys.exit("取り込める日がありません（ファイルの形式を確認してください）")
    print(f"{venue} {len(recs)}日分  {recs[0]['date']}〜{recs[-1]['date']}")
    for r in recs:
        q = " ".join(f"{p}{r['qty'][p]}" for p in fe.PRODUCTS)
        print(f"  {r['date']} 売上(税抜){r['salesExcl']:>8,}  {q}  個数の合計{r['sum']}（ファイルの小計{r['subtotal']}）")
    bad = [r for r in recs if r["subtotal"] is None or r["sum"] != r["subtotal"]]
    if bad:
        sys.exit(f"個数の合計がファイルの小計と合わない日が{len(bad)}日あります。商品の対応づけを確認してください"
                 f"（何も書き込んでいません）")
    if "--apply" not in sys.argv:
        print("確認だけで終了（書き込むには --apply を付ける）")
        sys.exit(0)
    import forecast_archive as archive
    res = archive.upsert_fixed(recs, "会場POS")
    print(f"アーカイブへ反映: 差し替え{res['updated']}日・新規{res['added']}日")
