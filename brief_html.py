#!/usr/bin/env python3
"""昨日の速報を、業界ウォッチと同じ「クリーム地・濃緑」デザインのHTML（A4向け）にする。

build_brief() の data（構造化した数字）から作る。数字が無い項目は「入力待ち」「読み取れませんでした」と書き、
0円や「目標内」と見せない。業界ウォッチの紙面（HTML→PDF）の先頭に差し込める断片と、単体で開けるページの両方を返す。
"""
from html import escape

# 業界ウォッチのデザイン
GREEN = "#2f5247"
RED = "#b85450"
OK_GREEN = "#3f7a58"
BLOCK_COLORS = ["#b85450", "#7aa88b", "#8b709c", "#c29853", "#5fa8a0", "#8c9ad1"]

STYLE = f"""
.sb{{font-family:-apple-system,"Hiragino Kaku Gothic ProN","Yu Gothic",sans-serif;color:#2a2622}}
.sb *{{box-sizing:border-box}}
.sb h2{{display:flex;align-items:center;gap:10px;margin:0 0 14px;font-size:17pt;font-weight:500;letter-spacing:.04em}}
.sb h2::before{{content:"";width:5px;height:24px;border-radius:3px;background:{GREEN}}}
.sb .row{{display:grid;gap:10px;margin-bottom:10px}}.sb .r2{{grid-template-columns:1fr 1fr}}.sb .r3{{grid-template-columns:repeat(3,1fr)}}
.sb .card{{position:relative;background:#fffdf9;border:1px solid #e6dccb;border-radius:14px;padding:12px 15px 11px;overflow:hidden}}
.sb .lab{{font-size:8.5pt;color:#7a7066;margin-bottom:2px}}
.sb .num{{font-size:22pt;font-weight:400;letter-spacing:.02em;line-height:1.25}}
.sb .num.sm{{font-size:19pt}}
.sb .sub{{font-size:7.5pt;color:#7a7066;margin-top:2px}}
.sb .tgt{{font-size:8pt;color:#6b6157;margin-top:6px}}
.sb .pill{{position:absolute;right:12px;bottom:14px;padding:3px 11px;border-radius:99px;font-size:8.5pt}}
.sb .pill.bad{{background:#f6dcd8;color:#b0453d}}.sb .pill.good{{background:#dcebe1;color:{OK_GREEN}}}.sb .pill.wait{{background:#f3e7c9;color:#9a7422}}
.sb .meter{{position:absolute;left:0;right:0;bottom:0;height:4px;background:#ece5d8}}
.sb .meter i{{display:block;height:100%}}
.sb .ok{{color:{OK_GREEN}}}.sb .over{{color:#b0453d}}.sb .mute{{color:#9a9084}}
.sb .strip{{display:flex;align-items:center;justify-content:space-between;gap:12px;background:#f1e4dc;border:1px solid #e4d3c8;border-radius:12px;padding:10px 16px;font-size:9.5pt;color:{RED};margin-bottom:16px}}
.sb .strip b{{font-weight:500;color:#2a2622;font-size:11pt}}.sb .strip .k{{color:#b8674f}}
.sb .strip.calm{{background:#eef2ec;border-color:#d6e2d6;color:{OK_GREEN}}}
.sb h3{{margin:0 0 8px;font-size:11pt;font-weight:500}}
.sb .stack{{display:flex;height:17px;border-radius:9px;overflow:hidden;background:#ece5d8}}.sb .stack i{{display:block;height:100%}}
.sb .legend{{display:flex;flex-wrap:wrap;gap:4px 22px;margin-top:8px;font-size:8.5pt;color:#6b6157}}
.sb .legend b{{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:5px}}
.sb .note{{margin-top:8px;font-size:8pt;color:#9a9084}}
.sb .charts .card{{padding:10px 14px 9px}}.sb .charts .legend{{margin-top:2px;gap:2px 12px;font-size:7.5pt}}
"""


def _yen(value):
    return f"{round(value):,}円"


def _signed(value):
    return ("+" if value >= 0 else "▲") + f"{abs(round(value)):,}円"


def _meter(achieve, good):
    if achieve is None:
        return ""
    color = OK_GREEN if good else RED
    return f'<div class="meter"><i style="width:{min(max(achieve, 0), 100):.1f}%;background:{color}"></i></div>'


