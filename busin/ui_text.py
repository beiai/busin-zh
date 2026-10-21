"""Small-font readings and a text dump of the static 16×16 strings."""
import json
import struct
from dataclasses import dataclass

from busin.disc import FILE_BASE
from busin.disasm import load_elf
from busin.paths import BUILD, DATA
from busin.runtime_names import load_names
from busin.ui_layout import (
    _RACE_LABELS, _RACE_X, _STAT_X, _CREATE_STAT_X, _POINT_STAT_X, _GUILD_MENU_X, _address,
    _assign, _encode, _fit, _fit_grid, _glyphs, _label, _layout_line,
    _layout_ok, _slacks, _tokens, _visible, _wrap,
)
from busin.ui_records import (
    BLURB_ROWS as _BLURB_ROWS, BLURB_WIDTH as _BLURB_WIDTH, NAME_DISPLAY_CELLS as _NAME_DISPLAY_CELLS, NAME_END,
    PLACE_POINTER_RANGES as _PLACE_POINTER_RANGES, blurb_record as _blurb_record,
    blurb_slots as _blurb_slots, grid as _grid, half as _half,
    monster_slots as _monster_slots, name_slots as _name_slots,
    packed_slots as _packed_slots, place_slots as _place_slots,
    read_ffff as _read_ffff, slot as _slot, strip_pad as _strip_pad,
    text_run as _text_run, word as _word,
)

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
_GEAR_NUDGE = {0x584308 + index * 12 for index in range(8) if index != 4}

STRING_TABLE = 0x43E9C8

DIRECT_UI_TEXT = (
    {
        "section": "ask",
        "id": "552430",
        "va": 0x552430,
        "source": "街に戻りますか?",
        "max_cells": 11,
        "next_data": 0x552448,
        "guards": (
            (0x388F54, 0x3C020055),
            (0x388F5C, 0x84442430),
            (0x388F60, 0x24522430),
            (0x388F6C, 0x94462430),
            (0x389014, 0x0C042252),
            (0x3890F0, 0x0C042252),
        ),
    },
    {
        "section": "ask",
        "id": "567F78",
        "va": 0x567F78,
        "source": "宝箱を開けますか?",
        "max_cells": 11,
        "next_data": 0x567F90,
        "guards": (
            (0x3C7E24, 0x86A37F78),
            (0x3C7E38, 0x26A57F78),
            (0x3C81E4, 0x0C042252),
        ),
    },
    {
        "section": "debug",
        "id": "569910",
        "va": 0x569910,
        "source": "死神{2}ON",
        "max_cells": 7,
        "next_data": 0x569920,
        "guards": (
            (0x3DAAC0, 0x3C020057),
            (0x3DAAC8, 0x24519910),
            (0x3DAB10, 0x0C042252),
        ),
    },
    {
        "section": "debug",
        "id": "569920",
        "va": 0x569920,
        "source": "死神{2}OFF",
        "max_cells": 7,
        "next_data": 0x569930,
        "guards": (
            (0x3DAACC, 0x3C020057),
            (0x3DAAD0, 0x24519920),
            (0x3DAB10, 0x0C042252),
        ),
    },
    {
        "section": "menu",
        "id": "59EB38",
        "va": 0x59EB38,
        "source": "タイトルへ戻ります。よろしいですか?",
        "max_cells": 19,
        "next_data": 0x59EB60,
        "guards": (
            (0x3E1268, 0x2447EB38),
            (0x3E1440, 0x0C042252),
        ),
    },
)


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


DIRECT_SECTIONS = ("menu", "debug", "ask")


def _direct_ui_by_section():
    out = {name: [] for name in DIRECT_SECTIONS}
    for item in DIRECT_UI_TEXT:
        out[item["section"]].append(item)
    return out


def _with_direct(section, rows, elf, direct_first=False):
    direct = [
        (item["id"], _read_ffff(elf, item["va"]))
        for item in _direct_ui_by_section()[section]
    ]
    return direct + rows if direct_first else rows + direct


def _mixed_slacks(rows):
    direct = {item["id"]: item for item in DIRECT_UI_TEXT}
    pointer_rows = [row for row in rows if " " in _label(row[0])]
    slack = _slacks(pointer_rows)
    for label, codes in rows:
        ident = _label(label)
        if ident in direct:
            slack[_address(label)] = direct[ident]["max_cells"] - len(codes)
    return slack


