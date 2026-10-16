"""Paint the town-plaque line from bottom.zh.png back into Tw_map02.

The frame uses the first palette. This rectangle uses the second.
Only its color indexes are replaced. A missing .zh.png leaves the
rectangle alone. The English line above it is the same name, so it
stays as on the disc.
"""
import struct
import tempfile
from pathlib import Path

from busin.disc import chunk_row, read_pack
from busin.paths import DATA
from busin.tmx import map_png_to_indices, rgb_palette

TEXT = DATA / "picture" / "images.json"
FILE = "Tw_map02"
DIR = "town"
CHUNK = 8
AT = 0x10480
SLOT = 2
SIZE = 33344
WIDTH = 256
HEIGHT = 256
PIXEL_AT = 0x40 + 16 * 32
PALETTE = 1
# Second palette, PSMCT16. Bit 15 is clear on every entry.
GRAY = (
    0x0000, 0x0842, 0x1084, 0x18C6, 0x2108, 0x2D6B, 0x39CE, 0x4210,
    0x4631, 0x4E73, 0x56B5, 0x5EF7, 0x6739, 0x6F7B, 0x77BD, 0x7FFF,
)
# Japanese line under the picture. The English line above it is the same name.
BOXES = {
    "bottom": (0, 232, 104, 16),
}
# ドゥーハンの迷宮. Read left to right, aligned to the right edge.
CAPTIONS = {
    "bottom": "杜汉的迷宫",
}
GLYPH = 14
# The original line stops on this palette step. The brighter entries read as white.
INK = 11
STEPS = tuple((value & 0x1F) << 3 for value in GRAY[:INK + 1])


def load_rects() -> list[dict]:
    from busin.ui import load_images

    parts = load_images(TEXT)["part"]
    if [item["name"] for item in parts] != [FILE]:
        raise ValueError("partial image is not Tw_map02")
    data = parts[0]
    if (data["name"], data["dir"], data["chunk"], int(data["offset"], 0), data["slot"], data["width"], data["height"]) != (
        FILE, DIR, CHUNK, AT, SLOT, WIDTH, HEIGHT,
    ):
        raise ValueError("plaque location")
    rows = data["rects"]
    if not isinstance(rows, list) or len(rows) != len(BOXES):
        raise ValueError("plaque rects")
    found = {}
    for item in rows:
        if not isinstance(item, dict) or set(item) != {"name", "x", "y", "width", "height", "palette"}:
            raise ValueError("plaque rect")
        label = item["name"]
        if (item["x"], item["y"], item["width"], item["height"]) != BOXES.get(label) or item["palette"] != PALETTE:
            raise ValueError(f"plaque {label} rectangle")
        if label in found:
            raise ValueError(f"plaque {label} is repeated")
        found[label] = item
    if set(found) != set(BOXES):
        raise ValueError("plaque rects")
    return [found[name] for name in BOXES]


def verify(blob: bytes) -> None:
    if len(blob) != SIZE or struct.unpack_from("<I", blob, 4)[0] != SIZE:
        raise ValueError("Tw_map02 size")
    if blob[:2] != b"\x02\x00" or blob[8:12] != b"TMX0":
        raise ValueError("Tw_map02 is not a TMX0 texture")
    if blob[0x24:0x40].split(b"\x00", 1)[0] != f"{FILE}.tim".encode():
        raise ValueError("Tw_map02 comment")
    width, height = struct.unpack_from("<HH", blob, 0x12)
    if (blob[0x10], blob[0x11], width, height, blob[0x16]) != (16, 2, WIDTH, HEIGHT, 0x14):
        raise ValueError("Tw_map02 layout")
    have = struct.unpack_from("<16H", blob, 0x40 + PALETTE * 32)
    if have != GRAY:
        raise ValueError("Tw_map02 second palette")


def locate(handle) -> tuple[int, bytes]:
    start, count = chunk_row(handle, CHUNK)
    if count * 2048 < AT + SIZE:
        raise ValueError("chunk 8 does not contain Tw_map02")
    offset = start * 2048 + AT
    blob = read_pack(handle, offset, SIZE)
    verify(blob)
    return offset, blob


