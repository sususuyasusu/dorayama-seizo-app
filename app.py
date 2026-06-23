#!/usr/bin/env python3
"""どら山 製造表アプリ — フェーズ1サーバー（標準ライブラリのみ）。
読み: 予定・売れた数・回転数 を製造表から（サービスアカウント）。
書き: 作った数 をアプリ専用ストアへ。製造表/エアレジ同期には触れない。"""
import os
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

import data_layer
import made_store
import egg_layer
import cost_layer
import material_layer

BASE = Path(__file__).parent


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
                "plan": p["plan"],
                "sold": p["actual"],
                "made": made.get(b["name"], {}).get(p["name"], [None] * 7),
            })
        blocks.append({"name": b["name"], "category": b["category"], "products": prods})
    return {"tab": tab, "days": d["days"], "blocks": blocks, "kaiten": d["kaiten"]}


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
        self.send_response(code)
        self.send_header("Content-Type", ctype)
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
        elif path == "/api/tabs":
            self._send(200, json.dumps(data_layer.list_tabs(), ensure_ascii=False))
        elif path == "/api/week":
            self._send(200, json.dumps(week_payload(tab), ensure_ascii=False))
        elif path == "/api/eggs":
            self._send(200, json.dumps(egg_layer.get_egg_nav(tab), ensure_ascii=False))
        elif path == "/api/cost":
            self._send(200, json.dumps(cost_layer.get_cost(tab), ensure_ascii=False))
        elif path == "/api/materials":
            self._send(200, json.dumps(material_layer.get_materials(tab), ensure_ascii=False))
        elif path == "/api/raw":
            self._send(200, json.dumps(data_layer.get_raw(tab), ensure_ascii=False))
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
        data = json.loads(self.rfile.read(n) or b"{}") if path.startswith("/api/") else {}
        if path == "/api/made":
            made_store.set_made(data["tab"], data["block"], data["product"], data["dayIndex"], data.get("value"))
            self._send(200, json.dumps({"ok": True}))
        elif path == "/api/cell":
            r = data_layer.set_cell(data["tab"], data["row"], data["col"], data.get("value", ""))
            self._send(200, json.dumps(r, ensure_ascii=False))
        else:
            self._send(404, "{}")

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8765))
    host = "0.0.0.0" if os.environ.get("PORT") else "127.0.0.1"
    ThreadingHTTPServer((host, port), Handler).serve_forever()
