"""Decode BUSIN 1 glyph-index message streams and encode page-1 translations."""
import bisect
import json
import re
import struct
from pathlib import Path

from busin.disc import BIN, CHUNK_TABLE, ELF_LBA, ELF_SIZE, FILE_BASE, PACK_LBA, read_user
from busin.paths import BUILD, DATA

MAX_GLYPH = 0x02AB
CONTROL_START = 0xFB00
TEXT_LIMIT = 0x8000  # glyphs and page-1 tokens stay below this; 0x29D310 skips the rest
PAD = 0x4000  # page-1 cell 0, kept blank
FILL = 0xFFFC  # zero-width; opening keeps it so the counted header stays put
# Page-0 cell readings; cells missing from the table stay undecoded.
PAGE0 = {int(cell, 16): char for cell, char in json.loads((DATA / "font" / "message-page0.json").read_text(encoding="utf-8")).items()}
CONTROL_NAMES = {
    0xFFFE: "<LINE>",
    0xFFFD: "<BREAK>",
    0xFFF9: "<PAGE>",
    0xFFE0: "<STYLE:0>",
    0xFFE1: "<STYLE:1>",
    0xFFE2: "<STYLE:2>",
    0xFFE3: "<STYLE:3>",
    0xFFE4: "<STYLE:4>",
    0xFFE5: "<STYLE:5>",
    0xFFE6: "<STYLE:6>",
    0xFFE7: "<STYLE:7>",
}
TAGS = {name[1:-1]: code for code, name in CONTROL_NAMES.items()}
# Line-leading choice marks, indents and styles. Kept on the same line index.
LAYOUT = range(0xFFC0, 0xFFE8)
CENTER_LINE = 19  # centered scene text; a full line on the disc is 19 cells
NARRATION_LINE = 21  # subtitles, type == "subtitle"
DIALOG_LINE = 19  # dialog keeps the 20th cell for the trailing icon
# Chunk 272's last 40 messages are one glyph each. Chunk 281 plays them by index.
BEAT_CHUNK = 272
BEAT_SCRIPT = 281
BEAT_AT = 0x330
BEAT_COUNT = 40
BEAT_INDEX = 0x11


def glyph_char(code: int) -> str | None:
    return PAGE0.get(code)


def decode_token(code: int) -> str:
    if code in CONTROL_NAMES:
        return CONTROL_NAMES[code]
    if code >= TEXT_LIMIT:
        return f"<{code:04X}>"
    char = glyph_char(code)
    return char if char is not None else f"[{code:04X}]"


def segments(data: bytes, counted: bool = False) -> list[tuple[int, int, list[int]]]:
    base = 0
    if counted:
        if len(data) < 4 or len(data) % 2:
            raise ValueError("counted message block is incomplete")
        count = int.from_bytes(data[:2], "big")
        stop = 2 + count * 2
        if not count or stop > len(data) or data[stop - 2:stop] != b"\xff\xff" or any(data[stop:]):
            raise ValueError("invalid counted message block or nonzero padding")
        data, base = data[2:stop], 2
    words = [int.from_bytes(data[i:i + 2], "big") for i in range(0, len(data) - 1, 2)]
    result = []
    start = 0
    for index, word in enumerate(words):
        if word == 0xFFFF:
            result.append((base + start * 2, base + index * 2, words[start:index]))
            start = index + 1
    return result


def is_clean(words: list[int]) -> bool:
    return bool(words) and all(word <= MAX_GLYPH or word >= CONTROL_START for word in words)


def decode_words(words: list[int]) -> str:
    return "".join(decode_token(word) for word in words)


def describe(data: bytes, counted: bool = False) -> dict:
    messages, used, controls = [], set(), set()
    for start, end, words in segments(data, counted):
        clean = is_clean(words)
        for word in words:
            (controls if word >= CONTROL_START else used).add(word)
        messages.append({
            "offset": start,
            "end": end,
            "clean": clean,
            "tokens": [f"{word:04X}" for word in words],
            "text": decode_words(words),
        })
    used_sorted = sorted(used)
    return {
        "size": len(data),
        "terminated_segments": len(messages),
        "clean_messages": sum(item["clean"] for item in messages),
        "glyph_cap": f"{MAX_GLYPH:04X}",
        "glyph_range": [f"{used_sorted[0]:04X}", f"{used_sorted[-1]:04X}"] if used_sorted else [],
        "controls": [f"{word:04X}" for word in sorted(controls)],
        "messages": messages,
    }


def split_lines(words: list[int]) -> list[list[int]]:
    lines = [[]]
    for word in words:
        if word == 0xFFFE:
            lines.append([])
        else:
            lines[-1].append(word)
    return lines


def line_prose(words: list[int], align: str = "left") -> tuple[list[int], list[int], str]:
    """Split a line into leading layout codes, the cells after them, and their reading.

    Glyph 0 between two glyphs is a space. A centered line also keeps a leading run of blanks.
    Left-aligned leading blanks and every trailing blank stay out of the reading.
    """
    if align not in ("left", "center"):
        raise ValueError(f"unknown align {align}")
    prefix, body = [], list(words)
    while body and body[0] in LAYOUT:
        prefix.append(body.pop(0))
    glyphs = [index for index, word in enumerate(body) if 0 < word < TEXT_LIMIT]
    chars = []
    if not glyphs:
        for word in body:
            if word != 0:
                chars.append(decode_token(word) if word >= TEXT_LIMIT else _glyph(word))
        return prefix, body, "".join(chars)
    first, last = glyphs[0], glyphs[-1]
    for index, word in enumerate(body):
        if word == 0:
            if first < index < last or (align == "center" and index < first):
                chars.append(" ")
            continue
        if word >= TEXT_LIMIT:
            chars.append(decode_token(word))
        else:
            chars.append(_glyph(word))
    return prefix, body, "".join(chars)


def _glyph(word: int) -> str:
    char = glyph_char(word)
    if not char:
        raise ValueError(f"source glyph {word:#x} has no reading")
    return char


def message_prose(words: list[int], align: str = "left") -> list[str]:
    return [line_prose(line, align)[2] for line in split_lines(words)]


def split_tags(text: str) -> list[tuple[str, str]]:
    """(tag, '') for each <...> control, ('', char) for each other character."""
    return re.findall(r"<([^<>]+)>|(.)", text)


def line_cells(line: str) -> int:
    """Visible cells. Tags take none; punctuation and spaces take one."""
    return sum(1 for _tag, char in split_tags(line) if char)


def indent_cells(prefix: list[int]) -> int:
    """Columns taken before the first glyph. Blanks take one; FFDn takes n-0xD0+1."""
    cells = 0
    for word in prefix:
        if word == 0:
            cells += 1
        elif 0xFFD0 <= word <= 0xFFDF:
            cells += word - 0xFFD0 + 1
    return cells


def dialog_line_limit(prefix: list[int]) -> int:
    """Glyphs that fit once the icon cell and this line's indent are reserved."""
    return DIALOG_LINE - indent_cells(prefix)


