"""Render the two cock banner labels with the shared menu banner renderer."""
from pathlib import Path

from busin.picture_text.menu_banner import (
    BACKGROUND,
    BASE_HEIGHT,
    BASE_WIDTH,
    CORE_INDEX,
    FONT,
    OUTLINE_INDEX,
    PALETTE_COLORS,
    load_background as load_blank_background,
    render_initial as render_banner,
    self_test as banner_self_test,
)
from busin.picture_text.render import TEXT_INDICES, load_font, render_text_indices
from busin.tmx import TmxImage

PALETTE = 17
TEXTS = {
    "mage_magic": ("魔術師魔法", "魔法师魔法"),
    "priest_magic": ("僧侶魔法", "僧侣魔法"),
}


def load_background() -> tuple[int, ...]:
    return load_blank_background()


def render_initial(
    image: TmxImage,
    rect: dict,
    output: Path,
    text_style: str,
) -> None:
    """Draw one of the two fixed cock labels on the shared banner background."""
    name = rect["name"]
    if name not in TEXTS:
        raise ValueError(f"{name} has no generated cock text")
    source, text = TEXTS[name]
    if rect.get("source") != source or rect.get("text") != text:
        raise ValueError(f"{name} source or translation changed")
    if rect["palette"] != PALETTE:
        raise ValueError(f"{name} must use palette {PALETTE}")
    render_banner(image, rect, output, text_style)


def self_test() -> None:
    banner_self_test()
    font = load_font(FONT)
    for name, (source, text) in TEXTS.items():
        assert source and text
        for text_style in ("stroke-aa", "outline-aa"):
            values = render_text_indices(
                text, font, BASE_WIDTH, BASE_HEIGHT, name, text_style, PALETTE_COLORS
            )
            assert CORE_INDEX in values and OUTLINE_INDEX in values
            assert set(value for value in values if value is not None) <= set(TEXT_INDICES)
