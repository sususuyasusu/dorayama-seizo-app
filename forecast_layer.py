#!/usr/bin/env python3
"""製造数予測の画面用データづくりと、毎日のアーカイブ更新（常駐）。

  get_forecast(venue) … 画面（/forecast）に出す全データ。10分キャッシュ。
  tick()              … フォームの取り込み・天気の穴埋め・過去検証・予測の控え。常駐が回す。
  start()             … 常駐を起動（app.py から。外部へは何も送信しない）。

計算は forecast_engine、シートの読み書きは forecast_archive、天気は forecast_weather。
"""
import json
import math
import threading
import time
from datetime import date, datetime, timedelta, timezone

import config_store
import forecast_archive as archive
import forecast_calendar as cal
import forecast_engine as fe
import forecast_weather as fw

JST = timezone(timedelta(hours=9))
DEFAULT_VENUE = "エキュート上野"
HORIZON_DAYS = 35            # 何日先まで出すか
PLAN_DAYS = 7                # 納品計画を出す日数
LOG_HORIZONS = 7             # 予測の控えを残す日数（今日から7日先まで）
SYNC_INTERVAL_SEC = 30 * 60
PAYLOAD_TTL = 600.0
FIRST_DELAY_SEC = 45

_lock = threading.Lock()
_payload = {}                # {venue: (時刻, データ)}
_backtest = {}               # {venue: {"key":..., "rows":..., "calib":..., "accuracy":..., "policy":...}}
status = {"lastTick": None, "lastResult": None, "lastError": None, "lastLog": None}


def _now():
    return datetime.now(JST)


def settings():
    s = dict(fe.DEFAULT_SETTINGS)
    for key, name in (("morningShare", "fc_morning_share"), ("marginBaked", "fc_margin_baked"),
                      ("marginNama", "fc_margin_nama"), ("shelfBaked", "fc_shelf_baked"),
                      ("shelfNama", "fc_shelf_nama")):
        v = fe.to_number(config_store.get_config(name))
        if v is not None:
            s[key] = v
    if not (0.2 <= s["morningShare"] <= 0.8):
        s["morningShare"] = fe.DEFAULT_SETTINGS["morningShare"]
    for k in ("marginBaked", "marginNama"):
        if not (0.0 <= s[k] <= 1.5):
            s[k] = fe.DEFAULT_SETTINGS[k]
    return s


def _round(v, nd=0):
    if v is None:
        return None
    return int(round(v)) if nd == 0 else round(v, nd)


# ───────────────────────── 過去検証（重いので常駐でやってキャッシュ） ─────────────────────────

def _data_key(recs, events):
    n_ev = sum(len(v) for v in events.values())
    return f"{len(recs)}|{recs[-1]['date'].isoformat() if recs else ''}|{n_ev}|" \
           f"{sum((r['qty'].get(p) or 0) for r in recs for p in fe.PRODUCTS)}"


def _weather_of(recs):
    return {r["date"]: r["weather"] for r in recs if r.get("weather")}


def run_backtest(venue, recs, events):
    key = _data_key(recs, events)
    hit = _backtest.get(venue)
    if hit and hit["key"] == key:
        return hit
    if len(recs) < 35:
        res = {"key": key, "rows": [], "calib": {}, "accuracy": {}, "policy": {}, "at": None,
               "note": f"データが{len(recs)}日分のため、過去検証はまだできません（35日分から）"}
        _backtest[venue] = res
        return res
    rows = fe.backtest(recs, _weather_of(recs), events)
    calib = fe.calibration(rows)
    res = {"key": key, "rows": rows, "calib": calib,
           "accuracy": fe.accuracy_summary(rows, calib),
           "policy": fe.simulate_policy(rows, settings()),
           "at": _now().strftime("%Y-%m-%d %H:%M"), "note": ""}
    _backtest[venue] = res
    try:    # 再起動の直後でも幅を出せるよう、ばらつきだけは設定タブに控える
        config_store.set_config(f"fc_calib_{venue}", json.dumps(
            {"key": key, "calib": {k: round(v, 4) for k, v in calib.items()}}, ensure_ascii=False))
    except Exception:
        pass
    return res


_bt_running = set()


