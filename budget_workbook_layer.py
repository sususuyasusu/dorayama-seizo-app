#!/usr/bin/env python3
"""2026年予算計画Excelの読み取り専用スナップショット。"""
import json
import re
from functools import lru_cache
from pathlib import Path


SNAPSHOT = Path(__file__).parent / "data" / "budget_workbook_snapshot.json"
AUTHORITATIVE = {"表紙", "どら山店舗・催事年間PL一覧"}
PLAN_REFERENCE = {"どら山店舗 年間PL一覧", "どら山催事年間PL一覧"}


@lru_cache(maxsize=1)
def _data():
    return json.loads(SNAPSHOT.read_text(encoding="utf-8"))


def _quality(name):
    if name in AUTHORITATIVE:
        return "2026確定・原本"
    if name in PLAN_REFERENCE:
        return "予算参考"
    return "旧様式参考"


def catalog():
    return [{
        "name": sheet["name"],
        "rows": sheet["maxRow"],
        "columns": sheet["maxColumn"],
        "formulaCount": sheet["formulaCount"],
        "errorCount": sheet["errorCount"],
        "quality": _quality(sheet["name"]),
    } for sheet in _data()["sheets"]]


def _sheet(name):
    return next((sheet for sheet in _data()["sheets"] if sheet["name"] == name), None)


def _column_number(coordinate):
    letters = re.match(r"[A-Z]+", coordinate or "")
    if not letters:
        return 0
    value = 0
    for letter in letters.group(0):
        value = value * 26 + ord(letter) - 64
    return value


def get_sheet(name):
    sheet = _sheet(name)
    if not sheet:
        return {"error": "指定されたシートがありません", "name": name}
    rows = []
    for source_row in sheet["rows"]:
        cells = []
        for item in source_row["items"]:
            value = item.get("value")
            kind = "error" if isinstance(value, str) and value.startswith("#") else "value"
            cells.append({
                "column": _column_number(item.get("cell")),
                "coordinate": item.get("cell"),
                "value": value,
                "kind": kind,
                "hasFormula": bool(item.get("formula")),
            })
        rows.append({"row": source_row["row"], "cells": cells})
    warning = (
        "2026年の確定実績として使用できます。" if name == "どら山店舗・催事年間PL一覧" else
        "どら山の店舗・催事予算の参考表です。" if name in PLAN_REFERENCE else
        "Excel原本に残る旧2007年・北堀江LIME様式です。内容は保持しますが、現在の確定損益には使用しません。"
    )
    return {
        "name": name,
        "quality": _quality(name),
        "warning": warning,
        "maxRow": sheet["maxRow"],
        "maxColumn": sheet["maxColumn"],
        "formulaCount": sheet["formulaCount"],
        "errorCount": sheet["errorCount"],
        "rows": rows,
    }


def row_values(sheet_name, row_number):
    sheet = _sheet(sheet_name)
    if not sheet:
        return {}
    row = next((row for row in sheet["rows"] if row["row"] == row_number), None)
    if not row:
        return {}
    return {item["cell"]: item.get("value") for item in row["items"]}


def pl_month_values(row_number):
    values = row_values("どら山店舗・催事年間PL一覧", row_number)
    columns = ["F", "G", "H", "J", "K", "L", "N"]
    return [values.get(f"{column}{row_number}") for column in columns]
