"""Name-entry survey, name-entry patches, and the Chinese UI font pages.

survey_name_entry writes name-entry resources under build/ and leaves the disc
alone. name_entry_patches writes the name-entry strings and the cock texture.
ui_font_pages paints the UI font pages from the same cells and the common-04
template.

Do not retarget character names to 0x2A3CE8. The slots hold small-font
codes, the pitch is 16, and the message-font object is often 0 on this screen.
"""
import hashlib
import json
import struct

from PIL import Image, ImageDraw

from busin.disc import BIN, FILE_BASE, chunk_row, read_pack
from busin.disasm import load_elf
from busin.paths import BUILD, DATA
from busin.tmx import PSMCT16, PSMT4, read_tmx, save_png

TEXTURES = (
    ("common-01", 0, 0x00000, "common01.tim", 12),
    ("common-02", 0, 0x081C0, "common02.tim", 12),
    ("common-03", 0, 0x10380, "common03.tim", 12),
    ("common-04", 0, 0x18540, "common04.tim", 12),
    ("cock-00", 0, 0x20700, "cock_00.tim", 18),
    ("cock-01", 0, 0x28980, "cock_01.tim", 24),
    ("guild-01", 16, 0x08240, "Guild_01.tim", 1),
    ("guild-02", 16, 0x102A0, "Guild_02.tim", 1),
)
STRINGS = (
    (0x07, 0x43DD80, "名前は？", (0x223, 0x13E, 0x89, 0x1F)),
    (0x2A, 0x43DF30, "名前", (0x223, 0, 0x13E)),
    (0x2B, 0x43DF38, "種族", (0x228, 0, 0x229)),
    (0x2C, 0x43DF40, "属性", (0x22A, 0, 0x22B)),
    (0x2D, 0x43DF48, "職業", (0x224, 0, 0x225)),
    (0x2E, 0x43DF50, "レベル", (0xEA, 0x101, 0xE9)),
    (0x2F, 0x43DF58, "性格", (0x22B, 0, 0x322)),
)
STRING_TABLE = 0x43E9C8
KEYBOARD = 0x582BB8
SPRITES = 0x583EA0
NAME_TO_MESSAGE = 0x5266E0
OPERATIONS = (
    (-1, "カナ", (0xC6, 0xD5), (0x15667C, 0x156688)),
    (-2, "かな", (0x75, 0x84), (0x156668, 0x156674)),
    (-3, "英数", (0x23D, 0x16B), (0x156690, 0x15669C)),
    (-4, "記号", (0x23E, 0x23F), (0x1566A4, 0x1566AC)),
    (-5, "空白", (0x1D2, 0x243), (0x156654, 0x156660)),
    (-6, "終了", (0x240, 0x241), (0x1566C0, 0x1566C4)),
)
SOURCE_WORDS = (
    (0x10859C, 0x28420004),  # Four common font pages; overflow becomes blank.
    (0x1085CC, 0x0000282D),
    (0x14E9D8, 0x2444F3C0),
    (0x14E9EC, 0x24050006),  # Copy seven slots, then append the terminator.
    (0x14EA28, 0xA5020010),
    (0x14EEA4, 0xA4910000),
    (0x14EEC4, 0x24030007),
    (0x151168, 0x2A620007),
    (0x1699F4, 0x2A620017),
    (0x169A54, 0x26103EA0),
    (0x14BC54, 0xAE020004),
    (0x14BC6C, 0xAE020008),
    (0x1559F8, 0x0C04923C),
    (0x124910, 0x080491BA),
    (0x1247A0, 0x0000282D),
    (0x1247A8, 0x0000302D),
    (0x1247B0, 0x240700E8),
    (0x1247B8, 0x24080100),
    (0x124858, 0x0000282D),
    (0x124860, 0x24060001),
    (0x124868, 0x240700E8),
    (0x124870, 0x24080039),
    (0x29BE14, 0x244266E0),
    (0x29D61C, 0x244266E0),
    (0x29EF98, 0x244266E0),
    (0x2A0BE0, 0x244266E0),
    (0x2A2884, 0x244266E0),
)
HINTS = ((9, "confirm", (112, 152, 32, 16)),
         (14, "page-switch", (64, 184, 88, 16)),
         (20, "switch-shared", (100, 184, 48, 16)))
