"""Registry for translated crops rendered by fixed picture-text rules."""

from busin.picture_text.cock import render_initial as render_cock
from busin.picture_text.menu_text import LABELS, render_initial as render_menu

COCK00_TEXT_NAMES = (
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
    "page_switch",
)
RENDERERS = {("cock_00", name): render_menu for name in COCK00_TEXT_NAMES}
RENDERERS.update(
    {
        ("cock_01", "mage_magic"): render_cock,
        ("cock_01", "priest_magic"): render_cock,
        ("cock_01", "first_allied"): render_menu,
        ("cock_01", "second_allied"): render_menu,
        ("cock_01", "skill"): render_menu,
        ("cock_01", "front_back_change"): render_menu,
    }
)


def render_initial(item: dict, rect: dict, image, output, text_style: str) -> None:
    renderer = RENDERERS.get((item["name"], rect["name"]))
    if renderer is None:
        raise ValueError(f"{item['name']}/{rect['name']} has no picture-text renderer")
    renderer(image, rect, output, text_style)