def _sales_card(title, part, event=False):
    if event and part["state"] == "none":
        return (f'<div class="card"><div class="lab">{title}</div><div class="num sm mute">催事なし</div>'
                f'<div class="sub">この日は催事の開催日ではありません</div></div>')
    if event and part["state"] == "pending":
        return (f'<div class="card"><div class="lab">{title}</div><div class="num sm mute">日報の入力待ち</div>'
                f'<div class="tgt">予算 {_yen(part["target"])}</div><span class="pill wait">入力待ち</span></div>')
    good = part["diff"] >= 0
    return (f'<div class="card"><div class="lab">{title}</div><div class="num">{_yen(part["sales"])}</div>'
            f'<div class="tgt">予算 {_yen(part["target"])}</div>'
            f'<span class="pill {"good" if good else "bad"}">{_signed(part["diff"])}</span>'
            f'{_meter(part["achieve"], good)}</div>')


def _svg_chart(days, series, max_value, width=300, height=96):
    """小さな累積グラフ。series=[(点の列, 色, 点線か, 塗りか)]。横軸は1日〜月末。"""
    left, right, top, bottom = 4, 4, 8, 14
    plot_w, plot_h = width - left - right, height - top - bottom

    def xy(i, value):
        return left + i * plot_w / max(days - 1, 1), top + plot_h - min(value / max_value, 1) * plot_h

    out = [f'<svg viewBox="0 0 {width} {height}" width="100%" role="img">',
           f'<line x1="{left}" y1="{top + plot_h}" x2="{width - right}" y2="{top + plot_h}" stroke="#d9cfc0" stroke-width="1"/>']
    for points, color, dashed, fill in series:
        if not points:
            continue
        coords = [xy(i, v) for i, v in enumerate(points)]
        line = " ".join(f"{x:.1f},{y:.1f}" for x, y in coords)
        if fill:
            out.append(f'<polygon points="{coords[0][0]:.1f},{top + plot_h} {line} {coords[-1][0]:.1f},{top + plot_h}" fill="{color}" fill-opacity=".22"/>')
        dash = ' stroke-dasharray="3 3"' if dashed else ""
        out.append(f'<polyline points="{line}" fill="none" stroke="{color}" stroke-width="{1.4 if dashed else 2}"{dash} stroke-linejoin="round"/>')
    last = series[0][0]
    if last:
        x, y = xy(len(last) - 1, last[-1])
        out.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3" fill="#fffdf9" stroke="{series[0][1]}" stroke-width="2"/>')
    for d in (1, 10, 20, days):
        x, _ = xy(d - 1, 0)
        anchor = "end" if d == days else ("start" if d == 1 else "middle")
        out.append(f'<text x="{x:.1f}" y="{height - 2}" font-size="8" fill="#9a9084" text-anchor="{anchor}">{d}</text>')
    out.append("</svg>")
    return "".join(out)


def _mini_charts(data):
    charts = data.get("charts")
    if not charts or not charts["sales"]["actual"]:
        return ""
    days, sales, labor = charts["days"], charts["sales"], charts["labor"]
    sales_max = max(sales["target"] + sales["actual"] + [1]) * 1.05
    labor_max = max(labor["total"] + labor["allowed"] + [1]) * 1.1
    sales_svg = _svg_chart(days, [(sales["actual"], GREEN, False, True), (sales["target"], "#c29853", True, False)], sales_max)
    labor_svg = _svg_chart(days, [(labor["total"], GREEN, False, True), (labor["store"], "#7aa88b", False, False),
                                  (labor["allowed"], RED, True, False)], labor_max)
    legend_sales = f'<div class="legend"><span><b style="background:{GREEN}"></b>売上（累積）</span><span><b style="background:#c29853"></b>予算（累積）</span></div>'
    legend_labor = (f'<div class="legend"><span><b style="background:{GREEN}"></b>人件費（合算）</span>'
                    f'<span><b style="background:#7aa88b"></b>うち店舗</span><span><b style="background:{RED}"></b>上限の目安</span></div>')
    return ('<div class="row r2 charts"><div class="card"><div class="lab">売上の進み（月初〜昨日）</div>' + sales_svg + legend_sales + "</div>"
            '<div class="card"><div class="lab">人件費の進み（製造実績×目標率が上限）</div>' + labor_svg + legend_labor + "</div></div>")


