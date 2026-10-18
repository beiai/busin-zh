"""Validate the paged 24-pixel message font plan and draw page glyphs."""
import hashlib
import json
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from busin.paths import DATA
from busin.tmx import PSMCT32, PSMT4, read_tmx

GRID = 42
CELL = 24
PAGE_CELLS = GRID * GRID
EXTENSION_BASE = 0x4000
EXTENSION_LIMIT = 0x7FFF
PIXEL_OFFSET = 0x240
PIXEL_SIZE = 524288
FONT_DIR = DATA / "font"


@dataclass(frozen=True)
class FontInput:
    role: str
    filename: str
    family: str
    style: str
    version: str
    sha256: str
    license: str


FONT_INPUTS = (
    FontInput(
        "message", "LXGWWenKai-Regular.ttf", "LXGW WenKai", "Regular", "1.530",
        "2d73eea94ad808999d90c0000c239a84a33b09d4bc6ab6c5b8bbf1eda8a7fc8b",
        "LICENSE-LXGWWenKai-Regular.txt",
    ),
    FontInput(
        "ui", "NotoSansSC-VF.ttf", "Noto Sans SC", "Regular", "2.04",
        "763146584cf0710223441356b4395e279021b0806c196614377a7a0174ae074a",
        "LICENSE-NotoSansSC-VF.txt",
    ),
)


def font_input(name: str) -> FontInput:
    found = next((item for item in FONT_INPUTS if item.filename == name), None)
    if found is None:
        raise ValueError(f"unknown font {name}")
    return found


