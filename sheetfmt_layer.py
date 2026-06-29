import data_layer

_F = ("sheets(data(rowData(values(formattedValue,effectiveFormat(backgroundColor,"
      "horizontalAlignment,textFormat(foregroundColor,fontSize,bold),borders)))))")


def _hx(c, d):
    if not c:
        return d
    return "#%02x%02x%02x" % (round(c.get("red", 0) * 255), round(c.get("green", 0) * 255), round(c.get("blue", 0) * 255))


def _bd(b):
    if not b or b.get("style") in (None, "NONE"):
        return None
    return _hx(b.get("color"), "#000000")


def _fmt(v):
    ef = (v or {}).get("effectiveFormat", {}) or {}
    o = {}
    bg = _hx(ef.get("backgroundColor"), "#ffffff")
    if bg.lower() != "#ffffff":
        o["bg"] = bg
    tf = ef.get("textFormat", {}) or {}
    fg = _hx(tf.get("foregroundColor"), "#000000")
    if fg.lower() != "#000000":
        o["fg"] = fg
    if tf.get("fontSize"):
        o["fs"] = round(tf["fontSize"] * 4 / 3)
    if tf.get("bold"):
        o["b"] = 1
    if ef.get("horizontalAlignment"):
        o["a"] = ef["horizontalAlignment"].lower()
    sd = {}
    for s in ("top", "bottom", "left", "right"):
        col = _bd((ef.get("borders") or {}).get(s))
        if col:
            sd[s[0]] = col
    if sd:
        o["bd"] = sd
    return o


def get_raw_styled(tab=None):
    ws = data_layer.open_ws(tab)
    meta = data_layer._spreadsheet().fetch_sheet_metadata(params={"includeGridData": True, "ranges": [ws.title], "fields": _F})
    rows = (((meta.get("sheets") or [{}])[0].get("data") or [{}])[0].get("rowData")) or []
    vals, fmts, mc = [], [], 0
    for row in rows:
        cs = row.get("values", []) or []
        mc = max(mc, len(cs))
        vals.append([(c.get("formattedValue", "") if c else "") for c in cs])
        fmts.append([_fmt(c) for c in cs])
    for i in range(len(vals)):
        vals[i] += [""] * (mc - len(vals[i]))
        fmts[i] += [{}] * (mc - len(fmts[i]))
    return {"tab": ws.title, "values": vals, "fmts": fmts}
