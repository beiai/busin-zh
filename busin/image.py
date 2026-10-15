"""Build the translated disc image. Does not modify the source BIN."""
import hashlib
import json
import struct
import subprocess
import unicodedata

from busin.disc import (
    BIN, CHUNK_TABLE, ELF_LBA, ELF_SIZE, FILE_BASE, PACK_LBA, SYNC,
    children, chunk_row, entry, finish_sector, read_pack, read_user,
)
from busin.font import (
    FONT_DIR, PIXEL_OFFSET, PIXEL_SIZE, assign_cells as place_cells, copy_cell,
    hash_collision_stats, load_plan, render_font,
)
from busin.patch import emu
from busin.patch.ui_pages import PAGE_BYTES, PAGE_COUNT, UI_LBA, UI_SECTORS
from busin.patch.font_paging import (
    BLOCK_FONT_LBA, BLOCK_FONT_SECTORS, BLOCK_SLOTS, CAVE, CHUNK_BYTES, CHUNK_SECTOR, CHUNK_SLOTS,
    CODE_OFF, COMBINED, GUARDS, MAP_SECTORS, PACK_SECTORS, PAGE1_OFF, PAGE_CELLS,
    READ_BYTES, READ_SECTORS, SHIFT, SHIFT_FROM, SLOT_SECTORS, TMX_BYTES,
    TOKMAP_COUNT,
    build_cave, build_pad, elf_writes, hooks, token_map,
)
from busin.paths import BUILD, DATA, ROOT
from busin.text import (
    BEAT_AT, BEAT_CHUNK, BEAT_INDEX, BEAT_SCRIPT, PAD, PACKED_CHUNKS, PAGE0,
    check_lines, compact_chunk, counted_messages, encode_message,
    message_prose, repack_beats, split_tags, text_cursors, trim_beat_script,
)
from busin.tmx import read_tmx, save_png
from busin.ui import replacement_spans

OUT_DIR = BUILD / "image"
PAGE_PATH = BUILD / "font" / "message_glyphs.page01.tmx"
MAZE_PATH = BUILD / "font" / "message_glyphs.page285.tmx"
PLAN_PATH = DATA / "font" / "message-plan.json"


def text_files():
    return sorted((DATA / "text").glob("*.json"))


def ui_files():
    root = DATA / "screen"
    return sorted([*root.rglob("manifest.json"), *root.rglob("*.png")])


