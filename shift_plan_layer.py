#!/usr/bin/env python3
"""当日の時間割アプリへ渡すシフト予定（2026-11-02 本人指示「人はエアシフトと連動して」）。

元は非公開シートの「シフト予定」タブ（Mac mini が Airシフトから定期的に書く）。
氏名を含むので、合言葉（同シートの 設定「時間割の合言葉」）が合うときだけ返す。
給与は含まず、店長用の合言葉とは別にしてある。
"""
import manager_hr_layer as hr


def check_access(code):
    try:
        rows = hr._values("設定")
    except Exception as e:  # noqa: BLE001
        return False, f"シートに接続できませんでした（{type(e).__name__}）", 503
    expected = ""
    for r in rows:
        if len(r) >= 2 and r[0].strip() == "時間割の合言葉":
            expected = r[1].strip()
    if not expected:
        return False, "時間割の合言葉が未設定です。", 503
    if (code or "").strip() != expected:
        return False, "合言葉が違います。", 401
    return True, "", 200


def get_day(date):
    rows = hr._values("シフト予定")
    updated = rows[0][6] if rows and len(rows[0]) > 6 else ""
    people = [{"name": r[1], "start": r[2], "end": r[3], "status": r[4] if len(r) > 4 else ""}
              for r in rows[1:] if len(r) >= 4 and r[0] == date]
    return {"date": date, "updated": updated, "people": people}
