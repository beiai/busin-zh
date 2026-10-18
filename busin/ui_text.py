"""Small-font readings and a text dump of the static 16×16 strings."""
import json
import re
import struct

from busin.disc import FILE_BASE
from busin.disasm import load_elf
from busin.paths import BUILD, DATA

UI = {
    int(code, 16): char
    for code, char in json.loads((DATA / "font" / "small.json").read_text(encoding="utf-8")).items()
}
# Cells with no ink. 0x0000 inside a string is a space, not one of these.
BLANKS = (0x000, 0x020, 0x040, 0x06F, 0x341)
# Parchment row pitch. 0x108820 still steps 16 pixels across.
PAPER_PITCH = (0x14E77C, 0x14E81C, 0x14E8BC)
PAPER_PITCH_OLD = 0x26750010  # addiu $s5, $s3, 0x10
PAPER_PITCH_NEW = 0x26750014  # addiu $s5, $s3, 0x14
# Three painters. Each is madd $s2, $a0, $v0: lo = base, v0 = 0x26, a0 = row, six rows of 19.
PAPER_PAGES = (0x445810, 0x4458F8, 0x4459E0)
PAPER_ROWS = 6
PAPER_WIDTH = 19
# Item-command rows whose first cell is the +16 nudge. The SP row is the one left out.
_GEAR_NUDGE = {0x584308 + index * 12 for index in range(7) if index != 4}

STRING_TABLE = 0x43E9C8


def ui_char(code: int) -> str | None:
    return UI.get(code)


def render(codes) -> str:
    parts = []
    for code in codes:
        if code == 0:
            parts.append(" ")
        elif code == 0xFFFE:
            parts.append("/")
        elif code == 0xFFFD:
            parts.append("{d}")
        else:
            parts.append(UI.get(code, f"[{code:03X}]"))
    return "".join(parts).rstrip()


def _runs(codes):
    runs, index = [], 0
    while index < len(codes):
        code = codes[index]
        if code == 0:
            end = index + 1
            while end < len(codes) and codes[end] == 0:
                end += 1
            runs.append(("sp", end - index))
        elif code == 0xFFFE:
            runs.append(("nl", None))
            end = index + 1
        else:
            end = index + 1
            while end < len(codes) and codes[end] not in (0, 0xFFFE):
                end += 1
            runs.append(("g", codes[index:end]))
        index = end
    return runs


def unwrap(codes, flow=False) -> str:
    """Keep spaces the translation can edit. A run of two or more stays as {n} unless flow joins the lines.

    One cell at either end is kept too. Joined lines drop a trailing empty cell; that one pads the window.
    """
    runs = _runs(codes)
    parts = []
    for index, (kind, item) in enumerate(runs):
        if kind == "nl":
            if not flow:
                parts.append("/")
            continue
        if kind == "g":
            parts.append(render(item))
            continue
        left = index > 0 and runs[index - 1][0] == "g"
        right = index + 1 < len(runs) and runs[index + 1][0] == "g"
        trailing = left and not right
        if item == 1 and (left or right) and not (flow and trailing):
            parts.append(" ")
        elif item >= 2 and not flow:
            parts.append(f"{{{item}}}")
    return "".join(parts)


def _row_text(chunk) -> str:
    """One fixed row. A blank row stays blank. Any other row keeps spaces at both ends."""
    if not any(chunk):
        return ""
    return unwrap(chunk, flow=False)


def _grid_source(codes, width) -> str:
    if width <= 0 or len(codes) % width:
        raise AssertionError(f"{len(codes)} cells do not fit width {width}")
    rows = [codes[index:index + width] for index in range(0, len(codes), width)]
    return "/".join(_row_text(row) for row in rows)


def _half(elf, va):
    return struct.unpack_from("<H", elf, va - FILE_BASE)[0]


def _word(elf, va):
    return struct.unpack_from("<I", elf, va - FILE_BASE)[0]


def _read_ffff(elf, va, limit=96, extra=()):
    out = []
    for index in range(limit):
        value = _half(elf, va + index * 2)
        if value == 0xFFFF:
            return out
        if value != 0xFFFE and value not in extra and value >= 0x400:
            return out
        out.append(value)
    return out


def _text_run(run):
    body = [code for code in run if code not in (0x6E, 0xFFFE, 0)]
    return len(body) >= 2 and any(code >= 0x70 for code in body)


def _strip_pad(run):
    # 0x1D (=) and 0x6E (_) pad item records. They are real glyphs elsewhere.
    while run and run[0] in (0x1D, 0x6E):
        run = run[1:]
    while run and run[-1] == 0x6E:
        run = run[:-1]
    return run


def _record_runs(elf, va, limit=3):
    found, current = [], []
    for index in range(32):
        value = _half(elf, va + index * 2)
        if value in (0, 0xFFFF):
            if current:
                found.append(current)
                current = []
        elif value == 0xFFFE or value < 0x400:
            current.append(value)
        elif current:
            found.append(current)
            current = []
    if current:
        found.append(current)
    kept = []
    for run in found:
        run = _strip_pad(run)
        if _text_run(run):
            kept.append(run)
        if len(kept) == limit:
            break
    return kept


def _pointers(elf, table, lo, hi, limit=400):
    rows = []
    for index in range(limit):
        ptr = _word(elf, table + index * 4)
        if not lo <= ptr < hi:
            break
        codes = _read_ffff(elf, ptr)
        if _half(elf, ptr + len(codes) * 2) != 0xFFFF:
            break
        rows.append((f"{index:02X} {ptr:06X}", codes))
    return rows


def _fixed(elf, va, width, count):
    step = width * 2
    return [(f"{va + index * step:06X}", _grid(elf, va + index * step, width, 1)) for index in range(count)]


def _grid_rows(elf, base, stride, count, width, rows, skip=0):
    size = width * rows
    out = []
    for index in range(count):
        va = base + index * stride + skip * 2
        codes = [_half(elf, va + cell * 2) for cell in range(size)]
        if any(code >= 0x400 for code in codes):
            raise AssertionError(f"grid cell at {va:#x}")
        out.append((f"{va:06X}", codes))
    return out


