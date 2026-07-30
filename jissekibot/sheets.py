"""製造表 Sheets の「実績」ブロックに催事の製造数を書き込む。

安全方針（既存シートを壊さない）:
- 会場（催事ブロック）は「カテゴリー」見出し行から動的に検出。行番号ハードコードなし。
- グループ名/ヒントと催事ブロック名の最長共通部分一致で会場を特定。曖昧・不一致なら書かない。
- 書き込むのは実績ブロックの「商品行 × 当日列(V〜AB)」だけ。数式セルには一切触れない。
- 列は固定: V=月 … AB=日（=22+weekday を1-indexedで）。
"""
from __future__ import annotations

import datetime as dt
import json
import os

import gspread
from google.oauth2.service_account import Credentials
from gspread.utils import rowcol_to_a1

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
PRODUCTS = {"黒どら", "あんバター", "白どら", "旬どら", "生", "生どら", "皮4枚セット", "皮だけ（パック）"}
ACT_COL_BASE = 22  # V列(1-indexed)=月曜の実績


def _lcs(a: str, b: str) -> int:
    """最長共通部分文字列の長さ。"""
    if not a or not b:
        return 0
    prev = [0] * (len(b) + 1)
    best = 0
    for i in range(1, len(a) + 1):
        cur = [0] * (len(b) + 1)
        for j in range(1, len(b) + 1):
            if a[i - 1] == b[j - 1]:
                cur[j] = prev[j - 1] + 1
                best = max(best, cur[j])
        prev = cur
    return best


class SheetsClient:
    def __init__(self, spreadsheet_id: str, credentials_json: str):
        creds = Credentials.from_service_account_info(json.loads(credentials_json), scopes=SCOPES)
        self.gc = gspread.authorize(creds)
        self.sh = self.gc.open_by_key(spreadsheet_id)

    @staticmethod
    def tab_name_for(date: dt.date) -> str:
        monday = date - dt.timedelta(days=date.weekday())
        return f"{monday.month:02d}{monday.day:02d}"

    @staticmethod
    def blocks(vals: list) -> list:
        """カテゴリー見出し行から全ブロックを検出。{name, category, rows{product:row}}。"""
        def g(r, c):
            return vals[r - 1][c] if r - 1 < len(vals) and c < len(vals[r - 1]) else ""
        out = []
        cur = None
        for r in range(1, 35):
            a = g(r, 0).strip()
            s = g(r, 18).strip()
            if s == "カテゴリー" and a:
                cur = {"name": a, "category": ("店舗用" if a == "店舗用" else "催事用"), "rows": {}}
                out.append(cur)
                continue
            if cur is not None and a in PRODUCTS:
                cur["rows"].setdefault(a, r)
        return [b for b in out if b["rows"]]

    @staticmethod
    def resolve_block(blocks: list, hint: str):
        """催事ブロックのうち hint と最長共通部分が最大(>=2)かつ一意のものを返す。曖昧なら None。"""
        cands = [b for b in blocks if b["category"] == "催事用"]
        scored = sorted(((_lcs(b["name"], hint or ""), b) for b in cands), key=lambda x: -x[0])
        if not scored or scored[0][0] < 2:
            return None
        if len(scored) > 1 and scored[1][0] == scored[0][0]:
            return None  # 同点＝曖昧 → 安全に書かない
        return scored[0][1]

    @staticmethod
    def _find_row(rowmap: dict, prod: str):
        if prod in rowmap:
            return rowmap[prod]
        if prod == "生":
            for k in ("生", "生どら"):
                if k in rowmap:
                    return rowmap[k]
        if prod == "皮4枚セット":
            for k in ("皮4枚セット", "皮だけ（パック）"):
                if k in rowmap:
                    return rowmap[k]
        return None

    def write_jisseki(self, hint: str, products: dict, date: dt.date) -> dict:
        tab = self.tab_name_for(date)
        try:
            ws = self.sh.worksheet(tab)
        except gspread.WorksheetNotFound:
            return {"ok": False, "reason": f"タブ {tab} なし", "tab": tab}
        vals = ws.get_all_values()
        blk = self.resolve_block(self.blocks(vals), hint)
        if blk is None:
            return {"ok": False, "reason": f"会場を特定できず(hint='{hint}')→書込せず", "tab": tab}
        col = ACT_COL_BASE + date.weekday()  # V..AB（月..日）
        updates = []
        wrote = {}
        for prod, qty in products.items():
            r = self._find_row(blk["rows"], prod)
            if r is None:
                continue
            updates.append({"range": rowcol_to_a1(r, col), "values": [[qty]]})
            wrote[prod] = {"row": r, "qty": qty}
        if updates:
            ws.batch_update(updates, value_input_option="USER_ENTERED")
        return {"ok": True, "tab": tab, "venue": blk["name"], "col": col,
                "weekday": date.weekday(), "wrote": wrote}


def get_client_from_env() -> SheetsClient:
    return SheetsClient(os.environ["SPREADSHEET_ID"], os.environ["GOOGLE_CREDENTIALS_JSON"])
