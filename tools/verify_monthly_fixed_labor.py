#!/usr/bin/env python3
"""月別固定費と8月内部人件費の表示用データを検証する。"""
from pathlib import Path
import sys


BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))

import management_analysis_layer


def main():
    data = management_analysis_layer.get_management_analysis()
    august_cost = next(
        row for row in data["costAnalysis"]["series"] if row["key"] == "8月"
    )
    if august_cost["status"] == "確定":
        # 2026-10-12 本人指示で8月確定。管理会計PLの会計値をそのまま使う
        # 2026-10-28更新: 役員報酬をデザイン50%・どら山50%に分割（10/18決定）し、8月にどら山分130,125円が加わった
        assert august_cost["internalLabor"] == 1972429  # 給与返却控除後、さらにデザイン(大矢)280,474円を一括振込から分けて除外（2026-10-12）
        assert august_cost["profit"] is not None
        assert "laborReconciliation" not in august_cost
    else:
        assert august_cost["internalLabor"] is None
        assert august_cost["accountingInternalLabor"] == 2122778  # 給与返却561,759円控除後（2026-10-12）
        assert august_cost["shiftCostEstimate"] == 1773251
        assert august_cost["profit"] is None
        reconciliation = august_cost["laborReconciliation"]
        assert reconciliation["status"] == "再集計中"
        assert reconciliation["payrollGross"] == 1314804
        assert reconciliation["timeeInvoice"] == 199990
        assert reconciliation["timeeServiceFee"] == 15615

    fixed = {row["key"]: row for row in data["fixed"]["history"]}
    # 2〜7月はfreee側の重複削除・返金整理で数百円単位まで動くため、固定値ではなく「確定で金額がある」ことを確認（2026-10-12）
    for month in ("2月", "3月", "4月", "5月", "6月", "7月"):
        assert fixed[month]["status"] == "確定" and fixed[month]["total"] and fixed[month]["total"] > 0, month
    # 8月固定費：締め(498,666円)後にfreeeへ記帳された3件（ソフトバンク16,218・ニチガス3,610・上下水道1,958）を反映。
    # 上下水道1,958円は締め時の月次調整と同じ請求のため、調整行は外して二重計上を避ける。
    assert fixed["8月"]["total"] == 518494
    assert fixed["8月"]["status"] == "確定"
    assert fixed["8月"]["isLowerBound"] is False
    assert fixed["8月"]["missingCategories"] == []
    assert fixed["8月"]["bookedTotal"] == 518494
    assert fixed["8月"]["accrualAdjustment"] == 0
    assert fixed["8月"]["employeeContribution"] == 35000
    assert fixed["8月"]["netCompanyBurden"] == 483494
    assert fixed["8月"]["transactionCount"] == 13
    assert fixed["8月"]["transactionTotal"] == 518494
    assert fixed["8月"]["transactionDifference"] == 0
    assert fixed["8月"]["transactionMatchesTotal"] is True
    august_categories = {
        row["category"]: row for row in fixed["8月"]["details"]
    }
    assert august_categories["地代家賃"]["transactionTotal"] == 316420
    assert august_categories["賃借料"]["transactionTotal"] == 82830
    assert august_categories["水道光熱費"]["transactionTotal"] == 98293
    assert august_categories["通信費"]["transactionTotal"] == 20951
    assert fixed["9月"]["total"] and fixed["9月"]["total"] > 0  # 9月（2026-10-12確定）

    html = (BASE / "templates" / "store_manager.html").read_text(encoding="utf-8")
    assert "fixed-month-select" in html
    assert "月別固定費" in html
    assert "固定費の取引明細" in html
    assert "内容・支払先" in html
    assert "data-fixed-category" in html
    assert "会社実負担" in html
    assert "operationalCostRows" in html
    assert "内部人件費は再集計中" in html
    assert "この金額を経常利益へ使いません" in html
    print("monthly fixed and labor verification passed")


if __name__ == "__main__":
    main()
