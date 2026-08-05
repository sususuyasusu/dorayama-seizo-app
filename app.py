#!/usr/bin/env python3
"""どら山 製造表アプリ — フェーズ1サーバー（標準ライブラリのみ）。
読み: 予定・売れた数・回転数 を製造表から（サービスアカウント）。
書き: 作った数 をアプリ専用ストアへ。製造表/エアレジ同期には触れない。"""
import os
import gzip
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

import data_layer
import made_store
import egg_layer
import cost_layer
import material_layer
import weather_layer
import inventory_layer
import matlink_layer
import anko_layer
import orderlist_layer
import manual_layer
import sms_layer
import seibun_layer

BASE = Path(__file__).parent
STATIC = (BASE / "static").resolve()
MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}


def week_payload(tab=None):
    d = data_layer.get_week_blocks(tab)
    tab = d["tab"]
    made_store.seed(tab, d["blocks"])
    made = made_store.get_made(tab)
    blocks = []
    for b in d["blocks"]:
        prods = []
        for p in b["products"]:
            prods.append({
                "name": p["name"],
                "row": p.get("row"),
                "plan": p["plan"],
                "sold": p["actual"],
                "made": made.get(b["name"], {}).get(p["name"], [None] * 7),
            })
        blocks.append({"name": b["name"], "category": b["category"], "products": prods})
    return {"tab": tab, "gid": d.get("gid"), "sheetId": data_layer.SHEET_ID,
            "days": d["days"], "blocks": blocks, "kaiten": d["kaiten"],
            "weather": weather_layer.week_weather(d["days"])}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            self._route_get()
        except Exception as e:
            try:
                self._send(500, json.dumps({"error": str(e)}))
            except Exception:
                pass

    def _send(self, code, body, ctype="application/json"):
        b = body.encode("utf-8") if isinstance(body, str) else body
        # 画面のHTMLもデータも文字ばかりで、圧縮すると1/4以下になる。
        # 店舗のスマホ回線での待ち時間短縮に効く（サーバー費用は増えない）。
        enc = None
        if len(b) > 1024 and "gzip" in (self.headers.get("Accept-Encoding") or ""):
            try:
                b = gzip.compress(b, 6)
                enc = "gzip"
            except Exception:
                enc = None
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        if enc:
            self.send_header("Content-Encoding", enc)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _route_get(self):
        u = urlparse(self.path)
        path = u.path
        tab = (parse_qs(u.query).get("tab") or [None])[0]
        if path == "/" or path.startswith("/index"):
            self._send(200, (BASE / "templates" / "index.html").read_text(encoding="utf-8"),
                       "text/html; charset=utf-8")
        elif path == "/manual" or path == "/manual/":
            # マニュアルは独立アプリへ移行（2026-07-14）。旧URLは転送で生かす。
            self.send_response(302)
            self.send_header("Location", "https://sususuyasusu.github.io/dorayama-manual/")
            self.send_header("Content-Length", "0")
            self.end_headers()
        elif path == "/seibun" or path == "/seibun/":
            self._send(200, (BASE / "templates" / "seibun.html").read_text(encoding="utf-8"),
                       "text/html; charset=utf-8")
        elif path == "/api/seibun/kawa":
            self._send(200, json.dumps(seibun_layer.kawa_recipe(), ensure_ascii=False))
        elif path == "/api/seibun/usage":
            self._send(200, json.dumps(seibun_layer.usage(), ensure_ascii=False))
        elif path == "/api/seibun/products":
            self._send(200, json.dumps(seibun_layer.products(), ensure_ascii=False))
        elif path == "/api/seibun/history":
            self._send(200, json.dumps(seibun_layer.history(), ensure_ascii=False))
        elif path == "/api/seibun/presets":
            self._send(200, json.dumps(seibun_layer.presets(), ensure_ascii=False))
        elif path.startswith("/seibun/photo/"):
            # シートに保管した原材料写真を画像として返す。セルの =IMAGE() から
            # Google側が取りに来るので、ここだけは合言葉を求めない。
            pid = path[len("/seibun/photo/"):].split(".")[0]
            body, mime = seibun_layer.get_photo(pid)
            if not body:
                self._send(404, "not found", "text/plain; charset=utf-8")
            else:
                self.send_response(200)
                self.send_header("Content-Type", mime or "image/jpeg")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "public, max-age=604800")
                self.end_headers()
                self.wfile.write(body)
        elif path == "/api/manual":
            self._send(200, json.dumps(manual_layer.get_manual(), ensure_ascii=False))
        elif path == "/api/tabs":
            self._send(200, json.dumps(data_layer.list_tabs(), ensure_ascii=False))
        elif path == "/api/gids":
            self._send(200, json.dumps(data_layer.list_gids(), ensure_ascii=False))
        elif path == "/api/week":
            self._send(200, json.dumps(week_payload(tab), ensure_ascii=False))
        elif path == "/api/eggs":
            self._send(200, json.dumps(egg_layer.get_egg_nav(tab), ensure_ascii=False))
        elif path == "/api/eggsync":
            import egg_stock_sync
            self._send(200, json.dumps(egg_stock_sync.status, ensure_ascii=False))
        elif path == "/api/cost":
            self._send(200, json.dumps(cost_layer.get_cost(tab), ensure_ascii=False))
        elif path == "/api/materials":
            self._send(200, json.dumps(material_layer.get_materials(tab), ensure_ascii=False))
        elif path == "/api/inventory":
            self._send(200, json.dumps(inventory_layer.get_inventory(), ensure_ascii=False))
        elif path == "/api/matstock":
            self._send(200, json.dumps(matlink_layer.get_material_inventory(tab), ensure_ascii=False))
        elif path == "/api/orderlist":
            self._send(200, json.dumps(orderlist_layer.get_orderlist(), ensure_ascii=False))
        elif path == "/api/anko":
            self._send(200, json.dumps(anko_layer.get_anko_order(tab), ensure_ascii=False))
        elif path == "/api/sms_status":
            self._send(200, json.dumps(sms_layer.status(), ensure_ascii=False))
        elif path == "/api/raw":
            self._send(200, json.dumps(data_layer.get_raw(tab), ensure_ascii=False))
        elif path == "/api/raw_styled":
            import sheetfmt_layer
            self._send(200, json.dumps(sheetfmt_layer.get_raw_styled(tab), ensure_ascii=False))
        elif path.startswith("/static/"):
            p = (STATIC / path[len("/static/"):]).resolve()
            if STATIC in p.parents and p.is_file() and p.suffix.lower() in MIME:
                self._send(200, p.read_bytes(), MIME[p.suffix.lower()])
            else:
                self._send(404, "{}")
        else:
            self._send(404, "{}")

    def do_POST(self):
        try:
            self._route_post()
        except Exception as e:
            try:
                self._send(500, json.dumps({"error": str(e)}))
            except Exception:
                pass

    def _route_post(self):
        path = urlparse(self.path).path
        n = int(self.headers.get("Content-Length", 0) or 0)
        # LINE Bot Webhook（卵発注/製造実績を同居）: 生ボディ＋署名で検証
        if path in ("/webhook/egg", "/webhook/jisseki"):
            raw = (self.rfile.read(n) or b"").decode("utf-8")
            sig = self.headers.get("X-Line-Signature", "")
            if path == "/webhook/egg":
                from eggbot import hook as _hook
            else:
                from jissekibot import hook as _hook
            ok = _hook.handle(raw, sig)
            if ok is None:
                self._send(503, json.dumps({"error": "not configured"}))
            elif ok is False:
                self._send(400, json.dumps({"error": "invalid signature"}))
            else:
                self._send(200, json.dumps({"status": "ok"}))
            return
        data = json.loads(self.rfile.read(n) or b"{}") if path.startswith("/api/") else {}
        if path == "/api/made":
            made_store.set_made(data["tab"], data["block"], data["product"], data["dayIndex"], data.get("value"))
            self._send(200, json.dumps({"ok": True}))
        elif path == "/api/cell":
            r = data_layer.set_cell(data["tab"], data["row"], data["col"], data.get("value", ""))
            self._send(200, json.dumps(r, ensure_ascii=False))
        elif path == "/api/anko_rate":
            self._send(200, json.dumps(
                anko_layer.set_jun_rate(data.get("value", 0), data.get("tab")),
                ensure_ascii=False,
            ))
        elif path == "/api/anko_config":
            self._send(200, json.dumps(
                anko_layer.set_anko_config(data, data.get("tab")),
                ensure_ascii=False,
            ))
        elif path == "/api/seibun/analyze":
            # 合言葉が通らなければ画像認識に進まない（URLを知っているだけの人に費用を使わせない）
            ok, msg, code = seibun_layer.check_access(data.get("accessCode"))
            if not ok:
                self._send(code, json.dumps({"error": msg}, ensure_ascii=False))
                return
            r = seibun_layer.analyze(
                data.get("image"), data.get("mediaType"),
                data.get("manufacturer", ""), data.get("productName", ""),
            )
            self._send(int(r.pop("status", 200)) if "error" in r else 200,
                       json.dumps(r, ensure_ascii=False))
        elif path == "/api/eggs/ordered":
            # 「発注済みにする」の押下を製造表に記録する（端末ごとに食い違わせない）
            try:
                import egg_order_store
                if data.get("clear"):
                    r = egg_order_store.clear_ordered(data.get("key"))
                else:
                    r = egg_order_store.set_ordered(
                        data.get("key"), data.get("date"),
                        data.get("yolkBags"), data.get("whiteBags"))
            except Exception as e:
                r = {"ok": False, "msg": f"記録できませんでした（{e}）"}
            self._send(200, json.dumps(r, ensure_ascii=False))
        elif path == "/api/seibun/photo":
            # 読み取った原材料写真を、そのままシートに保管する
            ok, msg, code = seibun_layer.check_access(data.get("accessCode"))
            if not ok:
                self._send(code, json.dumps({"error": msg}, ensure_ascii=False))
                return
            try:
                r = seibun_layer.save_photo(data.get("image"),
                                            data.get("mediaType") or "image/jpeg",
                                            data.get("label") or "")
            except Exception as e:
                r = {"ok": False, "msg": f"写真を保管できませんでした（{e}）"}
            self._send(200, json.dumps(r, ensure_ascii=False))
        elif path == "/api/seibun/save":
            # シートを書き換えるので、読み取りと同じ合言葉を必須にする
            ok, msg, code = seibun_layer.check_access(data.get("accessCode"))
            if not ok:
                self._send(code, json.dumps({"error": msg}, ensure_ascii=False))
                return
            try:
                r = seibun_layer.save_result(data)
            except Exception as e:
                r = {"ok": False, "msg": f"保存できませんでした（{e}）"}
            self._send(200, json.dumps(r, ensure_ascii=False))
        elif path == "/api/seibun/preset/add":
            ok, msg, code = seibun_layer.check_access(data.get("accessCode"))
            if not ok:
                self._send(code, json.dumps({"error": msg}, ensure_ascii=False))
                return
            try:
                r = seibun_layer.add_preset(data)
            except Exception as e:
                r = {"ok": False, "msg": f"登録できませんでした（{e}）"}
            self._send(200, json.dumps(r, ensure_ascii=False))
        elif path == "/api/manual/add":
            self._send(200, json.dumps(manual_layer.add_entry(data), ensure_ascii=False))
        elif path == "/api/manual/announce":
            self._send(200, json.dumps(manual_layer.announce(data), ensure_ascii=False))
        else:
            self._send(404, "{}")

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8765))
    host = "0.0.0.0" if os.environ.get("PORT") else "127.0.0.1"
    import egg_stock_sync
    egg_stock_sync.start()   # AppSheet在庫→製造表の10分同期（Apps Scriptトリガ停止の恒久対策）
    ThreadingHTTPServer((host, port), Handler).serve_forever()
