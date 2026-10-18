"""Patch verified original sprite records when translated crops move."""
import struct

from busin.disc import FILE_BASE
from busin.ui import translated_rect


# sub_169990 indexes this table in 12-byte steps. The first four shorts are
# source x/y/width/height; the last two are destination width/height.
MENU_TABLE = 0x583EA0
PAGE_INDEX = 14
SWITCH_INDEX = 20
PAGE_ORIGINAL = (64, 184, 88, 16, 88, 16)
SWITCH_ORIGINAL = (100, 184, 48, 16, 48, 16)


def elf_writes(elf: bytes, manifest: dict) -> dict[int, bytes]:
    writes = {}
    for item in manifest["part"]:
        for rect in item["rects"]:
            if "reuse" not in rect:
                continue
            target = translated_rect(item, rect)
            if (item["name"], rect["name"]) != ("cock_00", "switch"):
                raise ValueError(f"{item['name']}/{rect['name']} has no verified sprite-table patch")
            if rect["reuse"]["name"] != "page_switch":
                raise ValueError("switch must reuse page_switch")
            if (item["chunk"], item["offset"], item["slot"], item["width"], item["height"]) != (
                    0, "0x20700", 4, 256, 256):
                raise ValueError("cock_00 texture location changed")
            by_name = {r["name"]: r for r in item["rects"]}
            for name, index, expected in (
                ("page_switch", PAGE_INDEX, PAGE_ORIGINAL),
                ("switch", SWITCH_INDEX, SWITCH_ORIGINAL),
            ):
                original = by_name[name]
                if tuple(original[key] for key in ("x", "y", "width", "height")) != expected[:4]:
                    raise ValueError(f"{name} original-disc rect changed")
                offset = MENU_TABLE - FILE_BASE + index * 12
                if struct.unpack_from("<6h", elf, offset) != expected:
                    raise ValueError(f"{name} original sprite record differs at {offset + FILE_BASE:#x}")
            # Preserve destination dimensions and its caller-supplied screen
            # position. Only source x changes; leaving the original PNG rect
            # intact is necessary for honest original-disc comparisons.
            if target["y"] != rect["y"]:
                raise ValueError("switch sprite patch only supports moving source x")
            offset = MENU_TABLE - FILE_BASE + SWITCH_INDEX * 12
            writes[offset] = struct.pack("<h", target["x"])
    return writes
