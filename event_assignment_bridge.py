"""社員を催事会場に回した日かどうかを、予実自動化（非公開・Dropbox）の設定で判定する（2026-11-16）。

氏名を含む設定は公開リポジトリに置けないため、Mac 上の予実自動化フォルダの
config/staff_event_assignments.json を読む。見つからない環境（Render など）では常に「回っていない」。
"""
import importlib.util
import os
from pathlib import Path

_BUDGET = Path(os.environ.get(
    "DW_BUDGET_AUTOMATION_DIR",
    str(Path.home() / "Library/CloudStorage/Dropbox-Detale/D& W/どら山/過去/dw_budget_profit_sheets_automation"),
))
_MOD = None


def _module():
    global _MOD
    if _MOD is None:
        path = _BUDGET / "src" / "event_staff_assignment.py"
        if not path.exists():
            _MOD = False
        else:
            spec = importlib.util.spec_from_file_location("dw_event_staff_assignment", path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            _MOD = mod
    return _MOD or None


def match(name, day):
    """(氏名, 日付 'YYYY-MM-DD' か 'YYYY/M/D') が催事会場に回った日なら割り当てを返す。"""
    mod = _module()
    if not mod:
        return None
    d = str(day or "").strip().replace("/", "-")
    parts = d.split("-")
    if len(parts) == 3 and all(p.isdigit() for p in parts):
        d = f"{int(parts[0]):04d}-{int(parts[1]):02d}-{int(parts[2]):02d}"
    return mod.match(name, d)
