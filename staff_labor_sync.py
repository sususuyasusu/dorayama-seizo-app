#!/usr/bin/env python3
"""店長（人事権あり）向けの人別人件費を、非公開のGoogleシートへ書き出す（2026-10-20 本人指示）。

製造アプリのGitHubは公開なので、氏名・給与はリポジトリにも製造表（リンク閲覧可）にも置かない。
代わりにオーナー所有の非公開シート（サービスアカウントにだけ共有）へ毎日書き、アプリは合言葉付きで読む。

元データ:
  Airシフト給与計算表 CSV（氏名,日付,出勤,退勤,休憩,労働時間,残業時間,深夜労働時間,時給,基本給,残業手当,深夜手当,通勤手当,手当・その他,合計,メモ）
  タイミー明細 CSV（日付,スタッフ名,店舗名,出勤,退勤,休憩時間,実働時間,時給,交通費,役割）→ 月ごとに1行の束にする
  どら山/staff_roster.json の role（デザイン＝除外、社員／アルバイト／要確認）
出すもの: 勤務日数・労働時間・総支給（Airシフトの「合計」）・時給換算。控除・手取り・役員報酬・デザイン部門は出さない。
"""
import csv
import glob
import json
import re
from datetime import datetime
from zoneinfo import ZoneInfo

import data_layer

BASE = "/Users/suzuki3/Library/CloudStorage/Dropbox-Detale/D& W/どら山"
AIRSHIFT_GLOB = BASE + "/過去/dw_budget_profit_sheets_automation/data/input/airshift_worksheets/airshift_worksheet_*.csv"
TAIMEE_GLOB = BASE + "/過去/dw_budget_profit_sheets_automation/data/input/airshift/taimee_*.csv"
ROSTER = BASE + "/staff_roster.json"
HR_SHEET_ID = "1wrKIo7MqU_bm6z72cwvvwBSOArYP1Y5z9O8BrnOGqyE"
TAB_MONTHLY, TAB_DAILY, TAB_CONFIG = "人別月次", "人別日次", "設定"
EXCLUDE_NAMES = {"鈴木康之"}  # 役員（Airシフトには出ないはずだが念のため）
KEEP_DAILY_MONTHS = 3  # 人別日次は直近3か月ぶんだけ（行数を抑える）
JST = ZoneInfo("Asia/Tokyo")


def _num(s):
    s = str(s).replace(",", "").replace("¥", "").strip()
    try:
        return float(s) if s not in ("", "-") else 0.0
    except ValueError:
        return 0.0


def _hours(s):
    s = str(s).strip()
    if ":" in s:
        h, m = s.split(":")[:2]
        return int(h or 0) + int(m or 0) / 60
    return _num(s)


def _norm(name):
    return re.sub(r"[\s　]", "", str(name))


def load_roles():
    try:
        roster = json.load(open(ROSTER, encoding="utf-8"))
    except Exception:
        return {}
    roles = {}
    for s in roster.get("staff", []):
        roles[_norm(s.get("name", ""))] = s.get("role", "要確認")
    return roles


def role_label(role):
    # 名簿に無い人（freee名簿は振込名ベースで欠けが多い）はアルバイト扱い。社員とデザインだけ名簿で判定
    return {"社員": "社員", "デザイン": "デザイン"}.get(role, "アルバイト")


