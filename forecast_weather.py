#!/usr/bin/env python3
"""製造数予測のための天気（会場の場所ごと）。Open-Meteo を使用。APIキー不要・標準ライブラリのみ。

お客様が実際に体験する天気に寄せるため、1日の合計ではなく「営業時間（8〜22時）」の
雨量・雨の時間数・雲量を時間別データから作る。夜中だけの雨は売上に効かないため。

取得に失敗したら例外を上げる（黙って空を返さない）。呼び出し側がアーカイブ済みの天気で
代替し、画面に注意を出す。
"""
import json
import time
import urllib.request
from datetime import date, datetime, timedelta, timezone

JST = timezone(timedelta(hours=9))
OPEN_HOUR, CLOSE_HOUR = 8, 22      # 営業時間（この時間帯の雨を数える）
DAYLIGHT_END = 18                  # 雲量は日中（8〜18時）で見る

# 会場の場所。会場を増やすときはここに足す（日報フォームの店舗名と同じ名前で）。
VENUES = {
    "エキュート上野": (35.7138, 139.7773),
    "エキュート秋葉原": (35.6984, 139.7731),
    "新宿ニュウマン": (35.6889, 139.7006),
    "ルミネ大宮": (35.9064, 139.6237),
    "ルミネ立川": (35.6980, 139.4137),
    "日本橋高島屋": (35.6811, 139.7737),
    "日本橋三越": (35.6858, 139.7737),
    "東京大丸": (35.6814, 139.7690),
    "池袋西武": (35.7289, 139.7113),
    "池袋東武": (35.7303, 139.7095),
    "松坂屋上野": (35.7071, 139.7745),
    "エキア川越": (35.9074, 139.4829),
    "阪神梅田": (34.7010, 135.4974),
    "梅田大丸": (34.7017, 135.4963),
    "京都高島屋": (35.0037, 135.7686),
}

_HOURLY = "temperature_2m,precipitation,cloud_cover"
_DAILY = "temperature_2m_max,temperature_2m_min,precipitation_sum,weather_code"
_cache = {}