def ensure_backtest_async(venue, recs, events):
    """過去検証が古ければ、裏で作り直す（画面の表示は待たせない）。終わったら画面データを作り直させる。"""
    key = _data_key(recs, events)
    hit = _backtest.get(venue)
    if (hit and hit["key"] == key) or venue in _bt_running:
        return False
    _bt_running.add(venue)

    def work():
        try:
            run_backtest(venue, recs, events)
            _payload.pop(venue, None)
        except Exception as e:
            status["lastError"] = f"{_now().strftime('%Y-%m-%d %H:%M')} 過去検証 {type(e).__name__}: {str(e)[:160]}"
        finally:
            _bt_running.discard(venue)

    threading.Thread(target=work, name="forecast-backtest", daemon=True).start()
    return True


def _stored_calib(venue):
    try:
        raw = config_store.get_config(f"fc_calib_{venue}")
        return (json.loads(raw) or {}).get("calib") or {} if raw else {}
    except Exception:
        return {}


# ───────────────────────── 要因の一覧（何がどれだけ効くか） ─────────────────────────

WX_SCENES = [("晴れ・25℃", 0.0, 25.0), ("真夏日 32℃", 0.0, 32.0), ("猛暑日 35℃", 0.0, 35.0),
             ("涼しい 18℃", 0.0, 18.0), ("小雨 2mm", 2.0, 25.0), ("雨 8mm", 8.0, 25.0),
             ("大雨 30mm", 30.0, 25.0)]


def _avg(values):
    vals = [v for v in values if v]
    return (sum(vals) / len(vals), len(vals)) if vals else (None, 0)


def factor_tables(trained, recs, events, target):
    m = trained["models"].get(target)
    if not m:
        return None
    design = trained["design"]
    beta = m["beta"]

    def b(name):
        j = design.index.get(name)
        return beta[j] if j is not None else None

    feats = {r["date"]: fe.features_for(r["date"], r.get("weather"), events) for r in recs}
    value = {r["date"]: fe.target_value(r, target) for r in recs}

    dows = [b("dow%d" % i) or 0.0 for i in range(7)]
    mean_dow = sum(dows) / 7.0
    dow = []
    for i in range(7):
        avg, n = _avg(value[d] for d, f in feats.items() if d.weekday() == i and fe.is_normal_day(f))
        dow.append({"label": cal.WD_JA[i], "mult": round(math.exp(dows[i] - mean_dow), 3),
                    "avg": _round(avg, 1), "n": n})

    # 月: 実績のある月の平均を1.00倍とする。実績の無い月は、となりの月からの推定値
    months = []
    present = sorted({d.month for d in feats})
    mvals = {mo: (b("m%d" % mo) or 0.0) for mo in range(1, 13)}
    mean_m = sum(mvals[mo] for mo in present) / len(present) if present else 0.0
    for mo in range(1, 13):
        avg, n = _avg(value[d] for d in feats if d.month == mo)
        months.append({"label": f"{mo}月", "mult": round(math.exp(mvals[mo] - mean_m), 3),
                       "avg": _round(avg, 1), "n": n, "estimated": mo not in present})

    def wx_effect(rain, tmax):
        row = design.row(fe.weather_features({"rain": rain, "tmax": tmax}), has_weather=True)
        return sum(v * beta[j] for j, v in row if j and fe.group_of(design.names[j - 1]) == "wx")

    ref = wx_effect(0.0, 25.0)
    tmaxs = [r["weather"]["tmax"] for r in recs if r.get("weather")]
    rains = [r["weather"]["rain"] for r in recs if r.get("weather")]
    scenes = []
    for label, rain, tmax in WX_SCENES:
        seen = bool(tmaxs) and min(tmaxs) - 1.0 <= tmax <= max(tmaxs) + 1.0 and rain <= (max(rains) if rains else 0) + 1
        scenes.append({"label": label, "mult": round(math.exp(wx_effect(rain, tmax) - ref), 3), "seen": seen})
    by_label = []
    for label in ("晴れ", "晴れ時々曇り", "曇り", "雨", "大雨"):
        avg, n = _avg(value[r["date"]] for r in recs if r.get("weather") and r["weather"]["label"] == label)
        by_label.append({"label": label, "avg": _round(avg, 1), "n": n})

    calendar = []
    for key in ("hol_wd", "pre_hol", "long_run", "long_first", "long_last", "big", "p_obon", "p_renkyu",
                "p_gw", "p_nenmatsu", "post_big", "vac", "event"):
        coef = b(key)
        if coef is None:
            continue
        ds = [d for d, f in feats.items() if key in f]
        avg, n = _avg(value[d] for d in ds)
        mult = math.exp(coef)
        if key in ("p_obon", "p_renkyu", "p_gw", "p_nenmatsu"):     # 期間の倍率は「大型連休」共通分と合わせて見せる
            mult *= math.exp(b("big") or 0.0)
        calendar.append({"key": key, "label": fe.FEATURE_LABEL.get(key, key), "mult": round(mult, 3),
                         "avg": _round(avg, 1), "n": n})
    return {"base": _round(math.exp(beta[0]), 1), "level": round(math.exp(m["level"]), 3),
            "dow": dow, "month": months, "weather": scenes, "weatherActual": by_label,
            "calendar": calendar, "days": m["n"], "soldoutDays": m["soldoutDays"]}


