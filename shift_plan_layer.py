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
    }


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
    return {"date": date, "updated": updated, "people": people, "labor": labor}


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
