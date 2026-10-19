"""Render status00 parameter labels with the status screen's grayscale palette."""

import json
from pathlib import Path

from PIL import Image

from busin.paths import DATA
from busin.picture_text.render import FontSpec, load_font, render_text_alpha
from busin.tmx import PSMCT16, PSMT4, TmxImage


FONT = FontSpec("LXGWWenKai-Medium.ttf", "Medium", 16)
PALETTE = 2
PALETTE_COLORS = tuple(
    (value, value, value, 0xFF)
    for value in (
        0x00,
        0x08,
        0x10,
        0x18,
        0x28,
        0x30,
        0x38,
        0x48,
        0x50,
        0x58,
        0x68,
        0x70,
        0x78,
        0x88,
        0x90,
        0xA0,
    )
)
CORE_INDEX = 14
# Index 15 is one step brighter than the original labels. Keep it unused so
# 0x909090 remains the exact solid ink color.
GRAY_INDICES = tuple(range(CORE_INDEX + 1))
LABEL_SIZES = {
    "01": (32, 16),
    "05": (48, 16),
    "06": (40, 16),
    "07": (48, 16),
    "08": (40, 16),
    "09": (40, 16),
    "10": (40, 16),
    "11": (40, 16),
    "12": (40, 16),
    "13": (40, 16),
}
SOURCES = {
    "01": "レベル",
    "05": "攻撃力",
    "06": "回避力",
    "07": "防御力",
    "08": "力",
    "09": "知恵",
    "10": "信仰心",
    "11": "生命力",
    "12": "敏捷度",
    "13": "幸運度",
}


def translations() -> dict[str, str]:
    terms = json.loads((DATA / "terms.json").read_text(encoding="utf-8"))
    try:
        return {name: terms[source]["zh"] for name, source in SOURCES.items()}
    except KeyError as error:
        raise ValueError(f"status00 source is missing from terms.json: {error}") from None


def coverage_indices(values: list[int]) -> list[int]:
    """Map 16 coverage levels to palette indices 0-14 without a low-edge cutoff."""
    core = PALETTE_COLORS[CORE_INDEX][0]
    result = []
    for value in values:
        if type(value) is not int or not 0 <= value <= 15:
            raise ValueError(f"status00 coverage is {value}")
        target = round(value * core / 15)
        result.append(
            min(
                GRAY_INDICES,
                key=lambda index: abs(PALETTE_COLORS[index][0] - target),
            )
        )
    return result


def render_initial(
    image: TmxImage,
    rect: dict,
    output: Path,
    text_style: str | None = None,
) -> None:
    """Write one translated status crop using terms.json and the original palette."""
    del text_style  # Status labels always use their fixed grayscale renderer.
    name = rect["name"]
    source = SOURCES.get(name)
    if source is None:
        raise ValueError(f"{name} has no status00 text")
    text = translations()[name]
    if (rect.get("source"), rect.get("text")) != (source, text):
        raise ValueError(f"{name} source or translation changed")
    size = LABEL_SIZES[name]
    if (rect["width"], rect["height"], rect["palette"]) != (*size, PALETTE):
        raise ValueError(f"{name} must be {size[0]}x{size[1]} with palette {PALETTE}")
    if (image.pixel_format, image.palette_format) != (PSMT4, PSMCT16):
        raise ValueError(f"{name} needs PSMT4 pixels and PSMCT16 palettes")
    if tuple(image.palettes[PALETTE]) != PALETTE_COLORS:
        raise ValueError(f"{name} palette {PALETTE} colors changed")

    font = load_font(FONT)
    values = coverage_indices(
        render_text_alpha(text, font, *size, source)
    )
    if CORE_INDEX not in values:
        raise ValueError(f"{name} has no {PALETTE_COLORS[CORE_INDEX][0]:#04x} solid ink")
    bitmap = Image.new("RGBA", size)
    bitmap.putdata([PALETTE_COLORS[value] for value in values])
    output.parent.mkdir(parents=True, exist_ok=True)
    bitmap.save(output)


def self_test() -> None:
    assert PALETTE_COLORS[CORE_INDEX][:3] == (0x90, 0x90, 0x90)
    assert PALETTE_COLORS[15][:3] == (0xA0, 0xA0, 0xA0)
    assert set(coverage_indices(list(range(16)))) == set(GRAY_INDICES)
    assert coverage_indices([0, 15]) == [0, CORE_INDEX]
    assert set(LABEL_SIZES) == set(SOURCES)
    assert all(size[1] == 16 for size in LABEL_SIZES.values())
    assert set(translations()) == set(SOURCES)
