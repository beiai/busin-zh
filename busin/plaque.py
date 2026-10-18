"""Paint the town-plaque lines from their .zh.png crops.

The picture frame uses the first palette. These lines use the second.
Only those color indexes are replaced. A missing .zh.png leaves that
line alone. English lines stay on the disc. Characters go left to
right. The bar line starts at the left edge. The others end at the
right edge.
"""
import struct
import tempfile
from pathlib import Path

from busin.disc import chunk_row, read_pack
from busin.paths import DATA
from busin.tmx import map_png_to_indices, rgb_palette

TEXT = DATA / "picture" / "images.json"
DIR = "town"
CHUNK = 8
SIZE = 33344
WIDTH = 256
HEIGHT = 256
PIXEL_AT = 0x40 + 16 * 32
PALETTE = 1
# file: pack offset, texture slot, second palette (PSMCT16).
SOURCES = {
    "Tw_map01": (0x8240, 1, (
        0x8000, 0x0421, 0x1084, 0x18C6, 0x2108, 0x294A, 0x35AD, 0x3DEF,
        0x4631, 0x4E73, 0x56B5, 0x5EF7, 0x6739, 0x6F7B, 0x77BD, 0x7FFF,
    )),
    "Tw_map02": (0x10480, 2, (
        0x0000, 0x0842, 0x1084, 0x18C6, 0x2108, 0x2D6B, 0x39CE, 0x4210,
        0x4631, 0x4E73, 0x56B5, 0x5EF7, 0x6739, 0x6F7B, 0x77BD, 0x7FFF,
    )),
}
# name: file, x, y, width, height, caption. The two gray ramps share the
# brighter steps; ink stops on the step the labyrinth line already uses.
LINES = {
    "bar_bottom": ("Tw_map01", 0, 192, 40, 16, "酒馆"),  # 酒場, left edge
    "guild_bottom": ("Tw_map01", 72, 208, 80, 16, "冒险者公会"),  # 冒険者ギルド
    "save_bottom": ("Tw_map01", 192, 192, 48, 16, "存档"),  # セーブ
    "temple_bottom": ("Tw_map01", 120, 192, 72, 16, "塞勒姆寺院"),  # サルム寺院
    "inn_bottom": ("Tw_map01", 0, 208, 72, 16, "冒险者旅馆"),  # 冒険者の宿
    "shop_bottom": ("Tw_map01", 40, 192, 80, 16, "维加商店"),  # ヴィガー商店
    "maze_bottom": ("Tw_map02", 0, 232, 104, 16, "杜汉的迷宫"),  # ドゥーハンの迷宮
}
GLYPH = 16
# Five characters at 16px need 80. These two lines are 72 wide, so the
# glyphs stay 16px and sit two pixels closer. Solid ink does not meet.
COMPACT = {"temple_bottom", "inn_bottom"}
LEFT = {"bar_bottom"}
INK = 11


def ramp(file: str) -> tuple[int, ...]:
    gray = SOURCES[file][2]
    return tuple((value & 0x1F) << 3 for value in gray[:INK + 1])


def load_rects() -> list[dict]:
    from busin.ui import load_images

    parts = [item for item in load_images(TEXT)["part"] if item["name"] in SOURCES]
    if len(parts) != len(SOURCES) or {item["name"] for item in parts} != set(SOURCES):
        raise ValueError("plaque files")
    found = {}
    for data in parts:
        file = data["name"]
        at, slot, _gray = SOURCES[file]
        if (data["dir"], data["chunk"], int(data["offset"], 0), data["slot"], data["width"], data["height"]) != (
            DIR, CHUNK, at, slot, WIDTH, HEIGHT,
        ):
            raise ValueError(f"{file} location")
        for item in data["rects"]:
            if not isinstance(item, dict) or set(item) != {"name", "x", "y", "width", "height", "palette"}:
                raise ValueError("plaque rect")
            label = item["name"]
            spec = LINES.get(label)
            if (
                spec is None or spec[0] != file or item["palette"] != PALETTE
                or (item["x"], item["y"], item["width"], item["height"]) != spec[1:5]
            ):
                raise ValueError(f"plaque {label} rectangle")
            if label in found:
                raise ValueError(f"plaque {label} is repeated")
            found[label] = item
    if set(found) != set(LINES):
        raise ValueError("plaque rects")
    return [found[name] for name in LINES]


def verify(blob: bytes, file: str) -> None:
    if len(blob) != SIZE or struct.unpack_from("<I", blob, 4)[0] != SIZE:
        raise ValueError(f"{file} size")
    if blob[:2] != b"\x02\x00" or blob[8:12] != b"TMX0":
        raise ValueError(f"{file} is not a TMX0 texture")
    if blob[0x24:0x40].split(b"\x00", 1)[0] != f"{file}.tim".encode():
        raise ValueError(f"{file} comment")
    width, height = struct.unpack_from("<HH", blob, 0x12)
    if (blob[0x10], blob[0x11], width, height, blob[0x16]) != (16, 2, WIDTH, HEIGHT, 0x14):
        raise ValueError(f"{file} layout")
    have = struct.unpack_from("<16H", blob, 0x40 + PALETTE * 32)
    if have != SOURCES[file][2]:
        raise ValueError(f"{file} second palette")


