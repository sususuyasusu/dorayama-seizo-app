#!/usr/bin/env python3
"""製造数予測の計算本体（標準ライブラリのみ・シートにも通信にも触れない純粋な計算）。

考え方（掛け算モデル）:
    その日の販売数 ＝ 基準 × 曜日 × 祝日・連休 × 月 × 天気 × 特別日 × 直近の勢い
  各要因の倍率を、過去の実績から「縮小つき回帰」で推定する。データが少ない要因ほど
  倍率を1倍（＝影響なし）へ寄せるので、数回しか起きていない出来事に振り回されない。

  - 新しいデータほど重く見る（半減期 HALF_LIFE_DAYS 日）
  - 入力ミスなど飛び抜けた日は自動で軽く扱う（Huberの重み）
  - 夜の在庫がほぼ0の日は「売り切れ＝本当はもっと売れた」とみなし、実績を下限として扱う

納品数は 2026-09-10 に決めたルールをそのまま使う:
    夜の在庫の目安 ＝ 翌日の販売見込み ×（朝の販売割合 ＋ 余裕）   余裕: 焼き3割・生1割5分
    16時の納品数   ＝ 夜の在庫の目安 ＋ 当日の販売見込み − 前夜の在庫
"""
import math
import re
from datetime import date, datetime, timedelta

import forecast_calendar as cal

PRODUCTS = ["黒どら", "白どら", "あんバター", "旬どら", "皮だけ", "生どら"]
NAMA = {"生どら"}                         # 冷蔵品（売れる期間＝納品日と翌日）
EXTRA_TARGETS = ["売上", "客数"]          # 個数のほかに予測する項目
# 日報フォームの列（0始まり）。(販売数, 夜の在庫)
FORM_PRODUCT_COLS = {"黒どら": (5, 6), "白どら": (7, 8), "あんバター": (9, 10),
                     "旬どら": (11, 12), "皮だけ": (13, 14), "生どら": (15, 16)}
FORM_COL = {"ts": 0, "date": 1, "venue": 2, "salesExcl": 3, "customers": 4,
            "comment": 17, "salesIncl": 18, "dateManual": 19}
# 個数×単価で売上と突き合わせるための税抜単価（2026-07 会場POSの実績から）
UNIT_PRICE_EXCL = {"黒どら": 277, "白どら": 296, "あんバター": 380, "旬どら": 416, "皮だけ": 399, "生どら": 445}

MIN_TRAIN_DAYS = 21
HUBER_C = 2.0
SOLDOUT_RATIO = 0.10        # 夜の在庫が販売数の1割以下（または3個以下）なら売り切れ扱い
# 調整できる設定（過去検証で決めた値。変えるときは検証し直す）
OPT = {
    "halfLife": 180.0,          # 新しいデータほど重く見る。重みが半分になる日数
    "lambdas": [2.0, 4.0, 8.0],  # 縮小の強さの候補（1件抜き検証で選ぶ）
    "levelHalfLife": 7.0,       # 直近の勢いを見る半減期（日）
    "levelWindow": 21,
    "levelShrink": 0.6,         # 直近の勢いをどこまで信じるか（0=無視, 1=全部）
    "levelNormalOnly": True,    # 勢いは「ふつうの日」だけで測る（連休・特別日の上振れを平日に持ち込まない）
    "useTemp": True,            # 気温（連続）を要因に入れる
    "postBig": True,            # 連休明けの落ち込みを要因に入れる
}

VENUE_ALIASES = {"阪神梅田本店": "阪神梅田", "上野": "エキュート上野"}
TEST_WORDS = ("テスト", "確認用", "test", "dummy")

FLAVOR_ALIASES = [
    ("シャインマスカット", "マスカット"), ("マスカット", "マスカット"),
    ("白桃", "もも"), ("もも", "もも"), ("モモ", "もも"), ("桃", "もも"),
    ("和栗", "和栗"), ("栗", "和栗"), ("めっちゃ抹茶", "抹茶"), ("抹茶", "抹茶"),
    ("瀬戸内レモン", "レモン"), ("レモン", "レモン"), ("紅はるか", "紅はるか"), ("芋", "紅はるか"),
    ("バナナ", "バナナ"), ("マンゴー", "マンゴー"), ("メロン", "メロン"), ("パイン", "パイン"),
    ("いちご", "いちご"), ("苺", "いちご"), ("いちじく", "いちじく"),
]


# ───────────────────────── 日報の読み取りと掃除 ─────────────────────────

_Z2H = str.maketrans("０１２３４５６７８９．，－", "0123456789.,-")


def to_number(s):
    """数字だけのセルを数値に。空や文字は None。"""
    if isinstance(s, bool):
        return None
    if isinstance(s, (int, float)):
        return int(s) if s == int(s) else s
    s = str(s or "").translate(_Z2H).replace(",", "").replace("¥", "").replace("円", "").strip()
    if not s:
        return None
    if re.match(r"^\d{1,3}(\.\d{3})+$", s):     # 「212.951」＝桁区切りをピリオドで打った金額
        s = s.replace(".", "")
    try:
        v = float(s)
    except ValueError:
        return None
    return int(v) if v == int(v) else v