assert KEYBOARD + 110 * 2 == 0x582C94
LABELS = DATA / "screen" / "name" / "labels.json"
# Page 4 cells 1..23. Cell 0 stays blank. Sorted by Unicode scalar.
PAGE4 = tuple(range(0x401, 0x418))
INK = 15
COMMON4 = 0x18540
COCK0 = 0x20700
# Sprite 14 owns x=64..100 exclusively. Sprite 20 starts at x=100.
PAGE_TOGGLE = (14, 64, 184, 32, 16)
BACK = (10, 144, 152, 32, 16)
PAGE_WIDE_PNG = LABELS.parent / "page_toggle_wide.png"
PAGE_NARROW_PNG = LABELS.parent / "page_toggle_narrow.png"


def survey_name_entry():
    out = BUILD / "screen" / "name"
    out.mkdir(parents=True, exist_ok=True)
    elf = load_elf()
    report = {"textures": [], "strings": [], "operations": [],
              "keyboards": [], "hints": [], "source_instructions": []}
    for address, word in SOURCE_WORDS:
        assert struct.unpack_from("<I", elf, address - FILE_BASE)[0] == word
        report["source_instructions"].append({"address": address, "word": word})
    pictures = []
    with BIN.open("rb") as source:
        assert chunk_row(source, 0) == (6, 167)
        assert chunk_row(source, 16) == (2873, 194)
        for name, chunk, relative, comment, palettes in TEXTURES:
            sector, _count = chunk_row(source, chunk)
            offset = sector * 2048 + relative
            header = read_pack(source, offset, 16)
            size = struct.unpack_from("<I", header, 4)[0]
            assert header[:2] == b"\x02\x00" and header[8:12] == b"TMX0"
            raw = read_pack(source, offset, size)
            path = out / f"{name}.tmx"
            path.write_bytes(raw)
            texture = read_tmx(path)
            assert texture.comment == comment and len(texture.palettes) == palettes
            assert (texture.width, texture.height, texture.pixel_format,
                    texture.palette_format) == (256, 256, PSMT4, PSMCT16)
            palette = 2 if name.startswith("cock-") else 0
            png = out / f"{name}.pal{palette:02d}.png"
            save_png(texture, palette, png)
            with Image.open(png) as image:
                pictures.append(image.convert("RGB"))
            report["textures"].append({"name": name, "chunk": chunk,
                "pack_offset": offset, "size": size, "palettes": palettes,
                "preview_palette": palette, "sha256": hashlib.sha256(raw).hexdigest()})

    def glyph(code):
        page, cell = divmod(code, 256)
        x, y = cell % 16 * 16, cell // 16 * 16
        return pictures[page].crop((x, y, x + 16, y + 16))

    overview = Image.new("RGB", (4 * 280, 2 * 284), "#606060")
    draw = ImageDraw.Draw(overview)
    for i, (texture, picture) in enumerate(zip(report["textures"], pictures)):
        x, y = i % 4 * 280, i // 4 * 284
        draw.text((x, y), texture["name"], fill="white")
        overview.paste(picture, (x, y + 24))
    overview.save(out / "resources.png")
    text_preview = Image.new("RGB", (400, (len(STRINGS) + len(OPERATIONS)) * 26), "#444444")
    draw = ImageDraw.Draw(text_preview)
    for i, (index, address, reading, codes) in enumerate(STRINGS):
        assert struct.unpack_from("<I", elf, STRING_TABLE - FILE_BASE + index * 4)[0] == address
        assert struct.unpack_from(f"<{len(codes) + 1}H", elf, address - FILE_BASE) == (*codes, 0xFFFF)
        report["strings"].append({"index": index, "address": address,
                                  "source": reading, "codes": codes})
        draw.text((0, i * 26), f"{address:08x}", fill="white")
        for j, code in enumerate(codes):
            text_preview.paste(glyph(code).resize((24, 24)), (120 + j * 24, i * 26))
    for i, (value, reading, codes, addresses) in enumerate(OPERATIONS, len(STRINGS)):
        for j, (code, address) in enumerate(zip(codes, addresses)):
            register = 2 if value == -6 and j == 0 else 3 if j == 0 else 4
            assert struct.unpack_from("<I", elf, address - FILE_BASE)[0] == (0x24000000 | register << 16 | code)
        report["operations"].append({"value": value, "source": reading,
                                     "codes": codes, "instruction_addresses": addresses})
        draw.text((0, i * 26), f"op {value}", fill="white")
        for j, code in enumerate(codes):
            text_preview.paste(glyph(code).resize((24, 24)), (120 + j * 24, i * 26))
    text_preview.save(out / "screen-text.png")
    paper = Image.new("RGB", (232, 313))
    paper.paste(pictures[6].crop((0, 0, 232, 256)), (0, 0))
    paper.paste(pictures[7].crop((0, 1, 232, 58)), (0, 256))
    paper.save(out / "paper.png")
    for index, name, rectangle in HINTS:
        values = struct.unpack_from("<6h", elf, SPRITES - FILE_BASE + index * 12)
        assert values[:4] == rectangle and values[4:] == rectangle[2:]
        x, y, width, height = rectangle
        pictures[4].crop((x, y, x + width, y + height)).save(out / f"{name}.png")
        report["hints"].append({"index": index, "name": name, "rectangle": rectangle})
    for page in range(4):
        address = KEYBOARD + page * 220
        codes = struct.unpack_from("<110h", elf, address - FILE_BASE)
        assert codes[-10:] == (-6,) * 10
        assert all(-7 <= code < 1024 for code in codes)
        assert codes[0] == (0x70, 0xC1, 0x21, 1)[page]
        report["keyboards"].append({"page": page, "address": address, "codes": codes})
    selectable = sorted({code for page in report["keyboards"] for code in page["codes"] if code >= 0})
    assert len(selectable) == 255 and (selectable[0], selectable[-1]) == (1, 0x111)
    mapping = []
    for code in selectable:
        token = struct.unpack_from("<H", elf, NAME_TO_MESSAGE - FILE_BASE + code * 2)[0]
        assert 1 <= token <= 0x100
        mapping.append({"ui_code": code, "message_token": token})
    samples = ((0, 0), (0x10, 1), (0x21, 0xB), (0x70, 0x3F),
               (0xC1, 0x90), (0x111, 0xE0), (0x223, 0x257), (0x13E, 0xFFFF))
    for code, token in samples:
        assert struct.unpack_from("<H", elf, NAME_TO_MESSAGE - FILE_BASE + code * 2)[0] == token
    report["name_to_message"] = {"address": NAME_TO_MESSAGE, "selectable": mapping,
                                  "samples": [{"ui_code": c, "message_token": t} for c, t in samples]}
    report["elf_sha256"] = hashlib.sha256(elf).hexdigest()
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return out


