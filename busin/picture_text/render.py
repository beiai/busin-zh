"""Common fixed-pixel text rendering for generated picture translations."""
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from busin.font import SOLID, font_path
from busin.tmx import encode_psmt4

CORE_INDEX = 8
OUTLINE_INDEX = 9
TEXT_INDICES = (2, 3, 4, 5, 6, 7, CORE_INDEX, OUTLINE_INDEX)
TEXT_STYLES = ("stroke-aa", "outline-aa")
DEFAULT_TEXT_STYLE = "stroke-aa"
TEXT_AA_SCALE = 8
TEXT_GRAY_RAMP = (CORE_INDEX, 7, 6, 5, 4, 3, 2, OUTLINE_INDEX)
TEXT_PALETTE = (
    (0x00, 0x00, 0x00, 0xFF), (0x70, 0x70, 0x70, 0xFF),
    (0x60, 0x60, 0x60, 0xFF), (0x50, 0x50, 0x50, 0xFF),
    (0x48, 0x48, 0x48, 0xFF), (0x38, 0x38, 0x38, 0xFF),
    (0x30, 0x30, 0x30, 0xFF), (0x20, 0x20, 0x20, 0xFF),
    (0x18, 0x18, 0x18, 0xFF), (0x90, 0x90, 0x90, 0xFF),
    (0x70, 0x70, 0x70, 0xFF), (0x68, 0x68, 0x68, 0xFF),
    (0x60, 0x60, 0x60, 0xFF), (0x58, 0x58, 0x58, 0xFF),
    (0x50, 0x50, 0x50, 0xFF), (0x48, 0x48, 0x48, 0xFF),
)
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
    try:
        variations = font.get_variation_names()
    except OSError:
        variations = ()
    if variations:
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


def _seat_line(
    mask: Image.Image,
    label: str,
    cell_width: int,
    cell_height: int,
    max_edge_crop: int,
    horizontal: str,
):
    """Move a complete line into its cell and return the image plus its offset."""
    if horizontal not in ("left", "center"):
        raise ValueError(f"{label} horizontal alignment is {horizontal!r}")
    solid = solid_box(mask)
    ink = mask.getbbox()
    if solid is None or ink is None:
        raise ValueError(f"{label} is empty")

    if horizontal == "center":
        ink_x = (
            _seat(ink, cell_width)
            if ink[2] - ink[0] <= cell_width
            else _seat(solid, cell_width)
        )
    else:
        ink_x = -ink[0] if ink[2] - ink[0] <= cell_width else -solid[0]
    ink_y = (
        (cell_height - (ink[3] - ink[1])) // 2 - ink[1]
        if ink[3] - ink[1] <= cell_height
        else (cell_height - (solid[3] - solid[1])) // 2 - solid[1]
    )

    def placed(box, dx, dy):
        return (box[0] + dx, box[1] + dy, box[2] + dx, box[3] + dy)

    solid_box_placed = placed(solid, ink_x, ink_y)
    if (
        solid_box_placed[0] < 0
        or solid_box_placed[1] < 0
        or solid_box_placed[2] > cell_width
        or solid_box_placed[3] > cell_height
    ):
        raise ValueError(
            f"{label} solid ink is "
            f"{max(0, solid_box_placed[2] - solid_box_placed[0] - cell_width)}x"
            f"{max(0, solid_box_placed[3] - solid_box_placed[1] - cell_height)} pixels too large"
        )

    ink_box = placed(ink, ink_x, ink_y)
    overflow = (
        max(0, -ink_box[0]),
        max(0, -ink_box[1]),
        max(0, ink_box[2] - cell_width),
        max(0, ink_box[3] - cell_height),
    )
    if max(overflow) > max_edge_crop:
        raise ValueError(
            f"{label} needs to crop left={overflow[0]} top={overflow[1]} "
            f"right={overflow[2]} bottom={overflow[3]} pixels; limit {max_edge_crop}"
        )

    out = Image.new("L", (cell_width, cell_height), 0)
    out.paste(mask, (ink_x, ink_y))
    return out, (ink_x, ink_y)


def seat_line(
    mask: Image.Image,
    label: str,
    cell_width: int,
    cell_height: int,
    max_edge_crop: int = MAX_EDGE_CROP,
    horizontal: str = "center",
) -> Image.Image:
    """Move the complete line into the cell, then permit at most two cropped edge pixels."""
    return _seat_line(mask, label, cell_width, cell_height, max_edge_crop, horizontal)[
        0
    ]


def line_layout(
    text: str,
    font,
    cell_width: int,
    cell_height: int,
    label: str | None = None,
    tracking: int = 0,
    horizontal: str = "center",
):
    """Return the normal-resolution mask, chosen tracking, and seat offset."""
    label = label or repr(text)
    missing = missing_glyphs(font, text)
    if missing:
        raise ValueError(f"{label} missing glyphs: {' '.join(missing)}")
    while True:
        mask, boxes = draw_parts(text, font, cell_width, cell_height, tracking)
        solid = solid_box(mask)
        overlap = solid_overlap(boxes)
        if solid[2] - solid[0] <= cell_width:
            if overlap:
                raise ValueError(f"{label} solid strokes overlap")
            seated, offset = _seat_line(
                mask, label, cell_width, cell_height, MAX_EDGE_CROP, horizontal
            )
            return seated, tracking, offset
        if overlap:
            raise ValueError(
                f"{label} cannot tighten without overlapping solid strokes"
            )
        tracking -= 1
        if tracking < MIN_TRACKING:
            raise ValueError(f"{label} cannot fit after tightening letter spacing")