def pixels_of(blob: bytes, x: int, y: int, width: int, height: int) -> tuple[int, ...]:
    values = []
    for row in range(height):
        for col in range(width):
            byte = blob[PIXEL_AT + ((y + row) * WIDTH + x + col) // 2]
            values.append(byte & 0xF if (x + col) % 2 == 0 else byte >> 4)
    return tuple(values)


def paint(blob: bytearray, x: int, y: int, width: int, height: int, indices: list[int]) -> None:
    if x % 2 or width % 2 or len(indices) != width * height:
        raise ValueError("plaque rectangle")
    for row in range(height):
        for col in range(0, width, 2):
            low = indices[row * width + col]
            high = indices[row * width + col + 1]
            at = PIXEL_AT + ((y + row) * WIDTH + x + col) // 2
            blob[at] = (low & 0x0F) | ((high & 0x0F) << 4)


def patch(blob: bytes, folder: Path) -> bytes:
    verify(blob)
    out = bytearray(blob)
    changed = False
    for item in load_rects():
        zh = folder / f"{item['name']}.zh.png"
        if not zh.is_file():
            continue
        x, y, width, height = BOXES[item["name"]]
        raw = blob[0x40 + item["palette"] * 32:0x40 + (item["palette"] + 1) * 32]
        indices, _error = map_png_to_indices(
            zh, rgb_palette(raw), width, height, alpha=False, original=pixels_of(blob, x, y, width, height),
        )
        paint(out, x, y, width, height, indices)
        changed = True
    if not changed:
        return blob
    if len(out) != SIZE or out[:PIXEL_AT] != blob[:PIXEL_AT]:
        raise ValueError("plaque header changed")
    return bytes(out)


def spans(handle) -> list[tuple[int, bytes]]:
    offset, blob = locate(handle)
    updated = patch(blob, DATA / "picture" / DIR)
    if updated == blob:
        return []
    return [(offset, updated)]


def _serif_font():
    from PIL import ImageFont

    from busin.font import FONT_DIR

    font = ImageFont.truetype(str(FONT_DIR / "NotoSerifSC-VF.ttf"), GLYPH)
    font.set_variation_by_name("Black")
    return font


def _serif_cell(font, char):
    from busin.font import seat_baseline

    if bytes(font.getmask(char)) == bytes(font.getmask(chr(0x10FFFF))):
        raise ValueError(f"font has no glyph for {char!r}")
    return seat_baseline(font, char, GLYPH)[0]


def _ink(coverage: int) -> int:
    if not coverage:
        return 0
    return STEPS[round(coverage * INK / 255)]


def render_caption(name: str):
    from PIL import Image

    text = CAPTIONS[name]
    _x, _y, width, height = BOXES[name]
    if GLYPH > height or len(text) * GLYPH > width:
        raise ValueError(f"plaque {name} does not fit")
    font = _serif_font()
    image = Image.new("RGB", (width, height), (0, 0, 0))
    x0 = width - len(text) * GLYPH
    y0 = (height - GLYPH) // 2
    for index, char in enumerate(text):
        cell = _serif_cell(font, char)
        origin = x0 + index * GLYPH
        for y in range(GLYPH):
            for x in range(GLYPH):
                value = _ink(cell.getpixel((x, y)))
                image.putpixel((origin + x, y0 + y), (value, value, value))
    return image


def write_captions(folder: Path | None = None) -> Path:
    folder = folder or (DATA / "picture" / DIR)
    folder.mkdir(parents=True, exist_ok=True)
    for name in CAPTIONS:
        render_caption(name).save(folder / f"{name}.zh.png")
    return folder


def _sample() -> bytearray:
    blob = bytearray(b"\xAB" * SIZE)
    blob[:2] = b"\x02\x00"
    struct.pack_into("<I", blob, 4, SIZE)
    blob[8:12] = b"TMX0"
    blob[0x10] = 16
    blob[0x11] = 2
    struct.pack_into("<HH", blob, 0x12, WIDTH, HEIGHT)
    blob[0x16] = 0x14
    blob[0x24:0x30] = b"Tw_map02.tim"
    blob[0x30] = 0
    struct.pack_into("<16H", blob, 0x40 + PALETTE * 32, *GRAY)
    return blob


def self_test() -> None:
    from PIL import Image

    assert {item["name"] for item in load_rects()} == set(BOXES)
    base = bytearray(b"\xAB" * (WIDTH * HEIGHT // 2))
    blob = bytearray(SIZE)
    blob[PIXEL_AT:PIXEL_AT + len(base)] = base
    narrow = [0] * (104 * 16)
    narrow[0] = 15
    paint(blob, *BOXES["bottom"], narrow)
    row = PIXEL_AT + (232 * WIDTH) // 2
    assert blob[row] == 0x0F and blob[row + 1] == 0x00 and blob[row + 52] == 0xAB
    wide = (104, 232, 112, 16)
    before = bytes(blob)
    paint(blob, *wide, [4] * (112 * 16))
    assert blob[row] == 0x0F and blob[row + 52] == 0x44 and blob[row + 108] == 0xAB
    assert blob[PIXEL_AT:row] == before[PIXEL_AT:row]
    assert blob[PIXEL_AT + ((232 + 16) * WIDTH) // 2:] == before[PIXEL_AT + ((232 + 16) * WIDTH) // 2:]
    source = _sample()
    colors = rgb_palette(struct.pack("<16H", *GRAY))
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        assert patch(bytes(source), folder) == bytes(source)
        Image.new("RGB", BOXES["bottom"][2:], colors[15]).save(folder / "bottom.zh.png")
        painted = patch(bytes(source), folder)
        assert painted[:PIXEL_AT] == bytes(source[:PIXEL_AT])
        assert painted[row:row + 52] == bytes([0xFF]) * 52
        assert painted[row + 52:row + 108] == bytes(source[row + 52:row + 108])
        assert painted[PIXEL_AT:row] == bytes(source[PIXEL_AT:row])
        assert painted[PIXEL_AT + ((232 + 16) * WIDTH) // 2:] == bytes(source[PIXEL_AT + ((232 + 16) * WIDTH) // 2:])
        Image.new("RGB", (1, 1), colors[0]).save(folder / "bottom.zh.png")
        try:
            patch(bytes(source), folder)
        except ValueError:
            pass
        else:
            raise AssertionError("wrong-sized plaque png was accepted")
    from busin.font import FONT_DIR
    if (FONT_DIR / "NotoSerifSC-VF.ttf").is_file():
        font = _serif_font()
        glyphs = {char: _serif_cell(font, char) for char in set("".join(CAPTIONS.values()))}
        bottom = render_caption("bottom")
        assert bottom.size == (104, 16)
        black = (0, 0, 0)

        def bright(cell):
            for y in range(GLYPH):
                for x in range(GLYPH):
                    if cell.getpixel((x, y)) >= 200:
                        return x, y
            raise AssertionError("glyph has no bright pixel")

        dx, dy = bright(glyphs["杜"])
        gx, gy = bright(glyphs["宫"])
        x0 = 104 - len(CAPTIONS["bottom"]) * GLYPH
        y0 = (16 - GLYPH) // 2
        assert bottom.getpixel((x0 + dx, y0 + dy)) != black
        assert bottom.getpixel((x0 + 4 * GLYPH + gx, y0 + gy)) != black
        assert all(bottom.getpixel((x, y)) == black for x in range(x0) for y in range(16))
        assert STEPS[-1] == 184
        values = {bottom.getpixel((x, y))[0] for y in range(16) for x in range(104)}
        assert values <= set(STEPS) and STEPS[-1] in values
        assert any(0 < value < STEPS[-1] for value in values)
    print("plaque self-test: OK")


def disc_self_test() -> None:
    from busin.paths import BIN

    with BIN.open("rb") as handle:
        offset, blob = locate(handle)
        got = spans(handle)
        present = [DATA / "picture" / DIR / f"{name}.zh.png" for name in BOXES]
        if not any(path.is_file() for path in present):
            assert got == []
        elif got:
            assert len(got) == 1 and got[0][0] == offset and len(got[0][1]) == SIZE
            assert got[0][1][:PIXEL_AT] == blob[:PIXEL_AT]
    painted = bytearray(blob)
    paint(painted, 104, 232, 112, 16, [15] * (112 * 16))
    assert bytes(painted[:PIXEL_AT]) == blob[:PIXEL_AT] and len(painted) == SIZE
    row = PIXEL_AT + (232 * WIDTH) // 2
    assert painted[row:row + 52] == blob[row:row + 52]
    assert painted[row + 108] == blob[row + 108]
    assert painted[PIXEL_AT:row] == blob[PIXEL_AT:row]
    assert painted[PIXEL_AT + (248 * WIDTH) // 2:] == blob[PIXEL_AT + (248 * WIDTH) // 2:]
    print("plaque disc self-test: OK")