def load_labels():
    data = json.loads(LABELS.read_text(encoding="utf-8"))
    strings = {int(key, 0): value for key, value in data["strings"].items()}
    operations = {int(key, 0): value for key, value in data["operations"].items()}
    if set(strings) != {index for index, *_ in STRINGS}:
        raise ValueError("label strings do not match the name-entry fields")
    if set(operations) != {value for value, *_ in OPERATIONS}:
        raise ValueError("label operations do not match the name-entry keys")
    confirm = data["confirm"]
    back = data["back"]
    if not isinstance(confirm, str) or len(confirm) != 2 or " " in confirm:
        raise ValueError("confirm label must be two characters")
    if not isinstance(back, str) or len(back) != 2 or " " in back:
        raise ValueError("back label must be two characters")
    for value, text in operations.items():
        if len(text) != 2 or " " in text:
            raise ValueError(f"operation {value} must be two characters")
    return strings, operations, confirm, back


def label_chars(strings, operations):
    chars = sorted({ch for text in (*strings.values(), *operations.values()) for ch in text if ch != " "})
    if len(chars) != len(PAGE4):
        raise ValueError(f"{len(chars)} label glyphs, {len(PAGE4)} cells")
    return dict(zip(chars, PAGE4))


def encode_label(text, cells):
    out = []
    for ch in text:
        if ch == " ":
            out.append(0)
        elif ch not in cells:
            raise ValueError(f"no cell for {ch}")
        else:
            out.append(cells[ch])
    return out


def referenced_codes(elf):
    codes = set()
    base = STRING_TABLE - FILE_BASE
    for index in range(128):
        ptr = struct.unpack_from("<I", elf, base + index * 4)[0]
        if not 0x430000 <= ptr < 0x450000:
            raise ValueError(f"string table entry {index} is {ptr:#x}")
        off = ptr - FILE_BASE
        for _ in range(80):
            word = struct.unpack_from("<H", elf, off)[0]
            off += 2
            if word == 0xFFFF:
                break
            codes.add(word)
        else:
            raise ValueError(f"string {index} is unterminated")
    keyboard = struct.unpack_from("<440h", elf, KEYBOARD - FILE_BASE)
    codes.update(code for code in keyboard if code >= 0)
    for off in range(0, len(elf) - 3, 4):
        word = struct.unpack_from("<I", elf, off)[0]
        op = word >> 26
        rs = (word >> 21) & 31
        imm = word & 0xFFFF
        if op in (0x09, 0x0D) and rs == 0 and imm < 0x400:
            codes.add(imm)
    return codes


