"""Common fixed-pixel text rendering for generated picture translations."""
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from busin.font import SOLID, font_path
from busin.tmx import encode_psmt4

CORE_INDEX = 8
OUTLINE_INDEX = 9
TEXT_INDICES = (CORE_INDEX, OUTLINE_INDEX)
MAX_EDGE_CROP = 2
MIN_TRACKING = -8
GENERATED_KEYS = {"name", "renderer", "palette", "font", "labels"}
FONT_KEYS = {"file", "variation", "size"}
LABEL_KEYS = {"source", "text"}
RENDERERS = {"bar_buttons", "ali_buttons"}


@dataclass(frozen=True)
class FontSpec:
    file: str
    variation: str
    size: int


@dataclass(frozen=True)
class Label:
    source: str
    text: str


@dataclass(frozen=True)
class TextImageSpec:
    name: str
    renderer: str
    palette: int
    font: FontSpec
    labels: tuple[Label, ...]


def _name(value, label: str) -> str:
    if not isinstance(value, str) or not value or Path(value).name != value:
        raise ValueError(f"{label} name")
    return value


def parse_generated(data: dict) -> TextImageSpec:
    if not isinstance(data, dict) or set(data) != GENERATED_KEYS:
        raise ValueError("generated image needs name, renderer, palette, font, and labels")
    name = _name(data["name"], "generated")
    renderer = data["renderer"]
    if renderer not in RENDERERS:
        raise ValueError(f"{name} renderer is {renderer!r}")
    palette = data["palette"]
    if type(palette) is not int or not 0 <= palette <= 15:
        raise ValueError(f"{name} palette must be an integer from 0 to 15")
    raw_font = data["font"]
    if not isinstance(raw_font, dict) or set(raw_font) != FONT_KEYS:
        raise ValueError(f"{name} font")
    font = FontSpec(
        _name(raw_font["file"], f"{name} font"),
        raw_font["variation"],
        raw_font["size"],
    )
    if not isinstance(font.variation, str) or not font.variation:
        raise ValueError(f"{name} font variation")
    if type(font.size) is not int or font.size <= 0:
        raise ValueError(f"{name} font size")
    raw_labels = data["labels"]
    if not isinstance(raw_labels, list) or not raw_labels:
        raise ValueError(f"{name} labels")
    labels = []
    for index, item in enumerate(raw_labels):
        if not isinstance(item, dict) or set(item) != LABEL_KEYS:
            raise ValueError(f"{name} label {index}")
        source, text = item["source"], item["text"]
        if not isinstance(source, str) or not isinstance(text, str) or "\n" in text:
            raise ValueError(f"{name} label {index}")
        labels.append(Label(source, text))
    return TextImageSpec(name, renderer, palette, font, tuple(labels))


def load_font(spec: FontSpec):
    font = ImageFont.truetype(str(font_path(spec.file)), spec.size)
    font.set_variation_by_name(spec.variation)
    if font.getname()[1] != spec.variation:
        raise ValueError(f"{spec.file} is {font.getname()}")
    return font


def missing_glyphs(font, text: str) -> list[str]:
    missing = bytes(font.getmask(chr(0x10FFFF), mode="L"))
    return [
        char for char in sorted(set(text))
        if bytes(font.getmask(char, mode="L")) == missing
    ]


def solid_box(mask: Image.Image):
    return mask.point(lambda value: 255 if value >= SOLID else 0).getbbox()


def draw_parts(
    text: str,
    font,
    cell_width: int,
    cell_height: int,
    tracking: int = 0,
):
    """Draw fixed-pixel glyph positions and return the mask plus each solid ink box."""
    canvas = Image.new("L", (cell_width * 4, cell_height * 8), 0)
    draw = ImageDraw.Draw(canvas)
    baseline = cell_height * 5
    origin = cell_width // 2
    boxes = []
    for index, char in enumerate(text):
        x = round(origin + font.getlength(text[:index]) + index * tracking)
        probe = Image.new("L", canvas.size, 0)
        ImageDraw.Draw(probe).text((x, baseline), char, font=font, anchor="ls", fill=255)
        box = solid_box(probe)
        if box is None:
            raise ValueError(f"glyph {char!r} is empty")
        boxes.append(box)
        draw.text((x, baseline), char, font=font, anchor="ls", fill=255)
    ink = canvas.getbbox()
    if ink is None:
        raise ValueError("text is empty")
    return canvas.crop(ink), tuple(
        (left - ink[0], top - ink[1], right - ink[0], bottom - ink[1])
        for left, top, right, bottom in boxes
    )


def solid_overlap(boxes) -> bool:
    return any(
        a_right > b_left and a_bottom > b_top and a_left < b_right and a_top < b_bottom
        for (a_left, a_top, a_right, a_bottom), (b_left, b_top, b_right, b_bottom)
        in zip(boxes, boxes[1:])
    )


def _seat(box, cell):
    left, _top, right, _bottom = box
    return (cell - (right - left)) // 2 - left


