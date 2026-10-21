"""Build the translated disc image. Does not modify the source BIN."""
import hashlib
import json
import struct
import subprocess
import unicodedata

from busin.disc import (
    BIN, ELF_LBA, ELF_SIZE, FILE_BASE, PACK_LBA, SYNC,
    children, chunk_row, entry, finish_sector, read_pack, read_user,
)
from busin.font import (
    PIXEL_OFFSET, PIXEL_SIZE, assign_cells as place_cells, copy_cell, font_path,
    hash_collision_stats, load_plan, render_font,
)
from busin.patch import emu
from busin.patch.ledger import PatchLedger
from busin.patch.runtime_names_patch import elf_writes as runtime_name_elf_writes
from busin.patch.ui_pages import PAGE_BYTES, PAGE_COUNT, UI_LBA, UI_SECTORS
from busin.patch.font_paging import (
    BLOCK_FONT_LBA, BLOCK_FONT_SECTORS, BLOCK_SLOTS, CAVE, CHUNK_BYTES, CHUNK_SECTOR, CHUNK_SLOTS,
    CODE_OFF, COMBINED, GUARDS, MAP_SECTORS, PACK_SECTORS, PAGE1_OFF, PAGE_CELLS,
    READ_BYTES, READ_SECTORS, SHIFT, SHIFT_FROM, SLOT_SECTORS, TMX_BYTES,
    TOKMAP_COUNT,
    build_cave, build_pad, elf_writes, hooks, token_map,
)
from busin.paths import BUILD, DATA, ROOT
from busin.message_pack import (
    PLAN_PATH, assign_ids, character_map, ending_spans, load_messages, load_texts, text_files,
)
from busin.text import PAD, PAGE0
from busin.tmx import read_tmx, save_png
from busin.ui import part_spans, replacement_spans

OUT_DIR = BUILD / "image"
PAGE_PATH = BUILD / "font" / "message_glyphs.page01.tmx"
MAZE_PATH = BUILD / "font" / "message_glyphs.page285.tmx"


def ui_files():
    root = DATA / "picture"
    return sorted([root / "images.json", *root.rglob("*.png")])