def locate(handle, file: str) -> tuple[int, bytes]:
    at = SOURCES[file][0]
    start, count = chunk_row(handle, CHUNK)
    if count * 2048 < at + SIZE:
        raise ValueError(f"chunk 8 does not contain {file}")
    offset = start * 2048 + at
    blob = read_pack(handle, offset, SIZE)
    verify(blob, file)
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


def patch(blob: bytes, file: str, folder: Path) -> bytes:
    verify(blob, file)
    out = bytearray(blob)
    changed = False
    for item in load_rects():
        spec = LINES[item["name"]]
        if spec[0] != file:
            continue
        zh = folder / f"{item['name']}.zh.png"
        if not zh.is_file():
            continue
        _owner, x, y, width, height, _text = spec
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
    folder = DATA / "picture" / DIR
    result = []
    for file in SOURCES:
        offset, blob = locate(handle, file)
        updated = patch(blob, file, folder)
        if updated != blob:
            result.append((offset, updated))
    return result


def _wenkai_path():
    from busin.font import font_path

    return font_path("LXGWWenKai-Regular.ttf")


def _caption_font(size=GLYPH):
    from PIL import ImageFont

    path = _wenkai_path()
    font = ImageFont.truetype(str(path), size)
    if font.getname() != ("LXGW WenKai", "Regular"):
        raise ValueError(f"caption font is {font.getname()}")
    return font


def _caption_cell(font, char, cell):
    from busin.font import seat_baseline

    if bytes(font.getmask(char)) == bytes(font.getmask(chr(0x10FFFF))):
        raise ValueError(f"font has no glyph for {char!r}")
    return seat_baseline(font, char, cell)[0]


def _metrics(name: str) -> tuple[int, int, int, int]:
    _file, _x, _y, width, height, text = LINES[name]
    glyph, step = (GLYPH, 14) if name in COMPACT else (GLYPH, GLYPH)
    span = (len(text) - 1) * step + glyph
    if not text or glyph > height or span > width:
        raise ValueError(f"plaque {name} does not fit")
    x0 = 0 if name in LEFT else width - span
    return glyph, step, x0, (height - glyph) // 2


def _ink(scale: tuple[int, ...], coverage: int) -> int:
    if not coverage:
        return 0
    return scale[round(coverage * INK / 255)]


def render_caption(name: str):
    from PIL import Image

    file, _x, _y, width, height, text = LINES[name]
    glyph, step, x0, y0 = _metrics(name)
    scale = ramp(file)
    font = _caption_font(glyph)
    image = Image.new("RGB", (width, height), (0, 0, 0))
    for index, char in enumerate(text):
        cell = _caption_cell(font, char, glyph)
        origin = x0 + index * step
        for y in range(glyph):
            for x in range(glyph):
                value = _ink(scale, cell.getpixel((x, y)))
                px, py = origin + x, y0 + y
                if value > image.getpixel((px, py))[0]:
                    image.putpixel((px, py), (value, value, value))
    return image


def write_captions(folder: Path | None = None) -> Path:
    folder = folder or (DATA / "picture" / DIR)
    folder.mkdir(parents=True, exist_ok=True)
    for name in LINES:
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
    struct.pack_into("<16H", blob, 0x40 + PALETTE * 32, *SOURCES["Tw_map02"][2])
    return blob