# ───────────────────────── 味の内訳 ─────────────────────────

def flavor_mix(recs, today):
    """旬どら・生どらの味の比率（直近の味別記録から。新しい日ほど重い）。"""
    out = {}
    for p in ("旬どら", "生どら"):
        num, den, latest = {}, 0.0, None
        for r in recs:
            fl = (r.get("flavors") or {}).get(p)
            age = (today - r["date"]).days
            if not fl or age < 0 or age > 60:
                continue
            tot = float(sum(fl.values()))
            if tot <= 0:
                continue
            w = 0.5 ** (age / 10.0)
            for k, v in fl.items():
                num[k] = num.get(k, 0.0) + w * v / tot
            den += w
            latest = r["date"] if latest is None or r["date"] > latest else latest
        if den:
            mix = {k: v / den for k, v in num.items()}
            mix = {k: round(v, 3) for k, v in sorted(mix.items(), key=lambda x: -x[1]) if v >= 0.03}
            out[p] = {"mix": mix, "latest": latest.isoformat(), "ageDays": (today - latest).days}
    try:    # 設定タブに手入力の比率があれば、それを優先する
        raw = config_store.get_config("fc_flavor_mix")
        manual = json.loads(raw) if raw else {}
        for p, mix in (manual or {}).items():
            tot = sum(float(v) for v in mix.values())
            if p in ("旬どら", "生どら") and tot > 0:
                out[p] = {"mix": {k: round(float(v) / tot, 3) for k, v in mix.items()},
                          "latest": None, "ageDays": None, "manual": True}
    except Exception:
        pass
    return out


# ───────────────────────── 画面用データ ─────────────────────────

def _day_info(d, events):
    c = cal.describe(d)
    evs = [e for e in events.get(d, [])]
    return {"date": d.isoformat(), "md": f"{d.month}/{d.day}", "wd": c["wd"], "wdLabel": c["wdLabel"],
            "holiday": c["holiday"], "isOff": c["isOff"], "period": c["period"], "vacation": c["vacation"],
            "events": [e["name"] for e in evs if e["kind"] != "除外"],
            "excluded": any(e["kind"] == "除外" for e in evs)}


