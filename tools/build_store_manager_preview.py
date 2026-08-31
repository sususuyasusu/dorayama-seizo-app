#!/usr/bin/env python3
"""経常利益画面をAPI不要の確認用HTMLへ書き出す。"""
import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

import budget_workbook_layer
import management_analysis_layer

def main(output_path):
    html = (BASE / "templates" / "store_manager.html").read_text(encoding="utf-8")
    analysis = json.dumps(management_analysis_layer.get_management_analysis(), ensure_ascii=False, allow_nan=False)
    sheets = json.dumps(
        {item["name"]: budget_workbook_layer.get_sheet(item["name"])
         for item in budget_workbook_layer.catalog()},
        ensure_ascii=False,
        allow_nan=False,
    )
    mock = (
        "<script>window.__DORAYAMA_STATIC_PREVIEW__=true;const __previewAnalysis=" + analysis + ";const __previewSheets=" + sheets + ";"
        "window.fetch=(url)=>{const value=String(url);if(value.includes('/api/management/analysis'))"
        "return Promise.resolve({ok:true,json:()=>Promise.resolve(__previewAnalysis)});"
        "const name=decodeURIComponent((value.split('sheet=')[1]||'').split('&')[0]);"
        "const data=__previewSheets[name];return Promise.resolve({ok:!!data,json:()=>Promise.resolve(data)});};</script>"
    )
    html = html.replace("<script>\nconst state=", mock + "\n<script>\nconst state=", 1)
    html = html.replace('src="/static/logo.png"', f'src="{(BASE / "static" / "logo.png").as_uri()}"')
    Path(output_path).write_text(html, encoding="utf-8")


if __name__ == "__main__":
    main(sys.argv[1])
