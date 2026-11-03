#!/usr/bin/env python3
"""当日の時間割アプリへ渡すシフト予定（2026-11-02 本人指示「人はエアシフトと連動して」）。

元は非公開シートの「シフト予定」タブ（Mac mini が Airシフトから定期的に書く）。
給与は含まない。合言葉は本人指示で廃止（2026-11-02）。呼び出し元は app.py で時間割アプリの出どころに限る。
"""
import json
import re
import time
from datetime import datetime

import data_layer
import manager_hr_layer as hr


def labor_basis(day):
    """時間割の人件費の過不足に使う材料（2026-11-03 本人指示）。
    枠は経営管理と同じ「製造額×11%＋店舗売上×14%」（manager_labor_layer と同じ式）。
    予定日はまだ実績が無いので、製造額は直近の製造1個あたりの額×その日の個数（画面側で掛ける）、
    店舗売上は同じ曜日の直近8回の平均で見込む。時給は個人ではなく平均の目安。"""
    import management_analysis_layer as mal
    import manager_labor_layer as mll
    from datetime import date as _d
    a = mal.get_management_analysis()
    caps = (a.get("todayBoard") or {}).get("laborCaps") or mal.LABOR_CAPS
    prod = [r for r in ((a.get("production") or {}).get("daily") or []) if (r.get("pieces") or 0) > 0 and (r.get("value") or 0) > 0]
    pieces = sum(r["pieces"] for r in prod)
    unit = sum(r["value"] for r in prod) / pieces if pieces else None
    target = _d.fromisoformat(day)
    today = _d.today().isoformat()
    # 日別の店舗売上（保存済みAirメイト日次＋今月の同期分。同じ日付は今月の同期分を優先）
    hist = {r["date"]: r for r in ((a.get("weekdayTimeHistory") or {}).get("daily") or []) if r.get("date")}
    hist.update({r["date"]: r for r in (a.get("daily") or []) if r.get("date")})
    same = [float(hist[k].get("storeSales") or 0) for k in sorted(hist)
            if k < today and (hist[k].get("storeSales") or 0) > 0 and _d.fromisoformat(k).weekday() == target.weekday()][-8:]
    return {
        "unitValue": round(unit, 1) if unit else None,
        "storeSalesForecast": round(sum(same) / len(same)) if same else None,
        "storeSalesBasis": f"同じ曜日の直近{len(same)}回の平均",
        "rateProduction": caps["production"], "rateStoreSales": caps["storeSales"],
        "hourlyWage": mll.HOURLY_WAGE_ESTIMATE,
        **_employee_daily(day),
    }


def _employee_daily(day):
    """月給の社員（非公開シート 設定「時間割の月給社員」）は勤務時間によらず一律の日額（2026-11-03 本人指示）。
    日額＝前月までで直近の1か月の総支給÷勤務日数を、月給の社員みんなで平均した額（1人ずつの額は返さない）。"""
    names = []
    for r in hr._values("設定"):
        if len(r) >= 2 and r[0].strip() == "時間割の月給社員":
            names = [x.strip() for x in re.split(r"[、,，]", r[1]) if x.strip()]
    if not names:
        return {}
    key = {n.replace(" ", "").replace("　", "") for n in names}
    month = day[:7]
    rows = [r for r in hr._values("人別月次")[1:] if len(r) >= 7 and r[0] < month and r[1].replace(" ", "") in key]
    if not rows:
        return {"employees": names}
    last = max(r[0] for r in rows)
    pay = sum(hr._f(r[6]) for r in rows if r[0] == last)
    days = sum(hr._f(r[3]) for r in rows if r[0] == last)
    return {"employees": names, "employeeDaily": round(pay / days) if days else None, "employeeBasis": f"{last}の総支給÷勤務日数（月給の社員の平均）"}


TIMEE_HOURLY = 1226  # タイミー本店の時給（直近実績どおり）
TIMEE_COMMUTE = 500  # タイミーの交通費（1日）