def elf_word(handle, va, lba=ELF_LBA):
    off = va - FILE_BASE
    return struct.unpack_from("<I", read_user(handle, lba + off // 2048, 1), off % 2048)[0]


def read_terminated(handle, at: int) -> list[int]:
    raw = read_pack(handle, at, 512)
    words = []
    for index in range(0, len(raw), 2):
        word = int.from_bytes(raw[index:index + 2], "big")
        if word == 0xFFFF:
            return words
        words.append(word)
    raise ValueError(f"message at {at:#x} has no terminator within 256 words")


def load_texts():
    return [(path.name, json.loads(path.read_text(encoding="utf-8"))) for path in text_files()]


def text_chars(doc):
    """Visible characters in one text file. Spaces stay the blank cell."""
    return {char for message in doc["messages"] for line in message.get("text") or []
            for _tag, char in split_tags(line) if char and char not in " \u3000"}


# Narration blocks 265–272 and chunk 282 stay on the shared page. Their character numbers are the page-1 cells.
SHARED_CHUNKS = {265, 266, 267, 268, 269, 270, 271, 272, 282}


def character_map(ids, local, mark):
    """Character number to this page's cell. The last word is the slot mark."""
    table = [0] * TOKMAP_COUNT
    for char, cell in local.items():
        index = ids[char]
        if not 0 < index < TOKMAP_COUNT - 1 or table[index]:
            raise ValueError(f"{char} has character number {index}")
        table[index] = cell
    if not 0 < mark < BLOCK_SLOTS:
        raise ValueError(f"slot mark {mark}")
    table[TOKMAP_COUNT - 1] = mark
    return struct.pack(f"<{TOKMAP_COUNT}I", *table)


def assign_ids(plan, texts):
    """Page-1 cells for the shared page. Other characters get numbers from 1764 up."""
    pinned = {char: int(item["cell"], 0) for char, item in plan["characters"].items() if item["page"] == 1}
    by_chunk = {}
    for _name, doc in texts:
        by_chunk.setdefault(doc.get("chunk"), set()).update(text_chars(doc))
    shared = set(pinned)
    for chunk in SHARED_CHUNKS:
        shared |= by_chunk.get(chunk, set())
    page = place_cells(shared, pinned)
    assert page == place_cells(shared, pinned)
    ids = dict(page)
    rest = sorted(set().union(*by_chunk.values()) - set(ids))
    number = PAGE_CELLS
    for char in rest:
        if number >= TOKMAP_COUNT - 1:
            raise ValueError(f"character number {number} does not fit the token table")
        ids[char] = number
        number += 1
    for chunk, chars in by_chunk.items():
        if chars - shared and chunk not in CHUNK_SLOTS:
            raise ValueError(f"chunk {chunk} has no font slot")
    stats = hash_collision_stats(shared, pinned)
    if stats["max_probe_length"] > 10:
        print(f"warning: hash probe length {stats['max_probe_length']}", flush=True)
    return ids, page, stats, by_chunk


def chunk_table(handle):
    data = read_user(handle, ELF_LBA + CHUNK_TABLE // 2048, 8)
    blob = data[CHUNK_TABLE % 2048:]
    rows = []
    for index in range(512):
        start, count = struct.unpack_from("<QQ", blob, index * 16)
        if count:
            rows.append((index, int(start), int(count)))
    return rows


def load_messages(handle, texts, cells):
    """Pack offset -> (source token, translated token) for every word that changes.

    Packed chunks are rewritten without 0xFFFC and returned separately.
    """
    changed, starts, errors, packs = {}, set(), [], []
    cursors = text_cursors()
    rows = chunk_table(handle)
    for name, doc in texts:
        chunk = doc.get("chunk")
        if chunk in PACKED_CHUNKS:
            try:
                packs.append(pack_script(handle, doc, cells, cursors[chunk], rows))
            except ValueError as error:
                errors.append(f"{name}: {error}")
            continue
        if chunk == BEAT_CHUNK and any(message.get("text") is not None for message in doc["messages"]):
            continue
        base = int(doc.get("base", "0"), 0)
        for message in doc["messages"]:
            if message.get("text") is None:
                continue
            at = base + int(message["offset"], 0)
            try:
                if at in starts:
                    raise ValueError("offset is translated twice")
                starts.add(at)
                original = read_terminated(handle, at)
                prose = message_prose(original, doc["align"])
                if prose != message["source"].split("\n"):
                    raise ValueError(f"disc is {prose}")
                check_lines(original, message["text"], doc["align"], message.get("show"))
                encoded = encode_message(original, message["text"], doc["align"], lambda char: PAD + cells[char])
            except ValueError as error:
                errors.append(f"{name} {message['offset']}: {error}")
                continue
            for index, (was, word) in enumerate(zip(original, encoded)):
                if word != was:
                    changed[at + 2 * index] = (was, word)
    if errors:
        raise ValueError("\n".join(errors))
    return changed, packs


def ending_spans(handle, texts, cells):
    """Rewrite chunk 272's one-glyph tail and drop the unused chunk 281 commands."""
    doc = next((item for _name, item in texts if item.get("chunk") == BEAT_CHUNK), None)
    if doc is None or all(message.get("text") is None for message in doc["messages"]):
        return []
    rows = chunk_table(handle)
    start, count = next((item[1], item[2]) for item in rows if item[0] == BEAT_CHUNK)
    data = read_user(handle, PACK_LBA + start, count)
    disc = counted_messages(data)
    head = [item for item in doc["messages"] if int(item["offset"], 0) < BEAT_AT]
    beats = [item for item in doc["messages"] if int(item["offset"], 0) >= BEAT_AT]
    if len(head) != BEAT_INDEX or len(beats) > 40 or len(disc) != BEAT_INDEX + 40:
        raise ValueError("chunk 272 beat layout does not match the disc")
    have = [int(item["offset"], 0) for item in beats]
    want = [at for at, _end, _words in disc[BEAT_INDEX:BEAT_INDEX + len(beats)]]
    if have != want:
        raise ValueError("chunk 272 beat offsets are not the first slots")
    sector = bytearray(data)
    token_of = lambda char: PAD + cells[char]
    for record, (at, _end, words) in zip(head, disc[:BEAT_INDEX]):
        if record["source"].split("\n") != message_prose(words, doc["align"]):
            raise ValueError(f"chunk 272 message {at:#x} source does not match the disc")
        if record.get("text") is None:
            continue
        check_lines(words, record["text"], doc["align"], record["show"])
        encoded = encode_message(words, record["text"], doc["align"], token_of)
        cursor = at
        for word in encoded:
            sector[cursor:cursor + 2] = word.to_bytes(2, "big")
            cursor += 2
        sector[cursor:cursor + 2] = b"\xff\xff"
    tokens = []
    for record, (at, _end, words) in zip(beats, disc[BEAT_INDEX:]):
        if record["source"] != "\n".join(message_prose(words, doc["align"])):
            raise ValueError(f"chunk 272 beat {at:#x} source does not match the disc")
        text = record.get("text")
        if text is None or len(text) != 1 or len(text[0]) != 1:
            raise ValueError(f"chunk 272 beat {at:#x} is not one character")
        check_lines(words, text, doc["align"], record["show"])
        encoded = encode_message(words, text, doc["align"], token_of)
        if len(encoded) != 1:
            raise ValueError(f"chunk 272 beat {at:#x} did not stay one word")
        tokens.append(encoded[0])
    sector = repack_beats(bytes(sector), tokens)
    script_start, script_count = next((item[1], item[2]) for item in rows if item[0] == BEAT_SCRIPT)
    script = read_user(handle, PACK_LBA + script_start, script_count)
    return [
        (start * 2048, sector),
        (script_start * 2048, trim_beat_script(script, len(tokens))),
    ]


def pack_script(handle, doc, cells, cursor, rows):
    """Rewrite one script chunk and describe the sectors it no longer claims.

    Each line is placed in order. The translation file does not store that position.
    """
    chunk = doc["chunk"]
    start, count = next((item[1], item[2]) for item in rows if item[0] == chunk)
    base = start * 2048
    if int(doc.get("base", "0"), 0) != base:
        raise ValueError(f"base is not sector {start}")
    data = read_user(handle, PACK_LBA + start, count)
    packed, _offsets = compact_chunk(data, cursor, doc["messages"], doc["align"], lambda char: PAD + cells[char])
    sectors = (len(packed) + 2047) // 2048
    if sectors >= count:
        raise ValueError(f"sector count did not shrink ({count} -> {sectors})")
    if [item[1] for item in rows if item[1] == start + count] != [start + count]:
        raise ValueError("the next chunk does not start at the old end")
    hole = start + sectors
    if any(item[0] != chunk and item[1] < start + count and item[1] + item[2] > hole for item in rows):
        raise ValueError("freed sectors overlap another chunk")
    body = packed + bytes(sectors * 2048 - len(packed))
    return {
        "chunk": chunk,
        "spans": [(base, body), (hole * 2048, bytes((count - sectors) * 2048))],
        "table": CHUNK_TABLE + chunk * 16 + 8,
        "sectors": sectors,
        "free": {"chunk": chunk, "relative": hole, "sectors": count - sectors, "lba": PACK_LBA + hole},
    }


def check_source(handle, labels):
    pvd = read_user(handle, 16, 1)
    names = {name: (lba, size) for name, lba, size in children(handle, entry(pvd[156:156 + pvd[156]]))}
    if names["SLPM_620.98"] != (ELF_LBA, ELF_SIZE) or names["PACKDATA.CIG"][0] != PACK_LBA:
        raise ValueError(f"unexpected disc layout {names}")
    for va, word in [*((va, source) for va, source, _ in hooks(labels)), *GUARDS.items()]:
        have = elf_word(handle, va)
        if have != word:
            raise ValueError(f"hook {va:#x} is {have:#x}")
    if chunk_row(handle, 317) != (CHUNK_SECTOR, CHUNK_BYTES // 2048):
        raise ValueError(f"chunk 317 table {chunk_row(handle, 317)}")
    if chunk_row(handle, 278) != (31968, 257):
        raise ValueError("chunk 278 table is not the original font")


def make_page(chunk, plan, glyphs, dest=PAGE_PATH):
    if len(chunk) != CHUNK_BYTES or chunk[8:12] != b"TMX0":
        raise ValueError("chunk 317 does not start with the message TMX")
    if 0 in glyphs.values() or len(set(glyphs.values())) != len(glyphs):
        raise ValueError("page 1 cells overlap or use the blank cell")
    page = bytearray(chunk[:TMX_BYTES])
    page[PIXEL_OFFSET:PIXEL_OFFSET + PIXEL_SIZE] = b"\xFF" * PIXEL_SIZE
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    dest.parent.mkdir(parents=True, exist_ok=True)
    blank = OUT_DIR / "_blank.tmx"
    blank.write_bytes(page)
    # Only kanji are drawn. Latin, kana, punctuation, symbols and Roman numerals reuse the page-0 glyph,
    # whose size and placement the page-1 font does not reproduce.
    original = {char: cell for cell, char in PAGE0.items()}
    copied = {char: original[char] for char in glyphs if char in original and not unicodedata.name(char, "").startswith("CJK UNIFIED")}
    style = plan["style"]
    render_font(
        blank, dest, glyphs, [char for char in glyphs if char not in copied], style["font"],
        style["size"], style["variation"], style["alpha"],
    )
    blank.unlink()
    data = bytearray(dest.read_bytes())
    for char, cell in copied.items():
        copy_cell(data, chunk, cell, glyphs[char])
    dest.write_bytes(data)
    return bytes(data)


def patch_directory(blob):
    out = bytearray(blob)
    pos = 0
    found_pack = False
    moved = 0
    while pos < len(out):
        size = out[pos]
        if not size:
            pos = (pos // 2048 + 1) * 2048
            continue
        if pos % 2048 + size > 2048:
            raise ValueError("directory record crosses a sector")
        extent = struct.unpack_from("<I", out, pos + 2)[0]
        name = out[pos + 33:pos + 33 + out[pos + 32]].split(b";")[0].upper()
        if name == b"PACKDATA.CIG":
            struct.pack_into("<I", out, pos + 10, PACK_SECTORS * 2048)
            struct.pack_into(">I", out, pos + 14, PACK_SECTORS * 2048)
            found_pack = True
        elif extent >= SHIFT_FROM:
            struct.pack_into("<I", out, pos + 2, extent + SHIFT)
            struct.pack_into(">I", out, pos + 6, extent + SHIFT)
            moved += 1
        pos += size
    return bytes(out), found_pack, moved


def directory_patches(handle):
    """Return {lba: user2048} for every directory sector that changes."""
    patches = {}

    def walk(lba, size):
        count = (size + 2047) // 2048
        raw = read_user(handle, lba, count)
        new, found, moved = patch_directory(raw)
        if found or moved:
            for index in range(count):
                patches[lba + index] = new[index * 2048:(index + 1) * 2048]
        pos = 0
        data = raw[:size]
        while pos < len(data):
            rec = data[pos]
            if not rec:
                pos = (pos // 2048 + 1) * 2048
                continue
            flags = data[pos + 25]
            name = data[pos + 33:pos + 33 + data[pos + 32]].split(b";")[0]
            extent = struct.unpack_from("<I", data, pos + 2)[0]
            child = struct.unpack_from("<I", data, pos + 10)[0]
            if flags & 2 and name not in (b"\x00", b"\x01") and extent != lba:
                walk(extent, child)
            pos += rec

    pvd = read_user(handle, 16, 1)
    root = entry(pvd[156:156 + pvd[156]])
    walk(root[1], root[2])
    svd = read_user(handle, 17, 1)
    if svd[:7] == b"\x02CD001\x01":
        walk(*entry(svd[156:156 + svd[156]])[1:3])
    return patches, pvd, svd


def splice_pack(src, user_patches, spans) -> None:
    for offset, data in spans:
        end = offset + len(data)
        for rel in range(offset // 2048, (end - 1) // 2048 + 1):
            lba = PACK_LBA + rel
            sector = user_patches.get(lba)
            user_patches[lba] = bytearray(sector if sector is not None else read_user(src, lba, 1))
        pos = 0
        while pos < len(data):
            rel, at = divmod(offset + pos, 2048)
            step = min(2048 - at, len(data) - pos)
            user_patches[PACK_LBA + rel][at:at + step] = data[pos:pos + step]
            pos += step


def apply_ui(src, user_patches, messages, reserved=()) -> None:
    message_bytes = {index for off in messages for index in (off, off + 1)}
    spans = []
    for offset, data in replacement_spans(lambda off, size: read_pack(src, off, size), DATA / "screen"):
        end = offset + len(data)
        if any(index in message_bytes for index in range(offset, end)):
            raise ValueError(f"ui png at {offset:#x} overlaps a message")
        if any(end > start and offset < stop for start, stop in reserved):
            raise ValueError(f"ui png at {offset:#x} overlaps a packed chunk")
        spans.append((offset, data))
    splice_pack(src, user_patches, spans)


def _pad_ui(ui_pages, ui_tail):
    if len(ui_pages) != PAGE_BYTES * PAGE_COUNT:
        raise ValueError("ui font pages size")
    room = UI_SECTORS * 2048 - len(ui_pages)
    if len(ui_tail) > room or len(ui_tail) % 4:
        raise ValueError("button drawer does not fit the ui font tail")
    return ui_pages + ui_tail + bytes(room - len(ui_tail))


def build_image(chunk, page, pad, writes, messages, spans=(), slots=None, ui_pages=b"", ui_tail=b""):
    slots = slots or {}
    combined = bytearray(COMBINED)
    combined[:CHUNK_BYTES] = chunk
    combined[PAGE1_OFF:PAGE1_OFF + len(page)] = page
    combined[CODE_OFF:CODE_OFF + len(pad)] = pad
    if len(combined) != COMBINED:
        raise ValueError("combined block size")
    by_sector = {}
    for off, data in writes.items():
        for index, byte in enumerate(data):
            at = off + index
            if at >= ELF_SIZE:
                raise ValueError(f"ELF write {at:#x} is past the file")
            by_sector.setdefault(ELF_LBA + at // 2048, []).append((at % 2048, byte))
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    target = OUT_DIR / "busin-zh.bin"
    with BIN.open("rb") as src, target.open("wb") as dst:
        dir_patches, pvd, svd = directory_patches(src)
        volume = struct.unpack_from("<I", pvd, 80)[0]
        old_sectors = src.seek(0, 2) // 2352
        if (volume, old_sectors + SHIFT) != (283703, BLOCK_FONT_LBA):
            raise ValueError(f"block font LBA {BLOCK_FONT_LBA} does not follow volume {volume} file {old_sectors}")
        user_patches = dict(dir_patches)
        sectors = {}
        for off, pair in messages.items():
            sectors.setdefault(off // 2048, {})[off] = pair
        for rel, items in sectors.items():
            msg = bytearray(read_user(src, PACK_LBA + rel, 1))
            for off, (was, token) in items.items():
                have = struct.unpack_from(">H", msg, off % 2048)[0]
                if have != was:
                    raise ValueError(f"message at {off:#x} is {have:#x}")
                struct.pack_into(">H", msg, off % 2048, token)
            user_patches[PACK_LBA + rel] = bytes(msg)
        apply_ui(src, user_patches, messages, [(offset, offset + len(data)) for offset, data in spans])
        splice_pack(src, user_patches, spans)

        def patch_volume(user):
            buf = bytearray(user)
            struct.pack_into("<I", buf, 80, volume + SHIFT + BLOCK_FONT_SECTORS + UI_SECTORS)
            struct.pack_into(">I", buf, 84, volume + SHIFT + BLOCK_FONT_SECTORS + UI_SECTORS)
            return bytes(buf)

        user_patches[16] = patch_volume(user_patches.get(16, pvd))
        if svd[:7] == b"\x02CD001\x01":
            user_patches[17] = patch_volume(user_patches.get(17, svd))
        if max(user_patches) >= SHIFT_FROM:
            raise ValueError(f"sector {max(user_patches)} is past the copied prefix")
        src.seek(PACK_LBA * 2352)
        form = src.read(24)

        def write_user(lba, user):
            raw = bytearray(2352)
            raw[:12] = SYNC
            raw[12:24] = form[12:24]
            raw[15] = 2
            raw[24:2072] = user
            dst.write(finish_sector(raw, lba))

        def copy_span(start, count):
            src.seek(start * 2352)
            while count:
                step = min(count, 1024)
                dst.write(src.read(step * 2352))
                count -= step

        cursor = 0
        for lba in sorted(user_patches):
            if cursor < lba:
                copy_span(cursor, lba - cursor)
            src.seek(lba * 2352)
            raw = bytearray(src.read(2352))
            raw[24:2072] = user_patches[lba]
            dst.write(finish_sector(raw, lba))
            cursor = lba + 1
        copy_span(cursor, SHIFT_FROM - cursor)
        print("copied prefix", flush=True)
        for index in range(SHIFT):
            write_user(SHIFT_FROM + index, combined[index * 2048:(index + 1) * 2048])
        src.seek(SHIFT_FROM * 2352)
        for src_lba in range(SHIFT_FROM, old_sectors):
            raw = bytearray(src.read(2352))
            for rel, byte in by_sector.get(src_lba, ()):
                raw[24 + rel] = byte
            dst.write(finish_sector(raw, src_lba + SHIFT))
        print("moved tail", flush=True)
        if len(page) > CHUNK_BYTES or any(len(pixels) > CHUNK_BYTES or len(table) != MAP_SECTORS * 2048 for pixels, table in slots.values()):
            raise ValueError("page exceeds one font slot")
        font_user = bytearray(BLOCK_FONT_SECTORS * 2048)
        font_user[:len(page)] = page
        font_user[CHUNK_BYTES:CHUNK_BYTES + MAP_SECTORS * 2048] = token_map()
        for slot, (pixels, table) in slots.items():
            at = slot * SLOT_SECTORS * 2048
            font_user[at:at + len(pixels)] = pixels
            font_user[at + CHUNK_BYTES:at + CHUNK_BYTES + len(table)] = table
        for index in range(BLOCK_FONT_SECTORS):
            write_user(BLOCK_FONT_LBA + index, font_user[index * 2048:(index + 1) * 2048])
        print("appended block fonts", flush=True)
        ui_user = _pad_ui(ui_pages, ui_tail)
        for index in range(UI_SECTORS):
            write_user(UI_LBA + index, ui_user[index * 2048:(index + 1) * 2048])
        print("appended ui font pages", flush=True)
    target.with_suffix(".cue").write_text(
        f'FILE "{target.name}" BINARY\n  TRACK 01 MODE2/2352\n    INDEX 01 00:00:00\n',
        encoding="ascii",
    )
    return target, combined


def check_image(path, combined, writes, messages, spans=(), slots=None, ui_pages=b"", ui_tail=b""):
    slots = slots or {}
    with path.open("rb") as f, BIN.open("rb") as src:
        def root_files(handle):
            pvd = read_user(handle, 16, 1)
            return {name: (lba, size) for name, lba, size in children(handle, entry(pvd[156:156 + pvd[156]]))}

        names = root_files(f)
        for name, (lba, size) in root_files(src).items():
            want = (PACK_LBA, PACK_SECTORS * 2048) if name == "PACKDATA.CIG" else (lba + SHIFT if lba >= SHIFT_FROM else lba, size)
            if names.get(name) != want:
                raise ValueError(f"{name} is at {names.get(name)}, expected {want}")
        chunk_lba = PACK_LBA + CHUNK_SECTOR
        if read_user(f, chunk_lba, CHUNK_BYTES // 2048) != read_user(src, chunk_lba, CHUNK_BYTES // 2048):
            raise ValueError("original chunk 317 bytes changed")
        if read_user(f, SHIFT_FROM, SHIFT) != combined:
            raise ValueError("appended block mismatch")
        source_space = struct.unpack_from("<I", read_user(src, 16, 1), 80)[0]
        if struct.unpack_from("<I", read_user(f, 16, 1), 80)[0] != source_space + SHIFT + BLOCK_FONT_SECTORS + UI_SECTORS:
            raise ValueError("volume space")
        if read_user(f, UI_LBA, UI_SECTORS) != _pad_ui(ui_pages, ui_tail):
            raise ValueError("ui font pages")
        common = chunk_row(src, 0)[0] * 2048
        for relative in (0x00000, 0x081C0, 0x10380, 0x18540):
            if read_pack(f, common + relative, PAGE_BYTES) != read_pack(src, common + relative, PAGE_BYTES):
                raise ValueError(f"common page at {relative:#x} changed")
        slot = bytearray(CHUNK_BYTES)
        slot[:TMX_BYTES] = combined[PAGE1_OFF:PAGE1_OFF + TMX_BYTES]
        if read_user(f, BLOCK_FONT_LBA, CHUNK_BYTES // 2048) != slot:
            raise ValueError("block font slot 0")
        if read_user(f, BLOCK_FONT_LBA + CHUNK_BYTES // 2048, MAP_SECTORS) != token_map():
            raise ValueError("block font map")
        for slot_index, (pixels, table) in slots.items():
            have = bytearray(READ_BYTES)
            have[:TMX_BYTES] = pixels[:TMX_BYTES]
            have[CHUNK_BYTES:CHUNK_BYTES + MAP_SECTORS * 2048] = table
            if read_user(f, BLOCK_FONT_LBA + slot_index * SLOT_SECTORS, READ_SECTORS) != have:
                raise ValueError(f"block font slot {slot_index}")
        if 15 not in slots and read_user(f, BLOCK_FONT_LBA + 15 * SLOT_SECTORS, 1) != bytes(2048):
            raise ValueError("unused font slot is not empty")
        elf_lba = names["SLPM_620.98"][0]
        for off, data in writes.items():
            start, skip = divmod(off, 2048)
            have = read_user(f, elf_lba + start, (skip + len(data) + 2047) // 2048)[skip:skip + len(data)]
            if have != data:
                raise ValueError(f"ELF write at {off:#x} is missing")
        for lba in (16, elf_lba, BLOCK_FONT_LBA):
            f.seek(lba * 2352)
            raw = f.read(2352)
            if finish_sector(raw, lba) != raw:
                raise ValueError(f"sector {lba} ECC")
        for off, (was, token) in messages.items():
            if read_pack(f, off, 2) != token.to_bytes(2, "big"):
                raise ValueError(f"message token {off:#x}")
            if read_pack(src, off, 2) != was.to_bytes(2, "big"):
                raise ValueError("source image message changed")
        for offset, data in spans:
            if read_pack(f, offset, len(data)) != data:
                raise ValueError(f"pack span at {offset:#x} is missing")
    print("image check: OK")


def self_test():
    with BIN.open("rb") as src:
        src.seek(16 * 2352)
        sector = src.read(2352)
    if finish_sector(sector, 16) != sector:
        raise AssertionError("ECC does not reproduce an original sector")
    load_plan(PLAN_PATH)
    words, labels = build_cave()
    pad = build_pad()
    if labels["sample"] != CAVE:
        raise AssertionError("cave placement")
    emu.self_test(words, labels, pad)
    elf_writes(words, labels)
    print("self-test: OK", f"pad {len(pad)} cave {len(words)}")
    return words, labels, pad


def sha256(path):
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def label(path):
    return path.relative_to(ROOT).as_posix() if path.is_relative_to(ROOT) else path.as_posix()


def main():
    words, labels, pad = self_test()
    plan = load_plan(PLAN_PATH)
    texts = load_texts()
    ids, page_cells, collision_stats, by_chunk = assign_ids(plan, texts)
    grouped = {}
    for chunk, slot in CHUNK_SLOTS.items():
        grouped.setdefault(slot, set()).update(by_chunk.get(chunk, set()))
    slots = {}
    for slot, chars in sorted(grouped.items()):
        if not chars:
            raise ValueError(f"font slot {slot} has no characters")
        owners = [chunk for chunk, owned in CHUNK_SLOTS.items() if owned == slot]
        if len(owners) > 1 and any(by_chunk.get(chunk, set()) != chars for chunk in owners):
            raise ValueError(f"font slot {slot} mixes different character sets")
        local = place_cells(chars, {})
        slots[slot] = (None, local, character_map(ids, local, slot))
    ending = []
    with BIN.open("rb") as src:
        check_source(src, labels)
        messages, packs = load_messages(src, texts, ids)
        ending = ending_spans(src, texts, ids)
        chunk = read_user(src, PACK_LBA + CHUNK_SECTOR, CHUNK_BYTES // 2048)
    page = make_page(chunk, plan, page_cells)
    rendered = {}
    for slot, (_pending, local, table) in slots.items():
        dest = MAZE_PATH if slot == 2 else BUILD / "font" / f"slot{slot:02d}.tmx"
        pixels = make_page(chunk, plan, local, dest)
        if pixels[PIXEL_OFFSET:PIXEL_OFFSET + PIXEL_SIZE] == page[PIXEL_OFFSET:PIXEL_OFFSET + PIXEL_SIZE]:
            raise ValueError(f"slot {slot} pixels match slot 0")
        rendered[slot] = (pixels, table)
    slots = rendered
    save_png(read_tmx(PAGE_PATH), 0, OUT_DIR / "page01.pal00.png")
    save_png(read_tmx(MAZE_PATH), 0, OUT_DIR / "page285.pal00.png")
    from busin.disasm import load_elf
    from busin.naming import LABELS, name_entry_patches, ui_font_pages
    from busin.patch.button_font import CHARS, build_blob, elf_writes as button_elf_writes
    from busin.patch.ui_pages import elf_writes as ui_elf_writes
    from busin.ui_font import FONTS
    from busin.ui_text import ui_patches

    writes = elf_writes(words, labels)
    label_writes, spans, ui_info = name_entry_patches()
    elf = load_elf()
    ui_writes, ui_extra, ui_text_info = ui_patches(elf)
    pages = ui_font_pages(ui_extra)
    ui_code = ui_elf_writes(elf)
    button = button_elf_writes(elf)
    button_tail = build_blob({char: ui_extra[char] for char in CHARS})
    print("button", " ".join(f"U+{ord(char):04X}={ui_extra[char]:04X}" for char in CHARS))
    table_writes = {item["table"]: struct.pack("<Q", item["sectors"]) for item in packs}
    merged = {**writes, **label_writes, **table_writes, **ui_code, **ui_writes, **button}
    if len(merged) != (
            len(writes) + len(label_writes) + len(table_writes) + len(ui_code) + len(ui_writes) + len(button)):
        raise ValueError("ELF writes collide")
    covered = sorted((off, off + len(data)) for off, data in merged.items())
    if any(end > nxt for (_, end), (nxt, _) in zip(covered, covered[1:])):
        raise ValueError("ELF patch writes overlap")
    spans = [*spans, *(span for item in packs for span in item["spans"]), *ending]
    message_bytes = {index for off in messages for index in (off, off + 1)}
    pack_ranges = sorted((off, off + len(data)) for off, data in spans)
    if any(end > nxt for (_, end), (nxt, _) in zip(pack_ranges, pack_ranges[1:])):
        raise ValueError("pack spans overlap")
    for offset, data in spans:
        if any(index in message_bytes for index in range(offset, offset + len(data))):
            raise ValueError(f"pack span at {offset:#x} overlaps a message")
    path, combined = build_image(
        chunk, page, pad, merged, messages, spans, slots, pages, button_tail)
    check_image(path, combined, merged, messages, spans, slots, pages, button_tail)
    try:
        revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        revision = ""
    inputs = [BIN, PLAN_PATH, DATA / "font" / "message-page0.json", FONT_DIR / plan["style"]["font"],
              LABELS, DATA / "font" / "small.json",
              *sorted((DATA / "interface").glob("*.json")),
              *(path for _role, path, _version, _sha in FONTS),
              *sorted((ROOT / "busin").rglob("*.py")), *text_files(), *ui_files()]
    report = {
        "git": revision,
        "image": label(path),
        "image_sha256": sha256(path),
        "page": label(PAGE_PATH),
        "page_sha256": hashlib.sha256(page[PIXEL_OFFSET:PIXEL_OFFSET + PIXEL_SIZE]).hexdigest(),
        "maze_page_sha256": hashlib.sha256(slots[2][0][PIXEL_OFFSET:PIXEL_OFFSET + PIXEL_SIZE]).hexdigest(),
        "maze_cells": len(grouped[2]),
        "font_slots": {str(slot): len(chars) for slot, chars in sorted(grouped.items())},
        "name_entry": ui_info,
        "ui_text": ui_text_info,
        "inputs": {label(item): sha256(item) for item in inputs},
        "free_sectors": [item["free"] for item in packs],
        "message_tokens": {hex(off): hex(token) for off, (_was, token) in sorted(messages.items())},
        "cave": hex(CAVE),
        "code_offset": hex(CODE_OFF),
    }
    (OUT_DIR / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    font_report = PAGE_PATH.parent / "report.json"
    font_report.parent.mkdir(parents=True, exist_ok=True)
    font_report.write_text(json.dumps({"hash_collision_stats": collision_stats}, indent=2) + "\n", encoding="utf-8")
    print(
        f"hash cells {collision_stats['total_chars']} probe avg {collision_stats['avg_probe_length']} "
        f"max {collision_stats['max_probe_length']} collisions {collision_stats['collisions']}",
        flush=True,
    )
    print(path)
    return 0
