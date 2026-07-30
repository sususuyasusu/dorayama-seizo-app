"""卵発注Bot 受信処理（統合版）
旧 dw_line_egg_order_bot/main.py の処理を、製造アプリ同居用に移植。
環境変数は EGG_ 接頭辞（旧サービスと衝突しないため）。
未設定ならNoneを返し、アプリ側で503にする（アプリ本体は影響を受けない）。"""
from __future__ import annotations

import logging
import os

log = logging.getLogger("egg-order-bot")

_handler = None


def _build():
    global _handler
    from linebot.v3 import WebhookHandler
    from linebot.v3.webhooks import GroupSource, MessageEvent, TextMessageContent

    from .parser import parse, starts_with_egg_order
    from .sheets import get_client_from_env

    secret = os.environ.get("EGG_LINE_CHANNEL_SECRET", "")
    if not secret:
        return None
    allowed = {g.strip() for g in os.environ.get("ALLOWED_GROUP_IDS", "").split(",") if g.strip()}
    h = WebhookHandler(secret)

    @h.add(MessageEvent, message=TextMessageContent)
    def handle_text(event: MessageEvent):
        text = event.message.text or ""
        _gid = getattr(event.source, "group_id", None)
        if _gid:
            log.info("seen group_id=%s", _gid)
        if not starts_with_egg_order(text):
            return
        src = event.source
        if allowed:
            gid = getattr(src, "group_id", None) if isinstance(src, GroupSource) else None
            if gid not in allowed:
                log.info("ignored: group_id=%s not in allowed list", gid)
                return
        try:
            items = parse(text)
        except Exception:
            log.exception("parse failed")
            return
        if not items:
            log.info("no items parsed from text")
            return
        try:
            client = get_client_from_env()
            results = client.write_orders(items)
        except Exception:
            log.exception("sheets write failed")
            return
        for r in results:
            if r.ok:
                log.info("wrote tab=%s row=%s date=%s yolk=%s white=%s",
                         r.tab, r.row, r.item.date, r.item.yolk_rot, r.item.white_rot)
            else:
                log.warning("skip date=%s: %s", r.item.date, r.error)

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