def _get(url, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": "dorayama-seizo-app/forecast"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


FORECAST_TTL = 600.0            # 天気予報は10分ごとに取り直す（予報が変わったら、予測もすぐ変わるように）
STALE_OK = 36 * 3600.0          # 取得に失敗したとき、ここまで古い予報なら代わりに使う（36時間）
MIN_FUTURE_DAYS = 7             # きょう以降がこの日数そろっていない予報は「取得失敗」として扱う
_info = {}                      # {キー: {"fetchedAt": 時刻, "stale": 古い予報で代用中か, "error": 理由}}


def _cached(key, ttl, fn, stale_ok=0.0):
    """ttl秒は使い回す。取り直しに失敗したら、stale_ok秒以内の前回分で代用する（代用中の印を残す）。
    前回分も無ければ例外を上げる（黙って空を返さない）。"""
    now = time.time()
    hit = _cache.get(key)
    if hit and now - hit[0] < ttl:
        return hit[1]
    try:
        val = fn()
    except Exception as e:
        if hit and now - hit[0] < stale_ok:
            _info[key] = {"fetchedAt": hit[0], "stale": True, "error": str(e)[:100]}
            return hit[1]
        raise
    _cache[key] = (now, val)
    _info[key] = {"fetchedAt": now, "stale": False, "error": None}
    return val


def label_of(rain_open, cloud):
    """営業時間の雨量と日中の雲量から、ひと目で分かる天気の区分を作る。"""
    if rain_open is None:
        return ""
    if rain_open >= 10:
        return "大雨"
    if rain_open >= 1:
        return "雨"
    if cloud is None:
        return "晴れ"
    if cloud >= 75:
        return "曇り"
    if cloud >= 35:
        return "晴れ時々曇り"
    return "晴れ"


EMOJI = {"晴れ": "☀️", "晴れ時々曇り": "⛅", "曇り": "☁️", "雨": "🌧️", "大雨": "⛈️", "": ""}


def _aggregate(payload, with_pop=False):
    """時間別データを日別にまとめる。返り値: {date文字列: {...}}"""
    h = payload.get("hourly") or {}
    d = payload.get("daily") or {}
    times = h.get("time") or []
    temp, prec, cloud = h.get("temperature_2m") or [], h.get("precipitation") or [], h.get("cloud_cover") or []
    pop = h.get("precipitation_probability") or []
    by = {}
    for i, t in enumerate(times):
        day, hh = t[:10], int(t[11:13])
        rec = by.setdefault(day, {"rain": 0.0, "rainN": 0, "rainHours": 0, "cloudSum": 0.0, "cloudN": 0,
                                  "tempMaxOpen": None, "pop": None})
        if OPEN_HOUR <= hh < CLOSE_HOUR:
            p = prec[i] if i < len(prec) else None
            if p is not None:
                rec["rain"] += p
                rec["rainN"] += 1
                if p >= 0.3:
                    rec["rainHours"] += 1
            if with_pop and i < len(pop) and pop[i] is not None:
                rec["pop"] = pop[i] if rec["pop"] is None else max(rec["pop"], pop[i])
        if OPEN_HOUR <= hh < DAYLIGHT_END:
            c = cloud[i] if i < len(cloud) else None
            if c is not None:
                rec["cloudSum"] += c
                rec["cloudN"] += 1
    out = {}
    dt = d.get("time") or []
    for i, day in enumerate(dt):
        rec = by.get(day) or {}
        tmax = (d.get("temperature_2m_max") or [None] * len(dt))[i]
        tmin = (d.get("temperature_2m_min") or [None] * len(dt))[i]
        if tmax is None:
            continue                       # まだ値が無い日（再解析の遅れ）は入れない
        rain_open = round(rec["rain"], 1) if rec.get("rainN", 0) >= 10 else None
        cloud_mean = round(rec["cloudSum"] / rec["cloudN"]) if rec.get("cloudN", 0) >= 6 else None
        if rain_open is None:              # 時間別が揃わない日は1日合計で代用
            total = (d.get("precipitation_sum") or [None] * len(dt))[i]
            rain_open = round(total, 1) if total is not None else None
        label = label_of(rain_open, cloud_mean)
        out[day] = {
            "label": label, "emoji": EMOJI.get(label, ""),
            "tmax": round(tmax, 1), "tmin": round(tmin, 1) if tmin is not None else None,
            "rain": rain_open, "rainHours": rec.get("rainHours", 0) if rec else 0,
            "cloud": cloud_mean, "pop": rec.get("pop") if rec else None,
        }
    return out


def _coords(venue):
    if venue not in VENUES:
        raise KeyError(f"会場「{venue}」の場所が未登録です（forecast_weather.VENUES に追加してください）")
    return VENUES[venue]


def recent_and_forecast(venue, past_days=92, forecast_days=16):
    """直近の実績（最大92日前まで）と16日先までの予報。10分ごとに取り直す。"""
    lat, lon = _coords(venue)

    def load():
        url = ("https://api.open-meteo.com/v1/forecast"
               f"?latitude={lat}&longitude={lon}&hourly={_HOURLY},precipitation_probability&daily={_DAILY}"
               f"&timezone=Asia%2FTokyo&past_days={past_days}&forecast_days={forecast_days}")
        data = _aggregate(_get(url), with_pop=True)
        # 返ってきた予報が欠けていないか確かめる。きょう以降が抜けた予報をそのまま使うと、
        # 全日が「平年並み」扱いになってしまう（2026-09-29 朝の控えで発生）。欠けていたら失敗として扱い、
        # 直前の予報で計算を続ける。
        today = datetime.now(JST).date().isoformat()
        future = [k for k in data if k >= today]
        if today not in data or len(future) < MIN_FUTURE_DAYS:
            raise RuntimeError(f"天気予報が欠けています（きょう以降が{len(future)}日分）")
        return data
    return _cached(("fc", venue, past_days, forecast_days), FORECAST_TTL, load, stale_ok=STALE_OK)


def forecast_info(venue, past_days=92, forecast_days=16):
    """天気予報をいつ取得したか。返り値: {"fetchedAt": "YYYY-MM-DD HH:MM", "stale": bool, "error": str|None}"""
    i = _info.get(("fc", venue, past_days, forecast_days))
    if not i:
        return {"fetchedAt": None, "stale": False, "error": None}
    at = datetime.fromtimestamp(i["fetchedAt"], JST).strftime("%Y-%m-%d %H:%M")
    return {"fetchedAt": at, "stale": i["stale"], "error": i["error"]}


def history(venue, start, end):
    """過去の天気（再解析データ）。数日前までしか確定しない。6時間キャッシュ。"""
    lat, lon = _coords(venue)
    s = start.isoformat() if isinstance(start, date) else str(start)
    e = end.isoformat() if isinstance(end, date) else str(end)

    def load():
        url = ("https://archive-api.open-meteo.com/v1/archive"
               f"?latitude={lat}&longitude={lon}&start_date={s}&end_date={e}"
               f"&hourly={_HOURLY}&daily={_DAILY}&timezone=Asia%2FTokyo")
        return _aggregate(_get(url))
    return _cached(("hist", venue, s, e), 6 * 3600.0, load)


def actuals(venue, dates):
    """指定日の天気の実績を返す。直近は予報APIの過去分、それより古い日は再解析から。
    返り値: ({date文字列: 天気}, 取れなかった日のリスト)"""
    today = datetime.now(JST).date()
    want = sorted({d for d in dates if d < today})
    if not want:
        return {}, []
    out = {}
    recent_from = today - timedelta(days=90)
    if any(d >= recent_from for d in want):
        rec = recent_and_forecast(venue)
        for d in want:
            w = rec.get(d.isoformat())
            if d >= recent_from and w:
                out[d.isoformat()] = w
    old = [d for d in want if d.isoformat() not in out]
    if old:
        hist = history(venue, min(old), max(old))
        for d in old:
            w = hist.get(d.isoformat())
            if w:
                out[d.isoformat()] = w
    missing = [d for d in want if d.isoformat() not in out]
    return out, missing


def forecast(venue):
    """今日以降の予報（今日を含む）。返り値: {date文字列: 天気}"""
    today = datetime.now(JST).date().isoformat()
    return {k: v for k, v in recent_and_forecast(venue).items() if k >= today}


if __name__ == "__main__":
    v = "エキュート上野"
    f = forecast(v)
    print("予報:", len(f), "日分")
    for k in sorted(f)[:16]:
        print(" ", k, f[k])
    a, miss = actuals(v, [date(2026, 3, 30), date(2026, 8, 15), date(2026, 9, 21), date(2026, 9, 27)])
    for k in sorted(a):
        print(" 実績", k, a[k])
    print(" 取れなかった日:", miss)
