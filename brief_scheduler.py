#!/usr/bin/env python3
"""毎朝、昨日の速報をどら山社員グループLINEへ送る予備送信（Render上で常駐・PC不要）。
通常は6:30に業界ウォッチ(Mac)が速報を合流させて送り、送信済みの印(brief_last_sent)を書く。
6:40になっても印が無い日（Macが止まっていた日）だけ、ここが速報を単独で送る。

送信に必要な環境変数（Renderの Environment に本人が設定する。未設定なら何も送らない）:
  BRIEF_LINE_CHANNEL_ACCESS_TOKEN … 送信に使うLINE公式アカウントのチャネルアクセストークン
  BRIEF_LINE_GROUP_ID             … 送信先グループのID
  BRIEF_MIN_QUOTA_LEFT（任意）    … LINEの月間送信枠の残りがこの通数を切ったら送らない（既定25。卵発注を優先する保護）
1日1回だけ送る。送信済みの日付は製造表の _app_config に記録するので、再起動・再デプロイでも二重送信しない。
6:40〜7:00の間に限って取り返して送る（それ以外の時間の再デプロイでは絶対に送らない）。

【2026-09-22の事故と対策】以前は正午まで猶予があり、日中の作業用の再デプロイ（Renderの再起動）のたびに
「今日分がまだ」なら即座に本送信してしまい、確認中の内容が無断でLINEに投稿される事故が起きた。
再発防止として、猶予を6:40〜7:00の20分だけに縮め、日中の再デプロイでは絶対に自動送信されないようにした。
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
SEND_AT = (6, 40)  # 6:30に業界ウォッチ(Mac)が合流して送る。送られていなければ、この予備送信が単独で送る
SEND_UNTIL = (7, 0)  # この時刻を過ぎたら、その日はもう自動送信しない（日中の再デプロイでの誤送信を防ぐ）
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
    """送信し、(HTTPステータス, 送ったメッセージのID) を返す。
    IDは、誤送信時にLINEの取り消しAPI（/v2/bot/message/{id}/unsend）で消せるよう記録しておく。"""
    body = json.dumps({"to": group_id, "messages": [{"type": "text", "text": text[:4900]}]}).encode("utf-8")
    request = Request(
        f"{LINE_API}/push", data=body, method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    with urlopen(request, timeout=30) as response:
        try:
            sent = json.load(response).get("sentMessages") or []
            message_id = sent[0]["id"] if sent else None
        except Exception:
            message_id = None
        return response.status, message_id


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
    status, message_id = push_text(token, group_id, brief["text"])
    config_store.set_config("brief_last_sent", today)
    config_store.set_config("brief_last_status", f"{today} 送信済み（{brief['date']}分・{'数字そろい' if brief['complete'] else '一部入力待ちのまま'}）")
    if message_id:
        config_store.set_config("brief_last_message_id", message_id)
    return f"送信 HTTP {status}"


def _loop():
    while True:
        try:
            now = datetime.now(JST)
            clock = (now.hour, now.minute)
            due = SEND_AT <= clock <= SEND_UNTIL
            if due:
                result = send_once(now)
                if result != "送信済み":
                    print(f"[brief] {now.isoformat(timespec='minutes')} {result}", flush=True)
        except Exception as error:  # 通知が失敗してもアプリ本体は止めない
            print(f"[brief] エラー: {error}", flush=True)
            time.sleep(300)  # 失敗時は5分あけて再試行（6:40〜7:00の間だけ）
        time.sleep(60)


def start():
    if not enabled():
        print("[brief] 送信設定（BRIEF_LINE_*）が未設定のため、毎朝の速報通知は停止中", flush=True)
        return
    threading.Thread(target=_loop, name="brief-scheduler", daemon=True).start()
    print("[brief] 毎朝6:30の速報通知を開始", flush=True)
