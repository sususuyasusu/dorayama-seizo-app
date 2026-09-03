#!/usr/bin/env python3
"""毎日の売上速報を自動で最新化する。

このMac上のAirメイトCSV（dw_budget_profit_sheets_automationの日次同期で更新）から
Render向けスナップショット(data/airmate_history_2026.json)を作り直し、
差分があればコミット・push・Render再デプロイまで自動で行う。

使い方: python3 tools/auto_refresh_sales_snapshot.py
（scheduled-tasksからの定期実行を想定。手動実行もそのまま可能）
"""
import subprocess
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))


def _load_deploy_hook_url():
    env_path = BASE / ".env"
    if not env_path.is_file():
        return None
    for line in env_path.read_text(encoding="utf-8").splitlines():
        if line.startswith("RENDER_DEPLOY_HOOK_URL="):
            return line.split("=", 1)[1].strip()
    return None


def _run(cmd, cwd=BASE):
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    return result.returncode, result.stdout.strip(), result.stderr.strip()


def main():
    print(f"[{datetime.now().isoformat(timespec='seconds')}] 売上速報の自動更新を開始")

    # 1) スナップショット再生成（このMacのAirメイトCSVから）
    code, out, err = _run([sys.executable, "tools/build_airmate_history_snapshot.py"])
    print(out)
    if code == 2:
        print("実データに変化がないため、コミット・デプロイは行いません。")
        return 0
    if code != 0:
        print("スナップショット生成に失敗、または新規データ0件のためスキップ:", err)
        return 0

    # 2) 差分があるか確認
    code, out, err = _run(["git", "status", "--porcelain", "--", "data/airmate_history_2026.json"])
    if code != 0:
        print("git status に失敗:", err)
        return 1
    if not out.strip():
        print("差分なし。売上速報はすでに最新です。")
        return 0

    # 3) コミット & push
    code, out, err = _run(["git", "add", "data/airmate_history_2026.json"])
    if code != 0:
        print("git add に失敗:", err)
        return 1

    commit_message = f"売上速報を自動更新（{datetime.now().strftime('%Y-%m-%d %H:%M')}）"
    code, out, err = _run(["git", "commit", "-m", commit_message])
    if code != 0:
        print("git commit に失敗:", err)
        return 1
    print("コミット完了:", commit_message)

    code, out, err = _run(["git", "push", "origin", "main"])
    if code != 0:
        print("git push に失敗:", err)
        return 1
    print("push完了")

    # 4) Renderへデプロイをトリガー（Auto-Deployが不調な場合の保険として明示的に叩く）
    hook_url = _load_deploy_hook_url()
    if not hook_url:
        print("警告: .env に RENDER_DEPLOY_HOOK_URL が無いため、Renderへのデプロイ指示はスキップしました。")
        return 0

    try:
        req = urllib.request.Request(hook_url, method="POST")
        with urllib.request.urlopen(req, timeout=30) as resp:
            print("Renderへデプロイ指示を送信:", resp.status)
    except Exception as exc:  # noqa: BLE001
        print("Renderへのデプロイ指示に失敗:", exc)
        return 1

    print("売上速報の自動更新が完了しました。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
