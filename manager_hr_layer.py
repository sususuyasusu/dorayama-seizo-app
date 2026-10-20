#!/usr/bin/env python3
"""店長（人事権あり）向け人件費画面のデータ（2026-10-20 本人指示）。

人別の勤務時間・総支給・時給換算を出す。控除・手取り・役員報酬・デザイン部門は含めない。
元は非公開Googleシート（staff_labor_sync.py がMac miniから毎日書く）。合言葉はそのシートの 設定!B2。
公開リポジトリ・製造表（リンク閲覧可）には人別データを一切置かない。
"""
import os
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import data_layer
import manager_labor_layer

HR_SHEET_ID = os.environ.get("DORAYAMA_HR_SHEET_ID", "1wrKIo7MqU_bm6z72cwvvwBSOArYP1Y5z9O8BrnOGqyE")
JST = ZoneInfo("Asia/Tokyo")
_CACHE = {}  # {tab: (取得時刻, 値)}
_TTL = 600  # 10分。合言葉の変更もこの間隔で反映


def _values(tab):
    hit = _CACHE.get(tab)
    if hit and time.time() - hit[0] < _TTL:
        return hit[1]
    ws = data_layer._client().open_by_key(HR_SHEET_ID).worksheet(tab)
    vals = ws.get_all_values()
    _CACHE[tab] = (time.time(), vals)
    return vals


def check_access(code):
    """合言葉の照合。(通ってよいか, エラー文, HTTPコード)"""
    try:
        rows = _values("設定")
    except Exception as e:  # noqa: BLE001
        return False, f"人別データのシートに接続できませんでした（{type(e).__name__}）", 503
    expected = ""
    for r in rows:
        if len(r) >= 2 and r[0].strip() == "店長用の合言葉":
            expected = r[1].strip()
    if not expected:
        return False, "店長用の合言葉が未設定です（非公開シートの 設定!B2）。", 503
    if (code or "").strip() != expected:
        return False, "合言葉が違います。", 401
    return True, "", 200


def _f(s):
    try:
        return float(str(s).replace(",", "") or 0)
    except ValueError:
        return 0.0


def _i(s):
    return int(round(_f(s)))


def get_data(month=None):
    now = datetime.now(JST)
    monthly = _values("人別月次")
    daily = _values("人別日次")
    updated = ""
    for r in _values("設定"):
        if len(r) >= 2 and r[0].strip() == "最終更新":
            updated = r[1].strip()
    months = sorted({r[0] for r in monthly[1:] if r and r[0]})
    cur = now.strftime("%Y-%m")
    if not month or month not in months:
        month = cur if cur in months else (months[-1] if months else cur)

    staff = []
    for r in monthly[1:]:
        if len(r) < 10 or r[0] != month:
            continue
        hours = _f(r[4])
        staff.append({
            "name": r[1], "role": r[2], "days": _i(r[3]), "hours": round(hours, 1), "overtime": round(_f(r[5]), 1),
            "pay": _i(r[6]), "commute": _i(r[7]), "rate": _i(r[8]) if r[8] else None, "lastDate": r[9],
            "payPerDay": _i(_f(r[6]) / _f(r[3])) if _f(r[3]) > 0 else None,
            "hoursPerDay": round(hours / _f(r[3]), 1) if _f(r[3]) > 0 else None,
        })
    staff.sort(key=lambda s: -s["pay"])
    total_pay = sum(s["pay"] for s in staff)
    total_hours = sum(s["hours"] for s in staff)
    for s in staff:
        s["share"] = round(s["pay"] / total_pay * 100, 1) if total_pay else 0
    by_role = {}
    for s in staff:
        g = by_role.setdefault(s["role"], {"role": s["role"], "people": 0, "hours": 0.0, "pay": 0})
        g["people"] += 1
        g["hours"] += s["hours"]
        g["pay"] += s["pay"]
    roles = sorted(by_role.values(), key=lambda g: -g["pay"])
    for g in roles:
        g["hours"] = round(g["hours"], 1)
        g["share"] = round(g["pay"] / total_pay * 100, 1) if total_pay else 0

    detail = {}
    for r in daily[1:]:
        if len(r) < 7 or not r[0].startswith(month):
            continue
        start, end, hours, pay = r[3].strip(), r[4].strip(), round(_f(r[5]), 2), _i(r[6])
        if not start and not end and hours == 0 and pay == 0:
            continue  # シフト表に行だけあって勤務の無い日（休み）は出さない
        flag = "打刻漏れ？" if (start and not end) or (end and not start) else ""
        detail.setdefault(r[1], []).append({"date": r[0], "start": start, "end": end, "hours": hours, "pay": pay, "flag": flag})
    for rows in detail.values():
        rows.sort(key=lambda x: x["date"])

    frame = None
    if month == cur:
        try:
            ml = manager_labor_layer.get_manager_labor()
            frame = {"storeShiftBudget": ml["monthFrame"]["storeShiftBudget"], "monthToDate": ml["monthToDate"],
                     "perDay": ml["monthFrame"]["perDay"], "daysInMonth": ml["month"]["daysInMonth"]}
        except Exception:  # noqa: BLE001
            frame = None

    y, m = month.split("-")
    return {
        "generatedAt": now.isoformat(timespec="minutes"), "updatedAt": updated,
        "month": {"key": month, "label": f"{int(y)}年{int(m)}月", "isCurrent": month == cur}, "months": months,
        "totals": {"pay": total_pay, "hours": round(total_hours, 1), "people": len(staff),
                   "rate": _i(total_pay / total_hours) if total_hours else None},
        "roles": roles, "staff": staff, "detail": detail, "frame": frame,
        "notes": [
            "総支給＝Airシフト給与計算表の「合計」（基本給＋残業・深夜手当＋通勤手当＋その他）。控除前の金額で、手取りではありません。",
            "時給換算＝総支給÷労働時間。残業手当や通勤手当も含めて割った値なので、契約時給より高めに出ます。",
            "タイミーは月ごとに1行の束（実働×時給＋交通費）。タイミーへの手数料は含みません。",
            "役員報酬・デザイン部門のスタッフは出ません。区分は staff_roster.json の名簿どおり。",
            "数字は前日までの打刻。Mac miniが毎朝書き換えます（最終更新は右上）。",
        ],
    }