def build(venue=None, today=None):
    venue = venue or DEFAULT_VENUE
    now = _now()
    today = today or now.date()
    warnings = []

    recs = archive.load_daily(venue)
    if not recs:
        archive.sync(today)
        recs = archive.load_daily(venue)
    if not recs:
        raise RuntimeError(f"「{venue}」の実績がアーカイブにありません（日報フォームの店舗名を確認してください）")
    events = archive.load_events(venue)
    st = settings()

    hist_wx = _weather_of(recs)
    no_wx = [r["date"] for r in recs if not r.get("weather") and r["date"] < today]
    if no_wx:
        warnings.append(f"天気が未取得の日が{len(no_wx)}日あります（次の取り込みで自動的に埋めます）")

    try:
        fwx = {date(*map(int, k.split("-"))): v for k, v in fw.forecast(venue).items()}
    except Exception as e:
        fwx = {}
        warnings.append(f"天気予報を取得できませんでした。天気は平年並みとして計算しています（{str(e)[:60]}）")

    trained = fe.train(recs, hist_wx, events)
    if not trained or fe.TOTAL not in trained["models"]:
        raise RuntimeError(f"「{venue}」は実績が{len(recs)}日分のため、まだ予測できません"
                           f"（{fe.MIN_TRAIN_DAYS}日分から）")
    bt = _backtest.get(venue)
    if bt and bt["key"] != _data_key(recs, events):
        bt = None                                  # データが変わったので、検証は作り直し待ち
    if bt is None:
        ensure_backtest_async(venue, recs, events)
    calib = (bt or {}).get("calib") or _stored_calib(venue)

    # 予測（今日から）。納品計画のため1日多く作る
    days = [today + timedelta(days=i) for i in range(HORIZON_DAYS + 1)]
    fcs = {}
    for d in days:
        fcs[d] = fe.forecast_day(trained, d, fwx.get(d), events, calib)

    # 納品計画: 直近の日報の「夜の在庫」から始める
    by_date = {r["date"]: r for r in recs}
    last = recs[-1]
    if last["date"] >= today - timedelta(days=1):
        plan_start = last["date"] + timedelta(days=1)
        stock0 = {p: last["stock"].get(p) for p in fe.PRODUCTS if last["stock"].get(p) is not None}
        stock_note = f"前夜の在庫は {last['date'].month}/{last['date'].day} の日報の値です"
        missing_stock = [p for p in fe.PRODUCTS if p not in stock0]
        if missing_stock:
            stock_note += "（" + "・".join(missing_stock) + "は未記入のため目安で計算）"
    else:
        plan_start = today
        stock0 = {}
        gap = (today - last["date"]).days
        stock_note = (f"日報が {last['date'].month}/{last['date'].day} から{gap}日分届いていないため、"
                      f"前夜の在庫は目安どおりあったものとして計算しています")
        warnings.append(stock_note)
    plan_days = [plan_start + timedelta(days=i) for i in range(PLAN_DAYS + 1)]
    plan_in = [{"date": d, "qty": {p: fcs[d][p]["p50"] for p in fe.PRODUCTS if p in fcs.get(d, {})}}
               for d in plan_days if d in fcs]
    plan = {x["date"]: x["items"] for x in fe.recommend(plan_in, stock0, st)}

    mix = flavor_mix(recs, today)

    forecast = []
    for d in days[:HORIZON_DAYS]:
        fc = fcs[d]
        wx = fwx.get(d)
        item = _day_info(d, events)
        item["weather"] = wx
        item["weatherAssumed"] = wx is None
        item["qty"] = {p: {"p50": _round(fc[p]["p50"]), "lo": _round(fc[p]["lo"]), "hi": _round(fc[p]["hi"])}
                       for p in fe.PRODUCTS if p in fc}
        for key, t in (("total", fe.TOTAL), ("sales", "売上"), ("customers", "客数")):
            if t in fc:
                item[key] = {"p50": _round(fc[t]["p50"]), "lo": _round(fc[t]["lo"]), "hi": _round(fc[t]["hi"])}
        item["factors"] = {p: {g: round(v, 3) for g, v in fc[p]["factors"].items()}
                           for p in list(fe.PRODUCTS) + [fe.TOTAL, "売上"] if p in fc}
        if d in plan:
            item["plan"] = {p: {"delivery": _round(v["delivery"]), "nightTarget": _round(v["nightTarget"]),
                                "stockBefore": _round(v["stockBefore"]), "stockAssumed": v["stockAssumed"],
                                "expectedLoss": _round(v["expectedLoss"]), "note": v["note"]}
                            for p, v in plan[d].items()}
        forecast.append(item)

    history = []
    for r in recs[-150:]:
        item = _day_info(r["date"], events)
        item["weather"] = r.get("weather")
        item["qty"] = {p: r["qty"].get(p) for p in fe.PRODUCTS}
        item["stock"] = {p: r["stock"].get(p) for p in fe.PRODUCTS}
        item["total"] = fe.target_value(r, fe.TOTAL)
        item["sales"] = fe.target_value(r, "売上")
        item["customers"] = fe.target_value(r, "客数")
        item["soldout"] = [p for p in fe.PRODUCTS if fe.is_sold_out(r["qty"].get(p), r["stock"].get(p))]
        item["source"] = r.get("source", "")
        item["flags"] = r.get("flagsText", "")
        item["flavors"] = r.get("flavors") or {}
        history.append(item)

    # 直近60日で日報が無い日（会期の切れ目＝7日以上の空きは「出店していない」とみなして数えない）
    missing = []
    for a, b in zip(recs, recs[1:]):
        gap = (b["date"] - a["date"]).days
        if 1 < gap <= 7 and (today - b["date"]).days <= 60:
            missing += [(a["date"] + timedelta(days=k)).isoformat() for k in range(1, gap)]
    flagged = [{"date": r["date"].isoformat(), "text": r["flagsText"]} for r in recs[-60:] if r.get("flagsText")]
    sources = {}
    for r in recs:
        sources[r.get("source") or "日報フォーム"] = sources.get(r.get("source") or "日報フォーム", 0) + 1

    targets = list(fe.PRODUCTS) + [fe.TOTAL, "売上", "客数"]
    factors = {t: factor_tables(trained, recs, events, t) for t in targets}

    accuracy = None
    if bt and bt.get("accuracy"):
        accuracy = {"at": bt["at"], "days": max(a["days"] for a in bt["accuracy"].values()),
                    "byTarget": {t: {k: (round(v, 4) if isinstance(v, float) else v) for k, v in a.items()}
                                 for t, a in bt["accuracy"].items()},
                    "policy": {p: {k: (round(v, 4) if isinstance(v, float) else v) for k, v in s.items()}
                               for p, s in bt["policy"].items()}}
    elif bt and bt.get("note"):
        accuracy = {"note": bt["note"]}

    return {
        "venue": venue, "venues": sorted(v for v in fw.VENUES),
        "generatedAt": now.strftime("%Y-%m-%d %H:%M"), "today": today.isoformat(),
        "products": fe.PRODUCTS, "nama": sorted(fe.NAMA), "settings": st,
        "planStart": plan_start.isoformat(), "stockNote": stock_note,
        "data": {"days": len(recs), "first": recs[0]["date"].isoformat(), "last": last["date"].isoformat(),
                 "sources": sources, "missing": missing, "flagged": flagged,
                 # 再起動の直後でも分かるよう、取り込み時刻が無ければシートの更新日時の最新を使う
                 "lastSync": archive.status.get("lastSync") or max(
                     (r.get("updatedAt") or "" for r in recs), default="") or None,
                 "links": archive.sheet_links()},
        "model": {"trainedDays": trained["days"], "factorsCount": trained["design"].p - 1,
                  "shares": {p: round(v, 4) for p, v in (trained.get("shares") or {}).items()},
                  "calibrated": bool(calib)},
        "forecast": forecast, "history": history, "factors": factors,
        "flavorMix": mix, "accuracy": accuracy, "logAccuracy": log_accuracy(venue, by_date),
        "events": [{"date": d.isoformat(), "name": e["name"], "kind": e["kind"], "factor": e["factor"]}
                   for d in sorted(events) for e in events[d] if d >= today - timedelta(days=60)],
        "warnings": warnings + list(cal.warnings),
    }