def seat_line(
    mask: Image.Image,
    label: str,
    cell_width: int,
    cell_height: int,
    max_edge_crop: int = MAX_EDGE_CROP,
) -> Image.Image:
    """Move the complete line into the cell, then permit at most two cropped edge pixels."""
    solid = solid_box(mask)
    ink = mask.getbbox()
    if solid is None or ink is None:
        raise ValueError(f"{label} is empty")

    ink_x = _seat(ink, cell_width) if ink[2] - ink[0] <= cell_width else _seat(solid, cell_width)
    ink_y = (
        (cell_height - (ink[3] - ink[1])) // 2 - ink[1]
        if ink[3] - ink[1] <= cell_height
        else (cell_height - (solid[3] - solid[1])) // 2 - solid[1]
    )

    def placed(box, dx, dy):
        return (box[0] + dx, box[1] + dy, box[2] + dx, box[3] + dy)

    solid_box_placed = placed(solid, ink_x, ink_y)
    if (solid_box_placed[0] < 0 or solid_box_placed[1] < 0
            or solid_box_placed[2] > cell_width or solid_box_placed[3] > cell_height):
        raise ValueError(
            f"{label} solid ink is "
            f"{max(0, solid_box_placed[2] - solid_box_placed[0] - cell_width)}x"
            f"{max(0, solid_box_placed[3] - solid_box_placed[1] - cell_height)} pixels too large"
        )

    ink_box = placed(ink, ink_x, ink_y)
    overflow = (
        max(0, -ink_box[0]), max(0, -ink_box[1]),
        max(0, ink_box[2] - cell_width), max(0, ink_box[3] - cell_height),
    )
    if max(overflow) > max_edge_crop:
        raise ValueError(
            f"{label} needs to crop left={overflow[0]} top={overflow[1]} "
            f"right={overflow[2]} bottom={overflow[3]} pixels; limit {max_edge_crop}"
        )

    out = Image.new("L", (cell_width, cell_height), 0)
    out.paste(mask, (ink_x, ink_y))
    return out


def render_line(
    text: str,
    font,
    cell_width: int,
    cell_height: int,
    label: str | None = None,
) -> Image.Image:
    label = label or repr(text)
    missing = missing_glyphs(font, text)
    if missing:
        raise ValueError(f"{label} missing glyphs: {' '.join(missing)}")
    tracking = 0
    while True:
        mask, boxes = draw_parts(text, font, cell_width, cell_height, tracking)
        solid = solid_box(mask)
        overlap = solid_overlap(boxes)
        if solid[2] - solid[0] <= cell_width:
            if overlap:
                raise ValueError(f"{label} solid strokes overlap")
            return seat_line(mask, label, cell_width, cell_height)
        if overlap:
            raise ValueError(f"{label} cannot tighten without overlapping solid strokes")
        tracking -= 1
        if tracking < MIN_TRACKING:
            raise ValueError(f"{label} cannot fit after tightening letter spacing")


def outline_indices(
    mask: Image.Image,
    core_index: int = CORE_INDEX,
    outline_index: int = OUTLINE_INDEX,
) -> list[int]:
    """Use a solid core and a clean one-pixel 8-connected outer edge."""
    width, height = mask.size
    core = [
        [mask.getpixel((x, y)) >= SOLID for x in range(width)]
        for y in range(height)
    ]
    outline = [[False] * width for _ in range(height)]
    for y in range(height):
        for x in range(width):
            if core[y][x]:
                continue
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    if not (dx or dy):
                        continue
                    xx, yy = x + dx, y + dy
                    if 0 <= xx < width and 0 <= yy < height and core[yy][xx]:
                        outline[y][x] = True
                        break
                if outline[y][x]:
                    break
    result = []
    for y in range(height):
        for x in range(width):
            if core[y][x]:
                result.append(core_index)
            elif outline[y][x]:
                result.append(outline_index)
            else:
                result.append(0)
    return result


def replace_psmt4_pixels(
    blob: bytes,
    pixels,
    width: int,
    height: int,
    palette_count: int,
    palette_size: int,
    label: str,
) -> bytes:
    """Replace only PSMT4 pixels and leave the TMX header and palettes unchanged."""
    if width <= 0 or height <= 0 or width * height % 2:
        raise ValueError(f"{label} PSMT4 dimensions")
    pixel_offset = 0x40 + palette_count * palette_size
    pixel_size = width * height // 2
    if len(blob) != pixel_offset + pixel_size:
        raise ValueError(f"{label} is {len(blob)} bytes, expected {pixel_offset + pixel_size}")
    encoded = encode_psmt4(pixels)
    if len(encoded) != pixel_size:
        raise ValueError(f"{label} encoded pixels")
    out = bytearray(blob)
    out[pixel_offset:pixel_offset + pixel_size] = encoded
    if out[:pixel_offset] != blob[:pixel_offset]:
        raise ValueError(f"{label} header changed")
    return bytes(out)
