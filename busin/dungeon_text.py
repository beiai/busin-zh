"""Counted small-font inspection messages in PACKDATA chunk 264."""
import json
import struct

from busin.disc import BIN, FILE_BASE, PACK_LBA, chunk_row, read_pack
from busin.paths import DATA
from busin.ui_layout import _encode

CHUNK = 264
START = 31025
SECTORS = 4
COUNT = 213
CAPACITY = SECTORS * 2048
PATH = DATA / "interface" / "dungeon.json"
assert (PACK_LBA + START, START * 2048) == (31349, 0x3C98800)


def parse(raw):
    if len(raw) != CAPACITY or struct.unpack_from(">H", raw)[0] != COUNT:
        raise ValueError("dungeon chunk 264 size or count changed")
    rows, pos = [], 2
    for index in range(COUNT):
        body = []
        while pos + 2 <= len(raw):
            code = struct.unpack_from(">H", raw, pos)[0]
            pos += 2
            if code == 0xFFFF:
                break
            if code not in (0xFFFD, 0xFFFE) and not 0 <= code < 0x400:
                raise ValueError(f"dungeon {index:03d}: unknown code {code:#x}")
            body.append(code)
        else:
            raise ValueError(f"dungeon {index:03d}: missing FFFF terminator")
        if not body or body[-1] != 0xFFFE or 0xFFFE in body[:-1]:
            raise ValueError(f"dungeon {index:03d}: invalid FFFE end marker")
        if pos + 2 > len(raw):
            raise ValueError(f"dungeon {index:03d}: missing action")
        action = struct.unpack_from(">H", raw, pos)[0]
        pos += 2
        rows.append((f"{index:03d}", body, action))
    if any(raw[pos:]):
        raise ValueError("dungeon chunk 264 has nonzero trailing padding")
    return rows, pos


def read_source(elf=None):
    # 0x286408 loads this chunk; 0x286448 parses FFFF followed by an action word.
    if elf is not None:
        for va, expected in ((0x286418, 0x24040108), (0x28641C, 0x0C04172E),
                             (0x286420, 0x24050001), (0x286C14, 0x0C042252)):
            if struct.unpack_from("<I", elf, va - FILE_BASE)[0] != expected:
                raise ValueError(f"dungeon load/draw instruction changed at {va:#x}")
    with BIN.open("rb") as handle:
        if chunk_row(handle, CHUNK) != (START, SECTORS):
            raise ValueError("dungeon chunk 264 disc position changed")
        raw = read_pack(handle, START * 2048, CAPACITY)
    rows, used = parse(raw)
    return rows, used, raw


def source(codes):
    from busin.ui_text import UI

    parts = []
    for code in codes[:-1]:
        if code == 0xFFFD:
            parts.append("/")
        elif code == 0:
            parts.append(" ")
        elif code in UI and UI[code] != "/":
            parts.append(UI[code])
        else:
            raise ValueError(f"dungeon source has unknown or ambiguous glyph {code:#x}")
    return "".join(parts)


def dimensions(codes):
    widths = [0]
    for code in codes:
        if code == 0xFFFD:
            widths.append(0)
        elif code != 0xFFFE:
            widths[-1] += 1
    return max(widths), len(widths)


def translations(rows, doc=None):
    if doc is None:
        doc = json.loads(PATH.read_text(encoding="utf-8"))
    if not isinstance(doc, list) or len(doc) != len(rows):
        raise ValueError("dungeon translations do not match the disc")
    texts = []
    for row, (ident, codes, *_action) in zip(doc, rows):
        if not isinstance(row, dict) or row.get("id") != ident or row.get("source") != source(codes):
            raise ValueError(f"dungeon {ident} does not match the disc")
        if "text" not in row or row.get("keep_original") or row.get("type") or row.get("width"):
            raise ValueError(f"dungeon {ident}: expected id/source/text, null keeps Japanese")
        text = row["text"]
        if text is not None:
            if not isinstance(text, str) or (not text and source(codes)):
                raise ValueError(f"dungeon {ident}: expected a nonempty translation or null")
            if any(c in text for c in "{}\r\n"):
                raise ValueError(f"dungeon {ident}: use / for line breaks; controls are not editable")
        texts.append(text)
    return texts


def encode(rows, texts, table):
    out = bytearray(struct.pack(">H", len(rows)))
    if len(rows) != len(texts):
        raise ValueError("dungeon translation count changed")
    for (ident, original, action), text in zip(rows, texts):
        body = original
        if text is not None:
            try:
                # The generic UI uses FFFE for /; this painter uses FFFD instead.
                body = [0xFFFD if code == 0xFFFE else code for code in _encode(text, table, True)]
            except ValueError as exc:
                raise ValueError(f"dungeon {ident}: {exc}") from exc
            width, lines = dimensions(body)
            max_width, max_lines = dimensions(original)
            if width > max_width:
                raise ValueError(f"dungeon {ident}: width {width}, limit {max_width}, "
                                 f"exceeds by {width - max_width} cells ({(width - max_width) * 16} pixels)")
            if lines > max_lines:
                raise ValueError(f"dungeon {ident}: {lines} lines, limit {max_lines}, "
                                 f"exceeds by {lines - max_lines} lines")
            # A leading FFFE dispatches the action without showing a box (31 blank events).
            if original[0] != 0xFFFE and not body:
                raise ValueError(f"dungeon {ident}: translation would hide the message")
            body = [*body, 0xFFFE]
        words = [*body, 0xFFFF, action]
        out.extend(struct.pack(f">{len(words)}H", *words))
        if len(out) > CAPACITY:
            raise ValueError(f"dungeon {ident}: chunk 264 uses {len(out)} bytes, limit {CAPACITY}, "
                             f"exceeds by {len(out) - CAPACITY} bytes")
    return bytes(out).ljust(CAPACITY, b"\0"), len(out)


def pack_patches(table, doc=None):
    rows, original_used, raw = read_source()
    texts = translations(rows, doc)
    data, used = encode(rows, texts, table)
    translated = sum(text is not None for text in texts)
    info = {"total": len(rows), "translated": translated, "pending": len(rows) - translated,
            "original_bytes": original_used, "bytes": used, "capacity": CAPACITY,
            "free_bytes": CAPACITY - used}
    return ([(START * 2048, data)] if data != raw else []), info
