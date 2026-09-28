#!/usr/bin/env python3
"""製造数予測の自己点検（通信もシートも使わない）。計算を直したあとに必ず実行する。

  python3 tools/forecast_selftest.py

確かめること:
  1. 納品ルールが、2026-09-10 の「曜日別 製造・納品指示表」の数字を再現すること
  2. 日報の書き方のゆれ（味別の記入・全角数字・桁区切りの打ち間違い）を正しく読めること
  3. 祝日・連休の判定
  4. 学習と予測が一通り動き、曜日の差を学習できること
"""
import math
import os
import random
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import forecast_calendar as cal
import forecast_engine as fe

failed = []


def check(name, cond, detail=""):
    print(("  ✓ " if cond else "  ✗ ") + name + ("" if cond else f"  → {detail}"))
    if not cond:
        failed.append(name)


print("1. 納品ルール（2026-09-10の指示表と同じ数字になるか）")
# 指示表の前提になった曜日別の販売見込み（月〜日）と、表に載っている納品数・夜の在庫の目安
TABLE = {
    "黒どら": ([60, 95, 110, 110, 145, 167, 162], [88, 108, 109, 138, 162, 164, 80], [76, 89, 88, 116, 133, 130, 48]),
    "生どら": ([10, 14, 18, 20, 30, 26, 22], [12, 17, 19, 26, 28, 23, 15], [9, 12, 13, 19, 17, 14, 7]),
}
monday = date(2026, 9, 14)
for prod, (sales, deliveries, nights) in TABLE.items():
    fcs = [{"date": monday + timedelta(days=i), "qty": {prod: float(sales[i % 7])}} for i in range(15)]
    # 日曜の夜に「目安どおり」の在庫があった状態から始める
    start = {prod: fe.night_target(prod, sales[0], fe.DEFAULT_SETTINGS)}
    plan = fe.recommend(fcs, start, fe.DEFAULT_SETTINGS)
    got_d = [round(plan[i]["items"][prod]["delivery"]) for i in range(7)]
    got_n = [round(plan[i]["items"][prod]["nightTarget"]) for i in range(7)]
    label = "（バナナ味の行）" if prod == "生どら" else ""
    check(f"{prod}{label} 納品数", all(abs(a - b) <= 1 for a, b in zip(got_d, deliveries)), f"{got_d} / 表 {deliveries}")
    check(f"{prod}{label} 夜の在庫の目安", all(abs(a - b) <= 1 for a, b in zip(got_n, nights)), f"{got_n} / 表 {nights}")
# 指示表の例:「木曜の夜に黒どらが約190個なら、金曜の納品は約87個」
fcs = [{"date": date(2026, 9, 11), "qty": {"黒どら": 145.0}}, {"date": date(2026, 9, 12), "qty": {"黒どら": 167.0}}]
got = fe.recommend(fcs, {"黒どら": 190}, fe.DEFAULT_SETTINGS)[0]["items"]["黒どら"]["delivery"]
check("在庫が多い夜の翌日は、その分だけ納品を減らす", abs(got - 88) <= 2, f"{got:.0f}個（指示表の例は約87個）")
got = fe.recommend(fcs, {"黒どら": 900}, fe.DEFAULT_SETTINGS)[0]["items"]["黒どら"]
check("在庫が十分なら納品は0（マイナスにしない）", got["delivery"] == 0 and got["note"] != "", str(got))

print("2. 日報の読み取り")
check("味別の記入（矢印つき）", fe.parse_qty_cell("抹茶→87　和栗→21") == (108, {"抹茶": 87, "和栗": 21}), str(fe.parse_qty_cell("抹茶→87　和栗→21")))
check("味別の記入（詰めて書く）", fe.parse_qty_cell("バナナ16  マンゴー27") == (43, {"バナナ": 16, "マンゴー": 27}))
check("味の呼び方のゆれ（栗・桃）", fe.parse_qty_cell("抹茶35   栗31 桃15")[1] == {"抹茶": 35, "和栗": 31, "もも": 15}, str(fe.parse_qty_cell("抹茶35   栗31 桃15")))
check("矢印だけついた数字", fe.parse_qty_cell("→3") == (3, {}))
check("0個は0として読む（空欄と区別）", fe.parse_qty_cell("0") == (0, {}) and fe.parse_qty_cell("") == (None, {}))
check("全角数字", fe.to_number("１２３") == 123)
check("桁区切りをピリオドで打った金額", fe.to_number("212.951") == 212951, str(fe.to_number("212.951")))
check("カンマつき金額", fe.to_number("171,869") == 171869)
check("小数はそのまま", fe.to_number("26.5") == 26.5)
check("数値の0", fe.to_number(0) == 0)
rows = [["h"] * 20,
        ["2026/09/26 22:35:15", "2026/09/26", "エキュート上野", "182613", "165", "140", "186", "43", "56", "125", "174", "135", "161", "9", "15", "52", "45", "所感", "197731", ""],
        ["2026/09/27 19:29:33", "2026/09/27", "エキュート上野", "182613", "165", "140", "186", "43", "56", "125", "174", "135", "161", "9", "15", "52", "45", "まちがい", "197731", ""],
        ["2026/09/27 21:44:49", "2026/09/27", "エキュート上野", "133137", "129", "72", "174", "15", "71", "100", "133", "118", "111", "4", "17", "42", "30", "", "143876", ""],
        ["2026/09/27 23:00:00", "2026/09/27", "ルミネ大宮テスト確認用", "999", "1", "1", "1", "1", "1", "1", "1", "1", "1", "1", "1", "1", "1", "", "999", ""],
        ["2026/05/25 21:00:00", "", "日本橋高島屋", "147619", "200", "10", "1", "1", "1", "1", "1", "1", "1", "1", "1", "1", "1", "", "159447", "5/25"]]