def render_block(data):
    """速報の断片（見出し＋カード＋月ここまで＋製造実績の内訳）。data が無いときは空。"""
    if not data:
        return ""
    labor, prod, rate, month = data["labor"], data["production"], data["rate"], data.get("month")
    parts = [f'<div class="sb"><h2>昨日の速報　{escape(data["label"])}</h2>']
    parts.append('<div class="row r2">' + _sales_card("店舗の売上", data["store"]) + _sales_card("催事の売上", data["event"], True) + "</div>")

    labor_sub = f'店舗 {round(labor["store"]):,} + 催事 {round(labor["event"]):,}'
    if labor["storeMissing"]:
        labor_sub += "（店舗の打刻は取得待ち）"
    if prod["state"] == "ok":
        prod_card = (f'<div class="card"><div class="lab">製造実績</div><div class="num sm">{_yen(prod["value"])}</div>'
                     f'<div class="sub">作った数 × 売価（{prod["pieces"]:,}個）</div></div>')
        cls = "ok" if rate["verdict"] == "ok" else "over"
        verdict = "目標内" if rate["verdict"] == "ok" else "目標超過"
        rate_card = (f'<div class="card"><div class="lab">人件費率</div><div class="num sm {cls}">{rate["value"]:.1f}%</div>'
                     f'<div class="sub">目標 {rate["target"]:.0f}% / {verdict}（上限の目安より {_signed(rate["gap"])}）</div></div>')
    else:
        wording = "製造表を読み取れませんでした" if prod["state"] == "unreadable" else "入力待ち"
        prod_card = (f'<div class="card"><div class="lab">製造実績</div><div class="num sm mute">{wording}</div>'
                     f'<div class="sub">作った数 × 売価</div></div>')
        rate_card = (f'<div class="card"><div class="lab">人件費率</div><div class="num sm mute">未計算</div>'
                     f'<div class="sub">目標 {rate["target"]:.0f}%（製造実績が入ると計算します）</div></div>')
    parts.append('<div class="row r3">'
                 f'<div class="card"><div class="lab">人件費</div><div class="num sm">{_yen(labor["total"])}</div><div class="sub">{labor_sub}</div></div>'
                 + prod_card + rate_card + "</div>")

    if month:
        diff_class = "ok" if month["diff"] is not None and month["diff"] >= 0 else "over"
        diff = f'予算累計との差 <b class="{diff_class}">{_signed(month["diff"]) if month["diff"] is not None else "—"}</b>'
        rate_text = "—" if month["rate"] is None else (
            f'{month["rate"]:.1f}%（目標{month["rateTarget"]:.0f}%' + ("超過）" if month["rate"] > month["rateTarget"] else "内）"))
        calm = month["diff"] is not None and month["diff"] >= 0 and (month["rate"] is None or month["rate"] <= month["rateTarget"])
        parts.append(
            f'<div class="strip{" calm" if calm else ""}"><span class="k">{escape(month["label"])}</span>'
            f'<span>売上 <b>{_yen(month["sales"])}</b></span><span>{diff}</span><span>人件費率 {rate_text}</span></div>')

    parts.append(_mini_charts(data))

    if prod["state"] == "ok" and prod["blocks"]:
        names = [b["name"] for b in prod["blocks"]]
        color = {name: BLOCK_COLORS[i % len(BLOCK_COLORS)] for i, name in enumerate(names)}
        parts.append('<h3>製造実績の内訳</h3><div class="stack">'
                     + "".join(f'<i style="width:{b["share"]:.2f}%;background:{color[b["name"]]}"></i>' for b in prod["blocks"])
                     + '</div><div class="legend">'
                     + "".join(f'<span><b style="background:{color[b["name"]]}"></b>{escape(b["name"])}　{b["share"]:.0f}%</span>' for b in prod["blocks"])
                     + "</div>")
    parts.append("</div>")
    return "".join(parts)


def render_page(data, title="どら山 昨日の速報"):
    """単体で開ける（PDFにもできる）A4ページ。業界ウォッチと同じ地色・左の濃緑の帯。"""
    return f"""<!DOCTYPE html><html lang="ja"><head><meta charset="utf-8"><title>{escape(title)}</title><style>
@page{{size:A4;margin:0}}
body{{margin:0;background:#f5f1ea}}
.page{{width:210mm;min-height:297mm;box-sizing:border-box;padding:15mm 16mm 12mm 18mm;position:relative;background:#f5f1ea}}
.page::before{{content:"";position:absolute;left:0;top:0;bottom:0;width:3mm;background:{GREEN}}}
.top{{display:flex;justify-content:space-between;align-items:baseline;margin-bottom:14px}}
.top h1{{margin:0;font-size:22pt;font-weight:500;letter-spacing:.05em}}.top span{{font-size:9pt;color:#8a7f72}}
{STYLE}
</style></head><body><div class="page"><div class="top"><h1>どら山 昨日の速報</h1><span>アプリ「今日の速報」の抜粋</span></div>
{render_block(data)}
</div></body></html>"""
