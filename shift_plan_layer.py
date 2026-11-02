#!/usr/bin/env python3
"""当日の時間割アプリへ渡すシフト予定（2026-11-02 本人指示「人はエアシフトと連動して」）。

元は非公開シートの「シフト予定」タブ（Mac mini が Airシフトから定期的に書く）。
給与は含まない。合言葉は本人指示で廃止（2026-11-02）。呼び出し元は app.py で時間割アプリの出どころに限る。
"""
import manager_hr_layer as hr


def get_day(date):
    rows = hr._values("シフト予定")
    updated = rows[0][6] if rows and len(rows[0]) > 6 else ""
    people = [{"name": r[1], "start": r[2], "end": r[3], "status": r[4] if len(r) > 4 else ""}
              for r in rows[1:] if len(r) >= 4 and r[0] == date]
    return {"date": date, "updated": updated, "people": people}
