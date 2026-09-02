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
    assert august_cost["internalLabor"] == 388764
    assert august_cost["operationalInternalLabor"] == 1728853
    assert august_cost["accountingInternalLaborStatus"] == "給与未反映・法定福利費のみ"

    fixed = {row["key"]: row for row in data["fixed"]["history"]}
    assert fixed["2月"]["total"] == 389162
    assert fixed["3月"]["total"] == 874814
    assert fixed["4月"]["total"] == 408961
    assert fixed["5月"]["total"] == 489440
    assert fixed["6月"]["total"] == 386769
    assert fixed["7月"]["total"] == 414459
    assert fixed["8月"]["total"] == 498666
    assert fixed["8月"]["status"] == "確定"
    assert fixed["8月"]["isLowerBound"] is False
    assert fixed["8月"]["missingCategories"] == []
    assert fixed["8月"]["bookedTotal"] == 496708
    assert fixed["8月"]["accrualAdjustment"] == 1958
    assert fixed["8月"]["employeeContribution"] == 35000
    assert fixed["8月"]["netCompanyBurden"] == 463666
    assert fixed["9月"]["total"] is None

    html = (BASE / "templates" / "store_manager.html").read_text(encoding="utf-8")
    assert "fixed-month-select" in html
    assert "月別固定費" in html
    assert "会社実負担" in html
    assert "operationalCostRows" in html
    assert "給与は未反映" in html
    print("monthly fixed and labor verification passed")


if __name__ == "__main__":
    main()
