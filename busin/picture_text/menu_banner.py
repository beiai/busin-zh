from pathlib import Path

from PIL import Image

from busin.paths import DATA
from busin.picture_text.render import (
    DEFAULT_TEXT_STYLE,
    FontSpec,
    TEXT_INDICES,
    load_font,
    render_text_indices,
)
from busin.tmx import TmxImage

FONT = FontSpec("NotoSansSC-VF.ttf", "Medium", 18)
CORE_INDEX = 8
OUTLINE_INDEX = 9
PALETTE_COLORS = (
    (0x00, 0x00, 0x00, 0xFF), (0x70, 0x70, 0x70, 0xFF),
    (0x60, 0x60, 0x60, 0xFF), (0x50, 0x50, 0x50, 0xFF),
    (0x48, 0x48, 0x48, 0xFF), (0x38, 0x38, 0x38, 0xFF),
    (0x30, 0x30, 0x30, 0xFF), (0x20, 0x20, 0x20, 0xFF),
    (0x18, 0x18, 0x18, 0xFF), (0x90, 0x90, 0x90, 0xFF),
    (0x70, 0x70, 0x70, 0xFF), (0x68, 0x68, 0x68, 0xFF),
    (0x60, 0x60, 0x60, 0xFF), (0x58, 0x58, 0x58, 0xFF),
    (0x50, 0x50, 0x50, 0xFF), (0x48, 0x48, 0x48, 0xFF),
)
BASE_WIDTH = 128
BASE_HEIGHT = 32
BACKGROUND = DATA / "picture" / "templates" / "menu.png"


def load_background() -> tuple[int, ...]:
    """Load the clean banner as exact PSMT4 pixel indices."""
    with Image.open(BACKGROUND) as bitmap:
        if bitmap.mode != "P" or bitmap.size != (BASE_WIDTH, BASE_HEIGHT):
            raise ValueError(f"{BACKGROUND.name} is not a 128x32 indexed PNG")
        palette = bitmap.getpalette()
        if palette[:len(PALETTE_COLORS) * 3] != [
            channel for color in PALETTE_COLORS for channel in color[:3]
        ]:
            raise ValueError(f"{BACKGROUND.name} palette changed")
        pixels = tuple(bitmap.get_flattened_data())
    if len(pixels) != BASE_WIDTH * BASE_HEIGHT:
        raise ValueError(f"{BACKGROUND.name} pixel count")
    if any(type(value) is not int or not 0 <= value <= 15 for value in pixels):
        raise ValueError(f"{BACKGROUND.name} pixel index")
    if any(value in TEXT_INDICES for value in pixels):
        raise ValueError(f"{BACKGROUND.name} contains text")
    return pixels


def render_initial(
    image: TmxImage,
    rect: dict,
    output: Path,
    text_style: str = DEFAULT_TEXT_STYLE,
) -> None:
    """Draw one manifest label on the clean 128x32 banner background."""
    name = rect.get("name")
    source = rect.get("source")
    text = rect.get("text")
    if not isinstance(name, str) or not name:
        raise ValueError("banner rect name")
    if not isinstance(source, str) or not source:
        raise ValueError(f"{name} source")
    if not isinstance(text, str) or not text or "\n" in text:
        raise ValueError(f"{name} text")
    if (rect["width"], rect["height"]) != (BASE_WIDTH, BASE_HEIGHT):
        raise ValueError(f"{name} is {rect['width']}x{rect['height']}")
    palette_index = rect["palette"]
    if type(palette_index) is not int or palette_index < 0:
        raise ValueError(f"{name} palette must be nonnegative")
    if palette_index >= len(image.palettes):
        raise ValueError(f"{name} has no palette {palette_index}")
    palette = image.palettes[palette_index]
    if tuple(palette) != PALETTE_COLORS:
        raise ValueError(f"{name} palette {palette_index} colors changed")

    font = load_font(FONT)
    values = render_text_indices(
        text,
        font,
        BASE_WIDTH,
        BASE_HEIGHT,
        f"{name} {source}",
        text_style,
        PALETTE_COLORS,
    )
    pixels = list(load_background())
    for index, value in enumerate(values):
        if value is not None:
            pixels[index] = value

    bitmap = Image.new("RGBA", (BASE_WIDTH, BASE_HEIGHT))
    bitmap.putdata([palette[value] for value in pixels])
    output.parent.mkdir(parents=True, exist_ok=True)
    bitmap.save(output)


def self_test() -> None:
    font = load_font(FONT)
    assert PALETTE_COLORS[CORE_INDEX][:3] == (0x18, 0x18, 0x18)
    assert PALETTE_COLORS[OUTLINE_INDEX][:3] == (0x90, 0x90, 0x90)
    blank = load_background()
    assert len(blank) == BASE_WIDTH * BASE_HEIGHT
    assert not any(value in (CORE_INDEX, OUTLINE_INDEX) for value in blank)
    for text in ("魔法师魔法", "冒险者名册", "曲折指击"):
        for text_style in ("stroke-aa", "outline-aa"):
            values = render_text_indices(
                text,
                font,
                BASE_WIDTH,
                BASE_HEIGHT,
                text,
                text_style,
                PALETTE_COLORS,
            )
            assert CORE_INDEX in values and OUTLINE_INDEX in values
            assert set(value for value in values if value is not None) <= set(TEXT_INDICES)