def log_accuracy(venue, by_date):
    """実際に控えておいた予測（天気予報が外れる分も含む）の当たり具合。"""
    try:
        logs = archive.load_log(venue)
    except Exception:
        return None
    rows = {}
    for lg in logs:
        rec = by_date.get(lg["target"])
        h = lg["horizon"]
        if rec is None or h is None:
            continue
        for p in fe.PRODUCTS:
            a, f = rec["qty"].get(p), lg["pred"].get(p)
            if a and f:
                e = rows.setdefault(int(h), {"err": 0.0, "act": 0.0, "days": set()})
                e["err"] += abs(f - a)
                e["act"] += a
                e["days"].add(lg["target"])
    out = [{"horizon": h, "errorRate": round(v["err"] / v["act"], 4), "days": len(v["days"])}
           for h, v in sorted(rows.items()) if v["act"] and len(v["days"]) >= 5]
    return {"byHorizon": out, "logged": len({lg["made"] for lg in logs})}


_last_manual_sync = {"t": 0.0}


def get_forecast(venue=None, refresh=False):
    """画面用データ。refresh=True（「最新にする」）のときは、日報の取り込みからやり直す。"""
    venue = venue if venue in fw.VENUES else DEFAULT_VENUE
    hit = _payload.get(venue)
    if hit and not refresh and time.time() - hit[0] < PAYLOAD_TTL:
        return hit[1]
    with _lock:
        hit = _payload.get(venue)
        if hit and not refresh and time.time() - hit[0] < PAYLOAD_TTL:
            return hit[1]
        sync_error = None
        if refresh and time.time() - _last_manual_sync["t"] > 60:    # 連打されても取り込みは1分に1回まで
            _last_manual_sync["t"] = time.time()
            try:
                archive.sync()
            except Exception as e:
                sync_error = f"日報の取り込みに失敗しました。前回までのデータで表示しています（{str(e)[:80]}）"
        data = build(venue)
        if sync_error:
            data["warnings"].insert(0, sync_error)
        _payload[venue] = (time.time(), data)
        return data


