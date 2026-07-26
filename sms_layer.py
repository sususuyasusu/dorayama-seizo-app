"""アマダ東京への卵発注SMSを Twilio 経由で自動送信する層。

環境変数（Render の Environment に設定。未設定なら機能は眠ったまま＝従来どおり手動SMS）:
  TWILIO_SID   … Twilio Account SID (ACから始まる)
  TWILIO_TOKEN … Twilio Auth Token
  TWILIO_FROM  … Twilioで購入した送信元番号 (+1... など)
  EGG_SMS_TO   … 送信先。省略時はアマダ東京 +818043565441

安全装置:
  - 同じ本文は20時間以内は再送しない（二重タップ・再描画対策）
  - 送信結果は data/sms_sent.log に追記（監査用）
"""
import os
import json
import time
import hashlib
import base64
import urllib.request
import urllib.parse
from pathlib import Path

BASE = Path(__file__).parent
DATA = BASE / "data"
LOG_JSON = DATA / "sms_log.json"      # 重複ガード用 {hash: epoch}
LOG_TEXT = DATA / "sms_sent.log"      # 監査ログ

DEFAULT_TO = "+818043565441"  # アマダ東京
DUP_WINDOW_SEC = 20 * 3600


def _env():
    return (
        os.environ.get("TWILIO_SID", "").strip(),
        os.environ.get("TWILIO_TOKEN", "").strip(),
        os.environ.get("TWILIO_FROM", "").strip(),
        os.environ.get("EGG_SMS_TO", DEFAULT_TO).strip(),
    )


def enabled():
    sid, token, frm, to = _env()
    return bool(sid and token and frm and to)


def status():
    sid, token, frm, to = _env()
    return {"enabled": enabled(), "to": to if enabled() else ""}


def _load_dup():
    try:
        return json.loads(LOG_JSON.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_dup(d):
    try:
        DATA.mkdir(exist_ok=True)
        LOG_JSON.write_text(json.dumps(d), encoding="utf-8")
    except Exception:
        pass


def _audit(line):
    try:
        DATA.mkdir(exist_ok=True)
        with LOG_TEXT.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def send_sms(body, batch=""):
    """本文をアマダ東京へ送る。返り値: {ok, sent, duplicate, disabled, error}"""
    body = (body or "").strip()
    if not body:
        return {"ok": False, "error": "本文が空です"}
    if not enabled():
        return {"ok": False, "disabled": True}

    sid, token, frm, to = _env()

    h = hashlib.sha256(body.encode("utf-8")).hexdigest()[:24]
    now = time.time()
    dup = _load_dup()
    # 古い記録を掃除
    dup = {k: v for k, v in dup.items() if now - v < DUP_WINDOW_SEC}
    if h in dup:
        return {"ok": True, "duplicate": True}

    url = "https://api.twilio.com/2010-04-01/Accounts/%s/Messages.json" % sid
    payload = urllib.parse.urlencode({"To": to, "From": frm, "Body": body}).encode("utf-8")
    req = urllib.request.Request(url, data=payload, method="POST")
    auth = base64.b64encode(("%s:%s" % (sid, token)).encode("utf-8")).decode("ascii")
    req.add_header("Authorization", "Basic " + auth)
    req.add_header("Content-Type", "application/x-www-form-urlencoded")

    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            res = json.loads(r.read().decode("utf-8"))
        msid = res.get("sid", "")
        dup[h] = now
        _save_dup(dup)
        _audit("%s OK to=%s batch=%s sid=%s | %s" % (ts, to, batch, msid, body.splitlines()[2] if len(body.splitlines()) > 2 else ""))
        return {"ok": True, "sent": True, "sid": msid}
    except urllib.error.HTTPError as e:
        try:
            err = json.loads(e.read().decode("utf-8")).get("message", str(e))
        except Exception:
            err = str(e)
        _audit("%s FAIL to=%s batch=%s err=%s" % (ts, to, batch, err))
        return {"ok": False, "error": err}
    except Exception as e:
        _audit("%s FAIL to=%s batch=%s err=%s" % (ts, to, batch, e))
        return {"ok": False, "error": str(e)}
