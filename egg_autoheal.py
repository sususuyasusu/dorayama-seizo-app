#!/usr/bin/env python3
"""卵発注シートの自己修復（アプリ内蔵・1日1回）。

egg_normalize の正規化を当週＋未来週へ自動適用し、次の事故を未然に防ぐ:
 A) 見通しの起点を当日の実在庫に      B) 必要在庫を製造ベースの理想値に
 C) 見通し・発注チェックを実績ベースに  D) 翌週正味発注数を正しい参照で組み直す

Dが特に重要。過去に「翌々週タブが未作成の時点で発注ブロックを作り、
土便の翌々週分が 0 のまま6週間コピー継承されて過少発注表示」という事故があった。
毎日組み直すので、後からタブが増えれば自動的に埋まる。

状態は /api/eggheal で確認できる。
"""
import threading
import time
import datetime

INTERVAL_SEC = 24 * 60 * 60      # 1日1回
FIRST_DELAY_SEC = 120            # 起動直後の混雑を避けて2分後に初回

status = {"lastRun": None, "lastResult": None}


def _jst_now():
    return datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9)))


def _retry(fn, tries=5, wait=70):
    """Sheets APIの分間上限(429)に当たったら待って再試行する。"""
    for i in range(tries):
        try:
            return fn()
        except Exception as e:
            if "429" in str(e) and i < tries - 1:
                time.sleep(wait)
                continue
            raise


def run_once(max_weeks=6):
    """当週＋未来週(最大max_weeks件)を正規化。結果の要約文字列を返す。
    Sheets APIは読み取り60回/分の上限があるため、週ごとに間隔を空け429はリトライする。"""
    import egg_normalize
    import data_layer

    sh = _retry(lambda: data_layer.open_ws(None).spreadsheet)
    sheets = _retry(lambda: sh.worksheets())
    tabset = {w.title for w in sheets}
    today = _jst_now().date()
    monday = today - datetime.timedelta(days=today.weekday())

    # 対象週をタブ名(MMDD)から判定＝全シート読み込み(重い)を避ける
    # 週タブは古い順に並んでいる。タブ名に年が無いため、当週タブより前にあるタブは
    # 必ず過去週として除外する（去年の同月同日タブを未来週と誤認して書き込むのを防ぐ）
    cur_idx = None
    for i, w in enumerate(sheets):
        if w.title == monday.strftime("%m%d") or w.title == "%d%02d" % (monday.month, monday.day):
            cur_idx = i
            break
    targets = []
    for i, w in enumerate(sheets):
        if cur_idx is not None and i < cur_idx:
            continue
        t = w.title
        if len(t) != 4 or not t.isdigit():
            continue
        mm, dd = int(t[:2]), int(t[2:])
        cands = []
        for y in (today.year - 1, today.year, today.year + 1):
            try:
                cands.append(datetime.date(y, mm, dd))
            except ValueError:
                continue
        if not cands:
            continue
        d = min(cands, key=lambda x: abs((x - today).days))
        if d >= monday:                     # 過去週は触らない（履歴を変えない）
            targets.append((d, w))
    targets.sort(key=lambda x: x[0])

    lines = []
    for _, w in targets[:max_weeks]:
        try:
            lines.append(_retry(lambda w=w: egg_normalize.normalize(w.title, w, tabset)))
        except Exception as e:
            lines.append(f"[{w.title}] error: {type(e).__name__}: {str(e)[:120]}")
        time.sleep(12)                      # 分間上限に対する安全マージン
    return " / ".join(lines) if lines else "対象週なし"


def _loop():
    time.sleep(FIRST_DELAY_SEC)
    while True:
        try:
            res = run_once()
        except Exception as e:
            res = f"error: {type(e).__name__}: {str(e)[:200]}"
        status["lastRun"] = _jst_now().strftime("%Y-%m-%d %H:%M:%S")
        status["lastResult"] = res
        time.sleep(INTERVAL_SEC)


def start():
    threading.Thread(target=_loop, daemon=True, name="egg-autoheal").start()


if __name__ == "__main__":
    print(run_once())