def _ptr_until(elf, table, lo, hi, extra=()):
    rows = []
    for index in range(80):
        ptr = _word(elf, table + index * 4)
        if not lo <= ptr < hi:
            break
        codes = _read_ffff(elf, ptr, limit=160, extra=extra)
        if _half(elf, ptr + len(codes) * 2) != 0xFFFF:
            raise AssertionError(f"unterminated string at {ptr:#x}")
        rows.append((f"{table + index * 4:06X} {ptr:06X}", codes))
    if not rows:
        raise AssertionError(f"empty pointer table {table:#x}")
    return rows


def _ability_rows(elf):
    # 0x2854A0 multiplies the index by 0x5A. The 15×3 window starts at 0x4C04C6.
    # Record 0 is the packed blurb at 0x4C04C4. Record 37 would cross 0x4C1200.
    out = []
    for index in range(1, 37):
        va = 0x4C04C6 + index * 0x5A
        codes = [_half(elf, va + cell * 2) for cell in range(45)]
        if any(code >= 0x400 for code in codes):
            raise AssertionError(f"ability cell at {va:#x}")
        if any(code >= 0x70 for code in codes):
            out.append((f"{va:06X}", codes))
    return out


def _file_labels(elf):
    # Save-file names live in the 9-character cells drawn from 0x499438.
    va, rows = 0x499438, []
    while va < 0x499800:
        if _half(elf, va) >= 0x400:
            break
        sig = tuple(_half(elf, va + col * 2) for col in range(4))
        if sig == (0x26, 0x29, 0x2C, 0x25):
            rows.append((f"{va:06X}", [_half(elf, va + col * 2) for col in range(9)]))
            va += 18
        else:
            va += 2
    return rows


def _packed(elf, start, end):
    found = []
    va = start
    while va < end:
        value = _half(elf, va)
        if value in (0, 0xFFFF) or value >= 0x400:
            va += 2
            continue
        run, at, ok = [], va, True
        while at < end:
            code = _half(elf, at)
            if code in (0, 0xFFFF):
                break
            if code == 0xFFFE or code < 0x400:
                run.append(code)
                at += 2
                continue
            ok = False
            break
        run = _strip_pad(run)
        if ok and _text_run(run) and _half(elf, at) in (0, 0xFFFF):
            found.append((va, run))
            va = at + 2
        else:
            va += 2
    return found


def collect(elf):
    menu = []
    for index in range(0xA9):
        pointer = _word(elf, STRING_TABLE + index * 4)
        if not 0x430000 <= pointer < 0x450000:
            break
        menu.append((f"{index:02X} {pointer:06X}", _read_ffff(elf, pointer)))
    if len(menu) != 0xA9:
        raise AssertionError(f"menu {len(menu)}")

    paper = []
    for base in PAPER_PAGES:
        for row in range(PAPER_ROWS):
            va = base + row * 0x26
            codes = [_half(elf, va + index * 2) for index in range(PAPER_WIDTH)]
            if any(code >= 0x400 for code in codes):
                raise AssertionError(f"paper cell at {va:#x}")
            paper.append((f"{va:06X}", codes))

    names = []
    for va in range(0x4A4000, 0x4B0140, 0x40):
        for slot, run in enumerate(_record_runs(elf, va)):
            label = f"{va:06X}" if slot == 0 else f"{va:06X}.{slot + 1}"
            names.append((label, run))

    monsters = []
    for va in range(0x4F3400, 0x4F5400, 0x40):
        if _half(elf, va) == 0:
            continue
        run = []
        for index in range(20):
            code = _half(elf, va + index * 2)
            if code == 0:
                break
            if code >= 0x400:
                run = []
                break
            run.append(code)
        run = _strip_pad(run)
        if _text_run(run):
            monsters.append((f"{va:06X}", run))

    places = []
    for start, end in (
        (0x526C00, 0x527400), (0x529C20, 0x52A000),
        (0x567F90, 0x568400), (0x569940, 0x56AF00),
        (0x4C1200, 0x4C2400),
    ):
        places.extend(_packed(elf, start, end))

    return (
        ("menu", "菜单", menu),
        ("controller", "手柄", _pointers(elf, 0x43AED8, 0x43AE40, 0x43AEE0)),
        ("save", "存档提示", [
            (f"{index:02X} {ptr:06X}", _grid(elf, ptr, 28, 4))
            for index in range(32)
            for ptr in (_word(elf, 0x440970 + index * 4),)
        ]),
        ("files", "存档位", _file_labels(elf)),
        ("paper", "建角羊皮纸", paper),
        ("job", "转职", [
            (f"{va:06X}", [_half(elf, va + col * 2) for col in range(count)])
            for va, count in ((0x445AC8, 6), (0x445AD8, 11), (0x445AF0, 17))
        ]),
        ("create", "建角标签", (
            _fixed(elf, 0x589F60, 3, 6) + _fixed(elf, 0x589F88, 4, 5)
            + _fixed(elf, 0x589FB0, 4, 5) + _fixed(elf, 0x589FD8, 3, 8)
            + _fixed(elf, 0x58A008, 3, 8) + _fixed(elf, 0x58A038, 5, 15)
        )),
        ("personality", "性格", [
            (f"{0x582608 + index * 66:06X}", _grid(elf, 0x582608 + index * 66, 11, 3))
            for index in range(15)
        ]),
        ("quest", "委托确认", _fixed(elf, 0x583028, 28, 3)),
        ("town", "城镇说明", [
            (f"{0x4428E8 + index * 114 + 6:06X}", _grid(elf, 0x4428E8 + index * 114 + 6, 54, 1))
            for index in range(7)
        ]),
        ("shop", "商店", _pointers(elf, 0x444940, 0x443000, 0x445000)),
        ("names", "物品、魔法、吐息", names),
        ("monsters", "怪物", monsters),
        ("blurbs", "效果说明", _packed(elf, 0x4B0140, 0x4C0500)),
        ("places", "迷宫、城镇、委托", places),
        ("debug", "调试菜单", _pointers(elf, 0x487198, 0x486000, 0x488000)),
        ("race", "种族说明", _grid_rows(elf, 0x581AC0, 0xAA, 5, 12, 7, skip=1)),
        ("align", "阵营说明", _grid_rows(elf, 0x581E18, 0xAA, 3, 12, 7, skip=1)),
        ("class", "职业说明", _grid_rows(elf, 0x582018, 0x44, 8, 11, 3, skip=1)),
        ("stat", "能力说明", _grid_rows(elf, 0x582238, 0x48, 6, 12, 3)),
        ("rank", "队伍阶级", _grid_rows(elf, 0x583BC0, 0x26, 5, 19, 1)),
        ("gear", "物品操作", _grid_rows(elf, 0x584308, 12, 7, 6, 1)),
        ("inn", "旅馆", _ptr_until(elf, 0x442C40, 0x580000, 0x582000, extra=(0xFFFD,))),
        ("room", "房间", _ptr_until(elf, 0x48367C, 0x483000, 0x484000)),
        ("ask", "迷宫确认", _ptr_until(elf, 0x55282C, 0x59CF00, 0x59E000)),
        ("lift", "升降机", _packed(elf, 0x5525C0, 0x552650)),
        ("bark", "战斗喊话", [
            (f"{va:06X}", [_half(elf, va + cell * 2) for cell in range(width)])
            for va, width in _BARK
        ]),
        ("link", "连携说明", _ability_rows(elf)),
    )


