"""Render Noto Sans SC Regular into 16×16 UI cells and paint the Chinese pages."""
import hashlib
import json
import struct

from PIL import Image, ImageDraw, ImageFont

from busin.disc import BIN, chunk_row, read_pack
from busin.font import FONT_INPUTS, FONT_DIR, font_path, seat_stroke_baseline
from busin.patch.ui_pages import FIRST_CODE, LAST_CODE, PAGE_BYTES, PAGE_COUNT
from busin.paths import BUILD

CELL = 16
EM = 16
BASELINE = CELL - 1
SOLID = 128
SAMPLES = ("菜单", "存档", "设置", "攻击", "防御", "道具", "状态", "装备")
FONT = next(item for item in FONT_INPUTS if item.role == "ui")
FONTS = (
    ("primary", FONT_DIR / FONT.filename, FONT.version, FONT.sha256),
)
VARIATION = "Regular"


def _checked_fonts():
    fonts = []
    for role, path, version, expected in FONTS:
        font_path(path.name)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != expected:
            raise ValueError(f"{role} font SHA-256 does not match {version}: {path.name}")
        fonts.append({"role": role, "file": path.name, "version": version, "sha256": digest})
    return fonts


def _primary():
    font = ImageFont.truetype(str(FONTS[0][1]), EM)
    font.set_variation_by_name(VARIATION)
    if font.getname() != (FONT.family, FONT.style):
        raise ValueError(f"primary font is {font.getname()}")
    ascent, descent = font.getmetrics()
    if (ascent, descent) != (EM, 4):
        raise ValueError(f"primary em is {ascent}+{descent}, expected 16+4")
    return font


def _placed(font, char):
    return seat_stroke_baseline(font, char, CELL)


def load_ui_glyphs(wanted):
    fonts = _checked_fonts()
    font = _primary()
    missing = font.getmask(chr(0x10FFFF), mode="L")
    glyphs = {}
    raised = {}
    for char in sorted(set(wanted)):
        bitmap = font.getmask(char, mode="L")
        if bitmap.size == missing.size and bytes(bitmap) == bytes(missing):
            raise ValueError(f"font is missing {char!r}")
        glyphs[char], raised[char] = _placed(font, char)
    return glyphs, raised, fonts


