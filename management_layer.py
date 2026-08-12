#!/usr/bin/env python3
"""どら山の月次予実スナップショット（読み取り専用）。

元の製造表、freee、Air、数式には書き込まない。月次締めが確定するまでは
status を provisional のまま返し、画面側で暫定表示する。
"""

MONTHS = [
    {"month": "1月", "sales": 4790885, "budget": 8860000, "breakEven": 5770000, "profit": -734059},
    {"month": "2月", "sales": 10976371, "budget": 8957200, "breakEven": 11360000, "profit": -285895},
    {"month": "3月", "sales": 5339732, "budget": 9056300, "breakEven": 5610000, "profit": -202576},
    {"month": "4月", "sales": 5202708, "budget": 9157500, "breakEven": 7030000, "profit": -1376312},
    {"month": "5月", "sales": 9579671, "budget": 9260600, "breakEven": 6690000, "profit": 2171984},
    {"month": "6月", "sales": 3065779, "budget": 9365800, "breakEven": 7010000, "profit": -3000805},
]


def get_dorayama_management():
    cumulative = 0
    rows = []
    for item in MONTHS:
        cumulative += item["profit"]
        rows.append({**item, "cumulative": cumulative})
    latest = rows[-1]
    return {
        "asOf": "2026-08-08",
        "status": "provisional",
        "statusLabel": "暫定・月次締め前",
        "latest": latest,
        "months": rows,
        "breakEvenGap": max(0, latest["breakEven"] - latest["sales"]),
        "checks": [
            {"label": "催事売上・精算書", "status": "書類待ち"},
            {"label": "部門未設定", "status": "6件確認"},
            {"label": "鈴木康之 人件費", "status": "どら山50%"},
            {"label": "会社共通費", "status": "売上比で配賦"},
        ],
    }