def flavor_name(raw):
    raw = raw.strip()
    for key, name in FLAVOR_ALIASES:
        if key in raw:
            return name
    return raw


def parse_qty_cell(s):
    """販売数・在庫のセルを読む。返り値: (合計 or None, 味別の内訳dict)
    「抹茶→87　和栗→21」「バナナ16  マンゴー27」のような味別の書き方も受け付ける。"""
    text = str(s or "").translate(_Z2H).strip()
    if not text:
        return None, {}
    n = to_number(text)
    if n is not None:
        return (n if n >= 0 else None), {}
    pairs = re.findall(r"([^\d\s→:：・,、/／\-]+)\s*[→:：]?\s*(\d+)", text)
    flavors = {}
    for name, num in pairs:
        key = flavor_name(name)
        flavors[key] = flavors.get(key, 0) + int(num)
    if flavors:
        return sum(flavors.values()), flavors
    nums = re.findall(r"\d+", text)          # 「→3」など、数字が1つだけならそれを採用
    if len(nums) == 1:
        return int(nums[0]), {}
    return None, {}


def parse_date(s, fallback_year=None):
    s = str(s or "").translate(_Z2H).strip()
    if not s:
        return None
    m = re.match(r"^(\d{4})[/\-.年](\d{1,2})[/\-.月](\d{1,2})", s)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    m = re.match(r"^(\d{1,2})[/\-.月](\d{1,2})", s)
    if m and fallback_year:
        try:
            return date(fallback_year, int(m.group(1)), int(m.group(2)))
        except ValueError:
            return None
    return None