recs, skipped = fe.clean_form_rows(rows)
by = {(r["venue"], r["date"]): r for r in recs}
check("同じ日の出し直しは後のものを採用", by[("エキュート上野", date(2026, 9, 27))]["salesExcl"] == 133137)
check("テスト送信は取り込まない", not any("テスト" in r["venue"] for r in recs))
check("日付が手入力欄（5/25）だけの行も読める", ("日本橋高島屋", date(2026, 5, 25)) in by, str(list(by)))

print("3. 暦")
check("国民の休日（2026-09-22）", cal.holiday_name(date(2026, 9, 22)) == "国民の休日")
check("シルバーウィークは5連休", cal.off_run(date(2026, 9, 21)) == (5, 3), str(cal.off_run(date(2026, 9, 21))))
check("お盆の平日", cal.period_label(date(2026, 8, 13)) == "お盆")
f = fe.calendar_features(date(2026, 9, 18))
check("連休の前日", "pre_hol" in f, str(f))
f = fe.calendar_features(date(2026, 9, 24))
check("連休明け", f.get("post_big") == 1.0, str(f))
f = fe.calendar_features(date(2026, 9, 25))
check("ふつうの金曜は連休前日にしない", "pre_hol" not in fe.calendar_features(date(2026, 10, 2)))

print("4. 学習と予測")
random.seed(7)
DOW = [0.7, 0.9, 1.0, 1.0, 1.35, 1.3, 0.95]
recs, weather = [], {}
d0 = date(2026, 6, 1)
for i in range(90):
    d = d0 + timedelta(days=i)
    rain = 12.0 if i % 9 == 0 else 0.0
    base = 100 * DOW[d.weekday()] * (0.85 if rain else 1.0) * math.exp(random.gauss(0, 0.08))
    qty = {p: max(1, round(base * s)) for p, s in zip(fe.PRODUCTS, (1.0, 0.35, 0.8, 0.8, 0.08, 0.5))}
    recs.append({"date": d, "venue": "テスト店", "salesExcl": round(base * 1300), "salesIncl": round(base * 1404),
                 "customers": round(base * 1.2), "qty": qty, "stock": {p: 50 for p in fe.PRODUCTS},
                 "flavors": {}, "stockFlavors": {}, "comment": ""})
    # 気温は曜日と無関係に散らす（曜日と連動させると、曜日の効果と気温の効果を区別できなくなる）
    weather[d] = {"label": "雨" if rain else "晴れ", "tmax": round(24.0 + random.uniform(0, 10), 1), "tmin": 20.0, "rain": rain}
tr = fe.train(recs, weather, {})
check("全項目を学習できる", set(tr["models"]) == set(fe.ALL_TARGETS), str(sorted(tr["models"])))
fri = fe.forecast_day(tr, date(2026, 9, 4), {"tmax": 29.0, "rain": 0.0}, {})
mon = fe.forecast_day(tr, date(2026, 9, 7), {"tmax": 29.0, "rain": 0.0}, {})
ratio = fri["黒どら"]["p50"] / mon["黒どら"]["p50"]
check("金曜は月曜より多い（作った差 1.93倍に近い）", 1.65 < ratio < 2.2, f"{ratio:.2f}倍")
wet = fe.forecast_day(tr, date(2026, 9, 4), {"tmax": 29.0, "rain": 12.0}, {})
drop = wet[fe.TOTAL]["p50"] / fri[fe.TOTAL]["p50"]
check("雨の日は少なめに出る（作った差 0.85倍に近い）", 0.78 < drop < 0.95, f"{drop:.2f}倍")
none = fe.forecast_day(tr, date(2026, 9, 4), None, {})
check("天気が無い日は、晴れと雨の間になる", wet[fe.TOTAL]["p50"] <= none[fe.TOTAL]["p50"] <= fri[fe.TOTAL]["p50"] * 1.03,
      f"雨{wet[fe.TOTAL]['p50']:.0f} ≤ なし{none[fe.TOTAL]['p50']:.0f} ≤ 晴れ{fri[fe.TOTAL]['p50']:.0f}")
cold = fe.forecast_day(tr, date(2026, 12, 4), {"tmax": 5.0, "rain": 0.0}, {})
check("経験の無い寒さでも暴れない（0.5〜2倍の範囲）", 0.5 < cold[fe.TOTAL]["p50"] / fri[fe.TOTAL]["p50"] < 2.0,
      f"{cold[fe.TOTAL]['p50'] / fri[fe.TOTAL]['p50']:.2f}倍")
check("幅は 少なめ < 見込み < 多め", fri["黒どら"]["lo"] < fri["黒どら"]["p50"] < fri["黒どら"]["hi"])
bt = fe.backtest(recs, weather, {}, max_days=28)
acc = fe.accuracy_summary(bt, fe.calibration(bt))
check("過去検証のずれが小さい（作ったばらつき8%に対し15%未満）", acc[fe.TOTAL]["errorRate"] < 0.15, f"{acc[fe.TOTAL]['errorRate'] * 100:.1f}%")
sim = fe.simulate_policy(bt, fe.DEFAULT_SETTINGS)
check("納品ルールの再現ができる", "黒どら" in sim and sim["黒どら"]["delivered"] > 0)

print()
if failed:
    print(f"✗ {len(failed)}件の点検に失敗: " + " / ".join(failed))
    sys.exit(1)
print("すべての点検に合格")
