"""Render fixed-width menu labels with white grayscale anti-aliasing."""

from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from busin.picture_text.render import (
    FontSpec,
    load_font,
    render_line,
    render_text_alpha,
)
from busin.tmx import TmxImage

FONT = FontSpec("NotoSansSC-VF.ttf", "Medium", 16)
PALETTE = 2
PALETTE_COLORS = tuple(
    (value, value, value, 0xFF)
    for value in (
        0,
        16,
        32,
        48,
        64,
        80,
        96,
        112,
        136,
        152,
        168,
        184,
        200,
        216,
        232,
        248,
    )
)
CORE_INDEX = 15
# The Japanese labels skip the three darkest non-black steps. Keeping partial
# edge pixels out of them avoids a green cast after console/video filtering.
MINIMUM_INK_INDEX = 4
PALETTE_10_COLORS = tuple(
    (value, value, value, 0xFF)
    for value in (
        0,
        24,
        40,
        56,
        72,
        88,
        104,
        120,
        136,
        152,
        168,
        184,
        200,
        216,
        232,
        248,
    )
)
TEMPLATE_DIR = Path(__file__).resolve().parents[2] / "data" / "picture" / "templates"


@dataclass(frozen=True)
class MenuLabelSpec:
    source: str
    text: str
    width: int
    height: int = 16
    palette: int = PALETTE
    template: str | None = None
    tracking: int = 0
    source_cell_width: int | None = None


LABELS = {
    "accessory": MenuLabelSpec("装飾品", "饰品", 48),
    "armor": MenuLabelSpec("防具", "防具", 32),
    "back": MenuLabelSpec("戻る", "返回", 32),
    "confirm": MenuLabelSpec("決定", "确定", 32),
    "event_item": MenuLabelSpec("Event Item", "事件道具", 64),
    "item_data": MenuLabelSpec("Item Data", "道具数据", 64),
    "magic_list": MenuLabelSpec("魔法一覧", "魔法列表", 64),
    "magic_stone_info": MenuLabelSpec(
        "魔法石情報", "魔法石信息", 64, tracking=-1, source_cell_width=16
    ),
    "move": MenuLabelSpec("移動", "移动", 32),
    "status": MenuLabelSpec("ステータス", "状态", 56),
    "weapon": MenuLabelSpec("武器", "武器", 32),
    "first_allied": MenuLabelSpec(
        "1st アレイド", "第1连携", 72, palette=10, template="menu_title_tab1.png"
    ),
    "second_allied": MenuLabelSpec(
        "2nd アレイド", "第2连携", 72, palette=10, template="menu_title_tab1.png"
    ),
    "skill": MenuLabelSpec(
        "スキル", "技能", 56, palette=10, template="menu_title_tab2.png"
    ),
    "front_back_change": MenuLabelSpec("前後列交代", "前后切换", 64, palette=10),
    "page_switch": MenuLabelSpec("ページ切り替え", "页签切换", 88),
}


def _nearest_palette_index(rgb) -> int:
    return min(
        range(len(PALETTE_COLORS)),
        key=lambda index: sum(
            (value - PALETTE_COLORS[index][channel]) ** 2
            for channel, value in enumerate(rgb)
        ),
    )


def _scale_rgba_to_indices(
    values: list[int], source_width: int, height: int, output_width: int
) -> list[int]:
    if len(values) != source_width * height:
        raise ValueError("scaled text data has the wrong source dimensions")
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


def _render_values(spec: MenuLabelSpec, font) -> list[int]:
    if spec.source_cell_width is None:
        return _drop_low_indices(
            render_text_alpha(
                spec.text,
                font,
                spec.width,
                spec.height,
                spec.source,
                spec.tracking,
            )
        )

    if any(font.getlength(char) != spec.source_cell_width for char in spec.text):
        raise ValueError(
            f"{spec.source} glyph advance is not {spec.source_cell_width}px"
        )
    source_width = spec.source_cell_width * len(spec.text) + spec.tracking * (
        len(spec.text) - 1
    )
    values = render_text_alpha(
        spec.text,
        font,
        source_width,
        spec.height,
        spec.source,
        spec.tracking,
    )
    return _drop_low_indices(
        _scale_rgba_to_indices(values, source_width, spec.height, spec.width)
    )


def _template_background(spec: MenuLabelSpec) -> Image.Image:
    template_path = TEMPLATE_DIR / spec.template
    size = (spec.width, spec.height)
    with Image.open(template_path) as source_image:
        if source_image.size != size or source_image.mode not in ("RGB", "RGBA"):
            raise ValueError(
                f"{spec.template} must be an RGB/RGBA {size[0]}x{size[1]} template"
            )
        return source_image.convert("RGB")