def parse_timestamp(s):
    s = str(s or "").strip()
    for fmt in ("%Y/%m/%d %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%m/%d/%Y %H:%M:%S"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def normalize_venue(name):
    name = str(name or "").strip()
    return VENUE_ALIASES.get(name, name)


def is_test_venue(name):
    low = str(name or "").lower()
    return any(w in low for w in TEST_WORDS)


def clean_form_rows(rows):
    """日報フォームの全行 → 会場×日付ごとの1日1件の記録（同じ日の再提出は後勝ち）。
    返り値: (記録のリスト, 読めなかった行の説明リスト)"""
    out, skipped = {}, []
    for i, r in enumerate(rows[1:], start=2):
        r = list(r) + [""] * (20 - len(r))
        venue_raw = r[FORM_COL["venue"]]
        if not str(venue_raw).strip():
            continue
        if is_test_venue(venue_raw):
            continue
        ts = parse_timestamp(r[FORM_COL["ts"]])
        year = ts.year if ts else None
        d = parse_date(r[FORM_COL["date"]], year) or parse_date(r[FORM_COL["dateManual"]], year)
        if d is None:
            skipped.append(f"{i}行目: 日付が読めません（{r[FORM_COL['date']]!r} / {r[FORM_COL['dateManual']]!r}）")
            continue
        if ts and (d - ts.date()).days > 1:
            skipped.append(f"{i}行目: 日付 {d} が提出日 {ts.date()} より先です（入力ミスの疑い）")
            continue
        rec = {
            "date": d, "venue": normalize_venue(venue_raw),
            "salesExcl": to_number(r[FORM_COL["salesExcl"]]),
            "salesIncl": to_number(r[FORM_COL["salesIncl"]]),
            "customers": to_number(r[FORM_COL["customers"]]),
            "qty": {}, "stock": {}, "flavors": {}, "stockFlavors": {},
            "comment": str(r[FORM_COL["comment"]] or "").strip(),
            "source": "日報フォーム", "submittedAt": ts.isoformat(sep=" ") if ts else "",
            "formRow": i,
        }
        for p, (cq, cs) in FORM_PRODUCT_COLS.items():
            q, fl = parse_qty_cell(r[cq])
            st, sfl = parse_qty_cell(r[cs])
            rec["qty"][p] = q
            rec["stock"][p] = st
            if fl:
                rec["flavors"][p] = fl
            if sfl:
                rec["stockFlavors"][p] = sfl
        if not rec["salesExcl"] and not rec["salesIncl"] and not any(rec["qty"].values()):
            continue                          # 売上も個数も空の行は記録にしない
        out[(rec["venue"], d)] = rec          # 同じ日の再提出は後勝ち
    return sorted(out.values(), key=lambda x: (x["venue"], x["date"])), skipped


def quality_flags(rec, ratio_ref=None):
    """その日の記録の怪しい点を日本語で返す。"""
    flags = []
    c = rec.get("customers")
    sales = rec.get("salesIncl") or rec.get("salesExcl")
    if c and sales and (sales / c > 3500 or sales / c < 400):
        flags.append(f"客数{c:,}人は客単価が不自然（入力ミスの疑い）")
    ratio = qty_sales_ratio(rec)
    if ratio is not None:
        ref = ratio_ref or 1.0
        if abs(ratio / ref - 1.0) > 0.12:
            flags.append(f"個数×単価が売上と{(ratio / ref - 1.0) * 100:+.0f}%ずれ")
    missing = [p for p in PRODUCTS if rec["qty"].get(p) is None]
    if missing:
        flags.append("販売数が未記入: " + "・".join(missing))
    return flags


def qty_sales_ratio(rec):
    """（販売数×単価の合計）÷ 税抜売上。個数が全部そろう日だけ計算。"""
    if not rec.get("salesExcl"):
        return None
    total = 0
    for p in PRODUCTS:
        q = rec["qty"].get(p)
        if q is None:
            return None
        total += q * UNIT_PRICE_EXCL[p]
    return total / rec["salesExcl"] if rec["salesExcl"] else None


def is_sold_out(qty, stock):
    if qty is None or stock is None or qty <= 0:
        return False
    return stock <= max(3, SOLDOUT_RATIO * qty)


# ───────────────────────── 要因（説明変数）づくり ─────────────────────────

GROUP_LABEL = {"dow": "曜日", "cal": "祝日・連休", "month": "月", "wx": "天気", "event": "特別日", "level": "直近の勢い"}
PENALTY = {"dow": 0.25, "cal": 1.0, "month": 0.25, "wx": 1.0, "event": 0.5}
MONTH_SMOOTH = 2.0          # となり合う月どうしを近づける強さ（月ごとの水準をなめらかにつなぐ）
PERIOD_KEY = {"お盆": "p_obon", "ゴールデンウィーク": "p_gw", "年末年始": "p_nenmatsu", "大型連休": "p_renkyu"}
SPECIAL_KEYS = ("hol_wd", "pre_hol", "long_run", "big", "post_big", "event")   # これが付く日は「ふつうの日」ではない
FEATURE_LABEL = {
    "post_big": "連休明け（3日間）",
    "hol_wd": "祝日（平日にあたる日）", "pre_hol": "連休・祝日の前日", "long_run": "3連休以上の中の日",
    "long_first": "連休の初日", "long_last": "連休の最終日", "big": "お盆・大型連休・年末年始",
    "p_obon": "お盆", "p_gw": "ゴールデンウィーク", "p_nenmatsu": "年末年始", "p_renkyu": "大型連休（4連休以上）",
    "vac": "学校の長期休み", "rain": "雨量", "rain_heavy": "大雨（10mm以上）",
    "hot": "暑さ（30℃超）", "cool": "涼しさ（20℃未満）", "temp": "気温", "event": "特別日（催し等）",
}
CONTINUOUS = ("rain", "hot", "cool", "temp")


def group_of(name):
    if name.startswith("dow"):
        return "dow"
    if name.startswith("m") and name[1:].isdigit():
        return "month"
    if name in ("rain", "rain_heavy", "hot", "cool", "temp"):
        return "wx"
    if name == "event":
        return "event"
    return "cal"


def weather_features(wx):
    """天気 → 要因の値。天気が無い日は空（＝平年並みとして扱われる）。"""
    if not wx or wx.get("tmax") is None:
        return {}
    rain = wx.get("rain") or 0.0
    tmax = wx["tmax"]
    f = {"rain": math.log1p(max(0.0, rain)),
         "hot": max(0.0, tmax - 30.0) / 5.0, "cool": max(0.0, 20.0 - tmax) / 5.0}
    if OPT["useTemp"]:
        f["temp"] = (tmax - 25.0) / 10.0
    if rain >= 10:
        f["rain_heavy"] = 1.0
    return f


def _is_holiday_block(d):
    """連休（3連休以上）か、お盆などの大型連休の期間に入っている日か。"""
    return bool(cal.period_label(d)) or cal.off_run(d)[0] >= 3


def calendar_features(d):
    c = cal.describe(d)
    f = {"dow%d" % c["wd"]: 1.0, "m%d" % c["month"]: 1.0}
    if c["weekdayHoliday"]:
        f["hol_wd"] = 1.0
    if c["longRun"]:
        f["long_run"] = 1.0
        if c["offRunPos"] == 1:
            f["long_first"] = 1.0
        if c["offRunPos"] == c["offRunLen"]:
            f["long_last"] = 1.0
    block = _is_holiday_block(d)
    if not block:
        t = d + timedelta(days=1)
        if _is_holiday_block(t) or (not c["isOff"] and cal.holiday_name(t) and t.weekday() < 5):
            f["pre_hol"] = 1.0               # 連休・お盆・祝日の前日
        elif OPT["postBig"]:
            for back in (1, 2, 3):           # 連休明けの3日間（だんだん戻る）
                if _is_holiday_block(d - timedelta(days=back)):
                    f["post_big"] = (4 - back) / 3.0
                    break
    if c["period"]:
        f["big"] = 1.0
        f[PERIOD_KEY[c["period"]]] = 1.0
    if c["vacation"]:
        f["vac"] = 1.0
    return f


def is_normal_day(feats):
    return not any(k in feats for k in SPECIAL_KEYS)


def features_for(d, wx, events):
    """その日の全要因。events は {date: [{name, kind, factor}]}。"""
    f = calendar_features(d)
    f.update(weather_features(wx))
    if any(e.get("kind") != "除外" for e in (events or {}).get(d, [])):
        f["event"] = 1.0
    return f


# ───────────────────────── 線形代数（小さな行列用） ─────────────────────────

def _cholesky(a):
    n = len(a)
    low = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1):
            s = a[i][j]
            li, lj = low[i], low[j]
            for k in range(j):
                s -= li[k] * lj[k]
            if i == j:
                if s <= 1e-12:
                    raise ArithmeticError("行列が解けません（要因が重複している可能性）")
                li[j] = math.sqrt(s)
            else:
                li[j] = s / lj[j]
    return low


