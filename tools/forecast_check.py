#!/usr/bin/env python3
"""製造数予測の点検（読み取りのみ・シートへは書き込まない）。
計算の設定を変えたときや、予測がおかしいと感じたときに、当たり具合を確かめるために使う。

使い方:
  python3 tools/forecast_check.py            … 当たり具合と納品ルールの試算を表示
  python3 tools/forecast_check.py 黒どら      … その項目を、日ごとに「実績 対 予測」で表示
  python3 tools/forecast_check.py --compare  … 設定を少しずつ変えた場合と比べる
"""
import math
import os
import sys
import time
import warnings

warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import forecast_archive as archive
import forecast_calendar as cal
import forecast_engine as fe

VENUE = "エキュート上野"


def load():
    recs = archive.load_daily(VENUE)
    events = archive.load_events(VENUE)
    weather = {r["date"]: r["weather"] for r in recs if r.get("weather")}
    return recs, weather, events


def pc(v):
    return "  -  " if v is None else f"{v * 100:5.1f}"


def summary(recs, weather, events, label="現在の設定", **opt):
    saved = dict(fe.OPT)
    fe.OPT.update(opt)
    t0 = time.time()
    try:
        bt = fe.backtest(recs, weather, events)
    finally:
        fe.OPT.clear()
        fe.OPT.update(saved)
    calib = fe.calibration(bt)
    acc = fe.accuracy_summary(bt, calib)
    return bt, calib, acc, time.time() - t0


if __name__ == "__main__":
    recs, weather, events = load()
    print(f"{VENUE} 実績{len(recs)}日（{recs[0]['date']}〜{recs[-1]['date']}） 特別日{sum(len(v) for v in events.values())}件")
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if "--compare" in sys.argv:
        for label, opt in (("現在の設定", {}), ("直近の勢いなし", {"levelShrink": 0.0}),
                           ("気温を使わない", {"useTemp": False}), ("連休明けを使わない", {"postBig": False}),
                           ("重み半減期90日", {"halfLife": 90.0}), ("重み半減期365日", {"halfLife": 365.0})):
            bt, calib, acc, sec = summary(recs, weather, events, label, **opt)
            print(f"{label:<12} " + "  ".join(f"{t[:3]}{pc(acc[t]['errorRate'])}" for t in acc) + f"  {sec:.1f}秒")
        sys.exit(0)
    bt, calib, acc, sec = summary(recs, weather, events)
    if args:
        t = args[0]
        print(f"[{t}] 日付 曜 | 実績 予測 (曜日平均) | ずれ | 天気 | 暦")
        for r in bt:
            if r["target"] != t:
                continue
            c = cal.describe(r["date"])
            w = weather.get(r["date"]) or {}
            err = (r["pred"] - r["actual"]) / r["actual"] * 100
            tag = " ".join(x for x in (c["holiday"], c["period"], "売切" if r["soldout"] else "") if x)
            print(f"{r['date']} {c['wdLabel']} | {r['actual']:>8,.0f} {r['pred']:>8,.0f} ({(r['baseline'] or 0):>8,.0f}) | "
                  f"{err:>+5.0f}% {'★' if abs(err) > 35 else ' '} | {w.get('label', ''):<6} {w.get('tmax', '')}℃ 雨{w.get('rain', '')} | {tag}")
        sys.exit(0)
    print(f"過去検証 {len({r['date'] for r in bt})}日・{sec:.1f}秒")
    print("項目      日数 平均実績 | ずれ (ふつうの日/連休・特別日) 3日合計 | くせ | 曜日平均→予測 | 幅の的中 ばらつき")
    for t, a in acc.items():
        print(f"{t:<7} {a['days']:>3} {a['meanActual']:>9.1f} | {pc(a['errorRate'])} ({pc(a['errorRateNormal'])}/{pc(a['errorRateSpecial'])}) "
              f"{pc(a['errorRate3day'])} | {a['bias'] * 100:+5.1f} | {pc(a['baselineErrorRate'])}→{pc(a['modelErrorRateSameDays'])} | "
              f"{pc(a['insideRange'])} {calib.get(t, 0):.3f}")
    print("\n納品ルールを過去に当てはめた試算（需要＝実際の販売数）")
    for label, s in (("決めたルール 焼き3割・生1割5分", {}), ("余裕なし", {"marginBaked": 0.0, "marginNama": 0.0}),
                     ("焼き4割・生2割5分", {"marginBaked": 0.4, "marginNama": 0.25}),
                     ("焼き5割・生3割", {"marginBaked": 0.5, "marginNama": 0.3})):
        st = dict(fe.DEFAULT_SETTINGS)
        st.update(s)
        print(f"[{label}]")
        for p, v in fe.simulate_policy(bt, st).items():
            print(f"   {p:<6} 納品{v['delivered']:>6.0f} 販売{v['sold']:>6.0f} 売り逃し{v['lostSales']:>5.0f}({v['lostRate'] * 100:4.1f}%) "
                  f"期限切れ{v['expired']:>5.0f}({v['expiredRate'] * 100:4.1f}%) 品切れ日{v['shortDays']:>2}/{v['days']}")
    tr = fe.train(recs, weather, events)
    print("\n月ごとの倍率（合計個数）: " + "  ".join(
        f"{m}月×{math.exp(tr['models'][fe.TOTAL]['beta'][tr['design'].index['m%d' % m]]):.2f}"
        + ("" if m in tr["design"].months_seen else "(推定)") for m in range(1, 13)))