def string_rooms(elf):
    base = STRING_TABLE - FILE_BASE
    entries = [struct.unpack_from("<I", elf, base + index * 4)[0] for index in range(128)]
    ordered = sorted(set(entries))
    following = dict(zip(ordered, ordered[1:]))
    rooms = {}
    for index, addr in enumerate(entries):
        nxt = following.get(addr)
        if nxt is None:
            continue
        if (nxt - addr) % 2:
            raise ValueError(f"string {index:#x} is not halfword aligned to the next")
        rooms[index] = (addr, (nxt - addr) // 2)
    return rooms


def set_nibble(blob, pix, width, x, y, index):
    at = pix + (y * width + x) // 2
    if x & 1:
        blob[at] = (blob[at] & 0x0F) | ((index & 0xF) << 4)
    else:
        blob[at] = (blob[at] & 0xF0) | (index & 0xF)


def page_labels():
    data = json.loads(LABELS.read_text(encoding="utf-8"))
    wide, narrow = data["page_wide"], data["page_narrow"]
    if (wide, narrow) != ("页面切换", "切换"):
        raise ValueError("page toggle text does not fit the measured slot")
    return wide, narrow


def coverage_index(level):
    """Map 0–255 coverage onto common04's 16-step ramp. 0 is bare, 15 is solid."""
    return (level * 15 + 127) // 255


def render_page_image(text, glyphs):
    """Binary preview of a cock label. Mid-gray and above becomes ink."""
    image = Image.new("1", (len(text) * 16, 16), 0)
    for index, char in enumerate(text):
        cell = Image.new("1", (16, 16))
        cell.putdata([1 if value >= 128 else 0 for value in glyphs[char].get_flattened_data()])
        image.paste(cell, (index * 16, 0))
    return image


def paint_mask(blob, pix, width, x, y, mask, ink, ramp=False):
    for row in range(16):
        for col in range(16):
            level = mask.getpixel((col, row))
            if mask.mode == "1":
                level = 255 if level else 0
            index = coverage_index(level) if ramp else (ink if level >= 128 else 0)
            set_nibble(blob, pix, width, x + col, y + row, index)


def texture_blob(handle, relative):
    pack = chunk_row(handle, 0)[0] * 2048 + relative
    size = struct.unpack_from("<I", read_pack(handle, pack, 8), 4)[0]
    blob = bytearray(read_pack(handle, pack, size))
    if blob[8:12] != b"TMX0" or blob[0x16] != 0x14:
        raise ValueError(f"texture at {relative:#x} is not PSMT4")
    width, height = struct.unpack_from("<HH", blob, 0x12)
    pix = 0x40 + blob[0x10] * 32
    if (width, height) != (256, 256) or pix + width * height // 2 != len(blob):
        raise ValueError(f"texture at {relative:#x} is not 256x256")
    return pack, blob, pix


def _cell_bytes(blob, pix, cell):
    x = (cell % 16) * 16
    y = (cell // 16) * 16
    stride = 256 // 2
    return b"".join(
        blob[pix + (y + line) * stride + x // 2:pix + (y + line) * stride + x // 2 + 8]
        for line in range(16))


def ui_font_pages(extra=None):
    """Chinese UI pages. Name-entry glyphs stay at 0x0401; extra paints from 0x0418, including those 23 again."""
    from busin.patch.ui_pages import LAST_CODE, PAGE_BYTES, PAGE_COUNT
    from busin.ui_font import load_ui_glyphs

    extra = extra or {}
    strings, operations, _confirm, _back = load_labels()
    cells = label_chars(strings, operations)
    if set(extra.values()) & set(cells.values()):
        raise ValueError("a new glyph uses a name-entry cell")
    glyphs, _origins, _fonts = load_ui_glyphs([*cells, *extra])
    with BIN.open("rb") as handle:
        _pack, blob, pix = texture_blob(handle, COMMON4)
    if len(blob) != PAGE_BYTES or blob[0x10] != 12:
        raise ValueError("common04 is not the UI page template")
    blank = bytearray(blob)
    blank[pix:] = bytes(len(blank) - pix)
    pages = [bytearray(blank) for _index in range(PAGE_COUNT)]

    def paint(code, char):
        page = (code >> 8) - 4
        cell = code & 0xFF
        if not 0 <= page < PAGE_COUNT or (page == 0 and cell == 0):
            raise ValueError(f"{char} does not fit cell {code:#x}")
        paint_mask(pages[page], pix, 256, (cell % 16) * 16, (cell // 16) * 16, glyphs[char], INK, ramp=True)
        if _cell_bytes(pages[page], pix, cell) == bytes(128):
            raise ValueError(f"{char} painted blank")

    for char, code in cells.items():
        paint(code, char)
    for char, code in extra.items():
        if not 0x0418 <= code <= LAST_CODE:
            raise ValueError(f"{char} is outside 0x0418-{LAST_CODE:#x}")
        paint(code, char)
    if _cell_bytes(pages[0], pix, 0) != bytes(128) or _cell_bytes(pages[0], pix, 1) == bytes(128):
        raise ValueError("page 4 painted cell 0 or left cell 1 blank")
    packed = b"".join(pages)
    if len(packed) != PAGE_BYTES * PAGE_COUNT:
        raise ValueError("ui font page bytes")
    return packed


def name_entry_patches():
    """ELF file offsets and PACKDATA spans for the name-entry labels."""
    from busin.ui_font import load_ui_glyphs

    strings, operations, confirm, back = load_labels()
    wide, narrow = page_labels()
    cells = label_chars(strings, operations)
    glyphs, _origins, _fonts = load_ui_glyphs([*cells, *confirm, *back, *wide, *narrow])
    for path, text in ((PAGE_WIDE_PNG, wide), (PAGE_NARROW_PNG, narrow)):
        rendered = render_page_image(text, glyphs)
        with Image.open(path) as saved:
            got = saved.convert("1")
        if got.size != rendered.size or got.tobytes() != rendered.tobytes():
            raise ValueError(f"{path.name} does not match {text}")
    elf = load_elf()
    if set(PAGE4) & referenced_codes(elf):
        raise ValueError("a page-4 glyph code is already used")
    rooms = string_rooms(elf)
    writes = {}
    for index, address, _source, codes in STRINGS:
        encoded = encode_label(strings[index], cells)
        addr, room = rooms[index]
        if addr != address or len(encoded) + 1 > room:
            raise ValueError(f"string {index:#x} does not fit")
        off = address - FILE_BASE
        old = struct.unpack_from(f"<{len(codes) + 1}H", elf, off)
        if old != (*codes, 0xFFFF):
            raise ValueError(f"string {index:#x} on disc is not the expected Japanese")
        writes[off] = struct.pack(f"<{len(encoded) + 1}H", *encoded, 0xFFFF)
    for value, _source, codes, addresses in OPERATIONS:
        encoded = encode_label(operations[value], cells)
        for slot, (code, address, new) in enumerate(zip(codes, addresses, encoded)):
            register = 2 if value == -6 and slot == 0 else 3 if slot == 0 else 4
            word = struct.unpack_from("<I", elf, address - FILE_BASE)[0]
            expect = 0x24000000 | register << 16 | code
            if word != expect:
                raise ValueError(f"operation at {address:#x} is {word:#x}")
            writes[address - FILE_BASE] = struct.pack("<I", (expect & 0xFFFF0000) | new)
    with BIN.open("rb") as handle:
        if chunk_row(handle, 0) != (6, 167):
            raise ValueError("chunk 0 moved")
        pack, blob, pix = texture_blob(handle, COCK0)
        if blob[0x10] != 18:
            raise ValueError("cock_00 palette count")
        rect = struct.unpack_from("<6h", elf, SPRITES - FILE_BASE + 9 * 12)
        if rect != (112, 152, 32, 16, 32, 16):
            raise ValueError(f"confirm sprite is {rect}")
        for index, char in enumerate(confirm):
            paint_mask(blob, pix, 256, 112 + index * 16, 152, glyphs[char], INK)
        _index, x, y, w, h = PAGE_TOGGLE
        for index, char in enumerate(narrow):
            paint_mask(blob, pix, 256, x + index * 16, y, glyphs[char], INK)
        bx, by, bw, bh = BACK[1:]
        back_rect = struct.unpack_from("<6h", elf, SPRITES - FILE_BASE + BACK[0] * 12)
        if back_rect != (bx, by, bw, bh, bw, bh):
            raise ValueError(f"back sprite is {back_rect}")
        for index, char in enumerate(back):
            paint_mask(blob, pix, 256, bx + index * 16, by, glyphs[char], INK)
        spans = [(pack, bytes(blob))]
    rect_off = SPRITES - FILE_BASE + PAGE_TOGGLE[0] * 12
    old = struct.unpack_from("<6h", elf, rect_off)
    if old != (64, 184, 88, 16, 88, 16):
        raise ValueError(f"page-toggle sprite is {old}")
    if w > 100 - x:
        raise ValueError("page toggle overlaps sprite 20")
    writes[rect_off] = struct.pack("<6h", x, y, w, h, w, h)
    info = {"ink": INK, "cells": {char: hex(code) for char, code in cells.items()},
            "page_wide": wide, "page_narrow": narrow}
    return writes, spans, info


def self_test():
    mask = Image.new("1", (16, 16), 0)
    mask.putpixel((1, 0), 1)
    blob = bytearray(32 * 16 // 2)
    paint_mask(blob, 0, 32, 0, 0, mask, INK)
    if blob[0] != (INK << 4):
        raise AssertionError(f"nibble paint wrote {blob[0]:#x}")
    ramp = Image.new("L", (16, 16), 0)
    ramp.putpixel((0, 0), 255)
    ramp.putpixel((1, 0), 128)
    painted = bytearray(32 * 16 // 2)
    paint_mask(painted, 0, 32, 0, 0, ramp, INK, ramp=True)
    if coverage_index(255) != 15 or coverage_index(128) != 8 or painted[0] != 0x8F:
        raise AssertionError(f"coverage ramp wrote {painted[0]:#x}")
    strings, operations, confirm, back = load_labels()
    cells = label_chars(strings, operations)
    if tuple(cells.values()) != PAGE4 or encode_label("姓 名", cells) != [cells["姓"], 0, cells["名"]]:
        raise AssertionError("label codes are not 0x0401..0x0417 with space kept at 0")
    from busin.patch.button_font import self_test as button_code
    from busin.patch.ui_pages import self_test as page_code
    button_code()
    page_code()
    if len(confirm) != 2 or len(back) != 2 or any(len(text) != 2 for text in operations.values()):
        raise AssertionError("two-character labels changed")
    if strings[0x2E] != "等 级" or encode_label(strings[0x2E], cells) != [cells["等"], 0, cells["级"]]:
        raise AssertionError("level label is not two glyphs with a space")
    wide, narrow = page_labels()
    if (len(wide), len(narrow)) != (4, 2) or len(narrow) * 16 != PAGE_TOGGLE[3]:
        raise AssertionError("page toggle length")
    print("name-entry label self-test: OK")


def disc_self_test():
    from busin.patch.button_font import check_source as check_button
    from busin.patch.ui_pages import check_source

    elf = load_elf()
    check_source(elf)
    check_button(elf)
    strings, operations, _confirm, _back = load_labels()
    cells = label_chars(strings, operations)
    if set(PAGE4) & referenced_codes(elf):
        raise AssertionError("a page-4 glyph code is already used")
    writes, spans, _info = name_entry_patches()
    ui_font_pages()
    for index, address, _source, _codes in STRINGS:
        encoded = (*encode_label(strings[index], cells), 0xFFFF)
        got = struct.unpack_from(f"<{len(encoded)}H", writes[address - FILE_BASE])
        if got != encoded:
            raise AssertionError(f"string {index:#x} codes are {tuple(hex(item) for item in got)}")
    for value, _source, _codes, addresses in OPERATIONS:
        encoded = encode_label(operations[value], cells)
        for address, new in zip(addresses, encoded):
            word = struct.unpack_from("<I", writes[address - FILE_BASE])[0]
            if word & 0xFFFF != new:
                raise AssertionError(f"addiu at {address:#x} is {word:#x}")
    with BIN.open("rb") as handle:
        pack, blob, _pix = texture_blob(handle, COMMON4)
        if read_pack(handle, pack, len(blob)) != bytes(blob):
            raise AssertionError("common04 reread differs")
        if any(offset == pack for offset, _data in spans):
            raise AssertionError("name entry still rewrites common04")
    print("name-entry disc self-test: OK")