def _inverse_spd(a):
    """対称正定値行列の逆行列。"""
    n = len(a)
    low = _cholesky(a)
    inv_l = [[0.0] * n for _ in range(n)]      # L の逆行列（下三角）
    for i in range(n):
        inv_l[i][i] = 1.0 / low[i][i]
        for j in range(i):
            s = 0.0
            for k in range(j, i):
                s -= low[i][k] * inv_l[k][j]
            inv_l[i][j] = s / low[i][i]
    inv = [[0.0] * n for _ in range(n)]        # A^-1 = L^-T L^-1
    for i in range(n):
        for j in range(i + 1):
            s = 0.0
            for k in range(i, n):
                s += inv_l[k][i] * inv_l[k][j]
            inv[i][j] = s
            inv[j][i] = s
    return inv


def _median(xs):
    xs = sorted(xs)
    n = len(xs)
    if n == 0:
        return 0.0
    return xs[n // 2] if n % 2 else 0.5 * (xs[n // 2 - 1] + xs[n // 2])


# ───────────────────────── 学習 ─────────────────────────

class Design:
    """学習データを、計算しやすい形（疎な行の並び）にしたもの。"""

    def __init__(self, days, as_of):
        """days: [{date, feats(dict)}]。as_of: 重みの基準日（この日に近いほど重い）。"""
        self.days = days
        counts = {}
        for d in days:
            for k in d["feats"]:
                counts[k] = counts.get(k, 0) + 1
        n = len(days)
        names = []
        self.scale = {}
        self.range = {}       # 連続の要因が学習データで取った範囲（範囲の外へは延長しない）
        for k, c in counts.items():
            if k in CONTINUOUS:
                # 連続の要因（雨量・気温）は、天気がある日の平均とばらつきで標準化する
                vals = [d["feats"][k] for d in days if k in d["feats"]]
                mu = sum(vals) / len(vals)
                sd = math.sqrt(sum((v - mu) ** 2 for v in vals) / len(vals))
                if len(vals) >= 5 and sd > 1e-6:
                    self.scale[k] = (mu, sd)
                    self.range[k] = (min(vals), max(vals))
                    names.append(k)
            elif k.startswith("dow"):
                names.append(k)
            elif group_of(k) == "month":
                continue                     # 月は下で12か月ぶんまとめて入れる
            elif 2 <= c < n:
                # 学習データの中で1回しか起きていない／毎日起きている要因は推定できないので外す
                names.append(k)
        # 月は12か月すべてを要因に入れ、「となりの月とは似ている」という前提でつなぐ。
        # こうすると、まだ実績の無い月（例: 10月）は、近い月（9月）の水準から自然に引き継がれる。
        self.months_seen = sorted({d["date"].month for d in days})
        names += ["m%d" % m for m in range(1, 13)]
        self.names = sorted(names)
        self.index = {k: i + 1 for i, k in enumerate(self.names)}      # 0番は基準（切片）
        self.p = len(self.names) + 1
        self.base_w = [0.5 ** (max(0, (as_of - d["date"]).days) / OPT["halfLife"]) for d in days]
        self.rows = [self.row(d["feats"], has_weather=("rain" in d["feats"])) for d in days]

    def row(self, feats, has_weather=True):
        """要因dict → 疎な行 [(列番号, 値)]。天気が無い日は天気の列を0（＝平均的な天気）にする。
        雨量・気温は、学習データで経験した範囲の外へは延長しない（真冬の気温などで暴れないように）。"""
        r = [(0, 1.0)]
        for k, v in feats.items():
            j = self.index.get(k)
            if j is None:
                continue
            if k in self.scale:
                lo, hi = self.range[k]
                mu, sd = self.scale[k]
                v = (min(max(v, lo), hi) - mu) / sd
            r.append((j, v))
        if has_weather:
            for k, (mu, sd) in self.scale.items():     # 値が無い連続要因は0として標準化
                if k not in feats:
                    r.append((self.index[k], (0.0 - mu) / sd))
        return r

    def penalty(self, lam):
        """縮小の強さ。返り値: (各要因を0へ寄せる強さ, [(要因i, 要因j, 2つを近づける強さ)])"""
        pen = [1e-8] * self.p
        for k, j in self.index.items():
            pen[j] = lam * PENALTY[group_of(k)]
        links = []
        for m in range(1, 13):               # となり合う月（12月と1月も）を近づける
            a, b = self.index["m%d" % m], self.index["m%d" % (m % 12 + 1)]
            links.append((a, b, lam * MONTH_SMOOTH))
        return pen, links


def _wls(design, y, w, lam, mask):
    """重み付き・縮小つき最小二乗。mask[i]=False の行は使わない。返り値: (係数, 逆行列)"""
    p = design.p
    a = [[0.0] * p for _ in range(p)]
    b = [0.0] * p
    for i, row in enumerate(design.rows):
        if not mask[i]:
            continue
        wi = w[i]
        yi = y[i]
        for j, vj in row:
            wv = wi * vj
            b[j] += wv * yi
            aj = a[j]
            for k, vk in row:
                aj[k] += wv * vk
    pen, links = design.penalty(lam)
    for j in range(p):
        a[j][j] += pen[j]
    for i, j, k in links:
        a[i][i] += k
        a[j][j] += k
        a[i][j] -= k
        a[j][i] -= k
    inv = _inverse_spd(a)
    beta = [sum(inv[j][k] * b[k] for k in range(p)) for j in range(p)]
    return beta, inv


def _dot(row, beta):
    return sum(v * beta[j] for j, v in row)


def _leverage(row, inv, wi):
    s = 0.0
    for j, vj in row:
        ij = inv[j]
        for k, vk in row:
            s += vj * ij[k] * vk
    return min(0.95, wi * s)


def fit_target(design, values, soldout, lam=None, robust=True):
    """1つの項目（商品など）を学習する。
    values: 各日の実績（None＝その日は使わない）。soldout: 売り切れ扱いの日か。"""
    n = len(design.rows)
    mask = [v is not None and v > 0 for v in values]
    used = sum(mask)
    if used < MIN_TRAIN_DAYS:
        return None
    y = [math.log(v) if m else 0.0 for v, m in zip(values, mask)]
    w0 = design.base_w

    def loo_error(lm):
        beta, inv = _wls(design, y, w0, lm, mask)
        num = den = 0.0
        for i, row in enumerate(design.rows):
            if not mask[i]:
                continue
            e = y[i] - _dot(row, beta)
            h = _leverage(row, inv, w0[i])
            num += w0[i] * abs(e / (1.0 - h))
            den += w0[i]
        return num / den

    if lam is None:
        lam = min(OPT["lambdas"], key=loo_error)

    w = list(w0)
    y_adj = list(y)
    beta, inv = _wls(design, y_adj, w, lam, mask)
    if robust:
        for _ in range(4):
            res = [y_adj[i] - _dot(design.rows[i], beta) if mask[i] else 0.0 for i in range(n)]
            scale = 1.4826 * _median([abs(r) for r, m in zip(res, mask) if m]) or 1e-6
            for i in range(n):
                if not mask[i]:
                    continue
                pred = _dot(design.rows[i], beta)
                # 売り切れの日は、実績より多く売れたはず → 予測のほうが大きければ予測値を採用
                y_adj[i] = max(y[i], pred) if soldout[i] else y[i]
                r = abs(y_adj[i] - pred)
                w[i] = w0[i] * (1.0 if r <= HUBER_C * scale else HUBER_C * scale / r)
            beta, inv = _wls(design, y_adj, w, lam, mask)

    # ばらつき（1件抜き残差から。学習に使った日そのものより外の日の当たり具合に近い）
    num = den = 0.0
    residuals = [None] * n
    for i, row in enumerate(design.rows):
        if not mask[i]:
            continue
        e = y_adj[i] - _dot(row, beta)
        residuals[i] = e
        h = _leverage(row, inv, w[i])
        num += w[i] * (e / (1.0 - h)) ** 2
        den += w[i]
    sigma = math.sqrt(num / den) if den else 0.3

    # 直近の勢い（モデルで説明しきれない最近の上振れ・下振れ）
    last = max(d["date"] for d, m in zip(design.days, mask) if m)
    ln = ld = 0.0
    for i, d in enumerate(design.days):
        if residuals[i] is None:
            continue
        if OPT["levelNormalOnly"] and not is_normal_day(d["feats"]):
            continue
        age = (last - d["date"]).days
        if age > OPT["levelWindow"]:
            continue
        wt = 0.5 ** (age / OPT["levelHalfLife"])
        ln += wt * residuals[i]
        ld += wt
    level = OPT["levelShrink"] * (ln / ld) * (ld / (ld + 2.0)) if ld else 0.0

    return {"beta": beta, "lambda": lam, "sigma": sigma, "level": level, "n": used,
            "lastDate": last, "soldoutDays": sum(1 for s, m in zip(soldout, mask) if s and m)}


def predict(design, model, feats, has_weather=True, use_level=True, event_factor=None):
    """1日の予測。返り値: 中央値・幅（2割〜8割の範囲）と、要因ごとの倍率。"""
    row = design.row(feats, has_weather=has_weather)
    beta = model["beta"]
    groups = {}
    for j, v in row:
        if j == 0:
            continue
        g = group_of(design.names[j - 1])
        groups[g] = groups.get(g, 0.0) + v * beta[j]
    if event_factor:                       # 特別日に手で倍率が入っていれば、それを優先
        groups["event"] = math.log(event_factor)
    eta = beta[0] + sum(groups.values())
    if use_level:
        eta += model["level"]
        groups["level"] = model["level"]
    s = model["sigma"]
    return {
        "p50": math.exp(eta), "mean": math.exp(eta + 0.5 * s * s),
        "lo": math.exp(eta - 0.8416 * s), "hi": math.exp(eta + 0.8416 * s),
        "base": math.exp(beta[0]),
        "factors": {g: math.exp(v) for g, v in groups.items()},
    }


# ───────────────────────── まとめて学習・予測 ─────────────────────────

TOTAL = "合計個数"          # 全商品の合計（商品の比率で割る方式に使う内部項目）
ALL_TARGETS = PRODUCTS + [TOTAL] + EXTRA_TARGETS
BLEND = 0.5                 # 「商品ごとの予測」と「全体×直近の比率」を半々で混ぜる（過去検証で誤差が最小）
SHARE_HALF_LIFE = 10.0
SHARE_WINDOW = 28
Z80 = 0.8416                # 幅（2割〜8割）の係数


def target_value(rec, target):
    if target == "売上":
        return rec.get("salesIncl") or (round(rec["salesExcl"] * 1.08) if rec.get("salesExcl") else None)
    if target == "客数":
        c = rec.get("customers")
        sales = rec.get("salesIncl") or rec.get("salesExcl")
        if c and sales and (sales / c > 3500 or sales / c < 400):
            return None                     # 明らかな入力ミスは学習に使わない
        return c
    if target == TOTAL:
        vals = [rec["qty"].get(p) for p in PRODUCTS]
        return sum(vals) if all(v is not None for v in vals) else None
    return rec["qty"].get(target)


def recent_shares(records, as_of):
    """直近4週間の「商品の比率」（新しい日ほど重い）。"""
    num = {p: 0.0 for p in PRODUCTS}
    den = 0.0
    for r in records:
        age = (as_of - r["date"]).days
        if age < 0 or age > SHARE_WINDOW:
            continue
        vals = [r["qty"].get(p) for p in PRODUCTS]
        if any(v is None for v in vals) or sum(vals) <= 0:
            continue
        w = 0.5 ** (age / SHARE_HALF_LIFE)
        tot = float(sum(vals))
        for p, v in zip(PRODUCTS, vals):
            num[p] += w * v / tot
        den += w
    return {p: num[p] / den for p in PRODUCTS} if den else None


def train(records, weather, events, as_of=None, targets=None, lambdas=None, robust=True):
    """1会場ぶんの記録から全項目を学習。
    records: 記録のリスト / weather: {date: 天気} / events: {date: [特別日]}"""
    targets = targets or ALL_TARGETS
    usable = sorted((r for r in records
                     if not any(e.get("kind") == "除外" for e in (events or {}).get(r["date"], []))),
                    key=lambda r: r["date"])
    if not usable:
        return None
    last = usable[-1]["date"]
    as_of = as_of or last
    days = [{"date": r["date"], "feats": features_for(r["date"], weather.get(r["date"]), events)}
            for r in usable]
    design = Design(days, as_of)
    models = {}
    for t in targets:
        vals = [target_value(r, t) for r in usable]
        sold = [t in PRODUCTS and is_sold_out(r["qty"].get(t), r["stock"].get(t)) for r in usable]
        lam = (lambdas or {}).get(t)
        try:
            m = fit_target(design, vals, sold, lam=lam, robust=robust)
        except ArithmeticError:
            m = None
        if m:
            models[t] = m
    return {"design": design, "models": models, "asOf": as_of, "days": len(usable),
            "firstDate": usable[0]["date"], "lastDate": last,
            "shares": recent_shares(usable, last)}


def forecast_day(trained, d, wx, events, calib=None):
    """1日ぶんの全項目の予測。wx が None なら「平年並みの天気」として計算。
    calib: 過去検証で測ったばらつき {項目: 値}。あれば予測の幅に使う。"""
    feats = features_for(d, wx, events)
    manual = None
    for e in (events or {}).get(d, []):
        if e.get("kind") != "除外" and e.get("factor"):
            manual = float(e["factor"])
    has_wx = bool(wx and wx.get("tmax") is not None)
    raw = {t: predict(trained["design"], m, feats, has_weather=has_wx, event_factor=manual)
           for t, m in trained["models"].items()}
    shares = trained.get("shares")
    total = raw.get(TOTAL)
    out = {}
    for t, r in raw.items():
        p50 = r["p50"]
        if t in PRODUCTS and total and shares:
            p50 = (1.0 - BLEND) * p50 + BLEND * total["p50"] * shares[t]
        sigma = (calib or {}).get(t) or trained["models"][t]["sigma"] * 1.25
        out[t] = {"p50": p50, "lo": p50 * math.exp(-Z80 * sigma), "hi": p50 * math.exp(Z80 * sigma),
                  "sigma": sigma, "factors": r["factors"], "base": r["base"]}
    for p in PRODUCTS:                      # 商品モデルが作れなかった商品は、全体×比率だけで出す
        if p not in out and total and shares:
            sigma = (calib or {}).get(p) or 0.45
            v = total["p50"] * shares[p]
            out[p] = {"p50": v, "lo": v * math.exp(-Z80 * sigma), "hi": v * math.exp(Z80 * sigma),
                      "sigma": sigma, "factors": total["factors"], "base": total["base"] * shares[p]}
    return out


def weekday_baseline(records, d, target, weeks=4):
    """比較用の単純な予測: 直近4週の同じ曜日の平均（無ければ直近14日の平均）。"""
    same = [target_value(r, target) for r in records
            if r["date"] < d and (d - r["date"]).days <= 7 * weeks and r["date"].weekday() == d.weekday()]
    same = [v for v in same if v]
    if same:
        return sum(same) / len(same)
    near = [target_value(r, target) for r in records if r["date"] < d and (d - r["date"]).days <= 14]
    near = [v for v in near if v]
    return sum(near) / len(near) if near else None


def backtest(records, weather, events, min_train=28, max_days=56):
    """過去にさかのぼって「その日より前のデータだけで予測したら当たったか」を確かめる。
    天気は実績を使う（＝天気予報が外れる分は含まない）。
    翌日の予測も同時に出す（納品ルールの検証に使う）。"""
    recs = sorted(records, key=lambda r: r["date"])
    test = [r for i, r in enumerate(recs) if i >= min_train][-max_days:]
    rows = []
    lambdas = None
    for r in test:
        d = r["date"]
        if any(e.get("kind") == "除外" for e in (events or {}).get(d, [])):
            continue
        prior = [x for x in recs if x["date"] < d]
        if lambdas is None:                  # 縮小の強さは最初に1度だけ決める（計算を軽くする）
            first = train(prior, weather, events, as_of=d, robust=False)
            lambdas = {t: m["lambda"] for t, m in (first or {"models": {}})["models"].items()}
        trained = train(prior, weather, events, as_of=d, lambdas=lambdas)
        if not trained:
            continue
        fc = forecast_day(trained, d, weather.get(d), events)
        nd = d + timedelta(days=1)
        fc_next = forecast_day(trained, nd, weather.get(nd), events)
        feats = features_for(d, weather.get(d), events)
        for t in ALL_TARGETS:
            actual = target_value(r, t)
            if not actual or t not in fc:
                continue
            rows.append({"date": d, "target": t, "actual": actual,
                         "pred": fc[t]["p50"], "predNext": fc_next.get(t, {}).get("p50"),
                         "baseline": weekday_baseline(prior, d, t),
                         "normal": is_normal_day(feats),
                         "soldout": t in PRODUCTS and is_sold_out(r["qty"].get(t), r["stock"].get(t))})
    return rows


def calibration(rows):
    """過去検証の外れ具合から、項目ごとのばらつき（対数の標準偏差）を出す。予測の幅に使う。"""
    out = {}
    for t in ALL_TARGETS:
        logs = [math.log(r["pred"] / r["actual"]) for r in rows if r["target"] == t and r["pred"] > 0]
        if len(logs) >= 14:
            out[t] = math.sqrt(sum(x * x for x in logs) / len(logs))
    return out


def accuracy_summary(rows, calib=None):
    """検証結果を項目ごとに集計。誤差率＝|予測−実績|の合計÷実績の合計。"""
    out = {}
    for t in ALL_TARGETS:
        rs = [r for r in rows if r["target"] == t]
        if not rs:
            continue

        def rate(sel, key="pred"):
            sel = [r for r in sel if r.get(key)]
            a = sum(r["actual"] for r in sel)
            return (sum(abs(r[key] - r["actual"]) for r in sel) / a) if a else None

        act = sum(r["actual"] for r in rs)
        rb = [r for r in rs if r["baseline"]]
        sigma = (calib or {}).get(t)
        inside = None
        if sigma:
            inside = sum(1 for r in rs if r["pred"] * math.exp(-Z80 * sigma) <= r["actual"]
                         <= r["pred"] * math.exp(Z80 * sigma)) / len(rs)
        # 3日間の合計で見た誤差（在庫は翌日に持ち越せるので、実務ではこちらが効く）
        by = {r["date"]: r for r in rs}
        e3 = a3 = 0.0
        for d in sorted(by):
            trio = [by.get(d + timedelta(days=k)) for k in range(3)]
            if all(trio):
                e3 += abs(sum(x["pred"] - x["actual"] for x in trio))
                a3 += sum(x["actual"] for x in trio)
        out[t] = {
            "days": len(rs), "meanActual": act / len(rs),
            "errorRate": rate(rs),
            "bias": sum(r["pred"] - r["actual"] for r in rs) / act if act else None,
            "meanAbsError": sum(abs(r["pred"] - r["actual"]) for r in rs) / len(rs),
            "errorRateNormal": rate([r for r in rs if r["normal"]]),
            "errorRateSpecial": rate([r for r in rs if not r["normal"]]),
            "normalDays": sum(1 for r in rs if r["normal"]),
            "errorRate3day": e3 / a3 if a3 else None,
            "baselineErrorRate": rate(rb, "baseline"),
            "modelErrorRateSameDays": rate(rb),
            "insideRange": inside,
        }
    return out


# ───────────────────────── 納品数（＝本店で作る数） ─────────────────────────

DEFAULT_SETTINGS = {"morningShare": 0.5, "marginBaked": 0.30, "marginNama": 0.15,
                    "shelfBaked": 4, "shelfNama": 2}


def margin_of(product, settings):
    return float(settings.get("marginNama", 0.15) if product in NAMA else settings.get("marginBaked", 0.30))


def night_target(product, next_sales, settings):
    """夜の在庫の目安 ＝ 翌日の販売見込み ×（朝の販売割合 ＋ 余裕）"""
    return (float(settings.get("morningShare", 0.5)) + margin_of(product, settings)) * next_sales


def recommend(forecasts, night_stock, settings):
    """2026-09-10に決めたルールで、16時の納品数と夜の在庫の目安を出す。
    forecasts: [{date, qty:{商品: 見込み}}]（日付順・連続。最後の日は翌日の見込み用で、結果には含めない）
    night_stock: {商品: 前夜の在庫} … 最初の日の前夜（無ければ目安どおりあったとみなす）
    返り値: 各日の {商品: {delivery, nightTarget, stockBefore, stockAssumed, expectedLoss, note}}"""
    out = []
    prev = dict(night_stock or {})
    for i, day in enumerate(forecasts[:-1]):
        nxt = forecasts[i + 1]
        rec = {}
        for p in PRODUCTS:
            s_today = day["qty"].get(p)
            s_next = nxt["qty"].get(p)
            if s_today is None or s_next is None:
                continue
            target = night_target(p, s_next, settings)
            stock_in = prev.get(p)
            assumed = stock_in is None
            if assumed:                       # 前夜の在庫が分からない日は目安どおりあったとみなす
                stock_in = night_target(p, s_today, settings)
            loss = 0.0
            usable = stock_in
            if p in NAMA and stock_in > s_today:
                loss = stock_in - s_today     # 生どらは翌日いっぱいまで。売り切れない分は期限切れ
                usable = s_today
            delivery = max(0.0, target + s_today - usable)
            note = ""
            if p not in NAMA and s_today > 0 and stock_in > 2.5 * s_today:
                note = f"在庫が約{stock_in / s_today:.1f}日分あります。期限の近いものから先に"
            elif p in NAMA and loss >= 1:
                note = f"前日分が約{loss:.0f}個残りそうです。先に売り切る"
            rec[p] = {"delivery": delivery, "nightTarget": target, "stockBefore": stock_in,
                      "stockAssumed": assumed, "expectedLoss": loss, "note": note}
            prev[p] = max(0.0, usable + delivery - s_today)
        out.append({"date": day["date"], "items": rec})
    return out


def simulate_policy(rows, settings):
    """過去検証の予測を使い、「この納品ルールで回していたら」を再現する。
    需要＝実際の販売数とみなす（売り切れで買えなかった分は含まれない＝欠品は少なめに出る）。
    返り値: 商品ごとの 納品合計・販売合計・売り逃し・期限切れ・欠品した日数。"""
    morning = float(settings.get("morningShare", 0.5))
    out = {}
    for p in PRODUCTS:
        rs = sorted((r for r in rows if r["target"] == p and r.get("predNext")), key=lambda r: r["date"])
        if len(rs) < 10:
            continue
        shelf = int(settings.get("shelfNama", 2) if p in NAMA else settings.get("shelfBaked", 4))
        batches = []            # [売れる残り日数, 個数] 古い順
        first = True
        delivered = sold = lost = expired = 0.0
        short_days = 0
        prev_date = None
        for r in rs:
            if prev_date is not None and (r["date"] - prev_date).days > 1:
                batches = []    # 日報が欠けた日をまたぐときは在庫を引き継がない
                first = True
            prev_date = r["date"]
            demand = float(r["actual"])
            if first:           # 初日は前夜に目安どおりの在庫があったとする
                batches = [[shelf - 1, night_target(p, r["pred"], settings)]]
                first = False
            stock_in = sum(b[1] for b in batches)

            def sell(q):
                got = 0.0
                for b in batches:
                    take = min(b[1], q - got)
                    b[1] -= take
                    got += take
                    if got >= q - 1e-9:
                        break
                return got

            got_m = sell(morning * demand)
            # 16時の納品数は、その時点で分かっている前夜の在庫と予測だけで決める
            usable = min(stock_in, r["pred"]) if p in NAMA else stock_in
            q = max(0.0, night_target(p, r["predNext"], settings) + r["pred"] - usable)
            q = float(round(q))
            batches.append([shelf, q])
            delivered += q
            got_e = sell((1.0 - morning) * demand)
            miss = demand - got_m - got_e
            sold += got_m + got_e
            if miss > 0.5:
                lost += miss
                short_days += 1
            for b in batches:
                b[0] -= 1
            expired += sum(b[1] for b in batches if b[0] <= 0)
            batches = [b for b in batches if b[0] > 0 and b[1] > 1e-9]
        out[p] = {"days": len(rs), "delivered": delivered, "sold": sold, "lostSales": lost,
                  "expired": expired, "shortDays": short_days,
                  "lostRate": lost / (sold + lost) if sold + lost else None,
                  "expiredRate": expired / delivered if delivered else None}
    return out
