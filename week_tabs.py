#!/usr/bin/env python3
"""週タブ（MMDD）の名前から「その週の月曜日（年つき）」を決める。

週タブの名前には年が無い。去年の同じ時期のタブ（例: 2026年の 0105 と 2027年の 0104）が
同じブックに並ぶので、「今日に近い年を当てる」やり方だと、半年以上先の週を去年と取り違える。
ここではタブの並び順（古い順）を使う。今週のタブを基準に、右へ進むたびに日付が後ろへ、
左へ戻るたびに日付が前へ進むように年を割り当てる。
"""
import datetime
import re

_WEEK = re.compile(r"^\d{3,4}$")


def is_week_tab(title):
    return bool(_WEEK.match(str(title).strip()))


def _md_options(title):
    """'1214'→[(12,14)]、'915'→[(9,15)]、'106'→[(10,6),(1,6)] のように月日の候補を返す。"""
    t = str(title).strip()
    opts = []
    if len(t) == 4:
        opts.append((int(t[:2]), int(t[2:])))
    elif len(t) == 3:
        opts.append((int(t[:1]), int(t[1:])))
        opts.append((int(t[:2]), int(t[2:])))
    elif len(t) == 2:
        opts.append((int(t[:1]), int(t[1:])))
    return [(m, d) for m, d in opts if 1 <= m <= 12 and 1 <= d <= 31]


def _mk(y, m, d):
    try:
        return datetime.date(y, m, d)
    except ValueError:
        return None


def nearest(title, today):
    """並び順が使えないときの予備: 今日にいちばん近い年を当てる。"""
    cands = [x for m, d in _md_options(title) for y in (today.year - 1, today.year, today.year + 1)
             for x in [_mk(y, m, d)] if x]
    return min(cands, key=lambda x: abs((x - today).days)) if cands else None


def assign_dates(titles, today=None):
    """タブ名の一覧（ブックの並び順）→ {タブ名: 月曜日の日付}。週タブ以外は含めない。"""
    today = today or datetime.date.today()
    week = [t for t in titles if is_week_tab(t)]
    if not week:
        return {}
    monday = today - datetime.timedelta(days=today.weekday())
    names = {f"{monday.month:02d}{monday.day:02d}", f"{monday.month}{monday.day:02d}"}
    anchor = next((i for i, t in enumerate(week) if t in names), None)
    if anchor is not None:
        base = monday
    else:
        # 今週のタブが無いとき: いちばん新しい週タブを基準にする。週タブは月曜始まりなので、
        # 「その月日が月曜日になる年」を選べば年が決まる（同じ月日が月曜になるのは5〜6年に一度）。
        anchor = len(week) - 1
        t = week[anchor]
        mondays = [x for m, d in _md_options(t) for y in range(today.year - 2, today.year + 3)
                   for x in [_mk(y, m, d)] if x and x.weekday() == 0]
        base = min(mondays, key=lambda x: abs((x - today).days)) if mondays else nearest(t, today)
        if base is None:
            return {}
    out = {week[anchor]: base}
    prev = base
    for t in week[anchor + 1:]:                 # 右へ: 直前より後ろで、いちばん近い日付
        cands = [x for m, d in _md_options(t) for y in (prev.year, prev.year + 1)
                 for x in [_mk(y, m, d)] if x and x > prev]
        if not cands:
            continue
        prev = min(cands)
        out[t] = prev
    nxt = base
    for t in reversed(week[:anchor]):           # 左へ: 直後より前で、いちばん近い日付
        cands = [x for m, d in _md_options(t) for y in (nxt.year, nxt.year - 1)
                 for x in [_mk(y, m, d)] if x and x < nxt]
        if not cands:
            continue
        nxt = max(cands)
        out[t] = nxt
    return out


def name_for(date):
    """日付 → その週のタブ名（MMDD）。"""
    monday = date - datetime.timedelta(days=date.weekday())
    return f"{monday.month:02d}{monday.day:02d}"
