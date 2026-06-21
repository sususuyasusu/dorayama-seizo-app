#!/usr/bin/env python3
"""売上・人件費・回転の集計（製造表 計算済みセル）を読む。"""
import data_layer

DAYS = ["月", "火", "水", "木", "金", "土", "日"]


def _ws():
    gc = data_layer._client()
    sh = gc.open_by_key(data_layer.SHEET_ID)
    _, cands = data_layer.current_week_tab()
    for c in cands:
        try:
            return sh.worksheet(c)
        except Exception:
            continue
    raise RuntimeError("今週タブが見つからない")


def get_cost():
    ws = _ws()
    v = ws.get_all_values()

    def g(r, c):
        return v[r - 1][c] if r - 1 < len(v) and c < len(v[r - 1]) else ""

    days = []
    for i in range(7):
        days.append({
            "d": DAYS[i],
            "salesActual": g(12, 31 + i),   # AF..AL 実績売上
            "salesPlan": g(12, 11 + i),     # L..R   予定売上
            "kaiten": g(38, 21 + i),        # V..AB  回転数（実数）
            "labor": g(36, 31 + i),         # AF..AL 製造人件費
            "fl": g(42, 31 + i),            # AF..AL FL目安
        })
    return {
        "tab": ws.title,
        "weekSalesActual": g(13, 30),  # AE13 実績 合計
        "weekSalesPlan": g(13, 10),    # K13  予定 合計
        "avgActual": g(13, 37),        # AL13 実績 平均
        "days": days,
    }