def elf_word(handle, va, lba=ELF_LBA):
    off = va - FILE_BASE
    return struct.unpack_from("<I", read_user(handle, lba + off // 2048, 1), off % 2048)[0]


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
        style["size"], style.get("variation"), style["alpha"],
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


def _pad_ui(ui_pages):
    if len(ui_pages) != PAGE_BYTES * PAGE_COUNT:
        raise ValueError("ui font pages size")
    return ui_pages + bytes(UI_SECTORS * 2048 - len(ui_pages))


def build_image(chunk, page, pad, writes, messages, spans=(), slots=None, ui_pages=b"", changes=None):
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
        if changes is not None:
            changes.declare_user_sectors("prefix", "iso.directory", dir_patches)
        volume = struct.unpack_from("<I", pvd, 80)[0]
        old_sectors = src.seek(0, 2) // 2352
        if (volume, old_sectors + SHIFT) != (283703, BLOCK_FONT_LBA):
            raise ValueError(f"block font LBA {BLOCK_FONT_LBA} does not follow volume {volume} file {old_sectors}")
        user_patches = dict(dir_patches)
        sectors = {}
        for off, pair in messages.items():
            sectors.setdefault(off // 2048, {})[off] = pair
        if changes is not None:
            changes.declare_user_sectors(
                "prefix", "dialogue.inline", (PACK_LBA + rel for rel in sectors))
        for rel, items in sectors.items():
            msg = bytearray(read_user(src, PACK_LBA + rel, 1))
            for off, (was, token) in items.items():
                have = struct.unpack_from(">H", msg, off % 2048)[0]
                if have != was:
                    raise ValueError(f"message at {off:#x} is {have:#x}")
                struct.pack_into(">H", msg, off % 2048, token)
            user_patches[PACK_LBA + rel] = bytes(msg)
        splice_pack(src, user_patches, spans)

        def patch_volume(user):
            buf = bytearray(user)
            struct.pack_into("<I", buf, 80, volume + SHIFT + BLOCK_FONT_SECTORS + UI_SECTORS)
            struct.pack_into(">I", buf, 84, volume + SHIFT + BLOCK_FONT_SECTORS + UI_SECTORS)
            return bytes(buf)

        user_patches[16] = patch_volume(user_patches.get(16, pvd))
        volume_sectors = [16]
        if svd[:7] == b"\x02CD001\x01":
            user_patches[17] = patch_volume(user_patches.get(17, svd))
            volume_sectors.append(17)
        if changes is not None:
            changes.declare_user_sectors("prefix", "iso.volume", volume_sectors)
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
        if changes is not None:
            changes.declare_append("append.combined_block", SHIFT_FROM, SHIFT)
            changes.declare_append("append.block_font", BLOCK_FONT_LBA, BLOCK_FONT_SECTORS)
        print("appended block fonts", flush=True)
        ui_user = _pad_ui(ui_pages)
        for index in range(UI_SECTORS):
            write_user(UI_LBA + index, ui_user[index * 2048:(index + 1) * 2048])
        if changes is not None:
            changes.declare_append("append.ui_font", UI_LBA, UI_SECTORS)
        print("appended ui font pages", flush=True)
    target.with_suffix(".cue").write_text(
        f'FILE "{target.name}" BINARY\n  TRACK 01 MODE2/2352\n    INDEX 01 00:00:00\n',
        encoding="ascii",
    )
    return target, combined


def check_image(path, combined, writes, messages, spans=(), slots=None, ui_pages=b""):
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
        if read_user(f, UI_LBA, UI_SECTORS) != _pad_ui(ui_pages):
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
    with BIN.open("rb") as src:
        check_source(src, labels)
        messages, packs, blocks = load_messages(src, texts, ids)
        ending = ending_spans(src, texts, ids)
        part = part_spans(
            lambda off, size: read_pack(src, off, size), DATA / "picture",
            lambda chunk: chunk_row(src, chunk),
        )
        whole = replacement_spans(
            lambda off, size: read_pack(src, off, size), DATA / "picture",
            lambda chunk: chunk_row(src, chunk),
        )
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
    from busin.patch.ui_pages import elf_writes as ui_elf_writes
    from busin.patch.picture_rects import elf_writes as picture_elf_writes
    from busin.ui import load_images
    from busin.ui_font import FONTS, ui_font_pages
    from busin.ui_text import check_ui_text_report, ui_patches_with_table

    patches = PatchLedger()
    for offset, data in elf_writes(words, labels).items():
        patches.add_elf("elf.font_paging", offset, data)
    elf = load_elf()
    ui_writes, ui_extra, ui_text_info, ui_table = ui_patches_with_table(elf)
    check_ui_text_report(ui_text_info)
    pages = ui_font_pages(ui_extra)
    for offset, data in ui_elf_writes(elf).items():
        patches.add_elf("elf.ui_page_loader", offset, data)
    for offset, data in ui_writes.items():
        patches.add_elf("elf.ui_text_and_layout", offset, data)
    for offset, data in runtime_name_elf_writes(elf, ui_table).items():
        patches.add_elf("elf.runtime_names", offset, data, "memory-card and party rebuild name replacement")
    for offset, data in picture_elf_writes(elf, load_images(DATA / "picture" / "images.json")).items():
        patches.add_elf("elf.picture_rects", offset, data, "switch reuses the translated page_switch suffix")
    for item in packs:
        patches.add_elf(
            "dialogue.chunk_table", item["table"], struct.pack("<Q", item["sectors"]))
    for item in packs:
        for offset, data in item["spans"]:
            patches.add_pack("dialogue.packed", offset, data)
    for item in blocks:
        for offset, data in item["spans"]:
            patches.add_pack("dialogue.counted", offset, data)
    for offset, data in ending:
        patches.add_pack("dialogue.ending", offset, data)
    for offset, data in whole:
        patches.add_pack("picture.whole", offset, data)
    for offset, data in part:
        patches.add_pack("picture.part", offset, data)

    merged = patches.writes("elf")
    spans = patches.spans("pack")
    message_bytes = {index for off in messages for index in (off, off + 1)}
    for offset, data in spans:
        if any(index in message_bytes for index in range(offset, offset + len(data))):
            raise ValueError(f"pack span at {offset:#x} overlaps a message")
    path, combined = build_image(
        chunk, page, pad, merged, messages, spans, slots, pages, patches)
    check_image(path, combined, merged, messages, spans, slots, pages)
    try:
        revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        revision = ""
    inputs = [BIN, PLAN_PATH, DATA / "font" / "message-page0.json", font_path(plan["style"]["font"]),
              DATA / "font" / "small.json",
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
        "ui_text": ui_text_info,
        "inputs": {label(item): sha256(item) for item in inputs},
        "free_sectors": [item["free"] for item in packs],
        "message_tokens": {hex(off): hex(token) for off, (_was, token) in sorted(messages.items())},
        "cave": hex(CAVE),
        "code_offset": hex(CODE_OFF),
        "change_plan": patches.report(),
    }
    check_ui_text_report(report["ui_text"])
    (OUT_DIR / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    from busin.image_audit import audit_image
    change_audit = audit_image(OUT_DIR / "report.json", path)
    print(
        f"image audit: OK; {change_audit['sectors']['prefix_changed']} prefix sectors,"
        f" {change_audit['sectors']['moved_tail_changed']} moved-tail sectors,"
        f" {change_audit['sectors']['appended']} appended sectors"
    )
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
