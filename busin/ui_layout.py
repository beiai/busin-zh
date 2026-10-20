"""Encode and lay out translated small-font UI text."""

import re


def _label(label) -> str:
    return label if isinstance(label, str) else f"{label:06X}"


def _address(label) -> int:
    return int(_label(label).split()[-1].split(".")[0], 16)


def _slacks(rows):
    lengths = {}
    for label, codes in rows:
        lengths[_address(label)] = len(codes)
    order = sorted(lengths)
    following = dict(zip(order, order[1:]))
    slack = {}
    for va, nxt in following.items():
        room = (nxt - va) // 2 - lengths[va] - 1
        if room < 0:
            raise AssertionError(f"{va:#x} overlaps the next string by {-room}")
        slack[va] = room
    return slack


def _entry(label, text, width, slack=None) -> str:
    extra = "" if slack is None else f" +{slack}"
    return f"{_label(label)}  {text}  width={width}{extra}"


def _tokens(text, slash):
    index = 0
    while index < len(text):
        if text.startswith("{d}", index):
            yield ("dd", None)
            index += 3
            continue
        hole = re.match(r"\{(\d+)\}", text[index:])
        if hole:
            yield ("sp", int(hole.group(1)))
            index += len(hole.group(0))
            continue
        char = text[index]
        index += 1
        if char == " ":
            yield ("sp", 1)
        elif char == "/" and slash:
            yield ("nl", None)
        else:
            yield ("g", char)


def _encode(text, table, slash):
    out = []
    for kind, item in _tokens(text, slash):
        if kind == "sp":
            out.extend([0] * item)
        elif kind == "nl":
            out.append(0xFFFE)
        elif kind == "dd":
            out.append(0xFFFD)
        elif item not in table:
            raise ValueError(f"no cell for {item}")
        else:
            out.append(table[item])
    return out


def _glyphs(text, slash):
    return [item for kind, item in _tokens(text, slash) if kind == "g"]


def _centered(codes):
    return (
        bool(codes)
        and codes[0] == 0
        and codes[-1] == 0
        and any(code not in (0, 0xFFFE) for code in codes)
    )


# Race names are three cells. Two-kana rows started at x+8, four-kana rows at x+0.
# The short-name branch now always runs, so every row starts at x+8.
_RACE_X = {
    0x14D654: (0x14400004, 0x10000004),
}


_RACE_LABELS = {
    ("menu", "15"),
    ("menu", "16"),
    ("menu", "17"),
    ("menu", "18"),
    ("menu", "19"),
    ("create", "589FB0"),
    ("create", "589FB8"),
    ("create", "589FC0"),
    ("create", "589FC8"),
    ("create", "589FD0"),
}


# Status sheet centers a stat name by its Japanese length: one glyph at x+16, two at x+8, three at x+0.
# Chinese names are three cells with a middle blank, and share the three-kanji rows at x+0.
_STAT_X = {
    0x582A20: (0x0010000B, 0x0000000B),
    0x582A24: (0x0008000C, 0x0000000C),
    0x582A28: (0x0000000D, 0x0000000D),
    0x582A2C: (0x0000000E, 0x0000000E),
    0x582A30: (0x0000000F, 0x0000000F),
    0x582A34: (0x00000010, 0x00000010),
}


# Creation screen places the first stat at x+16 and the second at x+8.
# Chinese names are three cells, so those two rows use the same x as the other four.
_CREATE_STAT_X = {
    0x14D174: (0x27C20010, 0x27C20000),
    0x14D188: (0x27C30008, 0x27C30000),
}


# Ability points place the first name at x+16 and the second at x+8. The value column stays put.
# Chinese names are three cells, so the two short rows use x+0 with the others.
# The later list uses the same two offsets, and its number is drawn from that x.
_POINT_STAT_X = {
    0x14DAE0: (0x26A20010, 0x26A20000),
    0x14DAF0: (0x26A20008, 0x26A20000),
    0x152DA4: (0x26020010, 0x26020000),
    0x152DB4: (0x26020008, 0x26020000),
}


def _layout_line(text, raw, width, slack, table, slash, center=True):
    body = _encode(text, table, slash)
    limit = width + (slack or 0)
    if center and _centered(raw) and len(body) <= width:
        pad = width - len(body)
        left = pad // 2
        body = [0] * left + body + [0] * (pad - left)
    elif len(body) < width:
        body = body + [0] * (width - len(body))
    if len(body) > limit:
        raise ValueError(f"{len(body)} cells, budget {limit}")
    return body


def _fit_grid(text, slot, table):
    width, rows = slot["width"], slot["rows"]
    if "/" not in text:
        return _wrap(_encode(text, table, False), width, rows)
    parts = text.split("/")
    if len(parts) > rows:
        raise ValueError(f"{len(parts)} lines, budget {rows}")
    parts.extend([""] * (rows - len(parts)))
    out = []
    for part in parts:
        body = _encode(part, table, False)
        if len(body) > width:
            raise ValueError(f"{len(body)} cells, budget {width}")
        out.extend(body + [0] * (width - len(body)))
    return out


def _wrap(codes, width, rows):
    if len(codes) > width * rows:
        raise ValueError(f"{len(codes)} cells, budget {width * rows}")
    out = []
    for index in range(rows):
        chunk = codes[index * width : (index + 1) * width]
        out.extend(chunk + [0] * (width - len(chunk)))
    return out


def _layout_ok(encoded, written):
    """Written text is the encoding plus centering and right pad. A gap counts only if the translation wrote it."""
    index = got = 0
    while index < len(encoded) and got < len(written):
        if encoded[index] == written[got]:
            index += 1
            got += 1
            continue
        if written[got] != 0 or index != 0:
            return False
        got += 1
    while got < len(written) and written[got] == 0:
        got += 1
    return index == len(encoded) and got == len(written)


def _assign(chars, known):
    from busin.patch.ui_pages import FIRST_CODE, LAST_CODE

    fresh = sorted({char for char in chars if char not in known})
    if fresh and FIRST_CODE + len(fresh) - 1 > LAST_CODE:
        raise ValueError(
            f"{len(fresh)} new glyphs do not fit in {FIRST_CODE:#x}-{LAST_CODE:#x}"
        )
    table = dict(known)
    for index, char in enumerate(fresh):
        table[char] = FIRST_CODE + index
    return table, {char: table[char] for char in fresh}


def _visible(text, slot):
    return text.replace("/", "") if slot.get("type") == "grid" else text


def _fit(text, slot, table):
    if slot.get("type") == "grid":
        return _fit_grid(text, slot, table)
    if slot["mode"] == "flow":
        return _wrap(_encode(text, table, False), slot["width"], slot["rows"])
    if slot["mode"] == "span":
        body = _layout_line(text, slot["raw"], slot["width"], slot["slack"], table, slot["slash"])
        if len(body) > slot["room"]:
            raise ValueError(f"{len(body)} cells, room {slot['room']}")
        return body + [0] * (slot["room"] - len(body))
    if slot.get("keep"):
        # The first cell is an x offset. The label uses the cells after it.
        drawn = _encode(text, table, False)
        room = slot["width"] - 1
        if len(drawn) > room:
            raise ValueError(f"{len(drawn)} cells, budget {room}")
        return [slot["raw"][0], *drawn, *([0] * (room - len(drawn)))]
    return _layout_line(
        text,
        slot["raw"],
        slot["width"],
        slot["slack"],
        table,
        slot["slash"],
        center=slot.get("type") != "line",
    )