def labor_cost(day, staff):
    """時間割に並んだ人の人件費を、1人ずつの実際の単価で合計する（2026-11-03 本人指示）。
    月給の社員＝直近の月の総支給÷勤務日数（勤務時間によらず一律）、アルバイト等＝直近の月の時給換算×勤務時間、
    名簿に無い人（タイミーなど）＝タイミーの時給×勤務時間＋交通費。返すのは合計と内訳の人数・時間だけ（個人の単価は返さない）。"""
    norm = lambda s: str(s or "").replace(" ", "").replace("　", "")
    emp = set()
    for r in hr._values("設定"):
        if len(r) >= 2 and r[0].strip() == "時間割の月給社員":
            emp = {norm(x) for x in re.split(r"[、,，]", r[1]) if x.strip()}
    month = str(day)[:7]
    latest = {}
    for r in hr._values("人別月次")[1:]:
        if len(r) < 9 or r[0] >= month or not r[1]:
            continue
        k = norm(r[1])
        if hr._f(r[3]) <= 0 or hr._f(r[6]) <= 0:
            continue
        if k not in latest or r[0] > latest[k][0]:
            latest[k] = (r[0], hr._f(r[6]), hr._f(r[3]), hr._f(r[8]))
    # 単価は freee人事労務の基本給（非公開シート「単価（freee）」）を正とする（2026-11-03 本人指示：
    # 最低賃金の改定があり、Airシフトの時給換算は古い可能性がある）。通勤手当はAirシフトの前月分を1日あたりにして足す
    freee = {}
    try:
        for r in hr._values("単価（freee）")[1:]:
            if len(r) >= 3 and r[0] and hr._f(r[2]) > 0:
                freee[norm(r[0])] = (r[1].strip(), hr._f(r[2]))
    except Exception:  # noqa: BLE001
        freee = {}
    commute = {}
    for r in hr._values("人別月次")[1:]:
        if len(r) >= 8 and r[1] and norm(r[1]) in latest and r[0] == latest[norm(r[1])][0] and hr._f(r[3]) > 0:
            commute[norm(r[1])] = hr._f(r[7]) / hr._f(r[3])
    emp_days = [latest[k][2] for k in emp if k in latest]
    avg_days = sum(emp_days) / len(emp_days) if emp_days else 21
    total, n_emp, part_min, n_unknown, unknown_min = 0.0, 0, 0, 0, 0
    for s in staff or []:
        k, mins = norm(s.get("name")), max(0, int(s.get("mins") or 0))
        if not k or mins <= 0:
            continue
        rec, fr = latest.get(k), freee.get(k)
        if fr and fr[0] == "月給":
            total += fr[1] / avg_days
            n_emp += 1
        elif fr and fr[0] == "時給":
            total += fr[1] * mins / 60 + commute.get(k, 0)
            part_min += mins
        elif k in emp and rec:
            total += rec[1] / rec[2]
            n_emp += 1
        elif rec and rec[3] > 0:
            total += rec[3] * mins / 60
            part_min += mins
        else:
            total += TIMEE_HOURLY * mins / 60 + TIMEE_COMMUTE
            n_unknown += 1
            unknown_min += mins
    return {"total": round(total), "employees": n_emp, "partMinutes": part_min,
            "unknown": n_unknown, "unknownMinutes": unknown_min}


_LABOR_CACHE = {}


def get_day(date):
    rows = hr._values("シフト予定")
    updated = rows[0][6] if rows and len(rows[0]) > 6 else ""
    people = [{"name": r[1], "start": r[2], "end": r[3], "status": r[4] if len(r) > 4 else ""}
              for r in rows[1:] if len(r) >= 4 and r[0] == date]
    hit = _LABOR_CACHE.get(date)
    if hit and time.time() - hit[0] < 3600:
        labor = hit[1]
    else:
        try:
            labor = labor_basis(date)
            _LABOR_CACHE[date] = (time.time(), labor)
        except Exception as e:  # noqa: BLE001  人件費が出せなくても人の読み取りは止めない
            labor = {"error": type(e).__name__}
    # 責任者の優先順（非公開シート 設定「時間割の責任者順」。その日いる中で先頭の人が責任者）
    leaders = []
    try:
        for r in hr._values("設定"):
            if len(r) >= 2 and r[0].strip() == "時間割の責任者順":
                leaders = [x.strip() for x in re.split(r"[、,，]", r[1]) if x.strip()]
    except Exception:  # noqa: BLE001
        pass
    return {"date": date, "updated": updated, "people": people, "labor": labor, "leaders": leaders}


PRINT_TAB = "印刷依頼"
PRINT_HEAD = ["受付番号", "日付", "受付時刻", "内容", "状態", "メモ"]


def _print_ws():
    sh = data_layer._client().open_by_key(hr.HR_SHEET_ID)
    try:
        return sh.worksheet(PRINT_TAB)
    except Exception:  # noqa: BLE001
        ws = sh.add_worksheet(title=PRINT_TAB, rows=300, cols=6)
        ws.append_row(PRINT_HEAD)
        return ws


def add_print(date, body):
    """画面の「A3で印刷」の依頼を受け付ける。実際の印刷は Mac mini の見張りが行う。"""
    text = json.dumps(body, ensure_ascii=False)
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(date or "")) or len(text) > 40000:
        raise ValueError("印刷の依頼内容が正しくありません。")
    pid = str(int(time.time() * 1000))
    _print_ws().append_row([pid, date, datetime.now(hr.JST).strftime("%Y-%m-%d %H:%M:%S"), text, "待ち", ""],
                           value_input_option="RAW")
    return pid


def print_status(pid):
    for r in _print_ws().get_all_values()[1:]:
        if r and r[0] == str(pid):
            return {"status": r[4] if len(r) > 4 else "", "note": r[5] if len(r) > 5 else ""}
    return {"status": "不明", "note": ""}