# ───────────────────────── 毎日の更新（常駐） ─────────────────────────

def write_log(venue, payload):
    """今日の予測を控える（1日1回）。"""
    made = date(*map(int, payload["today"].split("-")))
    stamp = _now().strftime("%Y-%m-%d %H:%M")
    rows = []
    for item in payload["forecast"][:LOG_HORIZONS + 1]:
        d = date(*map(int, item["date"].split("-")))
        wx = item.get("weather") or {}
        row = [made.isoformat(), venue, d.isoformat(), (d - made).days,
               wx.get("label", "平年並み"), wx.get("tmax", ""), wx.get("rain", "")]
        row += [item["qty"].get(p, {}).get("p50", "") for p in fe.PRODUCTS]
        row += [(item.get("sales") or {}).get("p50", ""), (item.get("customers") or {}).get("p50", "")]
        row += [((item.get("plan") or {}).get(p) or {}).get("delivery", "") for p in fe.PRODUCTS]
        rows.append(row + [stamp])
    return archive.append_log(venue, made, rows)


def tick(venue=None, with_log=True):
    """取り込み → 過去検証 → 画面データの作り直し → 予測の控え。結果の要約を返す。"""
    venue = venue or DEFAULT_VENUE
    res = {"at": _now().strftime("%Y-%m-%d %H:%M")}
    res["sync"] = archive.sync()
    recs = archive.load_daily(venue)
    events = archive.load_events(venue)
    bt = run_backtest(venue, recs, events)
    res["backtestDays"] = len({r["date"] for r in bt["rows"]})
    with _lock:
        payload = build(venue)
        _payload[venue] = (time.time(), payload)
    res["forecastDays"] = len(payload["forecast"])
    # 予測の控えは朝5時以降に1日1回（前夜の日報が入ってから。夜中の再起動で古い状態を控えない）
    if with_log and _now().hour >= 5:
        res["logged"] = write_log(venue, payload)
        if res["logged"]:
            status["lastLog"] = payload["today"]
    status.update({"lastTick": res["at"], "lastResult": res, "lastError": None})
    return res


def _loop():
    time.sleep(FIRST_DELAY_SEC)
    while True:
        try:
            tick()
        except Exception as e:     # 失敗しても常駐は止めない。理由は /api/forecast/status で見える
            status["lastError"] = f"{_now().strftime('%Y-%m-%d %H:%M')} {type(e).__name__}: {str(e)[:200]}"
            print(f"[forecast] エラー: {status['lastError']}", flush=True)
        time.sleep(SYNC_INTERVAL_SEC)


def start():
    threading.Thread(target=_loop, name="forecast-archive", daemon=True).start()
    print("[forecast] 製造数予測のアーカイブ更新を開始（30分ごと）", flush=True)


def get_status():
    return {"tick": status, "archive": archive.status,
            "backtest": {v: {"at": b.get("at"), "note": b.get("note")} for v, b in _backtest.items()}}


if __name__ == "__main__":
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else "show"
    if cmd == "sync":
        print(json.dumps(archive.sync(), ensure_ascii=False, indent=1))
    elif cmd == "tick":
        print(json.dumps(tick(with_log="--no-log" not in sys.argv), ensure_ascii=False, indent=1, default=str))
    else:
        recs = archive.load_daily(DEFAULT_VENUE)
        run_backtest(DEFAULT_VENUE, recs, archive.load_events(DEFAULT_VENUE))
        p = build()
        print(f"{p['venue']} 実績{p['data']['days']}日（{p['data']['first']}〜{p['data']['last']}）"
              f" 納品計画の開始 {p['planStart']}")
        print(p["stockNote"])
        for w in p["warnings"]:
            print("注意:", w)
        for item in p["forecast"][:10]:
            wx = item.get("weather") or {}
            q = " ".join(f"{k[:2]}{v['p50']:>4}" for k, v in item["qty"].items())
            pl = " ".join(f"{k[:2]}{v['delivery']:>4}" for k, v in (item.get("plan") or {}).items())
            print(f"{item['md']:>5}({item['wdLabel']}) {wx.get('label', '平年並み'):<6} {item['holiday'] or item['period']:<6}"
                  f" 売上{item['sales']['p50']:>8,} | 販売 {q} | 納品 {pl}")