def dialog_limits(original: list[int], align: str, count: int) -> list[int]:
    """Per translated line. A line keeps the indent of the original line at that index."""
    parts = _line_parts(original, align)
    return [dialog_line_limit(parts[index][0] if index < len(parts) else []) for index in range(count)]


def parse_line(text: str, token_of) -> list[int]:
    tokens = []
    for tag, char in split_tags(text):
        if tag:
            code = TAGS.get(tag)
            if code is None and re.fullmatch(r"[0-9A-F]{4}", tag) and int(tag, 16) >= TEXT_LIMIT:
                code = int(tag, 16)
            if code is None:
                raise ValueError(f"unknown control <{tag}>")
            tokens.append(code)
        else:
            tokens.append(PAD if char in " \u3000" else token_of(char))
    return tokens


def _line_parts(original: list[int], align: str) -> list[tuple[list[int], list[int]]]:
    parts = []
    for line in split_lines(original):
        prefix, body, _prose = line_prose(line)
        if align == "left":
            while body and body[0] == 0:
                prefix.append(body.pop(0))
        parts.append((prefix, body))
    return parts


def _controls(words: list[int]) -> list[int]:
    return [word for word in words if word >= TEXT_LIMIT]


def _cut_trailing(encoded: list[int]) -> int:
    end = len(encoded)
    while end and encoded[end - 1] >= TEXT_LIMIT:
        end -= 1
    return end


def _place_line(prefix: list[int], line: list[int], fill: int) -> list[int]:
    end = _cut_trailing(line)
    return prefix + line[:end] + [FILL] * fill + line[end:]


def encode_message(original: list[int], text: list[str], align: str, token_of, *, pad: bool = True, fit: bool = True) -> list[int]:
    """Encode one message. pad keeps the disc word count with 0xFFFC; otherwise the slack is omitted."""
    if align not in ("left", "center"):
        raise ValueError(f"unknown align {align}")
    if not text:
        raise ValueError("translation has no lines")
    parts = _line_parts(original, align)
    encoded = [parse_line(line, token_of) for line in text]
    # Same breaks and per-line fit: pad each line so a fixed-length translation stays byte-identical.
    if len(encoded) == len(parts) and all(
        _controls(line) == _controls(body) and len(line) <= len(body)
        for line, (_prefix, body) in zip(encoded, parts)
    ):
        words = []
        for index, ((prefix, body), line) in enumerate(zip(parts, encoded)):
            fill = (len(body) - len(line)) if pad else 0
            if index:
                words.append(0xFFFE)
            words.extend(_place_line(prefix, line, fill))
        if words.count(0xFFFE) != original.count(0xFFFE) or (pad and len(words) != len(original)):
            raise ValueError("translation moved a control code")
        if not pad and FILL in words:
            raise ValueError("compact translation contains padding")
        return words
    disc = [word for _prefix, body in parts for word in _controls(body)]
    if [word for line in encoded for word in _controls(line)] != disc:
        raise ValueError("controls differ from the disc")
    original_len = len(original)
    budget = original_len - sum(len(prefix) for prefix, _body in parts[:len(encoded)])
    need = sum(len(line) for line in encoded) + len(encoded) - 1
    if fit and need > budget:
        raise ValueError(f"message needs {need} words, the disc message has {budget}")
    slack = (budget - need) if pad else 0
    words = []
    for index, line in enumerate(encoded):
        prefix = parts[index][0] if index < len(parts) else []
        # Leftover wait sits on the last line; borrowed cells are real glyphs.
        fill = slack if index == len(encoded) - 1 else 0
        if index:
            words.append(0xFFFE)
        words.extend(_place_line(prefix, line, fill))
    if pad and len(words) != original_len:
        raise ValueError("translation moved a control code")
    if not pad and (FILL in words or (fit and len(words) > original_len)):
        raise ValueError("compact translation contains padding")
    return words


SCRIPT_HEAD = b"\x00\x02\x00\x06"


def _be16(data, offset):
    return struct.unpack_from(">H", data, offset)[0]


def _be32(data, offset):
    return struct.unpack_from(">I", data, offset)[0]


