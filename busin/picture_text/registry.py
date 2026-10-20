"""Registry for translated crops rendered by fixed picture-text rules."""

from busin.picture_text.cock import render_initial as render_cock
from busin.picture_text.menu_banner import render_initial as render_banner
from busin.picture_text.menu_text import LABELS, render_initial as render_menu
from busin.picture_text.status import render_initial as render_status

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
STATUS_TEXT_NAMES = ("01", "05", "06", "07", "08", "09", "10", "11", "12", "13")
RENDERERS.update({("status00", name): render_status for name in STATUS_TEXT_NAMES})
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
FACILITY_TEXT_NAMES = {
    "guild_00": tuple(f"{index:02d}" for index in range(3)),
    "guild_a0": tuple(f"{index:02d}" for index in range(3, 19)),
    "guild_a1": tuple(f"{index:02d}" for index in range(19, 26)),
    "shop_00": tuple(f"{index:02d}" for index in range(15)),
    "temple00": tuple(f"{index:02d}" for index in range(8)),
}
MIGRATED_BANNER_TEXT_NAMES = {
    "bar_00": tuple(f"{index:02d}" for index in range(15)),
    "ali_00": tuple(f"{index:02d}" for index in range(16)),
    "ali_01": tuple(f"{index:02d}" for index in range(16, 28)),
    "ali_02": tuple(f"{index:02d}" for index in range(28, 34)),
    "ali_03": tuple(f"{index:02d}" for index in range(34, 46)),
    "yado3": tuple(f"{index:02d}" for index in range(2)),
}
for texture, names in {**FACILITY_TEXT_NAMES, **MIGRATED_BANNER_TEXT_NAMES}.items():
    RENDERERS.update({(texture, name): render_banner for name in names})


def render_initial(item: dict, rect: dict, image, output, text_style: str) -> None:
    renderer = RENDERERS.get((item["name"], rect["name"]))
    if renderer is None:
        raise ValueError(f"{item['name']}/{rect['name']} has no picture-text renderer")
    renderer(image, rect, output, text_style)
