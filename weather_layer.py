#!/usr/bin/env python3
"""天気予報（門前仲町）を Open-Meteo から取得する。APIキー不要・標準ライブラリのみ。
製造計画タブで各日の天気・最高/最低気温・降水確率を出し、来客と製造数の判断材料にする。
過去7日＋先14日ぶんを1回取得して30分キャッシュ。失敗時は空（画面では出さない）。"""
import json
import time
import urllib.request

LAT = 35.6716
LON = 139.7959
URL = (
    "https://api.open-meteo.com/v1/forecast"
    f"?latitude={LAT}&longitude={LON}"
    "&daily=weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max"
    "&timezone=Asia%2FTokyo&past_days=7&forecast_days=14"
)

_cache = {"t": 0.0, "map": {}}
_TTL = 1800.0

_WMO = {
    0: ("☀️", "晴れ"),
    1: ("🌤️", "晴れ"), 2: ("⛅", "晴れ時々曇り"), 3: ("☁️", "曇り"),
    45: ("🌫️", "霧"), 48: ("🌫️", "霧"),
    51: ("🌦️", "弱い霧雨"), 53: ("🌦️", "霧雨"), 55: ("🌦️", "強い霧雨"),
    56: ("🌧️", "氷霧雨"), 57: ("🌧️", "氷霧雨"),
    61: ("🌧️", "弱い雨"), 63: ("🌧️", "雨"), 65: ("🌧️", "強い雨"),
    66: ("🌧️", "みぞれ"), 67: ("🌧️", "みぞれ"),
    71: ("❄️", "弱い雪"), 73: ("❄️", "雪"), 75: ("❄️", "強い雪"), 77: ("❄️", "霧雪"),
    80: ("🌦️", "にわか雨"), 81: ("🌧️", "にわか雨"), 82: ("⛈️", "激しいにわか雨"),
    85: ("🌨️", "にわか雪"), 86: ("🌨️", "にわか雪"),
    95: ("⛈️", "雷雨"), 96: ("⛈️", "雷雨"), 99: ("⛈️", "激しい雷雨"),
}


def _icon(code):
    return _WMO.get(int(code), ("", ""))


def daily_map():
    now = time.time()
    if _cache["map"] and now - _cache["t"] < _TTL:
        return _cache["map"]
    try:
        with urllib.request.urlopen(URL, timeout=6) as r:
            data = json.loads(r.read().decode("utf-8"))
        d = data["daily"]
        m = {}
        for i, ds in enumerate(d["time"]):
            y, mo, da = ds.split("-")
            emoji, label = _icon(d["weather_code"][i])
            m[(int(mo), int(da))] = {
                "emoji": emoji, "label": label,
                "tmax": d["temperature_2m_max"][i],
                "tmin": d["temperature_2m_min"][i],
                "pop": d["precipitation_probability_max"][i],
            }
        _cache["t"] = now
        _cache["map"] = m
        return m
    except Exception:
        return _cache["map"]


def _parse_md(s):
    nums = []
    cur = ""
    for ch in str(s):
        if ch.isdigit():
            cur += ch
        elif cur:
            nums.append(int(cur)); cur = ""
    if cur:
        nums.append(int(cur))
    return (nums[0], nums[1]) if len(nums) >= 2 else None


def week_weather(days):
    m = daily_map()
    out = []
    for d in days:
        md = _parse_md(d.get("date", ""))
        out.append(m.get(md) if md else None)
    return out