def _mixed_slots(elf, rows, slash):
    direct = {item["id"]: item for item in DIRECT_UI_TEXT}
    pointer_rows = [row for row in rows if " " in _label(row[0])]
    pointer_slots = {slot["id"]: slot for slot in _ptr_slots(pointer_rows, slash)}
    slots = []
    for label, codes in rows:
        ident = _label(label)
        if ident in direct:
            item = direct[ident]
            slots.append(_slot(
                ident, item["va"], "direct", codes, len(codes),
                slack=item["max_cells"] - len(codes), slash=slash, ffff=True,
            ))
        else:
            slots.append(pointer_slots[ident])
    return slots


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
    return [(slot["id"], slot["raw"]) for slot in _packed_slots(elf, start, end)]


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
    for va in range(0x4A4000, NAME_END, 0x40):
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

    places = [(slot["id"], slot["raw"]) for slot in _place_slots(elf)]

    return (
        ("menu", "菜单", _with_direct("menu", menu, elf)),
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
        ("blurbs", "效果说明", _blurb_readings(elf)),
        ("places", "迷宫、城镇、委托", places),
        ("debug", "调试菜单", _with_direct("debug", _pointers(elf, 0x487198, 0x486000, 0x488000), elf)),
        ("race", "种族说明", _grid_rows(elf, 0x581AC0, 0xAA, 5, 12, 7, skip=1)),
        ("align", "阵营说明", _grid_rows(elf, 0x581E18, 0xAA, 3, 12, 7, skip=1)),
        ("class", "职业说明", _grid_rows(elf, 0x582018, 0x44, 8, 11, 3, skip=1)),
        ("stat", "能力说明", _grid_rows(elf, 0x582238, 0x48, 6, 12, 3)),
        ("rank", "队伍阶级", _grid_rows(elf, 0x583BC0, 0x26, 7, 19, 1)),
        ("gear", "物品操作", _grid_rows(elf, 0x584308, 12, 8, 6, 1)),
        ("inn", "旅馆", _ptr_until(elf, 0x442C40, 0x580000, 0x582000, extra=(0xFFFD,))),
        ("room", "房间", _ptr_until(elf, 0x48367C, 0x483000, 0x484000)),
        ("ask", "迷宫确认", _with_direct("ask", _ptr_until(elf, 0x55282C, 0x59CF00, 0x59E000), elf, direct_first=True)),
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
    if name == "blurbs":
        return [
            _entry(label, _grid_source(codes, _BLURB_WIDTH), f"{_BLURB_WIDTH}x{_BLURB_ROWS}")
            if _blurb_record(_address(label)) else _entry(label, unwrap(codes), len(codes))
            for label, codes in rows
        ]
    if name in _GRID:
        width, nrows = _GRID[name]
        lines = []
        for label, codes in rows:
            if len(codes) != width * nrows:
                raise AssertionError(f"{name} {_label(label)} has {len(codes)} cells")
            lines.append(_entry(label, _grid_source(codes, width), f"{width}x{nrows}"))
        return lines
    if name in DIRECT_SECTIONS:
        slack = _mixed_slacks(rows)
    else:
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
        if name == "names":
            extra = max(0, _NAME_DISPLAY_CELLS - budget)
        else:
            extra = None if slack is None else slack.get(_address(label))
        source = _row_text(codes) if name in _LINE else unwrap(codes)
        lines.append(_entry(label, source, budget, extra))
    return lines


_SLASH = {"menu", "shop", "debug", "inn"}


def _spec(slot):
    if slot["mode"] == "flow":
        return f"{slot['width']}x{slot['rows']}"
    if slot["slack"] is not None:
        return f"{slot['width']} +{slot['slack']}"
    return str(slot["width"])


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


def _blurb_readings(elf):
    return [(slot["id"], slot["raw"]) for slot in _blurb_slots(elf)]


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
    if name in DIRECT_SECTIONS:
        return _mixed_slots(elf, rows, name in _SLASH)
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
        return _blurb_slots(elf)
    if name == "places":
        return _place_slots(elf)
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


@dataclass(frozen=True)
class PendingText:
    name: str
    ident: str
    text: str
    slot: dict


def _load_translations(elf):
    docs = {
        path.stem: json.loads(path.read_text(encoding="utf-8"))
        for path in sorted((DATA / "interface").glob("*.json"))
    }
    pending, chars = [], []
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
                raise AssertionError(
                    f"{name} {ident} slot {slot['id']} spec {_spec(slot)} != {spec}"
                )
            if slot.get("type"):
                if row.get("type") != slot["type"] or row.get("width") != spec:
                    raise ValueError(
                        f"{name} {ident} type {row.get('type')} {row.get('width')} "
                        f"!= {slot['type']} {spec}"
                    )
            elif row.get("type") or row.get("width"):
                raise ValueError(
                    f"{name} {ident} has type {row.get('type')} {row.get('width')}"
                )
            text = _translation(row, name, ident)
            if text is None:
                kept_original += 1
                continue
            pending.append(PendingText(name, ident, text, slot))
            chars.extend(_glyphs(_visible(text, slot), slot["slash"]))
    return pending, chars, kept_original


def _reverse_table(table):
    reverse = {}
    for char, code in table.items():
        if code in reverse and reverse[code] != char:
            raise ValueError(f"{char} and {reverse[code]} share cell {code:#x}")
        reverse[code] = char
    return reverse


def _record_cells(pending, table, reverse):
    cells = {}
    for item in pending:
        name, ident, text, slot = item.name, item.ident, item.text, item.slot
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
        expected = [
            reverse[code] for code in written if code not in (0, 0xFFFE, 0xFFFD)
        ]
        if _glyphs(shown, slot["slash"]) != expected:
            raise ValueError(f"{name} {ident} glyphs do not match")
        newlines = sum(kind == "nl" for kind, _item in _tokens(shown, slot["slash"]))
        if written.count(0xFFFE) != newlines:
            raise ValueError(f"{name} {ident} slashes do not match")
        if slot["mode"] != "flow" and not _layout_ok(
            _encode(shown, table, slot["slash"]), written
        ):
            raise ValueError(f"{name} {ident} layout does not match")
        packed = [*codes, 0xFFFF] if slot["ffff"] else codes
        for index, code in enumerate(packed):
            va = slot["va"] + index * 2
            if any(start <= va < end for start, end in _PLACE_POINTER_RANGES):
                raise ValueError(
                    f"{name} {ident} overwrites a place pointer at {va:#x}"
                )
            old = cells.get(va)
            if old is None or old == code:
                cells[va] = code
            elif old == 0 or code == 0:
                # Padding yields. The job labels start after the third parchment page.
                cells[va] = old or code
            else:
                raise ValueError(
                    f"{name} {ident} overlaps a different glyph at {va:#x}"
                )
            if code not in (0, 0xFFFE) and cells[va] != code:
                raise ValueError(f"{name} {ident} lost a glyph at {va:#x}")
    return cells


def _serialize_cells(cells):
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
    return writes


def _replace_word(elf, writes, va, old, new, label):
    offset = va - FILE_BASE
    if struct.unpack_from("<I", elf, offset)[0] != old or offset in writes:
        raise AssertionError(f"{label} at {va:#x}")
    writes[offset] = struct.pack("<I", new)


def _layout_patches(elf, writes):
    for va in PAPER_PITCH:
        _replace_word(elf, writes, va, PAPER_PITCH_OLD, PAPER_PITCH_NEW, "paper pitch")
    for patches, label in (
        (_RACE_X, "race list x"),
        (_STAT_X, "stat name x"),
        (_CREATE_STAT_X, "create stat x"),
        (_POINT_STAT_X, "point stat x"),
        (_GUILD_MENU_X, "guild menu x"),
    ):
        for va, (old, new) in patches.items():
            _replace_word(elf, writes, va, old, new, label)


def _ui_patches(elf):
    """ELF writes for data/interface, plus glyphs on the new UI pages."""
    check_direct_ui_text(elf)
    pending, chars, kept_original = _load_translations(elf)
    party = load_names()
    for name in party:
        chars.extend(_glyphs(name.text, False))
    table, extra = _assign(chars, {})
    reverse = _reverse_table(table)
    cells = _record_cells(pending, table, reverse)
    writes = _serialize_cells(cells)
    from busin.patch.ui_names import elf_writes as name_writes
    for offset, data in name_writes(elf, table).items():
        end = offset + len(data)
        if any(offset < other + len(blob) and other < end for other, blob in writes.items()):
            raise ValueError(f"party name write at {offset:#x} overlaps UI text")
        writes[offset] = data
    _layout_patches(elf, writes)
    info = {
        "strings": len(pending),
        "skipped": 0,
        "kept_original": kept_original,
        "glyphs": len(extra),
        "names": len(party),
    }
    return writes, extra, info, table


def ui_patches(elf):
    writes, extra, info, _table = _ui_patches(elf)
    return writes, extra, info


def ui_patches_with_table(elf):
    return _ui_patches(elf)


def check_direct_ui_text(elf):
    sections = collect(elf)
    collected = {
        name: {_label(label) for label, _codes in rows}
        for name, _title, rows in sections
    }
    for item in DIRECT_UI_TEXT:
        ident = item["id"]
        if ident not in collected.get(item["section"], set()):
            raise AssertionError(f"direct UI text {ident} is not collected")
        codes = _read_ffff(elf, item["va"], limit=item["max_cells"] + 1)
        if unwrap(codes) != item["source"]:
            raise AssertionError(f"direct UI text {ident} source changed")
        if _half(elf, item["va"] + len(codes) * 2) != 0xFFFF:
            raise AssertionError(f"direct UI text {ident} has no terminator")
        stop = item["va"] + (len(codes) + 1) * 2
        if stop > item["next_data"]:
            raise AssertionError(f"direct UI text {ident} exceeds its record")
        for at in range(stop, item["next_data"], 2):
            if _half(elf, at) != 0:
                raise AssertionError(f"direct UI text {ident} record is not clean")
        for at, expected in item["guards"]:
            if _word(elf, at) != expected:
                raise AssertionError(f"direct UI text {ident} draw reference changed")

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
        "menu": 170, "controller": 4, "save": 32, "files": 40, "paper": 18, "job": 3,
        "create": 47, "personality": 15, "quest": 3, "town": 7, "shop": 158,
        "names": 732, "monsters": 127, "blurbs": 382, "places": 314, "debug": 134,
        "race": 5, "align": 3, "class": 8, "stat": 6, "rank": 7, "gear": 8,
        "inn": 41, "room": 24, "ask": 36, "lift": 6, "bark": 4, "link": 30,
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
    assert "回復系:味方1体の毒、麻痺、気絶、石化、HPを全快\n攻撃系:敵1体を瀬死状態にさせる" in text["blurbs"]
    assert "術者の魔法の効果範囲を拡大する。回復や攻撃魔法に使用可能\nPR:絆" in text["places"]
    assert "仲間のHPを少し消費し、確実に逃走を成功させる(一部の敵は除く)" in text["places"]
    assert "vぉ" not in text["places"] and "書緊急時切" not in text["places"]
    assert "ヘルガの部屋" in text["room"] and "開けてみますか?" in text["ask"]
    assert "街に戻りますか?" in text["ask"]
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
    guild_menu_x = {va - FILE_BASE for va in _GUILD_MENU_X}
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
    for va, (_old, new) in _GUILD_MENU_X.items():
        if writes.get(va - FILE_BASE) != struct.pack("<I", new):
            raise AssertionError(f"guild menu x at {va:#x}")
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
    direct = struct.unpack("<8H", _span(writes, 0x552430))
    if len(direct) != 8 or direct[-1] != 0xFFFF or 0 in direct[:-1]:
        raise AssertionError("maze return translation is not seven cells plus terminator")
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
        if off in pitch or off in race_x or off in stat_x or off in create_stat_x or off in point_stat_x or off in guild_menu_x:
            continue
        codes = struct.unpack(f"<{len(data) // 2}H", data)
        for index, code in enumerate(codes):
            if code in (0, 0xFFFE, 0xFFFD) or code >= FIRST_CODE:
                continue
            va = off + FILE_BASE + index * 2
            if code == 0x0010 and va in _GEAR_NUDGE:
                continue
            raise AssertionError("ui text uses an original UI cell")
    for index in range(8):
        va = 0x584308 + index * 12
        nudge = struct.unpack_from("<H", _span(writes, va))[0]
        if nudge != (0 if index == 4 else 0x0010):
            raise AssertionError(f"gear nudge at {va:#x}")

