#!/usr/bin/env python3
"""どら山 製造表アプリ — フェーズ1サーバー（標準ライブラリのみ）。
読み: 予定・売れた数・回転数 を製造表から（サービスアカウント）。
書き: 作った数 をアプリ専用ストアへ。製造表/エアレジ同期には触れない。"""
import os
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import data_layer
import made_store
import egg_layer
import cost_layer
import material_layer

BASE = Path(__file__).parent


def week_payload():
    d = data_layer.get_week_store_data()
    tab = d["tab"]
    made_store.seed(tab, d["products"])
    made = made_store.get_made(tab)
    prods = []
    for p in d["products"]:
        prods.append({
            "name": p["name"],
            "plan": p["plan"],
            "sold": p["actual"],
            "made": made.get(p["name"], [None] * 7),
        })
    return {"tab": tab, "days": d["days"], "products": prods, "kaiten": d["kaiten"]}


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json"):
        b = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if self.path == "/" or self.path.startswith("/index"):
            self._send(200, (BASE / "templates" / "index.html").read_text(encoding="utf-8"),
                       "text/html; charset=utf-8")
        elif self.path == "/api/week":
            self._send(200, json.dumps(week_payload(), ensure_ascii=False))
        elif self.path == "/api/eggs":
            self._send(200, json.dumps(egg_layer.get_egg_nav(), ensure_ascii=False))
        elif self.path == "/api/cost":
            self._send(200, json.dumps(cost_layer.get_cost(), ensure_ascii=False))
        elif self.path == "/api/materials":
            self._send(200, json.dumps(material_layer.get_materials(), ensure_ascii=False))
        else:
            self._send(404, "{}")

    def do_POST(self):
        if self.path == "/api/made":
            n = int(self.headers.get("Content-Length", 0) or 0)
            data = json.loads(self.rfile.read(n) or b"{}")
            made_store.set_made(data["tab"], data["product"], data["dayIndex"], data.get("value"))
            self._send(200, json.dumps({"ok": True}))
        else:
            self._send(404, "{}")

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8765))
    host = "0.0.0.0" if os.environ.get("PORT") else "127.0.0.1"
    ThreadingHTTPServer((host, port), Handler).serve_forever()