def _template_bitmap(spec: MenuLabelSpec, font) -> Image.Image:
    background = _template_background(spec)
    coverage = render_text_alpha(
        spec.text,
        font,
        spec.width,
        spec.height,
        spec.source,
        spec.tracking,
        horizontal="center",
    )
    pixels = [
        tuple(
            round(channel + (255 - channel) * alpha / 15)
            for channel in background.getpixel((x, y))
        )
        for y in range(spec.height)
        for x, alpha in enumerate(coverage[y * spec.width : (y + 1) * spec.width])
    ]
    bitmap = Image.new("RGB", (spec.width, spec.height))
    bitmap.putdata(pixels)
    return bitmap


def render_initial(
    image: TmxImage,
    rect: dict,
    output: Path,
    text_style: str | None = None,
) -> None:
    """Write one translated menu crop declared by LABELS."""
    del text_style  # Menu labels use their fixed grayscale renderer.
    name = rect["name"]
    spec = LABELS.get(name)
    if spec is None:
        raise ValueError(f"{name} has no menu text")
    if (rect.get("source"), rect.get("text")) != (spec.source, spec.text):
        raise ValueError(f"{name} source or translation changed")
    if (rect["width"], rect["height"], rect["palette"]) != (
        spec.width,
        spec.height,
        spec.palette,
    ):
        raise ValueError(
            f"{name} must be {spec.width}x{spec.height} with palette {spec.palette}"
        )

    font = load_font(FONT)
    if spec.template is not None:
        if tuple(image.palettes[spec.palette]) != PALETTE_10_COLORS:
            raise ValueError(f"{name} palette {spec.palette} colors changed")
        bitmap = _template_bitmap(spec, font)
    else:
        palette_colors = (
            PALETTE_COLORS if spec.palette == PALETTE else PALETTE_10_COLORS
        )
        if tuple(image.palettes[spec.palette]) != palette_colors:
            raise ValueError(f"{name} palette {spec.palette} colors changed")
        values = _render_values(spec, font)
        bitmap = Image.new("RGBA", (spec.width, spec.height))
        bitmap.putdata([palette_colors[value] for value in values])
    output.parent.mkdir(parents=True, exist_ok=True)
    bitmap.save(output)


def render_preview(output: Path, scale: int = 8) -> None:
    """Write an enlarged magic_stone_info preview for visual review."""
    if scale <= 0:
        raise ValueError("preview scale")
    spec = LABELS["magic_stone_info"]
    font = load_font(FONT)
    values = _render_values(spec, font)
    bitmap = Image.new("RGBA", (spec.width, spec.height))
    bitmap.putdata([PALETTE_COLORS[value] for value in values])
    bitmap.resize(
        (bitmap.width * scale, bitmap.height * scale),
        Image.Resampling.NEAREST,
    ).save(output)


def self_test() -> None:
    assert PALETTE_COLORS[CORE_INDEX][:3] == (0xF8, 0xF8, 0xF8)
    assert set(LABELS) == {
        "accessory",
        "armor",
        "back",
        "confirm",
        "event_item",
        "item_data",
        "magic_list",
        "magic_stone_info",
        "move",
        "status",
        "weapon",
        "first_allied",
        "second_allied",
        "skill",
        "front_back_change",
        "page_switch",
    }
    font = load_font(FONT)
    for spec in LABELS.values():
        assert spec.source and spec.text
        values = _render_values(spec, font)
        assert set(values) <= set(range(16))
        assert not set(values) & set(range(1, MINIMUM_INK_INDEX))
        assert max(values) == CORE_INDEX
        if spec.template is None:
            width = spec.width
            if spec.source_cell_width is not None:
                width = spec.source_cell_width * len(spec.text) + spec.tracking * (
                    len(spec.text) - 1
                )
            render_line(spec.text, font, width, spec.height, spec.source)

    templates = {name: spec.template for name, spec in LABELS.items() if spec.template}
    assert templates == {
        "first_allied": "menu_title_tab1.png",
        "second_allied": "menu_title_tab1.png",
        "skill": "menu_title_tab2.png",
    }
    for filename in set(templates.values()):
        with Image.open(TEMPLATE_DIR / filename) as template:
            assert template.size in ((56, 16), (72, 16))
            assert template.mode in ("RGB", "RGBA")
    special = LABELS["magic_stone_info"]
    assert (
        special.width,
        special.height,
        special.source_cell_width,
        special.tracking,
    ) == (64, 16, 16, -1)
    assert all(font.getlength(char) == 16 for char in special.text)
    assert special.source_cell_width * 5 + special.tracking * 4 == 76