def self_test() -> None:
    from PIL import Image

    assert {item["name"] for item in load_rects()} == set(LINES)
    assert ramp("Tw_map01")[-1] == ramp("Tw_map02")[-1] == 184
    assert ramp("Tw_map01") != ramp("Tw_map02")
    for name in LINES:
        _metrics(name)
    base = bytearray(b"\xAB" * (WIDTH * HEIGHT // 2))
    blob = bytearray(SIZE)
    blob[PIXEL_AT:PIXEL_AT + len(base)] = base
    _file, x, y, width, height, _text = LINES["maze_bottom"]
    narrow = [0] * (width * height)
    narrow[0] = 15
    paint(blob, x, y, width, height, narrow)
    row = PIXEL_AT + (y * WIDTH) // 2
    assert blob[row] == 0x0F and blob[row + 1] == 0x00 and blob[row + width // 2] == 0xAB
    wide = (104, 232, 112, 16)
    before = bytes(blob)
    paint(blob, *wide, [4] * (112 * 16))
    assert blob[row] == 0x0F and blob[row + 52] == 0x44 and blob[row + 108] == 0xAB
    assert blob[PIXEL_AT:row] == before[PIXEL_AT:row]
    assert blob[PIXEL_AT + ((232 + 16) * WIDTH) // 2:] == before[PIXEL_AT + ((232 + 16) * WIDTH) // 2:]
    source = _sample()
    colors = rgb_palette(struct.pack("<16H", *SOURCES["Tw_map02"][2]))
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        assert patch(bytes(source), "Tw_map02", folder) == bytes(source)
        Image.new("RGB", (width, height), colors[15]).save(folder / "maze_bottom.zh.png")
        painted = patch(bytes(source), "Tw_map02", folder)
        assert painted[:PIXEL_AT] == bytes(source[:PIXEL_AT])
        assert painted[row:row + width // 2] == bytes([0xFF]) * (width // 2)
        assert painted[row + width // 2:row + width // 2 + 56] == bytes(source[row + width // 2:row + width // 2 + 56])
        assert painted[PIXEL_AT:row] == bytes(source[PIXEL_AT:row])
        assert painted[PIXEL_AT + ((232 + 16) * WIDTH) // 2:] == bytes(source[PIXEL_AT + ((232 + 16) * WIDTH) // 2:])
        Image.new("RGB", (1, 1), colors[0]).save(folder / "maze_bottom.zh.png")
        try:
            patch(bytes(source), "Tw_map02", folder)
        except ValueError:
            pass
        else:
            raise AssertionError("wrong-sized plaque png was accepted")
    if _wenkai_path() is not None:
        from busin.font import SOLID

        font = _caption_font()
        glyphs = {
            char: _caption_cell(font, char, GLYPH)
            for char in set("".join(spec[5] for spec in LINES.values()))
        }
        for name in COMPACT:
            text = LINES[name][5]
            occupied = set()
            for index, char in enumerate(text):
                cell = glyphs[char]
                for py in range(GLYPH):
                    for px in range(GLYPH):
                        if cell.getpixel((px, py)) < SOLID:
                            continue
                        spot = (index * 14 + px, py)
                        assert spot not in occupied
                        occupied.add(spot)
            image = render_caption(name)
            assert image.size == (72, 16)
            got = {image.getpixel((px, py))[0] for py in range(16) for px in range(72)}
            assert got <= set(ramp("Tw_map01")) and ramp("Tw_map01")[-1] in got
        maze = render_caption("maze_bottom")
        assert maze.size == (width, height)
        black = (0, 0, 0)

        def bright(cell):
            for py in range(GLYPH):
                for px in range(GLYPH):
                    if cell.getpixel((px, py)) >= 200:
                        return px, py
            raise AssertionError("glyph has no bright pixel")

        dx, dy = bright(glyphs["杜"])
        gx, gy = bright(glyphs["宫"])
        text = LINES["maze_bottom"][5]
        x0 = width - len(text) * GLYPH
        y0 = (height - GLYPH) // 2
        assert maze.getpixel((x0 + dx, y0 + dy)) != black
        assert maze.getpixel((x0 + 4 * GLYPH + gx, y0 + gy)) != black
        assert all(maze.getpixel((px, py)) == black for px in range(x0) for py in range(height))
        values = {maze.getpixel((px, py))[0] for py in range(height) for px in range(width)}
        assert values <= set(ramp("Tw_map02")) and ramp("Tw_map02")[-1] in values
        assert any(0 < value < ramp("Tw_map02")[-1] for value in values)
        bar = render_caption("bar_bottom")
        bar_values = {bar.getpixel((px, py))[0] for py in range(bar.height) for px in range(bar.width)}
        assert bar_values <= set(ramp("Tw_map01")) and ramp("Tw_map01")[-1] in bar_values
        bx, by = bright(glyphs["酒"])
        assert bar.getpixel((bx, by)) != black
        assert all(bar.getpixel((px, py)) == black for px in range(32, 40) for py in range(16))
    print("plaque self-test: OK")


def disc_self_test() -> None:
    from busin.paths import BIN

    folder = DATA / "picture" / DIR
    with BIN.open("rb") as handle:
        located = {file: locate(handle, file) for file in SOURCES}
        got = {offset: data for offset, data in spans(handle)}
        for file, (offset, blob) in located.items():
            names = [name for name, spec in LINES.items() if spec[0] == file]
            if not any((folder / f"{name}.zh.png").is_file() for name in names):
                assert offset not in got
            elif offset in got:
                assert len(got[offset]) == SIZE and got[offset][:PIXEL_AT] == blob[:PIXEL_AT]
        _offset, blob = located["Tw_map02"]
    painted = bytearray(blob)
    paint(painted, 104, 232, 112, 16, [15] * (112 * 16))
    assert bytes(painted[:PIXEL_AT]) == blob[:PIXEL_AT] and len(painted) == SIZE
    row = PIXEL_AT + (232 * WIDTH) // 2
    assert painted[row:row + 52] == blob[row:row + 52]
    assert painted[row + 108] == blob[row + 108]
    assert painted[PIXEL_AT:row] == blob[PIXEL_AT:row]
    assert painted[PIXEL_AT + (248 * WIDTH) // 2:] == blob[PIXEL_AT + (248 * WIDTH) // 2:]
    print("plaque disc self-test: OK")