def render_line(
    text: str,
    font,
    cell_width: int,
    cell_height: int,
    label: str | None = None,
    tracking: int = 0,
    horizontal: str = "center",
) -> Image.Image:
    return line_layout(
        text, font, cell_width, cell_height, label, tracking, horizontal
    )[0]


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


def validate_text_style(style: str) -> str:
    if style not in TEXT_STYLES:
        raise ValueError(f"text style must be one of {', '.join(TEXT_STYLES)}")
    return style


def _scaled_font(font, size: int):
    scaled = ImageFont.truetype(str(font.path), size)
    variation = font.getname()[1]
    try:
        variations = scaled.get_variation_names()
    except OSError:
        variations = ()
    if variations:
        scaled.set_variation_by_name(variation)
    if scaled.getname()[1] != variation:
        raise ValueError(f"{font.path} is {scaled.getname()}")
    return scaled


def _nearest_text_index(rgb: tuple[int, int, int], palette) -> int:
    return min(
        TEXT_GRAY_RAMP,
        key=lambda index: sum(
            (value - palette[index][channel]) ** 2
            for channel, value in enumerate(rgb)
        ),
    )


def _quantize_text_coverage(
    core: list[int],
    outline: list[int],
    palette,
) -> list[int | None]:
    background = palette[10][:3]
    core_rgb = palette[CORE_INDEX][:3]
    outline_rgb = palette[OUTLINE_INDEX][:3]
    result: list[int | None] = []
    for core_value, outline_value in zip(core, outline):
        if not core_value and not outline_value:
            result.append(None)
            continue
        outline_alpha = outline_value / 255
        core_alpha = core_value / 255
        edge_rgb = tuple(
            round(background[channel] + (outline_rgb[channel] - background[channel]) * outline_alpha)
            for channel in range(3)
        )
        rgb = tuple(
            round(edge_rgb[channel] + (core_rgb[channel] - edge_rgb[channel]) * core_alpha)
            for channel in range(3)
        )
        result.append(_nearest_text_index(rgb, palette))
    return result


def _stroke_layer(text: str, font, width: int, height: int, stroke_width: int) -> Image.Image:
    scale = TEXT_AA_SCALE
    canvas = Image.new("L", (width * scale, height * scale), 0)
    draw = ImageDraw.Draw(canvas)
    box = draw.textbbox((0, 0), text, font=font, stroke_width=stroke_width)
    x = (canvas.width - (box[2] - box[0])) / 2 - box[0]
    y = (canvas.height - (box[3] - box[1])) / 2 - box[1]
    draw.text(
        (x, y),
        text,
        font=font,
        fill=255,
        stroke_width=stroke_width,
        stroke_fill=255,
    )
    return canvas.resize((width, height), Image.Resampling.BOX)


def render_text_alpha(
    text: str,
    font,
    cell_width: int,
    cell_height: int,
    label: str | None = None,
    tracking: int = 0,
    horizontal: str = "left",
    output_width: int | None = None,
) -> list[int]:
    """Render an anti-aliased mask with exactly the same seat and tracking as render_line."""
    label = label or repr(text)
    _mask, tracking, (dx, dy) = line_layout(
        text, font, cell_width, cell_height, label, tracking, horizontal
    )
    scale = TEXT_AA_SCALE
    scaled_font = _scaled_font(font, font.size * scale)
    scaled_mask, _boxes = draw_parts(
        text, scaled_font, cell_width * scale, cell_height * scale, tracking * scale
    )
    seated = Image.new("L", (cell_width * scale, cell_height * scale), 0)
    seated.paste(scaled_mask, (dx * scale, dy * scale))
    width = cell_width if output_width is None else output_width
    if width <= 0:
        raise ValueError(f"{label} output width is {width}")
    resized = seated.resize((width, cell_height), Image.Resampling.BOX)
    return [int(round(value * 15 / 255)) for value in resized.get_flattened_data()]


def render_text_alpha_left(
    text: str,
    font,
    cell_width: int,
    cell_height: int,
    label: str | None = None,
    tracking: int = 0,
) -> list[int]:
    """Compatibility wrapper for callers that explicitly want left alignment."""
    return render_text_alpha(
        text, font, cell_width, cell_height, label, tracking, "left"
    )


def render_text_indices(
    text: str,
    font,
    cell_width: int,
    cell_height: int,
    label: str | None = None,
    style: str = DEFAULT_TEXT_STYLE,
    palette=TEXT_PALETTE,
) -> list[int | None]:
    """Render one fixed-pixel line with the selected offline anti-aliasing style."""
    style = validate_text_style(style)
    render_line(text, font, cell_width, cell_height, label)
    scaled_font = _scaled_font(font, font.size * TEXT_AA_SCALE)
    if style == "outline-aa":
        mask = render_line(
            text,
            scaled_font,
            cell_width * TEXT_AA_SCALE,
            cell_height * TEXT_AA_SCALE,
            label,
        )
        outer = mask.filter(ImageFilter.MaxFilter(2 * TEXT_AA_SCALE + 1))
        core = list(mask.resize((cell_width, cell_height), Image.Resampling.BOX).get_flattened_data())
        outline = list(outer.resize((cell_width, cell_height), Image.Resampling.BOX).get_flattened_data())
    else:
        outline = list(
            _stroke_layer(text, scaled_font, cell_width, cell_height, TEXT_AA_SCALE).get_flattened_data()
        )
        core = list(
            _stroke_layer(text, scaled_font, cell_width, cell_height, 0).get_flattened_data()
        )
    return _quantize_text_coverage(core, outline, palette)


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