# Create-character tables, in collect() order: six widths, then five, five, eight, eight,
# then the fifteen personality names. The descriptions are a separate table, same order.
_CREATE_WIDTH = (3,) * 6 + (4,) * 5 + (4,) * 5 + (3,) * 8 + (3,) * 8 + (5,) * 15
_JOB_WIDTH = (6, 11, 17)
_GRID = {
    "save": (28, 4), "personality": (11, 3), "race": (12, 7), "align": (12, 7),
    "class": (11, 3), "stat": (12, 3), "link": (15, 3),
}
_LINE = {"town", "quest", "files", "rank", "gear", "create", "job", "bark"}
_FIXED = {"town": "54", "quest": "28", "files": "9", "rank": "19", "gear": "6"}
_POINTERS = ("menu", "controller", "shop", "debug", "inn", "room", "ask")
# Name slot scans 10 cells. The two shouts keep their leading spaces and replace the glyph run.
_BARK = ((0x4977E8, 10), (0x497810, 12), (0x497838, 16), (0x497880, 16))
_BARK_WIDTH = tuple(width for _va, width in _BARK)
_NOTE = "{n} 是要留出的格子。width 是原格子数，x 后是行数，+ 是这条后面还能多占的格子。格子说明的译文用 / 断行，不写就按行宽往下排。"


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


def _paper_groups(rows):
    groups, group, prev = [], [], None
    for label, codes in rows:
        va = _address(label)
        if prev is not None and va - prev != 0x26:
            groups.append(group)
            group = []
        group.append((va, codes))
        prev = va
    groups.append(group)
    return groups


def _paper_lines(rows):
    lines = []
    for group in _paper_groups(rows):
        if len(group) != PAPER_ROWS or any(len(codes) != PAPER_WIDTH for _va, codes in group):
            raise AssertionError("paper page is not 19x6")
        codes = [code for _va, row in group for code in row]
        lines.append(_entry(f"{group[0][0]:06X}", _grid_source(codes, PAPER_WIDTH), f"{PAPER_WIDTH}x{PAPER_ROWS}"))
    return lines


def manuscript_lines(name, rows):
    if name == "paper":
        return _paper_lines(rows)
    if name in _GRID:
        width, nrows = _GRID[name]
        lines = []
        for label, codes in rows:
            if len(codes) != width * nrows:
                raise AssertionError(f"{name} {_label(label)} has {len(codes)} cells")
            lines.append(_entry(label, _grid_source(codes, width), f"{width}x{nrows}"))
        return lines
    slack = _slacks(rows) if name in _POINTERS else None
    if name == "create":
        widths = _CREATE_WIDTH
    elif name == "job":
        widths = _JOB_WIDTH
    elif name == "bark":
        widths = _BARK_WIDTH
    else:
        widths = None
    if widths is not None and len(widths) != len(rows):
        raise AssertionError(f"{name} has {len(rows)} rows")
    lines = []
    for index, (label, codes) in enumerate(rows):
        if widths is not None:
            budget = widths[index]
        elif name in _FIXED:
            budget = _FIXED[name]
        else:
            budget = len(codes)
        extra = None if slack is None else slack.get(_address(label))
        source = _row_text(codes) if name in _LINE else unwrap(codes)
        lines.append(_entry(label, source, budget, extra))
    return lines


_SLASH = {"menu", "shop", "debug", "inn"}


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
    return bool(codes) and codes[0] == 0 and codes[-1] == 0 and any(code not in (0, 0xFFFE) for code in codes)


