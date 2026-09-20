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
    assert august_cost["internalLabor"] is None
    assert august_cost["accountingInternalLabor"] == 388764
    assert august_cost["shiftCostEstimate"] == 1766227
    assert august_cost["profit"] is None
    assert august_cost["accountingProfit"] == -684985
    reconciliation = august_cost["laborReconciliation"]
    assert reconciliation["status"] == "再集計中"
    assert reconciliation["payrollGross"] == 1314804
    assert reconciliation["timeeInvoice"] == 199990
    assert reconciliation["timeeServiceFee"] == 15615

    fixed = {row["key"]: row for row in data["fixed"]["history"]}
    assert fixed["2月"]["total"] == 389162
    assert fixed["3月"]["total"] == 874814
    assert fixed["4月"]["total"] == 408961
    assert fixed["5月"]["total"] == 489440
    assert fixed["6月"]["total"] == 386769
    assert fixed["7月"]["total"] == 414459
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
    assert fixed["9月"]["total"] is None

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
