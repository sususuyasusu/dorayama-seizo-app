#!/usr/bin/env python3
"""週タブ名→年つき日付の割り当て(week_tabs)の自己点検。シートには触らない。

去年の同じ時期のタブ（0105 など）と来年のタブ（0104 など）が並ぶ並びで、
いつの時点で見ても年を取り違えないことを確かめる。"""
import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import week_tabs  # noqa: E402

OLD = ["91", "_manual_history", "98", "915", "922", "929", "106"]
d = datetime.date(2025, 10, 13)
real = {}
titles = list(OLD)
skip = {datetime.date(2026, 1, 26), datetime.date(2026, 2, 2), datetime.date(2026, 2, 9)}   # 実際に無い週
while d <= datetime.date(2027, 5, 31):
    if d not in skip:
        t = week_tabs.name_for(d)
        assert t not in real, f"名前が重なる: {t}"
        real[t] = d
        titles.append(t)
    d += datetime.timedelta(days=7)
titles += ["シート28", "_app_made", "_fc_log"]
real.update({"915": datetime.date(2025, 9, 15), "922": datetime.date(2025, 9, 22),
             "929": datetime.date(2025, 9, 29), "106": datetime.date(2025, 10, 6)})

ng = 0
for today in [datetime.date(2026, 11, 25), datetime.date(2026, 12, 31), datetime.date(2027, 1, 1),
              datetime.date(2027, 1, 4), datetime.date(2027, 3, 3), datetime.date(2027, 5, 30),
              datetime.date(2027, 6, 20),          # 今週のタブがまだ無い日
              datetime.date(2026, 2, 4)]:          # 今週のタブが無い週（欠番）
    got = week_tabs.assign_dates(titles, today)
    bad = [(t, got.get(t), want) for t, want in real.items() if got.get(t) != want]
    near = [t for t, want in real.items() if week_tabs.nearest(t, today) != want]
    print(f"{today}: 週タブ {len(got)}枚 取り違え {len(bad)}件"
          f"（旧方式『今日に近い年』なら {len(near)}件を取り違え）{bad[:3]}")
    ng += len(bad)
print("OK" if ng == 0 else f"NG {ng}件")
sys.exit(1 if ng else 0)