def collect():
    roles = load_roles()
    ppd = {}
    for path in glob.glob(AIRSHIFT_GLOB):
        if "競合" in path or "conflicted" in path.lower():
            continue  # Dropboxの競合コピーは読まない（二重計上事故 10/9 の教訓）
        with open(path, encoding="utf-8-sig") as f:
            rows = list(csv.reader(f))
        for r in rows[1:]:
            if len(r) < 15 or not r[0].strip() or "/" not in r[1]:
                continue
            ppd[(_norm(r[0]), r[1].strip())] = r
    daily, excluded = [], set()
    for (nm, d), r in sorted(ppd.items(), key=lambda x: (x[0][1], x[0][0])):
        role = roles.get(nm, "要確認")
        if nm in EXCLUDE_NAMES or role == "デザイン":
            excluded.add(nm)
            continue
        ym = d[:7].replace("/", "-")
        daily.append({
            "month": ym, "date": d.replace("/", "-"), "name": nm, "role": role_label(role),
            "start": r[2].strip(), "end": r[3].strip(), "hours": _hours(r[5]), "overtime": _hours(r[6]),
            "pay": _num(r[14]), "commute": _num(r[12]),
        })
    # タイミーは月ごとの束
    taimee = {}
    for path in glob.glob(TAIMEE_GLOB):
        if "競合" in path:
            continue
        with open(path, encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                d = (r.get("日付") or "").strip()
                if len(d) < 7:
                    continue
                t = taimee.setdefault(d[:7], {"days": 0, "hours": 0.0, "pay": 0.0, "commute": 0.0, "people": set(), "last": ""})
                h, w, c = _num(r.get("実働時間")), _num(r.get("時給")), _num(r.get("交通費"))
                t["days"] += 1
                t["hours"] += h
                t["pay"] += h * w + c
                t["commute"] += c
                t["people"].add((r.get("スタッフ名") or "").strip())
                t["last"] = max(t["last"], d)
    monthly = {}
    for x in daily:
        m = monthly.setdefault((x["month"], x["name"]), {"role": x["role"], "days": 0, "hours": 0.0, "overtime": 0.0, "pay": 0.0, "commute": 0.0, "last": ""})
        m["days"] += 1
        m["hours"] += x["hours"]
        m["overtime"] += x["overtime"]
        m["pay"] += x["pay"]
        m["commute"] += x["commute"]
        m["last"] = max(m["last"], x["date"])
    for ym, t in taimee.items():
        monthly[(ym, "タイミー（スポット・{}人）".format(len(t["people"])))] = {
            "role": "タイミー", "days": t["days"], "hours": t["hours"], "overtime": 0.0,
            "pay": t["pay"], "commute": t["commute"], "last": t["last"],
        }
    return monthly, daily, excluded


def write_sheet(monthly, daily):
    sh = data_layer._client().open_by_key(HR_SHEET_ID)
    head_m = ["月", "氏名", "区分", "勤務日数", "労働時間", "残業時間", "総支給", "うち通勤手当", "時給換算", "最終勤務日"]
    rows_m = [head_m]
    for (ym, nm), m in sorted(monthly.items(), key=lambda x: (x[0][0], -x[1]["pay"])):
        rate = round(m["pay"] / m["hours"]) if m["hours"] > 0 else ""
        rows_m.append([ym, nm, m["role"], m["days"], round(m["hours"], 1), round(m["overtime"], 1),
                       round(m["pay"]), round(m["commute"]), rate, m["last"]])
    months = sorted({x["month"] for x in daily})[-KEEP_DAILY_MONTHS:]
    head_d = ["日付", "氏名", "区分", "出勤", "退勤", "労働時間", "総支給"]
    rows_d = [head_d] + [[x["date"], x["name"], x["role"], x["start"], x["end"], round(x["hours"], 2), round(x["pay"])]
                         for x in daily if x["month"] in months]
    for tab, rows in ((TAB_MONTHLY, rows_m), (TAB_DAILY, rows_d)):
        try:
            ws = sh.worksheet(tab)
        except Exception:
            ws = sh.add_worksheet(title=tab, rows=len(rows) + 20, cols=12)
        ws.clear()
        ws.resize(rows=max(len(rows) + 20, 50), cols=12)
        ws.update(range_name="A1", values=rows, value_input_option="RAW")
    sh.worksheet(TAB_CONFIG).update(range_name="B6", values=[[datetime.now(JST).strftime("%Y-%m-%d %H:%M")]])
    return len(rows_m) - 1, len(rows_d) - 1


if __name__ == "__main__":
    monthly, daily, excluded = collect()
    if not monthly:
        print("人別人件費 SKIP: CSVから集計0件のため書き込み中止（既存データを保持）")
        raise SystemExit(0)
    n_m, n_d = write_sheet(monthly, daily)
    print(f"人別人件費 月次{n_m}行 日次{n_d}行 除外{len(excluded)}人（デザイン・役員）")
    latest = max(m for m, _ in monthly)
    tot = sum(v["pay"] for (m, _), v in monthly.items() if m == latest)
    print(f"  {latest} 合計 {round(tot):,}円 / {sum(1 for (m, _) in monthly if m == latest)}行")