def render_ui_glyphs():
    wanted = sorted(set("".join(SAMPLES)) - {" "})
    glyphs, raised, fonts = load_ui_glyphs(wanted)
    out = BUILD / "font" / "ui-glyphs"
    out.mkdir(parents=True, exist_ok=True)
    columns = 16
    atlas = Image.new("RGBA", (columns * CELL, (len(wanted) + columns - 1) // columns * CELL))
    report = {"fonts": fonts, "variation": VARIATION, "em_size": EM, "cell_size": CELL,
              "font_ascent": EM, "font_descent": 4, "baseline": BASELINE,
              "samples": SAMPLES, "glyphs": []}
    packed = bytearray()
    for index, char in enumerate(wanted):
        mask = glyphs[char]
        png = Image.new("RGBA", (CELL, CELL), "white")
        png.putalpha(mask)
        filename = f"u{ord(char):04x}.png"
        png.save(out / filename)
        atlas.paste(png, (index % columns * CELL, index // columns * CELL))
        raw = mask.tobytes()
        assert len(raw) == CELL * CELL
        packed.extend(raw)
        report["glyphs"].append({"character": char, "preview_cell": index, "png": filename,
                                  "raised": raised[char], "ink_box": mask.getbbox(),
                                  "bitmap_sha256": hashlib.sha256(raw).hexdigest()})
    atlas.save(out / "cells.png")
    (out / "cells-l8.raw").write_bytes(packed)
    preview = Image.new("RGB", (160, len(SAMPLES) * 24 + 8), "#303030")
    for row, sample in enumerate(SAMPLES):
        for col, char in enumerate(sample):
            if char != " ":
                preview.paste("#e8e8e8", (8 + col * CELL, 4 + row * 24), glyphs[char])
    preview.save(out / "text.png")
    preview.resize((preview.width * 3, preview.height * 3), Image.Resampling.NEAREST).save(out / "text-large.png")
    sheet = Image.new("RGB", (8 * 88, (len(wanted) + 7) // 8 * 88), "#303030")
    draw = ImageDraw.Draw(sheet)
    for index, char in enumerate(wanted):
        x, y = index % 8 * 88, index // 8 * 88
        draw.rectangle((x, y, x + 63, y + 63), outline="#555555")
        sheet.paste("#e8e8e8", (x, y), glyphs[char].resize((64, 64), Image.Resampling.NEAREST))
        draw.text((x, y + 66), f"U+{ord(char):04X}", fill="white")
    sheet.save(out / "cells-large.png")
    output_names = [f"u{ord(char):04x}.png" for char in wanted]
    output_names.extend(("cells.png", "cells-l8.raw", "text.png", "text-large.png", "cells-large.png"))
    report["outputs_sha256"] = {name: hashlib.sha256((out / name).read_bytes()).hexdigest()
                                for name in sorted(output_names)}
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return out


def _solid_rows(mask):
    return [y for y in range(CELL) if any(mask.getpixel((x, y)) >= SOLID for x in range(CELL))]


def self_test():
    wanted = set("".join(SAMPLES)) - {" "}
    glyphs, raised, _fonts = load_ui_glyphs(wanted | set("()pqyQ/{}"))
    assert raised["菜"] == raised["单"] == 1
    assert raised["p"] == raised["q"] == raised["y"] == 4
    assert raised["("] == raised[")"] == raised["{"] == raised["}"] == 2
    assert raised["Q"] == 3
    assert raised["/"] == 2
    assert _solid_rows(glyphs["p"])[0] == 2 and _solid_rows(glyphs["p"])[-1] == CELL - 2
    assert any(0 < value < 255 for value in glyphs["单"].get_flattened_data())
    name = glyphs["菜"].getbbox()
    assert name[0] == 0 and name[2] == CELL
    try:
        load_ui_glyphs({chr(0x10FFFF)})
    except ValueError as error:
        assert "font is missing" in str(error)
    else:
        raise AssertionError("missing glyph was silently accepted")


INK = 15
UI_TEMPLATE = 0x18540


def set_nibble(blob, pix, width, x, y, index):
    at = pix + (y * width + x) // 2
    if x & 1:
        blob[at] = (blob[at] & 0x0F) | ((index & 0xF) << 4)
    else:
        blob[at] = (blob[at] & 0xF0) | (index & 0xF)


def coverage_index(level):
    """Map 0–255 coverage onto the template's 16-step ramp."""
    return (level * 15 + 127) // 255


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
    """Paint the Chinese UI glyphs into six pages after the four source pages."""
    extra = extra or {}
    glyphs, _origins, _fonts = load_ui_glyphs(extra)
    with BIN.open("rb") as handle:
        _pack, blob, pix = texture_blob(handle, UI_TEMPLATE)
    if len(blob) != PAGE_BYTES or blob[0x10] != 12:
        raise ValueError("the UI page template has changed")
    blank = bytearray(blob)
    blank[pix:] = bytes(len(blank) - pix)
    pages = [bytearray(blank) for _index in range(PAGE_COUNT)]

    for char, code in extra.items():
        if not FIRST_CODE <= code <= LAST_CODE:
            raise ValueError(f"{char} is outside {FIRST_CODE:#x}-{LAST_CODE:#x}")
        page = (code >> 8) - 4
        cell = code & 0xFF
        paint_mask(pages[page], pix, 256, (cell % 16) * 16, (cell // 16) * 16,
                   glyphs[char], INK, ramp=True)
        if _cell_bytes(pages[page], pix, cell) == bytes(128):
            raise ValueError(f"{char} painted blank")

    packed = b"".join(pages)
    if len(packed) != PAGE_BYTES * PAGE_COUNT:
        raise ValueError("ui font page bytes")
    return packed
