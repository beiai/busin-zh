"""Validate the paged 24-pixel message font plan and draw page glyphs."""
import json
import unicodedata
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from busin.tmx import PSMCT32, PSMT4, read_tmx

GRID = 42
CELL = 24
PAGE_CELLS = GRID * GRID
EXTENSION_BASE = 0x4000
EXTENSION_LIMIT = 0x7FFF
PIXEL_OFFSET = 0x240
PIXEL_SIZE = 524288
FONT_DIR = Path("C:/Windows/Fonts")


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


def render_font(source: Path, output: Path, char_map: dict[str, int], generated: list[str], font_name: str, size: int = 22, variation: str = "Black", alpha: list[int] | None = None) -> None:
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
    font = ImageFont.truetype(str(FONT_DIR / font_name), size)
    # Noto Serif SC's variable font defaults to ExtraLight; the atlas uses the heavier stroke.
    font.set_variation_by_name(variation)
    for char in generated:
        glyph = char_map[char]
        x = (glyph % GRID) * CELL
        y = (glyph // GRID) * CELL
        if glyph == 0 or any(image.pixels[(y + row) * 1024 + x + col] != 15 for row in range(CELL) for col in range(CELL)):
            raise ValueError(f"replacement cell {glyph:#x} is not unused")
        if bytes(font.getmask(char)) == bytes(font.getmask(chr(0x10FFFF))):
            raise ValueError(f"font has no glyph for {char!r}")
        mask = Image.new("L", (CELL, CELL), 0)
        draw = ImageDraw.Draw(mask)
        if unicodedata.name(char, "").startswith("CJK UNIFIED"):
            left, top, right, bottom = draw.textbbox((0, 0), char, font=font)
            if right - left > CELL or bottom - top > CELL:
                raise ValueError(f"font glyph {char!r} does not fit 24x24")
            draw.text(((CELL - (right - left)) // 2 - left, (CELL - (bottom - top)) // 2 - top), char, font=font, fill=255)
        else:
            # Punctuation keeps its place in the em square (「，」 sits low left), so centre the em box, not the ink.
            left, top, right, bottom = draw.textbbox((CELL // 2, CELL // 2), char, font=font, anchor="mm")
            if left < 0 or top < 0 or right > CELL or bottom > CELL:
                raise ValueError(f"font glyph {char!r} does not fit 24x24")
            draw.text((CELL // 2, CELL // 2), char, font=font, fill=255, anchor="mm")
        if not mask.getbbox():
            raise ValueError(f"font glyph {char!r} is empty")
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
