#!/usr/bin/env python3
"""製造数予測のための暦（祝日・連休・長期休み）。標準ライブラリのみ。

祝日は内蔵の表（2025〜2027年）を正とし、表に無い年は公開データ（holidays-jp）から
取得を試みる。取得できない年は「祝日なし」として扱い、その旨を warnings に残す
（黙って祝日ゼロで予測しない）。
"""
import json
import time
import urllib.request
from datetime import date, timedelta

WD_JA = ["月", "火", "水", "木", "金", "土", "日"]

# 内閣府の祝日（holidays-jp 2026-09-28 取得分と照合済み）
HOLIDAYS = {
    "2025-01-01": "元日", "2025-01-13": "成人の日", "2025-02-11": "建国記念の日",
    "2025-02-23": "天皇誕生日", "2025-02-24": "振替休日", "2025-03-20": "春分の日",
    "2025-04-29": "昭和の日", "2025-05-03": "憲法記念日", "2025-05-04": "みどりの日",
    "2025-05-05": "こどもの日", "2025-05-06": "振替休日", "2025-07-21": "海の日",
    "2025-08-11": "山の日", "2025-09-15": "敬老の日", "2025-09-23": "秋分の日",
    "2025-10-13": "スポーツの日", "2025-11-03": "文化の日", "2025-11-23": "勤労感謝の日",
    "2025-11-24": "振替休日",
    "2026-01-01": "元日", "2026-01-12": "成人の日", "2026-02-11": "建国記念の日",
    "2026-02-23": "天皇誕生日", "2026-03-20": "春分の日", "2026-04-29": "昭和の日",
    "2026-05-03": "憲法記念日", "2026-05-04": "みどりの日", "2026-05-05": "こどもの日",
    "2026-05-06": "振替休日", "2026-07-20": "海の日", "2026-08-11": "山の日",
    "2026-09-21": "敬老の日", "2026-09-22": "国民の休日", "2026-09-23": "秋分の日",
    "2026-10-12": "スポーツの日", "2026-11-03": "文化の日", "2026-11-23": "勤労感謝の日",
    "2027-01-01": "元日", "2027-01-11": "成人の日", "2027-02-11": "建国記念の日",
    "2027-02-23": "天皇誕生日", "2027-03-21": "春分の日", "2027-03-22": "振替休日",
    "2027-04-29": "昭和の日", "2027-05-03": "憲法記念日", "2027-05-04": "みどりの日",
    "2027-05-05": "こどもの日", "2027-07-19": "海の日", "2027-08-11": "山の日",
    "2027-09-20": "敬老の日", "2027-09-23": "秋分の日", "2027-10-11": "スポーツの日",
    "2027-11-03": "文化の日", "2027-11-23": "勤労感謝の日",
}
_BUILTIN_YEARS = {2025, 2026, 2027}
_remote = {"t": 0.0, "map": None, "error": None}
_REMOTE_TTL = 24 * 3600.0
warnings = []


def _remote_holidays():
    now = time.time()
    if _remote["map"] is not None and now - _remote["t"] < _REMOTE_TTL:
        return _remote["map"]
    try:
        with urllib.request.urlopen("https://holidays-jp.github.io/api/v1/date.json", timeout=8) as r:
            m = json.loads(r.read().decode("utf-8"))
        _remote.update({"t": now, "map": m, "error": None})
    except Exception as e:  # 取れない時は空で返すが、理由は残す
        _remote.update({"t": now, "map": {}, "error": str(e)[:120]})
    return _remote["map"]


def holiday_name(d):
    """祝日名（祝日でなければ None）。"""
    key = d.isoformat()
    if d.year in _BUILTIN_YEARS:
        return HOLIDAYS.get(key)
    m = _remote_holidays()
    if not any(k.startswith(str(d.year)) for k in m):
        msg = f"{d.year}年の祝日データが無いため、祝日なしとして計算しています"
        if msg not in warnings:
            warnings.append(msg)
        return None
    name = m.get(key)
    return name.replace("振替休日", "").strip() + "（振替休日）" if name and "振替休日" in name and name != "振替休日" else name


def is_off(d):
    """世間が休みの日（土日・祝日）。"""
    return d.weekday() >= 5 or holiday_name(d) is not None


def _in(d, m1, d1, m2, d2):
    """月日の範囲（年またぎ対応）に入っているか。"""
    a, b, x = (m1, d1), (m2, d2), (d.month, d.day)
    return a <= x <= b if a <= b else (x >= a or x <= b)


def off_run(d):
    """d を含む連休の (長さ, 何日目か)。休みでなければ (0, 0)。"""
    if not is_off(d):
        return 0, 0
    s = d
    while is_off(s - timedelta(days=1)):
        s -= timedelta(days=1)
    e = d
    while is_off(e + timedelta(days=1)):
        e += timedelta(days=1)
    return (e - s).days + 1, (d - s).days + 1


def period_label(d):
    """長期休み・大型連休の名前（無ければ空文字）。"""
    if _in(d, 12, 28, 1, 3):
        return "年末年始"
    if _in(d, 8, 8, 8, 16):
        return "お盆"
    if _in(d, 4, 29, 5, 6):
        return "ゴールデンウィーク"
    n, _ = off_run(d)
    if n >= 4:
        return "大型連休"
    return ""


def school_vacation(d):
    """学校の長期休み（東京の公立校のおおよその期間）。"""
    if _in(d, 7, 21, 8, 31):
        return "夏休み"
    if _in(d, 12, 26, 1, 7):
        return "冬休み"
    if _in(d, 3, 26, 4, 5):
        return "春休み"
    return ""


def describe(d):
    """その日の暦の情報をまとめて返す。"""
    hol = holiday_name(d)
    n, k = off_run(d)
    tomorrow_off = is_off(d + timedelta(days=1))
    return {
        "date": d.isoformat(),
        "wd": d.weekday(),
        "wdLabel": WD_JA[d.weekday()],
        "holiday": hol or "",
        "isOff": is_off(d),
        "weekdayHoliday": bool(hol) and d.weekday() < 5,   # 平日にあたる祝日
        "offRunLen": n,            # 連休の長さ（休みでない日は0）
        "offRunPos": k,            # 連休の何日目か
        "longRun": n >= 3,         # 3連休以上の一部
        "beforeOff": (not is_off(d)) and tomorrow_off,  # 休みの前日（平日）
        "period": period_label(d),
        "vacation": school_vacation(d),
        "month": d.month,
    }


if __name__ == "__main__":
    for s in ("2026-08-11", "2026-08-13", "2026-09-18", "2026-09-19", "2026-09-22", "2026-09-24",
              "2026-10-10", "2026-10-12", "2026-12-30", "2028-01-01"):
        y, m, dd = map(int, s.split("-"))
        print(describe(date(y, m, dd)))
    print(warnings)