# Race names are three cells. Two-kana rows started at x+8, four-kana rows at x+0.
# The short-name branch now always runs, so every row starts at x+8.
_RACE_X = {
    0x14D654: (0x14400004, 0x10000004),
}
_RACE_LABELS = {
    ("menu", "15"), ("menu", "16"), ("menu", "17"), ("menu", "18"), ("menu", "19"),
    ("create", "589FB0"), ("create", "589FB8"), ("create", "589FC0"),
    ("create", "589FC8"), ("create", "589FD0"),
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
        chunk = codes[index * width:(index + 1) * width]
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
        raise ValueError(f"{len(fresh)} new glyphs do not fit in {FIRST_CODE:#x}-{LAST_CODE:#x}")
    table = dict(known)
    for index, char in enumerate(fresh):
        table[char] = FIRST_CODE + index
    return table, {char: table[char] for char in fresh}


def _grid(elf, va, width, rows):
    return [_half(elf, va + index * 2) for index in range(width * rows)]


def _slot(ident, va, mode, raw, width, slack=None, rows=1, room=None, slash=False, ffff=False, kind=None):
    return {"id": ident, "va": va, "mode": mode, "raw": raw, "width": width, "slack": slack,
            "rows": rows, "room": room, "slash": slash, "ffff": ffff, "type": kind}


def _spec(slot):
    if slot["mode"] == "flow":
        return f"{slot['width']}x{slot['rows']}"
    if slot["mode"] == "ptr" and slot["slack"] is not None:
        return f"{slot['width']} +{slot['slack']}"
    return str(slot["width"])


def _visible(text, slot):
    return text.replace("/", "") if slot.get("type") == "grid" else text


def _fit(text, slot, table):
    if slot.get("type") == "grid":
        return _fit_grid(text, slot, table)
    if slot["mode"] == "flow":
        return _wrap(_encode(text, table, False), slot["width"], slot["rows"])
    if slot["mode"] == "span":
        body = _layout_line(text, slot["raw"], slot["width"], 0, table, False)
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
    return _layout_line(text, slot["raw"], slot["width"], slot["slack"], table, slot["slash"],
                        center=slot.get("type") != "line")


def _ptr_slots(rows, slash):
    slacks = _slacks(rows)
    slots = []
    for label, codes in rows:
        va = _address(label)
        slots.append(_slot(_label(label), va, "ptr", codes, len(codes), slacks.get(va), slash=slash, ffff=True))
    return slots


def _line_slots(elf, rows, widths):
    slots = []
    for index, (label, _codes) in enumerate(rows):
        va = _address(label)
        width = widths[index] if not isinstance(widths, int) else widths
        slots.append(_slot(_label(label), va, "line", _grid(elf, va, width, 1), width, kind="line"))
    return slots


def _flow_slots(rows, width, nrows):
    return [_slot(_label(label), _address(label), "flow", [], width, rows=nrows, kind="grid")
            for label, _codes in rows]


def _kept_spans(elf, va, limit=3):
    found, current, start = [], [], None
    for index in range(32):
        value = _half(elf, va + index * 2)
        at = va + index * 2
        if value in (0, 0xFFFF):
            if current:
                found.append((start, current, at))
                current, start = [], None
        elif value == 0xFFFE or value < 0x400:
            if start is None:
                start = at
            current.append(value)
        elif current:
            found.append((start, current, at))
            current, start = [], None
    if current:
        found.append((start, current, va + 64))
    kept = []
    for start, raw, term in found:
        stripped = _strip_pad(list(raw))
        if _text_run(stripped):
            kept.append((start, stripped, raw, term))
        if len(kept) == limit:
            break
    return kept


def _name_slots(elf):
    slots = []
    for va in range(0x4A4000, 0x4B0140, 0x40):
        for index, (start, stripped, raw, term) in enumerate(_kept_spans(elf, va)):
            ident = f"{va:06X}" if index == 0 else f"{va:06X}.{index + 1}"
            slots.append(_slot(ident, start, "span", stripped, len(stripped), room=(term - start) // 2))
            if slots[-1]["room"] != len(raw):
                raise AssertionError(f"{ident} room")
    return slots


def _monster_slots(elf):
    slots = []
    for va in range(0x4F3400, 0x4F5400, 0x40):
        if _half(elf, va) == 0:
            continue
        raw, term, bad = [], None, False
        for index in range(20):
            code = _half(elf, va + index * 2)
            if code == 0:
                term = va + index * 2
                break
            if code >= 0x400:
                bad = True
                break
            raw.append(code)
        else:
            term = va + 40
        stripped = _strip_pad(list(raw))
        if bad or term is None or not _text_run(stripped):
            continue
        slots.append(_slot(f"{va:06X}", va, "span", stripped, len(stripped), room=(term - va) // 2))
    return slots


def _packed_slots(elf, start, end):
    slots = []
    va = start
    while va < end:
        value = _half(elf, va)
        if value in (0, 0xFFFF) or value >= 0x400:
            va += 2
            continue
        raw, at, ok = [], va, True
        while at < end:
            code = _half(elf, at)
            if code in (0, 0xFFFF):
                break
            if code == 0xFFFE or code < 0x400:
                raw.append(code)
                at += 2
                continue
            ok = False
            break
        stripped = _strip_pad(list(raw))
        if ok and _text_run(stripped) and _half(elf, at) in (0, 0xFFFF):
            slots.append(_slot(f"{va:06X}", va, "span", stripped, len(stripped), room=len(raw)))
            va = at + 2
        else:
            va += 2
    return slots


def _paper_slots(rows):
    slots = []
    for group in _paper_groups(rows):
        va = group[0][0]
        for index, (row_va, _codes) in enumerate(group):
            if row_va != va + index * 0x26:
                raise AssertionError(f"paper row {row_va:#x} is not contiguous")
        slots.append(_slot(f"{va:06X}", va, "flow", [], 19, rows=len(group), kind="grid"))
    return slots


def _section_slots(elf, name, rows):
    if name in _POINTERS:
        return _ptr_slots(rows, name in _SLASH)
    if name == "operations":
        return []
    if name == "save":
        return _flow_slots(rows, 28, 4)
    if name == "personality":
        return _flow_slots(rows, 11, 3)
    if name == "paper":
        return _paper_slots(rows)
    if name == "files":
        return _line_slots(elf, rows, 9)
    if name == "quest":
        return _line_slots(elf, rows, 28)
    if name == "town":
        return _line_slots(elf, rows, 54)
    if name == "create":
        return _line_slots(elf, rows, _CREATE_WIDTH)
    if name == "job":
        return _line_slots(elf, rows, _JOB_WIDTH)
    if name == "race" or name == "align":
        return _flow_slots(rows, 12, 7)
    if name == "class":
        return _flow_slots(rows, 11, 3)
    if name == "stat":
        return _flow_slots(rows, 12, 3)
    if name == "link":
        return _flow_slots(rows, 15, 3)
    if name == "rank":
        return _line_slots(elf, rows, 19)
    if name == "gear":
        # 0x10 in the first cell moves the label one cell right. The SP row stays at 0.
        slots = _line_slots(elf, rows, 6)
        for slot in slots:
            slot["keep"] = True
        return slots
    if name == "bark":
        return _line_slots(elf, rows, _BARK_WIDTH)
    if name == "lift":
        return _packed_slots(elf, 0x5525C0, 0x552650)
    if name == "names":
        return _name_slots(elf)
    if name == "monsters":
        return _monster_slots(elf)
    if name == "blurbs":
        return _packed_slots(elf, 0x4B0140, 0x4C0500)
    if name == "places":
        slots = []
        for start, end in (
            (0x526C00, 0x527400), (0x529C20, 0x52A000),
            (0x567F90, 0x568400), (0x569940, 0x56AF00),
            (0x4C1200, 0x4C2400),
        ):
            slots.extend(_packed_slots(elf, start, end))
        return slots
    raise AssertionError(name)


def _line_parts(line):
    left, spec = line.rsplit("  width=", 1)
    ident, source = left.split("  ", 1)
    return ident, source, spec


def _translation(row, name, ident):
    """Return the translation, or None only when Japanese is intentionally kept."""
    keep = row.get("keep_original", False)
    if not isinstance(keep, bool):
        raise ValueError(f"{name} {ident} keep_original is {keep!r}, expected true or false")
    text = row.get("text")
    if keep:
        if text not in (None, ""):
            raise ValueError(f"{name} {ident} keeps Japanese but also has text {text!r}")
        return None
    if text is None or text == "":
        raise ValueError(f"{name} {ident} has no translation; use keep_original true to keep Japanese")
    if not isinstance(text, str):
        raise ValueError(f"{name} {ident} text is {text!r}, expected a string")
    return text


def ui_patches(elf):
    """ELF writes for data/interface, plus glyphs on the new UI pages."""
    docs = {path.stem: json.loads(path.read_text(encoding="utf-8"))
            for path in sorted((DATA / "interface").glob("*.json"))}
    known = {}
    pending = []
    chars = []
    kept_original = 0
    for name, _title, rows in collect(elf):
        lines = manuscript_lines(name, rows)
        slots = _section_slots(elf, name, rows)
        doc = docs.get(name)
        if doc is None or len(doc) != len(lines):
            raise ValueError(f"{name} translations do not match the disc")
        if len(slots) != len(lines):
            raise AssertionError(f"{name} has {len(slots)} slots, {len(lines)} lines")
        for index, (row, line) in enumerate(zip(doc, lines)):
            ident, source, spec = _line_parts(line)
            if row.get("id") != ident or row.get("source") != source:
                raise ValueError(f"{name} {ident} does not match the disc")
            slot = slots[index]
            if slot["id"] != ident or _spec(slot) != spec:
                raise AssertionError(f"{name} {ident} slot {slot['id']} spec {_spec(slot)} != {spec}")
            if slot.get("type"):
                if row.get("type") != slot["type"] or row.get("width") != spec:
                    raise ValueError(
                        f"{name} {ident} type {row.get('type')} {row.get('width')} != {slot['type']} {spec}")
            elif row.get("type") or row.get("width"):
                raise ValueError(f"{name} {ident} has type {row.get('type')} {row.get('width')}")
            text = _translation(row, name, ident)
            if text is None:
                kept_original += 1
                continue
            pending.append((name, ident, text, slot))
            chars.extend(_glyphs(_visible(text, slot), slot["slash"]))
    table, extra = _assign(chars, known)
    rev = {}
    for char, code in table.items():
        if code in rev and rev[code] != char:
            raise ValueError(f"{char} and {rev[code]} share cell {code:#x}")
        rev[code] = char
    cells = {}
    for name, ident, text, slot in pending:
        try:
            codes = _fit(text, slot, table)
        except ValueError as exc:
            raise ValueError(f"{name} {ident}: {exc}") from exc
        if (name, ident.split()[0]) in _RACE_LABELS:
            width = len(codes)
            while width and codes[width - 1] == 0:
                width -= 1
            if width != 3:
                raise ValueError(f"{name} {ident} is {width} cells")
        shown = _visible(text, slot)
        written = codes[1:] if slot.get("keep") else codes
        if _glyphs(shown, slot["slash"]) != [rev[code] for code in written if code not in (0, 0xFFFE, 0xFFFD)]:
            raise ValueError(f"{name} {ident} glyphs do not match")
        if written.count(0xFFFE) != sum(1 for kind, _item in _tokens(shown, slot["slash"]) if kind == "nl"):
            raise ValueError(f"{name} {ident} slashes do not match")
        if slot["mode"] != "flow" and not _layout_ok(_encode(shown, table, slot["slash"]), written):
            raise ValueError(f"{name} {ident} layout does not match")
        packed = ([*codes, 0xFFFF] if slot["ffff"] else codes)
        for index, code in enumerate(packed):
            va = slot["va"] + index * 2
            old = cells.get(va)
            if old is None or old == code:
                cells[va] = code
            elif old == 0 or code == 0:
                # Padding yields. The job labels start after the third parchment page.
                cells[va] = old or code
            else:
                raise ValueError(f"{name} {ident} overlaps a different glyph at {va:#x}")
            if code not in (0, 0xFFFE) and cells[va] != code:
                raise ValueError(f"{name} {ident} lost a glyph at {va:#x}")
    writes = {}
    ordered = sorted(cells)
    start = prev = None
    buf = []
    for va in ordered:
        if prev is not None and va != prev + 2:
            writes[start - FILE_BASE] = struct.pack(f"<{len(buf)}H", *buf)
            buf = []
            start = None
        if start is None:
            start = va
        buf.append(cells[va])
        prev = va
    if buf:
        writes[start - FILE_BASE] = struct.pack(f"<{len(buf)}H", *buf)
    for va in PAPER_PITCH:
        off = va - FILE_BASE
        if struct.unpack_from("<I", elf, off)[0] != PAPER_PITCH_OLD or off in writes:
            raise AssertionError(f"paper pitch at {va:#x}")
        writes[off] = struct.pack("<I", PAPER_PITCH_NEW)
    for va, (old, new) in _RACE_X.items():
        off = va - FILE_BASE
        if struct.unpack_from("<I", elf, off)[0] != old or off in writes:
            raise AssertionError(f"race list x at {va:#x}")
        writes[off] = struct.pack("<I", new)
    for va, (old, new) in _STAT_X.items():
        off = va - FILE_BASE
        if struct.unpack_from("<I", elf, off)[0] != old or off in writes:
            raise AssertionError(f"stat name x at {va:#x}")
        writes[off] = struct.pack("<I", new)
    for va, (old, new) in _CREATE_STAT_X.items():
        off = va - FILE_BASE
        if struct.unpack_from("<I", elf, off)[0] != old or off in writes:
            raise AssertionError(f"create stat x at {va:#x}")
        writes[off] = struct.pack("<I", new)
    for va, (old, new) in _POINT_STAT_X.items():
        off = va - FILE_BASE
        if struct.unpack_from("<I", elf, off)[0] != old or off in writes:
            raise AssertionError(f"point stat x at {va:#x}")
        writes[off] = struct.pack("<I", new)
    info = {
        "strings": len(pending),
        "skipped": 0,
        "kept_original": kept_original,
        "glyphs": len(extra),
    }
    return writes, extra, info


def check_ui_text_report(info):
    """The report may list intentional keeps, but never an implicit skipped string."""
    try:
        strings, skipped, kept, glyphs = (
            info["strings"], info["skipped"], info["kept_original"], info["glyphs"])
    except (KeyError, TypeError) as exc:
        raise ValueError(f"ui text report is incomplete: {info!r}") from exc
    if not all(isinstance(value, int) and value >= 0
               for value in (strings, skipped, kept, glyphs)):
        raise ValueError(f"ui text report has invalid counts: {info!r}")
    if skipped:
        raise ValueError(f"ui text report skipped {skipped} strings")


def dump_ui(out_dir=None):
    out_dir = out_dir or (BUILD / "text" / "ui")
    out_dir.mkdir(parents=True, exist_ok=True)
    lines = [f"{code:04X}  {UI[code]}" for code in sorted(UI)]
    (out_dir / "codes.txt").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    for name, title, rows in collect(load_elf()):
        body = [title]
        body.extend(f"{label if isinstance(label, str) else f'{label:06X}'}  {render(codes)}" for label, codes in rows)
        (out_dir / f"{name}.txt").write_text("\n".join(body) + "\n", encoding="utf-8", newline="\n")
    return out_dir


def dump_manuscript(out_dir=None):
    out_dir = out_dir or (BUILD / "text" / "ui-src")
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, title, rows in collect(load_elf()):
        body = [title, _NOTE, *manuscript_lines(name, rows)]
        (out_dir / f"{name}.txt").write_text("\n".join(body) + "\n", encoding="utf-8", newline="\n")
    return out_dir


def self_test():
    assert set(UI) == set(range(0x400)) - set(BLANKS)
    assert all(len(char) == 1 for char in UI.values())
    assert ui_char(0x070) == "あ" and ui_char(0x112) == "戦" and ui_char(0x223) == "名"
    assert ui_char(0x3B0) == "練" and ui_char(0x3A8) == "端" and ui_char(0x17E) == "隊"
    assert ui_char(0x15F) == "闘" and ui_char(0x1B8) == "間"
    assert ui_char(0x1DF) == "塀" and ui_char(0x1E8) == "窒" and ui_char(0x1ED) == "爆"
    assert ui_char(0x232) == "違" and ui_char(0x271) == "夫"
    assert ui_char(0x1E6) == "塵" and ui_char(0x285) == "雑" and ui_char(0x29D) == "牽"
    assert ui_char(0x2E2) == "事" and ui_char(0x2E4) == "費"
    assert ui_char(0x24D) == "眺" and ui_char(0x29F) == "援" and ui_char(0x2A1) == "斉"
    assert ui_char(0x30A) == "嘆" and ui_char(0x35D) == "接"
    assert ui_char(0x35F) == "軽" and ui_char(0x365) == "囮" and ui_char(0x367) == "聖"
    assert ui_char(0x372) == "歓" and ui_char(0x3A4) == "界" and ui_char(0x1D1) == "異"
    assert ui_char(0x3B7) == "桶" and ui_char(0x3BA) == "棚" and ui_char(0x3C1) == "敷"
    assert ui_char(0x3CE) == "犠" and ui_char(0x3CF) == "牲" and ui_char(0x3D0) == "実"
    assert ui_char(0x3EE) == "類"
    assert render([0x223, 0x13E, 0x89, 0x1F]) == "名前は?"
    assert unwrap([0, 0x223, 0x13E]) == " 名前"
    assert unwrap([0x223, 0]) == "名 "
    assert unwrap([0x223, 0], flow=True) == "名"
    assert unwrap([0x223, 0, 0x13E]) == "名 前"
    assert unwrap([0x112, 0, 0, 0, 0x112]) == "戦{3}戦"
    assert unwrap([0] * 10 + [0x112]) == "{10}戦"
    assert unwrap([0x112, 0x112, 0, 0x112, 0x112]) == "戦戦 戦戦"
    assert unwrap([0x112, 0xFFFE, 0, 0x112], flow=True) == "戦 戦"
    assert unwrap([0x112, 0xFFFE, 0, 0x112]) == "戦/ 戦"
    assert _row_text([0, 0x223, 0]) == " 名 "
    assert _row_text([0, 0, 0]) == ""
    assert _grid_source([0x112, 0, 0, 0, 0, 0, 0, 0, 0], 3) == "戦{2}//"
    table = {"甲": 5, "乙": 6, "丙": 7, "/": 9}
    assert _encode("甲/乙", table, True) == [5, 0xFFFE, 6]
    assert _encode("{2}甲", table, False) == [0, 0, 5]
    assert _layout_line("甲 乙", [1, 0, 2], 3, 0, table, False) == [5, 0, 6]
    assert _layout_line("甲乙", [1, 0, 2], 3, 0, table, False) == [5, 6, 0]
    assert _layout_line("甲", [0, 1, 0], 3, 0, table, False, center=False) == [5, 0, 0]
    assert _layout_line("甲", [0, 1, 0], 3, 0, table, False) == [0, 5, 0]
    grid = _slot("x", 0, "flow", [], 3, rows=2, kind="grid")
    assert _fit("甲/乙", grid, table) == [5, 0, 0, 6, 0, 0]
    assert _fit("甲乙", grid, table) == [5, 6, 0, 0, 0, 0]
    try:
        _fit("甲乙丙甲/乙", grid, table)
        raise AssertionError("a grid line can be longer than its width")
    except ValueError:
        pass
    assert _layout_line("甲乙", [0, 1, 0], 4, 0, table, False) == [0, 5, 6, 0]
    assert _layout_line("甲乙", [1, 2], 4, 0, table, False) == [5, 6, 0, 0]
    assert _layout_line("甲乙丙", [1], 1, 2, table, False) == [5, 6, 7]
    assert _wrap([5, 6, 7], 2, 2) == [5, 6, 7, 0]
    assert _layout_ok([5, 0, 6], [5, 0, 6, 0]) and _layout_ok([0, 0, 5], [0, 0, 5])
    assert not _layout_ok([5, 6], [5, 0, 6, 0])
    assert not _layout_ok([5, 0, 0, 6], [5, 6])
    gear = _slot("g", 0x584308, "line", [0x10, 1, 0, 2, 0, 0], 6, kind="line")
    gear["keep"] = True
    assert _fit("甲 乙", gear, {"甲": 5, "乙": 6}) == [0x10, 5, 0, 6, 0, 0]
    table, extra = _assign(["菜", "单"], {})
    assert table == extra == {"单": 0x0401, "菜": 0x0402}
    assert _translation({"text": "姓名"}, "menu", "00") == "姓名"
    assert _translation({"text": None, "keep_original": True}, "menu", "00") is None
    for row in ({"text": None}, {"text": ""}, {"text": None, "keep_original": False},
                {"text": "姓名", "keep_original": True}):
        try:
            _translation(row, "menu", "00")
        except ValueError:
            pass
        else:
            raise AssertionError(f"bad translation row was accepted: {row!r}")
    check_ui_text_report({"strings": 1, "skipped": 0, "kept_original": 0, "glyphs": 1})
    try:
        check_ui_text_report({"strings": 1, "skipped": 1, "kept_original": 0, "glyphs": 1})
    except ValueError:
        pass
    else:
        raise AssertionError("a skipped ui string was accepted")


def _span(writes, va):
    off = va - FILE_BASE
    for start, data in writes.items():
        if start <= off < start + len(data):
            return data[off - start:]
    raise AssertionError(f"{va:#x} was not written")


def disc_self_test():
    sections = {name: rows for name, _title, rows in collect(load_elf())}
    expect = {
        "menu": 169, "controller": 4, "save": 32, "files": 40, "paper": 18, "job": 3,
        "create": 47, "personality": 15, "quest": 3, "town": 7, "shop": 158,
        "names": 734, "monsters": 127, "blurbs": 684, "places": 301, "debug": 132,
        "race": 5, "align": 3, "class": 8, "stat": 6, "rank": 5, "gear": 7,
        "inn": 41, "room": 24, "ask": 34, "lift": 6, "bark": 4, "link": 30,
    }
    got = {name: len(rows) for name, rows in sections.items() if name in expect}
    assert got == expect, got
    text = {name: "\n".join(render(codes) for _label, codes in rows) for name, rows in sections.items()}
    assert "FILE00(W)" in text["files"] and "酒場" in text["town"]
    assert text["shop"].startswith("いらっしゃい") and "転職必要条件" in text["job"]
    assert "好戦的" in text["create"] and "正義感" in text["create"]
    assert "強いモンスター" in text["personality"]
    assert "依頼を受けますか?" in text["quest"] and "リーダーフラグ" in text["debug"]
    assert "メモリーカード" in text["save"]
    assert "平均的なパラメーター" in text["race"] and "属性が変わる" in text["align"]
    assert "武具のエキスパート" in text["class"] and "ダメージに影響" in text["stat"]
    assert "パーティランク" in text["rank"] and "SPの解放" in text["gear"]
    assert "冒険者の宿" in text["inn"] and text["inn"].count("{d}") == 5 + 11 + 11 + 11
    assert "ヘルガの部屋" in text["room"] and "開けてみますか?" in text["ask"]
    assert "地下牢第1層" in text["lift"] and "オスカー" in text["bark"]
    assert "前衛と後衛が一斉に入れ替わる" in text["link"]
    joined = "\n".join(text.values())
    assert "バブリースライム" in joined and "練習" in joined and "[" not in joined
    sheets = {name: manuscript_lines(name, rows) for name, rows in sections.items()}

    def column(line):
        return line.split("  ", 1)[1].rsplit("  width=", 1)[0]

    menu = [column(line) for line in sheets["menu"]]
    budgets = [line.rsplit("  width=", 1)[1] for line in sheets["menu"]]
    assert " 転{3}職" in menu and " 名前の変更" in menu and "人 間" in menu and "人間" not in menu
    assert budgets[menu.index(" 転{3}職")] == "6 +1"
    slacks = [int(item.split("+", 1)[1]) for item in budgets if "+" in item]
    assert slacks.count(0) == 54 and max(slacks) == 3 and budgets.count("6 +1") >= 1
    assert sum("+" not in item for item in budgets) == 1
    assert "  " not in "\n".join(menu)
    create = [column(line) for line in sheets["create"]]
    assert "戦 士" in create and "知 恵" in create and " 力 " in create
    assert sheets["create"][create.index("戦 士")].endswith("width=3")
    assert "好戦的{2}" in create
    shop = "\n".join(column(line) for line in sheets["shop"])
    assert "{10}の魔法を習得した!" in shop and "短 刀" in shop and "  " not in shop
    save = "\n".join(column(line) for line in sheets["save"])
    assert "BUSINのセーブデータをロードしますか?" in save
    assert "プレイステーション 2" in save and "専用メモリーカード" in save
    assert all(column(line).count("/") == 3 for line in sheets["save"])
    paper = [column(line) for line in sheets["paper"]]
    assert paper[0] == (
        "{6}種族とは{9}/この世界には、五つの種族が存在します。/"
        "{3}力にひいでたドワーフや{5}/{3}魔法にひいでたエルフなど{4}/"
        " 各種族により得意な事が異なります。 /"
    )
    assert paper[1] == (
        "{6}属性とは{9}/この世界に生きる者達の行動理念です。 /"
        "{3}主に三つの属性に分けられ{4}/ 相反する属性の者とは仲が悪いと{3}/"
        "{5}言われています。{6}/"
    )
    assert paper[2] == (
        "{5}冒険者達の職業{7}/{2}冒険者達は、ひめられた能力を{3}/"
        "{2}ひきだし、各職業につきます。{3}/ 剣をにぎる者、魔法をあやつる者など /"
        "{2}職種は様々で、選んだ職業により{2}/{4}戦法が異なります。{6}"
    )
    assert [line.split("  ", 1)[0] for line in sheets["paper"]] == ["445810", "4458F8", "4459E0"]
    assert [line.rsplit("width=", 1)[1] for line in sheets["paper"]] == ["19x6", "19x6", "19x6"]
    elf = load_elf()
    shared_labels = {
        "名前は?": "姓名？",
        "名 前": "姓 名",
        "種 族": "种 族",
        "属 性": "阵 营",
        "職 業": "职 业",
        "レベル": "等 级",
        "性 格": "性 格",
    }
    menu_doc = json.loads((DATA / "interface" / "menu.json").read_text(encoding="utf-8"))
    menu_slots = {
        slot["id"]: slot
        for slot in _section_slots(elf, "menu", sections["menu"])
    }
    shared_ids = {"07", "2A", "2B", "2C", "2D", "2E", "2F"}
    shared = [
        (row, menu_slots[row["id"]])
        for row in menu_doc
        if row["source"] in shared_labels and row["id"].split()[0] in shared_ids
    ]
    if len(shared) != len(shared_labels):
        raise AssertionError(f"shared labels: {[(row['id'], row['source']) for row, _slot in shared]}")
    for row, _slot in shared:
        if row["text"] != shared_labels[row["source"]]:
            raise AssertionError(f"shared label {row['id']} is {row['text']!r}")
    assert _word(elf, 0x14E780) == 0x24425810 and _word(elf, 0x14E820) == 0x244258F8
    assert _word(elf, 0x14E8C0) == 0x244259E0
    if any(_word(elf, va) != 0x28820006 for va in (0x14E7EC, 0x14E88C, 0x14E928)):
        raise AssertionError("paper row count")
    if PAPER_PAGES[2] + PAPER_ROWS * 0x26 > 0x445AC8:
        raise AssertionError("paper job page overlaps the class-change labels")
    person = column(sheets["personality"][0])
    assert person == "強いモンスターと戦うの/が大好き。弱いやつと戦/うの大嫌い。{5}"
    assert all(column(line).count("/") == 2 for line in sheets["personality"])
    town = column(sheets["town"][0])
    assert "酒場" in town and "。 " in town and sheets["town"][0].endswith("width=54")
    debug = "\n".join(column(line) for line in sheets["debug"])
    assert "あれいど({9})" in debug and "PARTY ALL" in debug
    assert all("[" not in column(line) for lines in sheets.values() for line in lines)
    from busin.patch.ui_pages import FIRST_CODE, LAST_CODE
    from busin.ui_font import ui_font_pages
    writes, extra, info = ui_patches(load_elf())
    pitch = {va - FILE_BASE for va in PAPER_PITCH}
    race_x = {va - FILE_BASE for va in _RACE_X}
    stat_x = {va - FILE_BASE for va in _STAT_X}
    create_stat_x = {va - FILE_BASE for va in _CREATE_STAT_X}
    point_stat_x = {va - FILE_BASE for va in _POINT_STAT_X}
    if any(writes.get(off) != struct.pack("<I", PAPER_PITCH_NEW) for off in pitch):
        raise AssertionError("paper line pitch was not written")
    for va, (_old, new) in _RACE_X.items():
        if writes.get(va - FILE_BASE) != struct.pack("<I", new):
            raise AssertionError(f"race list x at {va:#x}")
    for va, (_old, new) in _STAT_X.items():
        if writes.get(va - FILE_BASE) != struct.pack("<I", new):
            raise AssertionError(f"stat name x at {va:#x}")
    for va, (_old, new) in _CREATE_STAT_X.items():
        if writes.get(va - FILE_BASE) != struct.pack("<I", new):
            raise AssertionError(f"create stat x at {va:#x}")
    for va, (_old, new) in _POINT_STAT_X.items():
        if writes.get(va - FILE_BASE) != struct.pack("<I", new):
            raise AssertionError(f"point stat x at {va:#x}")
    stat_names = (
        list(range(0x43DDC0, 0x43DDF0, 8))
        + list(range(0x589F60, 0x589F84, 6))
        + list(range(0x4448D0, 0x444900, 8))
        + list(range(0x486F48, 0x486F70, 8))
        + [0x486F90]
    )
    for va in stat_names:
        codes = struct.unpack_from("<3H", _span(writes, va))
        if codes[1] or not codes[0] or not codes[2]:
            raise AssertionError(f"stat name gap at {va:#x}")
    # Parchment rows are grouped into three pages before they are written.
    check_ui_text_report(info)
    written = sum(expect.values()) - info["kept_original"] - (expect["paper"] - 3)
    if info["strings"] != written:
        raise AssertionError(info)
    for row, slot in shared:
        count = len(slot["raw"]) + (1 if slot["ffff"] else 0)
        codes = struct.unpack_from(f"<{count}H", _span(writes, slot["va"]))
        if len(codes) != count or codes[-1] != 0xFFFF or 0xFFFE in codes:
            raise AssertionError(f"shared label {row['id']} changed width, wrapping, or terminator")
        if any(code not in (0, 0xFFFD) and not FIRST_CODE <= code <= LAST_CODE
               for code in codes[:-1]):
            raise AssertionError(f"shared label {row['id']} uses an original UI cell")
    if not extra or max(extra.values()) > LAST_CODE or min(extra.values()) < FIRST_CODE:
        raise AssertionError(info)
    ui_font_pages(extra)
    for off, data in writes.items():
        if off in pitch or off in race_x or off in stat_x or off in create_stat_x or off in point_stat_x:
            continue
        codes = struct.unpack(f"<{len(data) // 2}H", data)
        for index, code in enumerate(codes):
            if code in (0, 0xFFFE, 0xFFFD) or code >= FIRST_CODE:
                continue
            va = off + FILE_BASE + index * 2
            if code == 0x0010 and va in _GEAR_NUDGE:
                continue
            raise AssertionError("ui text uses an original UI cell")
    for index in range(7):
        va = 0x584308 + index * 12
        nudge = struct.unpack_from("<H", _span(writes, va))[0]
        if nudge != (0 if index == 4 else 0x0010):
            raise AssertionError(f"gear nudge at {va:#x}")
