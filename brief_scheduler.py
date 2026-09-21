#!/usr/bin/env python3
"""毎朝6:30（日本時間）に、昨日の速報をどら山社員グループLINEへ送る（Render上で常駐・PC不要）。

送信に必要な環境変数（Renderの Environment に本人が設定する。未設定なら何も送らない）:
  BRIEF_LINE_CHANNEL_ACCESS_TOKEN … 送信に使うLINE公式アカウントのチャネルアクセストークン
  BRIEF_LINE_GROUP_ID             … 送信先グループのID
  BRIEF_MIN_QUOTA_LEFT（任意）    … LINEの月間送信枠の残りがこの通数を切ったら送らない（既定25。卵発注を優先する保護）
1日1回だけ送る。送信済みの日付は製造表の _app_config に記録するので、再起動・再デプロイでも二重送信しない。
6:30以降、正午までの間に起動していれば取り返して送る。
"""
import json
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from urllib.request import Request, urlopen

import config_store
import daily_brief
import management_analysis_layer

JST = timezone(timedelta(hours=9))
SEND_AT = (6, 30)
GRACE_UNTIL_HOUR = 12
LINE_API = "https://api.line.me/v2/bot/message"


def enabled():
    return bool(os.environ.get("BRIEF_LINE_CHANNEL_ACCESS_TOKEN") and os.environ.get("BRIEF_LINE_GROUP_ID"))


def _line_get(path, token):
    with urlopen(Request(f"{LINE_API}/{path}", headers={"Authorization": f"Bearer {token}"}), timeout=20) as response:
        return json.load(response)


def quota_left(token):
    """今月あと何通送れるか。上限なし・取得失敗のときは None。"""
    try:
        quota = _line_get("quota", token)
        if quota.get("type") != "limited":
            return None
        return int(quota["value"]) - int(_line_get("quota/consumption", token).get("totalUsage", 0))
    except Exception:
        return None


def push_text(token, group_id, text):
    body = json.dumps({"to": group_id, "messages": [{"type": "text", "text": text[:4900]}]}).encode("utf-8")
    request = Request(
        f"{LINE_API}/push", data=body, method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    with urlopen(request, timeout=30) as response:
        return response.status


def send_once(now=None):
    """今日分がまだなら、昨日の速報を送る。返り値は結果の説明。"""
    now = now or datetime.now(JST)
    today = now.date().isoformat()
    if config_store.get_config("brief_last_sent") == today:
        return "送信済み"
    token = os.environ["BRIEF_LINE_CHANNEL_ACCESS_TOKEN"]
    group_id = os.environ["BRIEF_LINE_GROUP_ID"]
    left = quota_left(token)
    if left is not None and left < int(os.environ.get("BRIEF_MIN_QUOTA_LEFT", "25")):
        config_store.set_config("brief_last_status", f"{today} 見送り：LINEの月間送信枠の残り{left}通")
        config_store.set_config("brief_last_sent", today)  # 枠が無い日に何度も試さない
        return f"見送り（送信枠の残り{left}通）"
    brief = daily_brief.build_brief(management_analysis_layer.get_management_analysis())
    status = push_text(token, group_id, brief["text"])
    config_store.set_config("brief_last_sent", today)
    config_store.set_config("brief_last_status", f"{today} 送信済み（{brief['date']}分・{'数字そろい' if brief['complete'] else '一部入力待ちのまま'}）")
    return f"送信 HTTP {status}"


def _loop():
    while True:
        try:
            now = datetime.now(JST)
            due = (now.hour, now.minute) >= SEND_AT and now.hour < GRACE_UNTIL_HOUR
            if due:
                result = send_once(now)
                if result != "送信済み":
                    print(f"[brief] {now.isoformat(timespec='minutes')} {result}", flush=True)
        except Exception as error:  # 通知が失敗してもアプリ本体は止めない
            print(f"[brief] エラー: {error}", flush=True)
            time.sleep(300)  # 失敗時は5分あけて再試行（正午まで）
        time.sleep(60)


def start():
    if not enabled():
        print("[brief] 送信設定（BRIEF_LINE_*）が未設定のため、毎朝の速報通知は停止中", flush=True)
        return
    threading.Thread(target=_loop, name="brief-scheduler", daemon=True).start()
    print("[brief] 毎朝6:30の速報通知を開始", flush=True)
