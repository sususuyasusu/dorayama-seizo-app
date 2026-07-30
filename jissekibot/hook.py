"""製造実績Bot 受信処理（統合版）
旧 dw_line_seizou_jisseki_bot/main.py の処理を、製造アプリ同居用に移植。
環境変数は JISSEKI_ 接頭辞。未設定ならNoneを返す。"""
from __future__ import annotations

import datetime as dt
import logging
import os

log = logging.getLogger("seizou-jisseki-bot")
JST = dt.timezone(dt.timedelta(hours=9))

_handler = None


def _build():
    from linebot.v3 import WebhookHandler
    from linebot.v3.messaging import ApiClient, Configuration, MessagingApi
    from linebot.v3.webhooks import GroupSource, MessageEvent, TextMessageContent

    from .parser import parse, starts_with_jisseki
    from .sheets import get_client_from_env

    secret = os.environ.get("JISSEKI_LINE_CHANNEL_SECRET", "")
    token = os.environ.get("JISSEKI_LINE_CHANNEL_ACCESS_TOKEN", "")
    if not secret:
        return None
    configuration = Configuration(access_token=token)
    group_map = {}
    for pair in os.environ.get("GROUP_VENUE_MAP", "").split(","):
        if ":" in pair:
            k, v = pair.split(":", 1)
            group_map[k.strip()] = v.strip()

    def _group_name(group_id: str) -> str:
        try:
            with ApiClient(configuration) as api:
                return MessagingApi(api).get_group_summary(group_id).group_name or ""
        except Exception:
            log.exception("get_group_summary failed for %s", group_id)
            return ""

    h = WebhookHandler(secret)

    @h.add(MessageEvent, message=TextMessageContent)
    def handle_text(event: MessageEvent):
        text = event.message.text or ""
        if not starts_with_jisseki(text):
            return
        src = event.source
        group_id = getattr(src, "group_id", None) if isinstance(src, GroupSource) else None
        if not group_id:
            log.info("not a group message; skip")
            return
        hint = group_map.get(group_id) or _group_name(group_id)
        if not hint:
            log.warning("venue hint empty for group_id=%s; skip (sheet untouched)", group_id)
            return
        products = parse(text)
        if not products:
            log.info("no products parsed")
            return
        date = dt.datetime.fromtimestamp(event.timestamp / 1000, tz=JST).date()
        try:
            res = get_client_from_env().write_jisseki(hint, products, date)
        except Exception:
            log.exception("sheets write failed")
            return
        if res.get("ok"):
            log.info("wrote venue=%s tab=%s col=%s products=%s",
                     res.get("venue"), res.get("tab"), res.get("col"), res.get("wrote"))
        else:
            log.warning("skip group_id=%s hint=%s: %s", group_id, hint, res.get("reason"))

    return h


def handle(body: str, signature: str):
    """True=処理OK / False=署名不正 / None=未設定"""
    global _handler
    if _handler is None:
        _handler = _build()
    if _handler is None:
        return None
    from linebot.v3.exceptions import InvalidSignatureError
    try:
        _handler.handle(body, signature)
        return True
    except InvalidSignatureError:
        return False