def _script_chunks():
    with BIN.open("rb") as src:
        data = read_user(src, ELF_LBA + CHUNK_TABLE // 2048, 8)
    blob = data[CHUNK_TABLE % 2048:]
    found = []
    for index in range(512):
        start, count = struct.unpack_from("<QQ", blob, index * 16)
        if not 0 < count < 20000 or not 0 <= start < 300000:
            continue
        found.append((index, int(start), int(count)))
    return found


def _load_chunk(start, count):
    with BIN.open("rb") as src:
        return read_user(src, PACK_LBA + start, count)


def dialogue_segments(data):
    found = []
    offset = 0
    limit = len(data) - 1
    while offset < limit:
        word = _be16(data, offset)
        if not (word < 0x8000 or word >= CONTROL_START):
            offset += 2
            continue
        start = offset
        words = []
        while offset < limit:
            word = _be16(data, offset)
            if not (word < 0x8000 or word >= CONTROL_START):
                break
            words.append(word)
            offset += 2
            if words[-1] == 0xFFFF:
                break
        if words and words[-1] == 0xFFFF and any(glyph_char(item) for item in words):
            found.append((start, offset, words))
    return found


DESC_BASE = 0x5978B0
DESC_STRIDE = 0xA8
CHOICE_STRIDE = 0x18
assert DESC_STRIDE == 7 * CHOICE_STRIDE


def text_cursors() -> dict[int, int]:
    """Block number -> text cursor. The second u64 of each descriptor."""
    file_off = DESC_BASE - FILE_BASE
    size = 14 * DESC_STRIDE
    with BIN.open("rb") as handle:
        blob = read_user(handle, ELF_LBA + file_off // 2048, (file_off % 2048 + size + 2047) // 2048)
    base = file_off % 2048
    found = {}
    for index in range(14):
        for choice in range(7):
            at = base + index * DESC_STRIDE + choice * CHOICE_STRIDE
            first, second = struct.unpack_from("<QQ", blob, at)
            block = struct.unpack_from("<H", blob, at + 0x10)[0]
            if first or not block or second & 1:
                continue
            if block in found and found[block] != second:
                raise ValueError(f"block {block} has two text cursors")
            found[block] = second
    return found


# Operand bytes for opcodes 0..0x145. 0xFF has no handler (0000, 0001, 0008, 008B).
# 0083 and 00A5 read operands only after the object allocates; the script is written for that path.
_OP_OPERANDS = bytes.fromhex(
    "ffff000608000c04ff040402020202020204040404040200000400000000000000020202040404040404040404060606"
    "020202020404040202020206060606060604060606060404040606060604040606060002020204060004000006000200"
    "00020204040404040204020200020002000202020002020608060806040206060000000400000202060606ff02000202"
    "0404000004000000000000000000000000000000080a0200000204040404040404040404040404040404020404040606"
    "0202020002000604040404020008080204040204040406060406060006080a0a00000000000200000000000604060404"
    "040406020202020200000202040402020404020204040202040402020404020204040202040406060202000204000004"
    "0202020202080000000000000000000000000000020c12181e04020006000000000000000000"
)
assert len(_OP_OPERANDS) == 0x146


def _decode_script(data: bytes, origin: int, limit: int):
    """Opcode at origin and everything reachable by fallthrough or a jump, stopping at the text cursor."""
    ins = {}
    queue = [origin]
    bad = []
    while queue:
        pc = queue.pop()
        if pc in ins:
            continue
        if pc < 0 or pc >= limit or pc % 2:
            bad.append(pc)
            continue
        op = _be16(data, pc)
        size = _OP_OPERANDS[op] if op < len(_OP_OPERANDS) else 0xFF
        if size == 0xFF or pc + 2 + size > limit:
            bad.append(pc)
            continue
        raw = data[pc + 2:pc + 2 + size]
        fall = pc + 2 + size
        if op == 6:
            nxt = [fall, _be32(raw, 8)]
        elif op in (7, 0x11):
            nxt = [_be32(raw, 0)]
        elif op == 0x145:
            nxt = []
        else:
            nxt = [fall]
        ins[pc] = (op, raw, nxt)
        queue.extend(target for target in nxt if target not in ins)
    return ins, bad


def _show_mode(op: int, mode: int) -> int:
    if op in (0x59, 0x13C):
        return mode | 1
    if op == 0x5A:
        return mode & ~1
    if op == 0x92:
        return mode | 4
    if op == 0x93:
        return mode & ~0xE
    if op == 0xCB and mode & 4:
        return mode | 2
    if op == 0xCC:
        return mode & ~2
    if op == 0xE5 and mode & 4:
        return mode | 8
    if op == 0xE6:
        return mode & ~8
    return mode


def _script_map(data: bytes, cursor: int) -> tuple[dict, set]:
    """Instructions before the text cursor, and the entry points a walk starts from."""
    ins, bad = _decode_script(data, 0, cursor)
    if bad:
        raise ValueError(f"script does not decode from 0: {bad[0]:#x}")
    pending = [_be32(raw, 2) for _pc, (op, raw, _nxt) in ins.items() if op == 3]
    entered = set()
    while pending:
        origin = pending.pop()
        if origin in entered:
            continue
        entered.add(origin)
        if origin in ins:
            continue
        more, more_bad = _decode_script(data, origin, cursor)
        if more_bad or origin not in more:
            raise ValueError(f"procedure {origin:#x} does not decode")
        pending.extend(_be32(raw, 2) for _pc, (op, raw, _nxt) in more.items() if op == 3)
        ins.update(more)
    while True:
        covered = {index for pc, (op, raw, _nxt) in ins.items() for index in range(pc, pc + 2 + len(raw))}
        origin = next((index for index in range(0, cursor, 2) if index not in covered), None)
        if origin is None:
            break
        more, more_bad = _decode_script(data, origin, cursor)
        if more_bad or not more:
            break
        entered.add(origin)
        pending = [_be32(raw, 2) for _pc, (op, raw, _nxt) in more.items() if op == 3]
        ins.update(more)
        while pending:
            origin = pending.pop()
            if origin in entered:
                continue
            entered.add(origin)
            if origin in ins:
                continue
            more, more_bad = _decode_script(data, origin, cursor)
            if more_bad or origin not in more:
                raise ValueError(f"procedure {origin:#x} does not decode")
            pending.extend(_be32(raw, 2) for _pc, (op, raw, _nxt) in more.items() if op == 3)
            ins.update(more)
    return ins, entered


def script_shows(data: bytes, cursor: int) -> dict[int, str]:
    """Message offset -> center or dialog, from the display flag at each 0004.

    0003 registers a procedure offset. 0004's two u32s are a word index past the
    text cursor and a word count; every message in that span shares the flag.
    Bit 0 and bit 2 both center. Anything else is the dialog box.
    """
    messages, skipped = script_messages(data, cursor)
    if skipped:
        raise ValueError(f"unreadable message at {skipped[0]:#x}")
    return classify_shows(data, cursor, [at for at, _words in messages])


def classify_shows(data: bytes, cursor: int, offsets: list[int]) -> dict[int, str]:
    """Message offset -> center or dialog. offsets are the messages 0004 must cover."""
    ins, entered = _script_map(data, cursor)
    known = set(offsets)
    ordered = sorted(offsets)
    shown = {}
    for origin in entered:
        queue = [(origin, 0)]
        seen = set()
        while queue:
            pc, mode = queue.pop()
            if (pc, mode) in seen or pc not in ins:
                continue
            seen.add((pc, mode))
            op, raw, nxt = ins[pc]
            mode = _show_mode(op, mode)
            if op == 4:
                start = cursor + _be32(raw, 0) * 2
                end = start + _be32(raw, 4) * 2
                if start not in known:
                    index = bisect.bisect_right(ordered, start)
                    if not 0 < index < len(ordered):
                        raise ValueError(f"message index {start:#x} is not a message")
                else:
                    for at in offsets:
                        if start <= at < end:
                            shown.setdefault(at, set()).add("center" if mode & 5 else "dialog")
            queue.extend((target, mode) for target in nxt)
    kinds = {}
    missing = []
    for at in offsets:
        modes = shown.get(at)
        if not modes:
            missing.append(at)
        elif len(modes) != 1:
            raise ValueError(f"message {at:#x} is {' and '.join(sorted(modes))}")
        else:
            kinds[at] = modes.pop()
    if missing:
        raise ValueError(f"message {missing[0]:#x} is never shown")
    return kinds


def script_messages(data: bytes, origin: int) -> tuple[list[tuple[int, list[int]]], list[int]]:
    """FFFF-terminated messages starting at the descriptor cursor.

    The next message begins on the following word. Words at or above TEXT_LIMIT
    stay inside the message. A record that never reaches FFFF ends the stream.
    """
    messages, skipped = [], []
    offset = origin
    limit = len(data) - 1
    while offset < limit:
        words = []
        cursor = offset
        while cursor < limit:
            word = _be16(data, cursor)
            cursor += 2
            words.append(word)
            if word == 0xFFFF:
                break
        if not words or words[-1] != 0xFFFF:
            break
        start, offset = offset, cursor
        body = words[:-1]
        if not any(0 < word < TEXT_LIMIT for word in body):
            continue
        if any(word < TEXT_LIMIT and word and glyph_char(word) is None for word in body):
            skipped.append(start)
            continue
        messages.append((start, words))
    return messages, skipped


# Operand readers. Signed u16, u16, and the two u32 readers. None sits in a delay slot.
_READERS = {0x28B3F8: ("s16", 2), 0x28B428: ("u16", 2), 0x28B450: ("u32", 4), 0x28B4A0: ("u32", 4)}
# Placed in order while packing. The translation does not store those positions.
# 293, 297, 301 and 309 fit, but packing them frees no sector, so they stay at the disc address.
# Narration chunks 265–272 keep their counted header, so they stay padded.
PACKED_CHUNKS = (282, 285, 291, 292, 296, 298, 299, 302, 306, 308, 310, 312, 313, 314, 315, 316)
# Source center-line counts.
PACKED_SCREENS = {
    282: 80,
    285: 337,
    291: 600,
    292: 174,
    296: 401,
    298: 393,
    299: 75,
    302: 630,
    306: 245,
    308: 234,
    310: 90,
    312: 37,
    313: 37,
    314: 37,
    315: 37,
    316: 37,
}
# Once every line has text, pack the chunk and drop the disc address from the file.
AWAITING_PACK = (283, 286, 287, 288, 289, 290, 300, 304, 305)
# A script number matches a message start, so this chunk cannot be moved even when it is translated.
UNMOVABLE_PACK = (294,)


def _elf():
    with BIN.open("rb") as src:
        return read_user(src, ELF_LBA, (ELF_SIZE + 2047) // 2048)[:ELF_SIZE]


def _operand_layouts() -> dict[int, list[tuple[str, int]]]:
    """Opcode -> field widths, from the jal sequence in each handler."""
    cached = getattr(_operand_layouts, "value", None)
    if cached is not None:
        return cached
    elf = _elf()

    def word_at(va):
        return struct.unpack_from("<I", elf, va - FILE_BASE)[0]

    table = 0x525480
    layouts = {}
    for op in range(len(_OP_OPERANDS)):
        if _OP_OPERANDS[op] == 0xFF:
            continue
        va = struct.unpack_from("<I", elf, table - FILE_BASE + op * 4)[0]
        fields = []
        pc = va
        for _step in range(80):
            word = word_at(pc)
            kind = word >> 26
            if kind == 3:
                target = ((pc + 4) & 0xF0000000) | ((word & 0x03FFFFFF) << 2)
                if target in _READERS:
                    fields.append(_READERS[target])
            if kind == 2 or (kind == 0 and (word & 0x3F) in (8, 9)):
                break
            pc += 4
        if sum(width for _name, width in fields) != _OP_OPERANDS[op]:
            raise ValueError(f"opcode {op:#x} operand layout does not match its size")
        layouts[op] = fields
    _operand_layouts.value = layouts
    return layouts


def _field_values(op: int, raw: bytes, layouts) -> list[int]:
    values = []
    offset = 0
    for kind, width in layouts[op]:
        if kind == "s16":
            values.append(struct.unpack_from(">h", raw, offset)[0])
        elif kind == "u16":
            values.append(_be16(raw, offset))
        else:
            values.append(_be32(raw, offset))
        offset += width
    return values


def _guard_operands(ins: dict, cursor: int, text_end: int, starts: set[int]) -> None:
    """Stop when a non-0004 operand is an address in the text or equals a message offset.

    Compare immediates can sit numerically inside the text byte range. Chunk 285
    has 0006's first u32 equal to 0xF800. Those are not pointers and are left alone.
    """
    layouts = _operand_layouts()
    for pc, (op, raw, _nxt) in ins.items():
        if op == 4 or op not in layouts:
            continue
        values = _field_values(op, raw, layouts)
        for value in values:
            if value in starts:
                raise ValueError(f"operand at {pc:#x} equals message {value:#x}")
        if op == 3 and cursor <= values[-1] < text_end:
            raise ValueError(f"procedure at {pc:#x} points into the text")
        if op == 6 and cursor <= values[-1] < text_end:
            raise ValueError(f"jump at {pc:#x} points into the text")
        if op in (7, 0x11) and cursor <= values[0] < text_end:
            raise ValueError(f"jump at {pc:#x} points into the text")


def _wait_parts(line: str) -> list[str]:
    """A wait in the middle starts the next line. A wait at the end only pauses."""
    return line.split("<BREAK>")


def check_lines(original: list[int], text: list[str], align: str, kind: str) -> None:
    if kind in ("center", "subtitle"):
        limit = NARRATION_LINE if kind == "subtitle" else CENTER_LINE
        for line in text:
            for part in _wait_parts(line):
                width = line_cells(part)
                if width > limit:
                    raise ValueError(f"{kind} line is {width} characters, limit {limit}: {part}")
    elif kind == "dialog":
        for line, limit in zip(text, dialog_limits(original, align, len(text))):
            for index, part in enumerate(_wait_parts(line)):
                cap = limit if index == 0 else DIALOG_LINE
                width = line_cells(part)
                if width > cap:
                    raise ValueError(f"dialog line is {width} characters, limit {cap}: {part}")
    else:
        raise ValueError("missing type")


def compact_chunk(data: bytes, cursor: int, records: list[dict], align: str, token_of) -> tuple[bytes, list[int]]:
    """Drop 0xFFFC, retarget 0004, and return the chunk through the last message plus the new offsets.

    records follow the disc messages. The bytes stop at the last terminator; the caller pads to a sector.
    """
    disc, skipped = script_messages(data, cursor)
    if skipped:
        raise ValueError(f"unreadable message at {skipped[0]:#x}")
    if len(records) != len(disc):
        raise ValueError(f"{len(records)} translations for {len(disc)} messages")
    ins, _entered = _script_map(data, cursor)
    text_end = disc[-1][0] + len(disc[-1][1]) * 2
    if any(data[text_end:]):
        raise ValueError("bytes after the last message are not zero")
    starts = [at for at, _words in disc]
    _guard_operands(ins, cursor, text_end, set(starts))
    encoded = []
    for record, (at, words) in zip(records, disc):
        body = words[:-1]
        if message_prose(body, align) != record["source"].split("\n"):
            raise ValueError(f"disc at {at:#x} does not match source")
        if not record.get("text"):
            raise ValueError(f"message {at:#x} is not translated")
        check_lines(body, record["text"], align, record.get("type"))
        encoded.append(encode_message(body, record["text"], align, token_of, pad=False))
    blob = bytearray()
    offsets = []
    for words in encoded:
        offsets.append(cursor + len(blob))
        for word in words:
            blob.extend(word.to_bytes(2, "big"))
        blob.extend(b"\xff\xff")
    prefix = bytearray(data[:cursor])
    for pc, (op, raw, _nxt) in ins.items():
        if op != 4:
            continue
        start = cursor + _be32(raw, 0) * 2
        end = start + _be32(raw, 4) * 2
        covered = [index for index, at in enumerate(starts) if start <= at < end]
        if not covered or starts[covered[0]] != start or covered != list(range(covered[0], covered[-1] + 1)):
            raise ValueError(f"0004 at {pc:#x} is not a run of messages")
        last = covered[-1]
        if starts[last] + len(disc[last][1]) * 2 != end:
            raise ValueError(f"0004 at {pc:#x} does not end on a message")
        new_start = offsets[covered[0]]
        new_end = offsets[last] + (len(encoded[last]) + 1) * 2
        struct.pack_into(">II", prefix, pc + 2, (new_start - cursor) // 2, (new_end - new_start) // 2)
    packed = bytes(prefix) + bytes(blob)
    shows = classify_shows(packed, cursor, offsets)
    for record, offset in zip(records, offsets):
        if shows.get(offset) != record.get("type"):
            raise ValueError(f"message {offset:#x} type is {shows.get(offset)}")
    return packed, offsets


def render_dialogue(words):
    text = []
    for word in words:
        if word == 0xFFFF or 0xFFD0 <= word <= 0xFFD9:
            continue
        if word == 0xFFFE:
            text.append("\n")
        elif word in CONTROL_NAMES:
            text.append(CONTROL_NAMES[word])
        else:
            text.append(glyph_char(word) or f"[{word:04X}]")
    return "".join(text)


def dialogue_references(data, spans):
    mark = bytearray(len(data))
    for start, end, _words in spans:
        mark[start:end] = b"\x01" * (end - start)
    found = []
    limit = len(data)
    for offset in range(0, limit - 8, 2):
        if mark[offset]:
            continue
        opcode = _be16(data, offset)
        target = _be32(data, offset + 2)
        if not (0 <= target < limit and target % 2 == 0 and mark[target]):
            continue
        if opcode == 4 and offset + 10 <= limit and _be16(data, offset + 6) == 0:
            found.append({"op": 4, "at": offset + 2, "target": target, "length": _be16(data, offset + 8)})
        elif opcode == 0x11:
            found.append({"op": 0x11, "at": offset + 2, "target": target, "length": None})
    return found


def _unclassified(data, spans, refs):
    mark = bytearray(len(data))
    for start, end, _words in spans:
        mark[start:end] = b"\x01" * (end - start)
    covered = set()
    for ref in refs:
        covered.update(range(ref["at"], ref["at"] + 4))
    count = 0
    for offset in range(0, len(data) - 4, 2):
        if mark[offset] or offset in covered:
            continue
        target = _be32(data, offset)
        if 0 <= target < len(data) and target % 2 == 0 and mark[target]:
            count += 1
    return count


def _gaps(data, spans):
    pending = []
    previous = 0
    for start, end, _words in spans:
        if start > previous:
            pending.append(start - previous)
        previous = end
    if len(data) > previous:
        pending.append(len(data) - previous)
    return pending


def _event_starts(script: bytes, op: int, index: int) -> list[int]:
    needle = op.to_bytes(2, "big") + index.to_bytes(2, "big")
    found = []
    pos = 0
    while True:
        at = script.find(needle, pos)
        if at < 0:
            break
        if at < 2:
            raise ValueError(f"opcode {op:#x} index {index:#x} has no timestamp")
        found.append(at - 2)
        pos = at + 2
    return found


def trim_beat_script(script: bytes, keep: int) -> bytes:
    """Drop chunk 281 commands for the unused one-glyph messages.

    Each beat index has one 18-byte opcode 0x12 followed by its 10-byte opcode 0x14,
    plus a later 10-byte opcode 0x14. An index at or past the counted message count
    makes those handlers fail.
    """
    if not 0 <= keep <= BEAT_COUNT:
        raise ValueError(f"beat count {keep} is outside 0..{BEAT_COUNT}")
    if keep == BEAT_COUNT:
        return script
    ranges = []
    for index in range(BEAT_INDEX + keep, BEAT_INDEX + BEAT_COUNT):
        show = _event_starts(script, 0x12, index)
        later = _event_starts(script, 0x14, index)
        if len(show) != 1 or len(later) != 2 or later[0] != show[0] + 18:
            raise ValueError(f"beat index {index:#x} commands are not a show pair plus one later reference")
        if script[show[0] + 20:show[0] + 24] != (0x14).to_bytes(2, "big") + index.to_bytes(2, "big"):
            raise ValueError(f"beat index {index:#x} is not followed by opcode 0x14")
        ranges.append((show[0], show[0] + 28))
        ranges.append((later[1], later[1] + 10))
    ranges.sort()
    if any(end > nxt for (_, end), (nxt, _) in zip(ranges, ranges[1:])):
        raise ValueError("beat commands overlap")
    out = bytearray()
    cursor = 0
    for start, end in ranges:
        out.extend(script[cursor:start])
        cursor = end
    out.extend(script[cursor:])
    if len(out) > len(script):
        raise ValueError("trimmed beat script grew")
    out.extend(bytes(len(script) - len(out)))
    for index in range(BEAT_INDEX + keep, BEAT_INDEX + BEAT_COUNT):
        if _event_starts(out, 0x12, index) or _event_starts(out, 0x14, index):
            raise ValueError(f"beat index {index:#x} is still in the script")
    return bytes(out)


def repack_beats(data: bytes, tokens: list[int]) -> bytes:
    """Keep the bytes before the one-glyph run and write one word per token.

    The counted header shrinks by two words for each dropped message. The sector stays
    the same length; the unused tail is zero.
    """
    if len(tokens) > BEAT_COUNT:
        raise ValueError(f"{len(tokens)} beats do not fit in {BEAT_COUNT}")
    if len(data) < BEAT_AT + BEAT_COUNT * 4:
        raise ValueError("counted block is shorter than the beat run")
    header = int.from_bytes(data[:2], "big")
    end = 2 + header * 2
    if end != BEAT_AT + BEAT_COUNT * 4 or any(data[end:]):
        raise ValueError("beat run does not end the counted block")
    for index in range(BEAT_COUNT):
        at = BEAT_AT + index * 4 + 2
        if data[at:at + 2] != b"\xff\xff":
            raise ValueError(f"beat {index} is not one word")
    out = bytearray(len(data))
    out[:BEAT_AT] = data[:BEAT_AT]
    out[:2] = (header - (BEAT_COUNT - len(tokens)) * 2).to_bytes(2, "big")
    for index, token in enumerate(tokens):
        at = BEAT_AT + index * 4
        out[at:at + 2] = token.to_bytes(2, "big")
        out[at + 2:at + 4] = b"\xff\xff"
    return bytes(out)


def counted_messages(data: bytes) -> list[tuple[int, int, list[int]]]:
    """Counted block as (start, end past FFFF, words). Words omit the terminator."""
    return [(at, end + 2, words) for at, end, words in segments(data, True)]


def compact_counted(data: bytes, records: list[dict], align: str, token_of) -> bytes:
    """Repack a counted narration block, keeping its header and zero padding.

    Unlike fixed script messages, a counted message may grow past its source
    length. The block only has to stay within its original sector allocation.
    """
    disc = counted_messages(data)
    if len(records) != len(disc):
        raise ValueError(f"{len(records)} translations for {len(disc)} messages")
    encoded = []
    for record, (at, _end, words) in zip(records, disc):
        if message_prose(words, align) != record["source"].split("\n"):
            raise ValueError(f"disc at {at:#x} does not match source")
        if not record.get("text"):
            encoded.append(words)
            continue
        check_lines(words, record["text"], align, record.get("type"))
        encoded.append(encode_message(words, record["text"], align, token_of, pad=False, fit=False))
    payload = bytearray()
    for words in encoded:
        payload.extend(b"".join(word.to_bytes(2, "big") for word in words))
        payload.extend(b"\xff\xff")
    count = len(payload) // 2
    stop = 2 + len(payload)
    if not count or count > 0xFFFF or stop > len(data):
        raise ValueError(f"counted block needs {stop:#x} bytes, chunk has {len(data):#x}")
    out = bytearray(len(data))
    struct.pack_into(">H", out, 0, count)
    out[2:stop] = payload
    return bytes(out)


# The narration player at 0x3B2AB4 indexes this table with gp-0x44FE, then 0x3B4AA8 parses the block.
COUNTED_TABLE = 0x554CC0


def counted_chunks() -> tuple[int, ...]:
    """Eight narration chunk numbers. The halfwords at 0x554CC0."""
    file_off = COUNTED_TABLE - FILE_BASE
    with BIN.open("rb") as handle:
        blob = read_user(handle, ELF_LBA + file_off // 2048, 1)
    ids = struct.unpack_from("<8H", blob, file_off % 2048)
    if ids != tuple(range(0x109, 0x111)):
        raise ValueError(f"narration chunks are {tuple(hex(item) for item in ids)}")
    return ids


def dump_dialogue(out_dir: Path | None = None) -> Path:
    out_dir = out_dir or (BUILD / "text")
    out_dir.mkdir(parents=True, exist_ok=True)
    index = []
    cursors = text_cursors()
    counted = set(counted_chunks())
    with (out_dir / "messages.jsonl").open("w", encoding="utf-8") as message_file, (out_dir / "refs.jsonl").open("w", encoding="utf-8") as ref_file:
        for index_id, start, count in _script_chunks():
            data = _load_chunk(start, count)
            if index_id in counted:
                found, refs, unclassified = counted_messages(data), [], 0
            elif data[:4] == SCRIPT_HEAD:
                found = dialogue_segments(data)
                refs = dialogue_references(data, found)
                unclassified = _unclassified(data, found, refs)
            elif index_id not in cursors:
                continue
            else:
                messages, _skipped = script_messages(data, cursors[index_id])
                if not messages:
                    continue
                found = [(at, at + len(words) * 2, words) for at, words in messages]
                refs, unclassified = [], 0
            base = start * 2048
            for at, end, words in found:
                message_file.write(json.dumps({
                    "chunk": index_id, "at": at, "end": end, "pack": base + at,
                    "lines": words.count(0xFFFE) + 1, "text": render_dialogue(words),
                }, ensure_ascii=False) + "\n")
            for ref in refs:
                ref_file.write(json.dumps({"chunk": index_id, **ref}) + "\n")
            index.append({
                "chunk": index_id, "sectors": count, "messages": len(found),
                "refs": len(refs), "unclassified": unclassified,
            })
    (out_dir / "index.json").write_text(json.dumps(index, indent=2), encoding="utf-8")
    return out_dir


def script_self_test() -> None:
    start, count = next((item[1], item[2]) for item in _script_chunks() if item[0] == 282)
    data = _load_chunk(start, count)
    if data[:4] != SCRIPT_HEAD:
        raise AssertionError("chunk 282 is not a dialogue script")
    found = dialogue_segments(data)
    refs = dialogue_references(data, found)
    if [item[0] for item in found[:1]] != [0x1108] or _gaps(data, found) != [0x1108, 168]:
        raise AssertionError("chunk 282 dialogue is not one trailing run")
    cursors = text_cursors()
    messages, skipped = script_messages(data, cursors[282])
    if cursors[282] != 0x1180 or cursors[285] != 0x6000 or messages[0][0] != 0x1180 or len(messages) != 232 or skipped:
        raise AssertionError("chunk 282 messages do not follow the descriptor cursor")
    shows = script_shows(data, cursors[282])
    if shows[0x1238] != "center" or shows[0x13B8] != "center" or shows[0x127C] != "dialog":
        raise AssertionError("chunk 282 display kind does not match the center and dialog lines")
    if set(shows) != {at for at, _words in messages} or sum(kind == "center" for kind in shows.values()) != 80:
        raise AssertionError("chunk 282 messages are not all classified")
    maze_start, maze_count = next((item[1], item[2]) for item in _script_chunks() if item[0] == 285)
    maze_messages, maze_skipped = script_messages(_load_chunk(maze_start, maze_count), cursors[285])
    diary = next(words for at, words in maze_messages if at == 0xE8BE)
    if (maze_messages[0][0] != 0x6000 or maze_skipped
            or message_prose(maze_messages[0][1][:-1])[0] != "立ちふさがるオーク"
            or 0xE0DB not in diary or any(at == 0xE8E6 for at, _words in maze_messages)):
        raise AssertionError("chunk 285 messages do not follow the descriptor cursor")
    maze_shows = script_shows(_load_chunk(maze_start, maze_count), cursors[285])
    if set(maze_shows) != {at for at, _words in maze_messages} or sum(kind == "center" for kind in maze_shows.values()) != 337:
        raise AssertionError("chunk 285 messages are not all classified")
    tavern = next(words for at, _end, words in found if at <= 0x15A2 < _end)
    if render_dialogue(tavern).split("\n")[0].count("よ") < 1 or "「" not in render_dialogue(tavern):
        raise AssertionError("tavern entrance line was not decoded")
    if not any(ref["op"] == 4 and ref["at"] == 0xBFC and ref["target"] == 0x15A2 and ref["length"] == 0x28 for ref in refs):
        raise AssertionError("tavern pointer 0x15A2 is missing")
    if _unclassified(data, found, refs) != 0:
        raise AssertionError("chunk 282 has a text pointer outside opcodes 4 and 11")
    if tuple(PACKED_SCREENS) != PACKED_CHUNKS:
        raise AssertionError("packed layout is not the packed chunk list")
    if set(AWAITING_PACK) & set(PACKED_CHUNKS) or set(UNMOVABLE_PACK) & set(PACKED_CHUNKS):
        raise AssertionError("a chunk is both packed and waiting")
    for chunk, screens in PACKED_SCREENS.items():
        _assert_packed(chunk, cursors[chunk], screens)
    for chunk in AWAITING_PACK:
        waiting = json.loads((DATA / "text" / f"{chunk}.json").read_text(encoding="utf-8"))
        if waiting["messages"] and all(item.get("text") for item in waiting["messages"]):
            raise AssertionError(f"chunk {chunk} is translated and still not packed")
    for chunk in UNMOVABLE_PACK:
        held = json.loads((DATA / "text" / f"{chunk}.json").read_text(encoding="utf-8"))
        if held["messages"] and all(item.get("text") for item in held["messages"]):
            raise AssertionError(f"chunk {chunk} is translated, but a script number matches a message start")
    open_start, open_count = next((item[1], item[2]) for item in _script_chunks() if item[0] == 266)
    opening = counted_messages(_load_chunk(open_start, open_count))
    castle = next((end, words) for at, end, words in opening if at == 0xC6)
    if (open_start * 2048 != 0x3C9B000 or open_count != 1 or len(opening) != 12 or castle[0] != 0xEA
            or message_prose(castle[1]) != ["その美しさを", "たたえられた城も……"]):
        raise AssertionError("chunk 266 opening was not decoded")
    narration = {
        265: 1, 266: 12, 267: 3, 268: 3, 269: 32, 270: 6, 271: 35, 272: 57,
    }
    if counted_chunks() != tuple(narration):
        raise AssertionError("narration table is not chunks 265-272")
    for chunk, count in narration.items():
        start, sectors = next((item[1], item[2]) for item in _script_chunks() if item[0] == chunk)
        messages = counted_messages(_load_chunk(start, sectors))
        if sectors != 1 or len(messages) != count:
            raise AssertionError(f"chunk {chunk} is not {count} counted messages")
        if chunk == 266:
            opening = json.loads((DATA / "text" / f"{chunk}.json").read_text(encoding="utf-8"))
            if any(item.get("type") != "subtitle" for item in opening["messages"]):
                raise AssertionError("chunk 266 is not subtitles")
            continue
        doc = json.loads((DATA / "text" / f"{chunk}.json").read_text(encoding="utf-8"))
        if any(item.get("type") != "subtitle" for item in doc["messages"]):
            raise AssertionError(f"chunk {chunk} is not subtitles")
        if chunk == 272:
            _assert_beats(doc, messages, _load_chunk(start, sectors))
            continue
        if (doc["chunk"] != chunk or doc["align"] != "center" or int(doc["base"], 0) != start * 2048
                or len(doc["messages"]) != count):
            raise AssertionError(f"chunk {chunk} skeleton does not match the disc")
        for record, (at, _end, words) in zip(doc["messages"], messages):
            prose = "\n".join(message_prose(words, doc["align"]))
            translated = record["text"] is not None
            if (record["type"] != "subtitle" or int(record["offset"], 0) != at
                    or record["source"] != prose):
                raise AssertionError(f"chunk {chunk} message {at:#x} does not match the disc")
            if translated:
                check_lines(words, record["text"], doc["align"], record["type"])
                encode_message(words, record["text"], doc["align"], lambda _char: 0x4001, pad=False, fit=False)
    prologue = counted_messages(_load_chunk(*next((item[1], item[2]) for item in _script_chunks() if item[0] == 265)))
    if message_prose(prologue[0][2], "center")[0] != "かつて、禍々しき「武神」を倒し":
        raise AssertionError("chunk 265 prologue was not decoded")
    note_start, note_count = next((item[1], item[2]) for item in _script_chunks() if item[0] == 309)
    note, note_skipped = script_messages(_load_chunk(note_start, note_count), cursors[309])
    if len(note) != 14 or note_skipped or message_prose(note[0][1][:-1])[0] != "一行は袋を開けた。":
        raise AssertionError("chunk 309 messages do not follow the descriptor cursor")
    siege_start, siege_count = next((item[1], item[2]) for item in _script_chunks() if item[0] == 304)
    siege_data = _load_chunk(siege_start, siege_count)
    siege, siege_skipped = script_messages(siege_data, cursors[304])
    siege_shows = script_shows(siege_data, cursors[304])
    if len(siege) != 3048 or siege_skipped or set(siege_shows) != {at for at, _words in siege}:
        raise AssertionError("chunk 304 messages are not all classified")


def _assert_beats(doc: dict, messages, data: bytes) -> None:
    head = [item for item in doc["messages"] if int(item["offset"], 0) < BEAT_AT]
    beats = [item for item in doc["messages"] if int(item["offset"], 0) >= BEAT_AT]
    sentence = "".join(item["text"][0] for item in beats)
    if (len(messages) != BEAT_INDEX + BEAT_COUNT or len(head) != BEAT_INDEX or not beats or len(beats) > BEAT_COUNT
            or len(sentence) != len(beats) or any(len(item["text"]) != 1 or len(item["text"][0]) != 1 for item in beats)):
        raise AssertionError("chunk 272 beats were not shortened to one translated character each")
    for record, (at, _end, words) in zip(head, messages[:BEAT_INDEX]):
        if (int(record["offset"], 0) != at
                or record["source"] != "\n".join(message_prose(words, "center"))):
            raise AssertionError(f"chunk 272 message {at:#x} does not match the disc")
        if record["text"] is not None:
            check_lines(words, record["text"], "center", record["type"])
            encode_message(words, record["text"], "center", lambda _char: 0x4001, pad=False, fit=False)
    for index, (record, (at, _end, words)) in enumerate(zip(beats, messages[BEAT_INDEX:])):
        if (int(record["offset"], 0) != at or at != BEAT_AT + index * 4 or len(words) != 1
                or record["text"] != [sentence[index]]
                or record["source"] != message_prose(words, "center")[0]):
            raise AssertionError(f"chunk 272 beat {at:#x} does not match the sentence")
    tokens = [0x4001] * len(beats)
    packed = repack_beats(data, tokens)
    if (counted_messages(packed)[-1][1] != BEAT_AT + len(beats) * 4
            or len(counted_messages(packed)) != BEAT_INDEX + len(beats)):
        raise AssertionError("chunk 272 beat repack did not shrink the counted header")
    script_start, script_count = next((item[1], item[2]) for item in _script_chunks() if item[0] == BEAT_SCRIPT)
    script = _load_chunk(script_start, script_count)
    trimmed = trim_beat_script(script, len(beats))
    first = _event_starts(script, 0x12, BEAT_INDEX + len(beats))[0]
    if trimmed == script or len(trimmed) != len(script) or trimmed[:first] != script[:first]:
        raise AssertionError("chunk 281 beat commands were not trimmed")
    if _event_starts(trimmed, 0x12, BEAT_INDEX + len(beats) - 1) == []:
        raise AssertionError("chunk 281 lost the last kept beat")


def _assert_packed(chunk: int, cursor: int, screens: int) -> None:
    start, count = next((item[1], item[2]) for item in _script_chunks() if item[0] == chunk)
    doc = json.loads((DATA / "text" / f"{chunk}.json").read_text(encoding="utf-8"))
    packed, _offsets = compact_chunk(_load_chunk(start, count), cursor, doc["messages"], doc["align"], lambda _char: 0x4001)
    body = packed[cursor:]
    # Word-aligned. The byte pair can straddle a terminator and the next glyph.
    padded = len(body) % 2 or any(int.from_bytes(body[i:i + 2], "big") == FILL for i in range(0, len(body), 2))
    sectors = (len(packed) + 2047) // 2048
    if padded or sectors >= count:
        raise AssertionError(f"chunk {chunk} packs to {len(packed):#x}")
    if any("offset" in item for item in doc["messages"]):
        raise AssertionError(f"chunk {chunk} stores a packed position")
    if sum(item["type"] == "center" for item in doc["messages"]) != screens:
        raise AssertionError(f"chunk {chunk} center count is not {screens}")
    if [nxt for _index, nxt, _size in _script_chunks() if nxt == start + count] != [start + count]:
        raise AssertionError(f"chunk {chunk} does not leave a hole before the next chunk")


def self_test() -> None:
    assert glyph_char(0x003F) == "あ"
    assert glyph_char(0x0067) == "る"
    assert glyph_char(0x0090) == "ア"
    assert glyph_char(0x0354) == "議" and glyph_char(0x06D1) is None
    assert decode_words([0x011E, 0x0247, 0x0148, 0x006C, 0x0067, 0x026E, 0x0149]) == "戦士「んる。」"
    sample = bytes.fromhex("003F0040FFFF00410300FFFF")
    clean = [words for _, _, words in segments(sample) if is_clean(words)]
    assert len(clean) == 1 and clean[0] == [0x003F, 0x0040]
    report = describe(bytes.fromhex("003F0040FFFF031EFFFF"))
    assert report["terminated_segments"] == 2 and report["clean_messages"] == 1
    assert report["messages"][1]["clean"] is False and "031E" in report["messages"][1]["tokens"]
    assert not is_clean([0x6D1])
    assert segments(bytes.fromhex("0003003FFFFEFFFF0000"), True) == [(2, 6, [0x3F, 0xFFFE])]
    for broken in ("0004003FFFFEFFFF", "0003003FFFFEFFFF0001", "0002003FFFFE", "0000FFFF"):
        try:
            segments(bytes.fromhex(broken), True)
        except ValueError:
            pass
        else:
            raise AssertionError("invalid counted message was accepted")

    source = [0xFFD2, 0x003F, 0x0040, 0x0041, 0xFFFE, 0x0042, 0x0000]
    assert message_prose(source) == ["あいう", "え"]
    spaced = [0x003F, 0x0000, 0x0040, 0x0000, 0xFFFE, 0x0000, 0x0000, 0x0041]
    assert message_prose(spaced) == ["あ い", "う"]
    assert message_prose(spaced, "center") == ["あ い", "  う"]
    assert message_prose([0, 0, 0], "center") == [""]
    check_lines([0x003F], ["甲" * 19], "center", "center")
    check_lines([0x003F], ["甲" * 19 + "<BREAK>" + "甲" * 19], "center", "center")
    check_lines([0xFFD2, 0x003F], ["甲" * 16 + "<BREAK>" + "甲" * 19], "left", "dialog")
    check_lines([0x003F], ["甲" * 21], "center", "subtitle")
    for bad in (("甲" * 22, "subtitle"), ("甲" * 20, "center"), ("甲" * 19 + "<BREAK>" + "甲" * 20, "center")):
        try:
            check_lines([0x003F], [bad[0]], "center", bad[1])
        except ValueError:
            pass
        else:
            raise AssertionError("center line limit accepted an overlong line")
    assert line_cells("近乎疯狂的笑声在杜汉城中回荡开来。<BREAK>") == 17
    assert line_cells("男人像是受了伤，死死攥着剑，撑住衰弱的身体。") == 22
    assert indent_cells([0xFFD2, 0x003F]) == 3
    assert indent_cells([0xFFD4]) == 5
    assert indent_cells([0, 0, 0xFFE2]) == 2
    assert dialog_line_limit([]) == 19
    assert dialog_line_limit([0xFFD2]) == 16
    assert dialog_line_limit([0xFFD4]) == 14
    assert dialog_limits([0x003F, 0xFFFE, 0xFFD2, 0x0042, 0xFFFE, 0xFFD4, 0x0043], "left", 3) == [19, 16, 14]
    tokens = {"甲": 0x4001, "乙": 0x4002}.__getitem__
    for align in ("center", "left"):
        assert encode_message(source, ["甲", "乙"], align, tokens) == [0xFFD2, 0x4001, FILL, FILL, 0xFFFE, 0x4002, FILL]
        assert encode_message(source, ["甲", "乙"], align, tokens, pad=False) == [0xFFD2, 0x4001, 0xFFFE, 0x4002]
    assert encode_message([0, 0x003F, 0x0040, 0x0041], ["甲"], "left", tokens) == [0, 0x4001, FILL, FILL]
    assert encode_message([0x003F] * 4, ["甲　乙"], "center", tokens) == [0x4001, PAD, 0x4002, FILL]
    assert encode_message([0x003F] * 6 + [0xFFFD], ["甲<BREAK>"], "left", tokens) == [0x4001] + [FILL] * 5 + [0xFFFD]
    assert encode_message([0x003F] * 3 + [0xFFF9], ["甲<PAGE>"], "left", tokens) == [0x4001, FILL, FILL, 0xFFF9]
    inline = [0xFFE2, 0x0000, 0x003F, 0xFB01, 0x0040, 0xFB00, 0x0041, 0xFFFD]
    assert message_prose(inline) == ["あ<FB01>い<FB00>う<BREAK>"]
    assert encode_message(inline, ["<FB01>甲<FB00>　乙<BREAK>"], "left", tokens) == [0xFFE2, 0, 0xFB01, 0x4001, 0xFB00, PAD, 0x4002, 0xFFFD]
    assert encode_message(source, ["甲甲甲甲", "乙"], "left", tokens) == [0xFFD2, 0x4001, 0x4001, 0x4001, 0x4001, 0xFFFE, 0x4002]
    assert encode_message(source, ["甲", "乙乙乙"], "left", tokens) == [0xFFD2, 0x4001, 0xFFFE, 0x4002, 0x4002, 0x4002, FILL]
    assert encode_message(source, ["甲", "乙乙乙"], "left", tokens, pad=False) == [0xFFD2, 0x4001, 0xFFFE, 0x4002, 0x4002, 0x4002]
    assert encode_message(source, ["甲乙乙乙乙乙"], "center", tokens) == [0xFFD2, 0x4001, 0x4002, 0x4002, 0x4002, 0x4002, 0x4002]
    assert encode_message(source, ["甲", "乙", "甲"], "left", tokens) == [0xFFD2, 0x4001, 0xFFFE, 0x4002, 0xFFFE, 0x4001, FILL]
    moved = [0x003F, 0xFB01, 0x0040, 0xFFFE, 0xFB00, 0x0041]
    assert encode_message(moved, ["<FB01><FB00>甲", "乙"], "left", tokens) == [0xFB01, 0xFB00, 0x4001, 0xFFFE, 0x4002, FILL]
    assert encode_message([0xFFD2, 0x003F, 0xFFFE, 0xFFD3, 0x0040], ["甲乙乙"], "left", tokens) == [0xFFD2, 0x4001, 0x4002, 0x4002, FILL]
    for original, text, align in (
        (source, ["甲甲甲甲甲", "乙乙"], "left"), (source, [], "left"), (source, ["甲", "乙"], "right"),
        (inline, ["甲乙<BREAK>"], "left"), (inline, ["<FB00>甲<FB01>乙<BREAK>"], "left"),
        (inline, ["<FB01>甲<FB00>乙<BREAK><XYZ>"], "left"), (moved, ["<FB00><FB01>甲", "乙"], "left"),
    ):
        try:
            encode_message(original, text, align, tokens)
        except ValueError:
            pass
        else:
            raise AssertionError(f"bad translation {text} {align} was accepted")
    blob = bytes.fromhex("e0db003fffff0040fffffffdffff003fffff06d1ffff0041ffff")
    walked, unread = script_messages(blob, 0)
    assert [at for at, _words in walked] == [0, 6, 14, 22] and unread == [18]
    assert message_prose(walked[0][1][:-1]) == ["<E0DB>あ"]
    assert encode_message(walked[0][1][:-1], ["<E0DB>甲"], "left", tokens) == [0xE0DB, 0x4001]
    try:
        line_prose([0x06D1])
    except ValueError:
        pass
    else:
        raise AssertionError("glyph without a reading was accepted")