def font_path(name: str) -> Path:
    item = font_input(name)
    path = FONT_DIR / item.filename
    if not path.is_file():
        raise FileNotFoundError(path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != item.sha256:
        raise ValueError(f"{item.role} font SHA-256 does not match {item.version}: {item.filename}")
    license_path = FONT_DIR / item.license
    if not license_path.is_file():
        raise FileNotFoundError(license_path)
    return path


SOLID = 128
_PAD = 8
STROKE_AA_SCALE = 8
STROKE_AA_WIDTH = 2
MAX_GLYPH_CROP = 2


def load_plan(path: Path) -> dict:
    plan = json.loads(path.read_text(encoding="utf-8"))
    if plan.get("format") != 1 or (plan.get("cell_size"), plan.get("columns"), plan.get("rows")) != (CELL, GRID, GRID):
        raise ValueError("unsupported message font page format")
    if int(plan.get("page_capacity", -1)) != PAGE_CELLS:
        raise ValueError("message font page capacity must be 1764")
    raw_base = plan.get("extension_token_base")
    base = int(raw_base, 0) if isinstance(raw_base, str) else raw_base
    if base != EXTENSION_BASE:
        raise ValueError("extension token base must be 0x4000")
    pages = plan.get("pages")
    if not isinstance(pages, list) or not pages or [page.get("id") for page in pages] != list(range(len(pages))):
        raise ValueError("pages must have contiguous ids starting at zero")
    chars = plan.get("characters", {})
    if not isinstance(chars, dict) or any(len(char) != 1 or char.isspace() for char in chars):
        raise ValueError("page character keys must be single visible characters")
    seen = set()
    for char, item in chars.items():
        if not isinstance(item, dict):
            raise ValueError(f"mapping for {char!r} must be an object")
        page, raw_cell = item.get("page"), item.get("cell")
        if type(page) is not int or not 0 <= page < len(pages) or not isinstance(raw_cell, str):
            raise ValueError(f"invalid page/cell mapping for {char!r}")
        cell = int(raw_cell, 0)
        if not 0 <= cell < PAGE_CELLS or (page, cell) in seen:
            raise ValueError(f"duplicate or invalid cell mapping for {char!r}")
        seen.add((page, cell))
        expected = cell if page == 0 else EXTENSION_BASE + (page - 1) * PAGE_CELLS + cell
        if expected > EXTENSION_LIMIT:
            raise ValueError(f"extension token for {char!r} exceeds 0x7FFF")
        if item.get("token") != f"0x{expected:04X}":
            raise ValueError(f"token for {char!r} does not match page/cell")
    return plan


def token(page: int, cell: int) -> int:
    if not 0 <= cell < PAGE_CELLS or page < 0:
        raise ValueError("page/cell is outside the 42x42 atlas")
    value = cell if page == 0 else EXTENSION_BASE + (page - 1) * PAGE_CELLS + cell
    if value > EXTENSION_LIMIT:
        raise ValueError("extension token exceeds the reserved u16 range")
    return value


def locate(value: int) -> tuple[int, int]:
    if type(value) is not int or value < 0 or PAGE_CELLS <= value < EXTENSION_BASE or value > EXTENSION_LIMIT:
        raise ValueError("token is outside the paged font")
    if value < PAGE_CELLS:
        return 0, value
    page, cell = divmod(value - EXTENSION_BASE, PAGE_CELLS)
    page += 1
    if token(page, cell) != value:
        raise ValueError("token is outside the paged font")
    return page, cell


def stable_hash(text: str) -> int:
    """Hash UTF-8 bytes. Python's hash() changes between processes."""
    return sum(byte * (31 ** index) for index, byte in enumerate(text.encode("utf-8"))) & 0x7FFFFFFF


def _place(chars: set[str], pinned: dict[str, int]) -> tuple[dict[str, int], dict]:
    """Double-hash into cells 1..1763. Cell 0 stays the blank glyph."""
    capacity = PAGE_CELLS - 1
    if any(not 0 < cell <= capacity for cell in pinned.values()) or len(set(pinned.values())) != len(pinned):
        raise ValueError("pinned cells must be unique and in 1..1763")
    result = dict(pinned)
    occupied = set(pinned.values())
    probes, collisions = [], 0
    for char in sorted(chars):
        if char in result:
            continue
        digest = stable_hash(char)
        h1 = digest % capacity
        h2 = 1 + (digest // capacity) % (capacity - 1)
        # ponytail: 1763=41*43, so a step sharing that factor only walks 41 or 43
        # cells. Force a coprime step if this fails while free cells remain.
        collided = False
        for k in range(capacity):
            cell = 1 + (h1 + k * h2) % capacity
            if cell not in occupied:
                result[char] = cell
                occupied.add(cell)
                probes.append(k + 1)
                collisions += collided
                break
            collided = True
        else:
            raise RuntimeError(f"no free cell for {char!r}")
    return result, {
        "total_chars": len(result),
        "avg_probe_length": round(sum(probes) / len(probes), 3) if probes else 0,
        "max_probe_length": max(probes) if probes else 0,
        "collisions": collisions,
    }


def assign_cells(chars: set[str], pinned: dict[str, int]) -> dict[str, int]:
    """Cell for each character. Same set and pinned map always give the same cells."""
    return _place(chars, pinned)[0]


def hash_collision_stats(chars: set[str], pinned: dict[str, int]) -> dict:
    return _place(chars, pinned)[1]


def copy_cell(data: bytearray, source: bytes, source_cell: int, cell: int) -> None:
    """Copy a 24x24 PSMT4 cell between two message TMX images (pixels at PIXEL_OFFSET, 512 bytes a row)."""
    for row in range(CELL):
        to = PIXEL_OFFSET + ((cell // GRID * CELL + row) * 1024 + cell % GRID * CELL) // 2
        at = PIXEL_OFFSET + ((source_cell // GRID * CELL + row) * 1024 + source_cell % GRID * CELL) // 2
        data[to:to + CELL // 2] = source[at:at + CELL // 2]


def fit_cell(cell, paint, label="glyph"):
    """Seat a glyph in a fixed cell. Solid ink that hangs below the cell is raised until it sits inside. Lighter fringe may hang one pixel outside."""
    image = paint(0)
    solid = image.point(lambda value: 255 if value >= SOLID else 0).getbbox()
    if solid is None:
        raise ValueError(f"{label} is empty")
    shift = max(0, solid[3] - (_PAD + cell))
    if shift:
        image = paint(shift)
        solid = image.point(lambda value: 255 if value >= SOLID else 0).getbbox()
    ink = image.getbbox()
    if solid is None or solid[0] < _PAD or solid[1] < _PAD or solid[2] > _PAD + cell or solid[3] > _PAD + cell:
        raise ValueError(f"{label} solid ink does not fit {cell}x{cell}")
    if ink[0] < _PAD - 1 or ink[1] < _PAD - 1 or ink[2] > _PAD + cell + 1 or ink[3] > _PAD + cell + 1:
        raise ValueError(f"{label} fringe exceeds 1 pixel")
    return image.crop((_PAD, _PAD, _PAD + cell, _PAD + cell)), shift


def font_at_scale(font, scale):
    scaled = ImageFont.truetype(str(font.path), font.size * scale)
    try:
        names = scaled.get_variation_names()
    except OSError:
        names = []
    if names:
        scaled.set_variation_by_name(font.getname()[1])
    return scaled


def fit_stroke_cell(cell, paint, label="glyph"):
    """Move a whole stroked glyph into the cell; only then crop at most two pixels per edge."""
    from PIL import ImageChops

    image = paint()
    ink = image.getbbox()
    if ink is None:
        raise ValueError(f"{label} is empty")
    left, top, right, bottom = ink
    width, height = right - left, bottom - top
    if width > cell + 2 * MAX_GLYPH_CROP or height > cell + 2 * MAX_GLYPH_CROP:
        raise ValueError(f"{label} stroked ink is {width}x{height}, limit {cell + 2 * MAX_GLYPH_CROP}px")

    if width <= cell:
        dx = _PAD - left if left < _PAD else _PAD + cell - right if right > _PAD + cell else 0
    else:
        dx = _PAD + (cell - width) // 2 - left
    if height <= cell:
        dy = _PAD - top if top < _PAD else _PAD + cell - bottom if bottom > _PAD + cell else 0
    else:
        dy = _PAD + (cell - height) // 2 - top
    image = ImageChops.offset(image, dx, dy)

    ink = image.getbbox()
    left, top, right, bottom = ink
    crops = (
        max(0, _PAD - left), max(0, _PAD - top),
        max(0, right - _PAD - cell), max(0, bottom - _PAD - cell),
    )
    if any(crop > MAX_GLYPH_CROP for crop in crops):
        raise ValueError(f"{label} needs edge crops {crops}, limit {MAX_GLYPH_CROP}px")
    return image.crop((_PAD, _PAD, _PAD + cell, _PAD + cell)), max(0, -dy)


def seat_stroke_baseline(font, char, cell):
    """Render one UI glyph with the stroke-aa rule and seat it by baseline."""
    advance = font.getlength(char)
    rounded = round(advance)
    if abs(advance - rounded) > 0.01 or not 0 < rounded <= cell:
        raise ValueError(f"glyph {char!r} has unsupported advance {advance}")
    x = (cell - rounded) // 2
    scaled = font_at_scale(font, STROKE_AA_SCALE)

    def paint():
        size = (cell + 16) * STROKE_AA_SCALE
        image = Image.new("L", (size, size))
        ImageDraw.Draw(image).text(
            ((8 + x) * STROKE_AA_SCALE, (8 + cell - 1) * STROKE_AA_SCALE),
            char, font=scaled, anchor="ls", fill=255,
            stroke_width=STROKE_AA_WIDTH, stroke_fill=255,
        )
        return image.resize((cell + 16, cell + 16), Image.Resampling.BOX)

    return fit_stroke_cell(cell, paint, f"stroked glyph {char!r}")


def seat_baseline(font, char, cell):
    advance = font.getlength(char)
    rounded = round(advance)
    if abs(advance - rounded) > 0.01 or not 0 < rounded <= cell:
        raise ValueError(f"glyph {char!r} has unsupported advance {advance}")
    x = (cell - rounded) // 2

    def paint(shift):
        image = Image.new("L", (cell + 16, cell + 16))
        ImageDraw.Draw(image).text((8 + x, 8 + cell - 1 - shift), char, font=font, anchor="ls", fill=255)
        return image

    return fit_cell(cell, paint, f"glyph {char!r}")


def message_cell(font, char, cell=CELL):
    """Render one message glyph with the stroke-aa rule."""
    cjk = unicodedata.name(char, "").startswith("CJK UNIFIED")
    scaled = font_at_scale(font, STROKE_AA_SCALE)

    def paint():
        size = (cell + 16) * STROKE_AA_SCALE
        image = Image.new("L", (size, size))
        draw = ImageDraw.Draw(image)
        if cjk:
            left, top, right, bottom = draw.textbbox((0, 0), char, font=scaled)
            x = (size - right + left) / 2 - left
            y = (size - bottom + top) / 2 - top
            draw.text(
                (x, y), char, font=scaled, fill=255,
                stroke_width=STROKE_AA_WIDTH, stroke_fill=255,
            )
        else:
            # Punctuation keeps its place in the em square (「，」 sits low left), so centre the em box, not the ink.
            draw.text(
                (size / 2, size / 2), char, font=scaled, fill=255, anchor="mm",
                stroke_width=STROKE_AA_WIDTH, stroke_fill=255,
            )
        return image.resize((cell + 16, cell + 16), Image.Resampling.BOX)

    return fit_stroke_cell(cell, paint, f"font glyph {char!r}")


def render_font(source: Path, output: Path, char_map: dict[str, int], generated: list[str], font_name: str, size: int = 24, variation: str | None = None, alpha: list[int] | None = None) -> None:
    data = bytearray(source.read_bytes())
    image = read_tmx(source)
    if (image.width, image.height, image.pixel_format, image.palette_format, len(image.palettes)) != (1024, 1024, PSMT4, PSMCT32, 8):
        raise ValueError("expected the 1024x1024 message TMX with eight PSMCT32 palettes")
    if len(generated) != len(set(generated)):
        raise ValueError("generated list contains duplicate characters")
    if any(image.pixels[row * 1024 + col] != 15 for row in range(CELL) for col in range(CELL)):
        raise ValueError("padding glyph 0 is not blank")
    coverage = [color[3] for color in image.palettes[0]]
    if alpha is not None and coverage != list(alpha):
        raise ValueError("CLUT alpha does not match the font plan")
    if coverage[15] != 0 or not all(coverage[index] > coverage[index + 1] for index in range(15)):
        raise ValueError("expected the verified descending coverage CLUT")
    font = ImageFont.truetype(str(font_path(font_name)), size)
    if variation:
        font.set_variation_by_name(variation)
    for char in generated:
        glyph = char_map[char]
        x = (glyph % GRID) * CELL
        y = (glyph // GRID) * CELL
        if glyph == 0 or any(image.pixels[(y + row) * 1024 + x + col] != 15 for row in range(CELL) for col in range(CELL)):
            raise ValueError(f"replacement cell {glyph:#x} is not unused")
        if bytes(font.getmask(char)) == bytes(font.getmask(chr(0x10FFFF))):
            raise ValueError(f"font has no glyph for {char!r}")
        mask, _shift = message_cell(font, char)
        for row in range(CELL):
            for col in range(CELL):
                index = (y + row) * 1024 + x + col
                level_coverage = mask.getpixel((col, row)) * coverage[0] / 255
                value = min(range(16), key=lambda level: abs(coverage[level] - level_coverage))
                offset = PIXEL_OFFSET + index // 2
                if index & 1:
                    data[offset] = (data[offset] & 0x0F) | (value << 4)
                else:
                    data[offset] = (data[offset] & 0xF0) | value
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(data)


def self_test() -> None:
    import tempfile
    import json
    plan = {
        "format": 1, "cell_size": 24, "columns": 42, "rows": 42,
        "extension_token_base": "0x4000", "page_capacity": 1764,
        "pages": [{"id": 0}, {"id": 1}],
        "characters": {"甲": {"page": 1, "cell": "0x0", "token": "0x4000"}},
    }
    with tempfile.TemporaryDirectory() as temp:
        path = Path(temp) / "plan.json"
        path.write_text(json.dumps(plan), encoding="utf-8")
        assert load_plan(path)["characters"]["甲"]["token"] == "0x4000"
    assert token(0, 0x6E3) == 0x6E3
    assert token(1, 0) == 0x4000
    assert token(3, 1763) == 0x54AB
    assert locate(0x6E3) == (0, 0x6E3)
    assert locate(0x4000) == (1, 0)
    assert locate(0x54AB) == (3, 1763)
    try:
        locate(PAGE_CELLS)
    except ValueError:
        pass
    else:
        raise AssertionError("gap below 0x4000 was accepted")
    try:
        token(10, 1763)
    except ValueError:
        pass
    else:
        raise AssertionError("extension token range was not enforced")

    def pixel(data, cell, row, col):
        index = (cell // GRID * CELL + row) * 1024 + cell % GRID * CELL + col
        byte = data[PIXEL_OFFSET + index // 2]
        return byte >> 4 if index & 1 else byte & 15

    source = bytes((index * 7) & 0xFF for index in range(PIXEL_OFFSET + PIXEL_SIZE))
    page = bytearray(b"\xFF" * (PIXEL_OFFSET + PIXEL_SIZE))
    copy_cell(page, source, 43, 1)
    assert all(pixel(page, 1, row, col) == pixel(source, 43, row, col) for row in range(CELL) for col in range(CELL))
    assert pixel(page, 0, 0, CELL - 1) == pixel(page, 2, 0, 0) == pixel(page, 1, CELL, 0) == 15
    assert stable_hash("名") == 140194
    pinned = {"甲": 1}
    chars = {"甲", "乙", "丙"}
    placed = assign_cells(chars, pinned)
    assert placed == assign_cells(chars, pinned) == assign_cells({"乙", "丙"}, pinned)
    assert placed["甲"] == 1 and 0 not in placed.values() and len(set(placed.values())) == 3
    solo = assign_cells({"乙"}, {})
    blocked = assign_cells({"乙"}, {"甲": solo["乙"]})
    assert blocked["乙"] != solo["乙"] and hash_collision_stats({"乙"}, {"甲": solo["乙"]})["collisions"] == 1
    try:
        assign_cells({"乙"}, {"甲": 0})
    except ValueError:
        pass
    else:
        raise AssertionError("pinned cell 0 was accepted")

    def hanging(shift):
        image = Image.new("L", (16 + 16, 16 + 16))
        ImageDraw.Draw(image).rectangle((10, 20 - shift, 14, 26 - shift), fill=255)
        return image

    mask, shift = fit_cell(16, hanging, "block")
    assert shift == 3 and mask.getpixel((2, 15)) == 255 and mask.getpixel((2, 0)) == 0

    def fringe(shift):
        image = Image.new("L", (32, 32))
        ImageDraw.Draw(image).rectangle((10, 10 - shift, 14, 20 - shift), fill=255)
        image.putpixel((12, 6 - shift), 40)
        return image

    try:
        fit_cell(16, fringe, "block")
    except ValueError:
        pass
    else:
        raise AssertionError("fringe past one pixel was accepted")
    wenkai = font_path("LXGWWenKai-Regular.ttf")
    font = ImageFont.truetype(str(wenkai), CELL)
    if font.getname() != ("LXGW WenKai", "Regular"):
        raise AssertionError(f"message font is {font.getname()}")
    mask, shift = message_cell(font, "娘")
    assert shift == 0 and mask.getbbox()[0] >= 0 and mask.getbbox()[2] <= CELL
