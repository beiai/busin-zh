"""Render text-only menu labels with white grayscale anti-aliasing."""
from pathlib import Path

from PIL import Image

from busin.picture_text.render import FontSpec, load_font, render_text_alpha_left
from busin.tmx import TmxImage

FONT = FontSpec("NotoSansSC-VF.ttf", "Medium", 16)
PALETTE = 2
PALETTE_COLORS = tuple(
    (value, value, value, 0xFF)
    for value in (0, 16, 32, 48, 64, 80, 96, 112, 136, 152, 168, 184, 200, 216, 232, 248)
)
CORE_INDEX = 15
# The Japanese labels skip the three darkest non-black steps. Keeping partial
# edge pixels out of them avoids a green cast after console/video filtering.
MINIMUM_INK_INDEX = 4
WIDTHS = {
    "accessory": 48,
    "armor": 32,
    "back": 32,
    "confirm": 32,
    "event_item": 64,
    "item_data": 64,
    "magic_list": 64,
    "magic_stone_info": 64,
    "move": 32,
    "status": 56,
    "weapon": 32,
}
TRACKING = {
    "magic_stone_info": -1,
}
# Only this label is rendered at 16x16 per character, tightened by 1px, then
# scaled horizontally into the original 64px output width.
SPECIAL_LAYOUTS = {
    "magic_stone_info": {
        "cell_width": 16,
        "cell_height": 16,
        "tracking": -1,
        "output_width": 64,
    },
}
TEXTS = {
    "accessory": ("装飾品", "饰品"),
    "armor": ("防具", "防具"),
    "back": ("戻る", "返回"),
    "confirm": ("決定", "确定"),
    "event_item": ("Event Item", "事件道具"),
    "item_data": ("Item Data", "道具数据"),
    "magic_list": ("魔法一覧", "魔法列表"),
    "magic_stone_info": ("魔法石情報", "魔法石信息"),
    "move": ("移動", "移动"),
    "status": ("ステータス", "状态"),
    "weapon": ("武器", "武器"),
}


def _nearest_palette_index(rgb) -> int:
    return min(
        range(len(PALETTE_COLORS)),
        key=lambda index: sum(
            (value - PALETTE_COLORS[index][channel]) ** 2
            for channel, value in enumerate(rgb)
        ),
    )


def _scale_rgba_to_indices(values: list[int], source_width: int, height: int, output_width: int) -> list[int]:
    source = Image.new("RGBA", (source_width, height))
    source.putdata([PALETTE_COLORS[value] for value in values])
    scaled = source.resize((output_width, height), Image.Resampling.LANCZOS)
    return [
        _nearest_palette_index(scaled.getpixel((x, y))[:3])
        for y in range(height)
        for x in range(output_width)
    ]


def _drop_low_indices(values: list[int]) -> list[int]:
    return [value if value >= MINIMUM_INK_INDEX else 0 for value in values]


def _render_values(
    name: str,
    text: str,
    font,
    width: int,
    height: int,
) -> list[int]:
    if name not in SPECIAL_LAYOUTS:
        return _drop_low_indices(render_text_alpha_left(
            text,
            font,
            width,
            height,
            name,
            TRACKING.get(name, 0),
        ))

    layout = SPECIAL_LAYOUTS[name]
    if width != layout["output_width"] or height != layout["cell_height"]:
        raise ValueError(f"{name} needs a {layout['output_width']}x{layout['cell_height']} output area")
    if any(font.getlength(char) != layout["cell_width"] for char in text):
        raise ValueError(f"{name} glyph advance is not {layout['cell_width']}px")
    source_width = (
        layout["cell_width"] * len(text)
        + layout["tracking"] * (len(text) - 1)
    )
    values = render_text_alpha_left(
        text,
        font,
        source_width,
        height,
        name,
        layout["tracking"],
    )
    return _drop_low_indices(
        _scale_rgba_to_indices(values, source_width, height, width))


def render_initial(image: TmxImage, rect: dict, output: Path) -> None:
    """Write one translated text-only crop using palette 2."""
    name = rect["name"]
    if name not in TEXTS:
        raise ValueError(f"{name} has no menu text")
    source, text = TEXTS[name]
    if rect.get("source") != source or rect.get("text") != text:
        raise ValueError(f"{name} source or translation changed")
    if rect["palette"] != PALETTE:
        raise ValueError(f"{name} must use palette {PALETTE}")
    x, y, width, height = rect["x"], rect["y"], rect["width"], rect["height"]
    if image.palettes[PALETTE] != PALETTE_COLORS:
        raise ValueError(f"{name} palette {PALETTE} colors changed")

    font = load_font(FONT)
    values = _render_values(name, text, font, width, height)
    if any(type(value) is not int or not 0 <= value <= 15 for value in values):
        raise ValueError(f"{name} pixel index")
    bitmap = Image.new("RGBA", (width, height))
    bitmap.putdata([PALETTE_COLORS[value] for value in values])
    output.parent.mkdir(parents=True, exist_ok=True)
    bitmap.save(output)


def render_preview(output: Path, scale: int = 8) -> None:
    """Write an enlarged magic_stone_info preview for visual review."""
    if scale <= 0:
        raise ValueError("preview scale")
    name = "magic_stone_info"
    source, text = TEXTS[name]
    font = load_font(FONT)
    values = _render_values(name, text, font, WIDTHS[name], 16)
    bitmap = Image.new("RGBA", (WIDTHS[name], 16))
    bitmap.putdata([PALETTE_COLORS[value] for value in values])
    bitmap.resize(
        (bitmap.width * scale, bitmap.height * scale),
        Image.Resampling.NEAREST,
    ).save(output)


def self_test() -> None:
    assert PALETTE_COLORS[CORE_INDEX][:3] == (0xF8, 0xF8, 0xF8)
    font = load_font(FONT)
    for name, (source, text) in TEXTS.items():
        assert source and text
        values = _render_values(name, text, font, WIDTHS[name], 16)
        assert set(values) <= set(range(16))
        assert not set(values) & set(range(1, MINIMUM_INK_INDEX))
        assert max(values) == CORE_INDEX
    special = SPECIAL_LAYOUTS["magic_stone_info"]
    assert special == {
        "cell_width": 16,
        "cell_height": 16,
        "tracking": -1,
        "output_width": 64,
    }
    assert all(font.getlength(char) == 16 for char in TEXTS["magic_stone_info"][1])
    source_width = special["cell_width"] * 5 + special["tracking"] * 4
    assert source_width == 76
