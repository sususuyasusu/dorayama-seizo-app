#!/usr/bin/env python3
"""卵発注ナビの読み取り層。製造表が計算済みの AO..AX（締切・在庫・やること）を今週分だけ取得。"""
import data_layer


def get_egg_nav(today=None):
    gc = data_layer._client()
    sh = gc.open_by_key(data_layer.SHEET_ID)
    monday, cands = data_layer.current_week_tab(today)
    ws = None
    for c in cands:
        try:
            ws = sh.worksheet(c); break
        except Exception:
            continue
    if ws is None:
        raise RuntimeError("今週タブが見つからない")
    vals = ws.get_all_values()

    def g(r, col):
        return vals[r - 1][col] if r - 1 < len(vals) and col < len(vals[r - 1]) else ""

    days = []
    for r in range(6, 13):  # 行6..12 = 月..日
        lines = []
        for txt in (g(r, 48), g(r, 49)):  # AW, AX
            for ln in str(txt).split("\n"):
                ln = ln.strip()
                if ln and ln not in lines:
                    lines.append(ln)
        days.append({
            "date": g(r, 40), "weekday": g(r, 41),
            "yolk": g(r, 42), "white": g(r, 43),
            "needYolk": g(r, 44), "needWhite": g(r, 45),
            "todo": lines,
        })
    return {"tab": ws.title, "note": str(g(2, 40)).strip(), "days": days}
