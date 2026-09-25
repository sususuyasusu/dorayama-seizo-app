#!/usr/bin/env python3
"""毎朝、昨日の速報をどら山社員グループLINEへ送る予備送信（Render上で常駐・PC不要）。
通常は6:30に業界ウォッチ(Mac)が速報を合流させて送り、送信済みの印(brief_last_sent)を書く。
6:40になっても印が無い日（Macが止まっていた日）だけ、ここが速報を単独で送る。

送信に必要な環境変数（Renderの Environment に本人が設定する。未設定なら何も送らない）:
  BRIEF_LINE_CHANNEL_ACCESS_TOKEN … 送信に使うLINE公式アカウントのチャネルアクセストークン
  BRIEF_LINE_GROUP_ID             … 送信先グループのID
1日1回だけ送る。送信済みの日付は製造表の _app_config に記録するので、再起動・再デプロイでも二重送信しない。

【2026-09-24 卵発注Bot優先の保護を撤廃・本人指示】以前は月間送信枠の残りが少ない日（既定25通未満）は
速報を見送っていたが、卵発注Bot（Render上の別サービス dw_line_egg_order_bot）はLINEへの送信を一切せず
（受信して製造表へ書くだけ／証憑取込の応答はreplyで枠を消費しない）枠を使っていないと判明したため、
この保護は不要と判断し撤廃した。速報は枠の残りに関わらず常に送る。
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
    """今日分がまだなら、昨日の速報を送る。返り値は結果の説明。

    【2026-09-25追加】数字が「未反映の疑い」(daily_brief.pyのstore_suspect/event_suspect)で
    complete=Falseの間は、締切(SEND_UNTIL)前ならまだ送らずリトライに回す。0円のまま自信満々に
    送って翌朝の実績と全く違う、という事故（2026-09-24）の再発防止。締切に達したらそれ以上待てない
    ため、未確定である旨を隠さない文面のまま送る。"""
    now = now or datetime.now(JST)
    today = now.date().isoformat()
    if config_store.get_config("brief_last_sent") == today:
        return "送信済み"
    token = os.environ["BRIEF_LINE_CHANNEL_ACCESS_TOKEN"]
    group_id = os.environ["BRIEF_LINE_GROUP_ID"]
    brief = daily_brief.build_brief(management_analysis_layer.get_management_analysis())
    at_deadline = (now.hour, now.minute) >= SEND_UNTIL
    if not brief["complete"] and not at_deadline:
        return "未反映の疑いありのため待機（リトライ）"
    status, message_id = push_text(token, group_id, brief["text"])
    config_store.set_config("brief_last_sent", today)
    config_store.set_config("brief_last_status", f"{today} 送信済み（{brief['date']}分・{'数字そろい' if brief['complete'] else '未確定のまま締切送信'}）")
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
