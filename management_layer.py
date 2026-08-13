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
    achievement = round(latest["sales"] / latest["budget"] * 100, 1) if latest["budget"] else None
    break_even_rate = round(latest["sales"] / latest["breakEven"] * 100, 1) if latest["breakEven"] else None
    return {
        "asOf": "2026-08-08",
        "period": "6月締め",
        "status": "provisional",
        "statusLabel": "暫定・月次締め前",
        "latest": latest,
        "months": rows,
        "breakEvenGap": max(0, latest["breakEven"] - latest["sales"]),
        "budgetGap": max(0, latest["budget"] - latest["sales"]),
        "achievement": achievement,
        "breakEvenRate": break_even_rate,
        "todayDecisions": [
            {"level": "urgent", "title": "まず売上を確定する", "detail": "店舗の決済手数料と催事の精算書を確認。未確定のまま追加発注・増員を決めない。"},
            {"level": "watch", "title": "損益分岐点との差を毎日確認", "detail": "確定済み売上では損益分岐点まで差があります。日次売上の積み上がりと残営業日で判断します。"},
            {"level": "normal", "title": "変動費を売上と一緒に見る", "detail": "商品原価25%、催事手数料20%、催事販売員・配送費を催事ごとに確認します。"},
        ],
        "checks": [
            {"category": "店舗売上", "source": "Airレジ・決済明細", "status": "未確定", "reason": "決済端末手数料の確定・差引確認待ち", "owner": "店長", "next": "決済明細とAirレジ売上を照合"},
            {"category": "催事売上", "source": "Googleフォーム・催事精算書", "status": "未確定", "reason": "催事場から届く最終入金額の書類待ち", "owner": "管理", "next": "精算書到着後に入金額を確定"},
            {"category": "原価", "source": "商品原価・請求書", "status": "暫定", "reason": "商品原価率25%で暫定計上。請求書実額との照合前", "owner": "管理", "next": "仕入請求書と振込額を照合"},
            {"category": "人件費", "source": "Airシフト・freee人事労務", "status": "未確定", "reason": "勤務実績と確定給与の照合待ち", "owner": "店長・管理", "next": "シフト実績と確定人件費を照合"},
            {"category": "催事経費", "source": "精算書・請求書", "status": "暫定", "reason": "手数料20%、販売員45,000円/日、配送7,150円/日で暫定計上", "owner": "管理", "next": "催事別の日数・請求額を確認"},
            {"category": "経費・固定費", "source": "領収書・請求書・カード明細", "status": "未確定", "reason": "現金、振込、ネット購入、インフラ引落の証憑照合中", "owner": "管理", "next": "証憑と口座・カード引落を照合"},
            {"category": "会社共通費", "source": "事業別損益", "status": "暫定", "reason": "どら山・デザイン・会社共通への配賦確定前", "owner": "経営", "next": "確定した配賦基準で再計算"},
        ],
        "assumptions": [
            "商品の原価率：売上の25%（実請求額確定まで）",
            "催事手数料：売上の20%",
            "催事販売員：45,000円／日、配送料：7,150円／日",
            "鈴木康之の人件費：どら山50%・デザイン50%",
        ],
        "expenseAudit": {
            "label": "6月・freee明細と社内台帳の照合",
            "directTotal": 5970901,
            "status": "直接費確認済み・共通費配賦待ち",
            "items": [
                {"label": "仕入高", "amount": 1540153, "type": "変動費"},
                {"label": "外注費（タイミー人件費の重複356,572円を除外）", "amount": 1963785, "type": "催事・外注"},
                {"label": "給料手当", "amount": 1077118, "type": "人件費"},
                {"label": "消耗品費", "amount": 585530, "type": "変動・間接"},
                {"label": "荷造運賃", "amount": 269956, "type": "配送費"},
                {"label": "地代家賃", "amount": 248880, "type": "固定費"},
                {"label": "賃借料（加瀬レンタルスペース15,950円を含む）", "amount": 197469, "type": "固定費"},
                {"label": "水道光熱費", "amount": 71147, "type": "固定費"},
                {"label": "支払手数料ほか", "amount": 16863, "type": "間接費"},
            ],
            # 2026-08-13 再調査（freee wallet_txns API・読み取り専用）:
            #   セゾンカード/PayPayカードは6月分が実際には全件仕訳済み(未仕訳0件)だったため
            #   unresolvedから除外した。「PayPay銀行」はfreee上の口座名では
            #   「ジャパンネット（法人）」(walletable_id 788086)。6月90件(入金13+出金77)は
            #   件数が一致し全件仕訳済みだが、この口座の自動同期は2026-05-09で止まっている
            #   （手動計上で埋まっている可能性）。帳簿残高との差23,963,739円は今回のAPI調査
            #   では特定できず、freee画面での口座同期状況の確認が必要。
            "unresolved": [
                {"label": "会社共通費", "reason": "6月963,677円。既存の売上比配賦方針(どら山売上比80.4%)で計算するとどら山負担775,036円・デザイン負担188,641円。この画面の経常利益額にはまだ反映していません（反映には月次全体の再計算が必要）"},
                {"label": "PayPay銀行（ジャパンネット口座）", "reason": "6月の入出金90件は件数一致・全件仕訳済みを確認。ただし口座の自動同期が2026-05-09から止まっており、帳簿残高との差23,963,739円の内訳は未特定。freee側で口座同期の復旧・確認が必要"},
            ],
        },
    }
