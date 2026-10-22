"""Patch verified original sprite records when translated crops move."""

from dataclasses import dataclass
import struct

from busin.disc import FILE_BASE
from busin.ui import translated_rect

# sub_169990 indexes these tables in 12-byte steps. The first four shorts are
# source x/y/width/height; the last two are destination width/height.
SHARED_MENU_TABLE = 0x583EA0
COCK01_MENU_TABLE = 0x583DAC
MENU_TABLE = SHARED_MENU_TABLE


@dataclass(frozen=True)
class SpriteRecord:
    table: int
    index: int


SHARED_MENU_RECORDS = {
    "confirm": SpriteRecord(SHARED_MENU_TABLE, 9),
    "back": SpriteRecord(SHARED_MENU_TABLE, 10),
    "event_item": SpriteRecord(SHARED_MENU_TABLE, 11),
    "item_data": SpriteRecord(SHARED_MENU_TABLE, 12),
    "magic_list": SpriteRecord(SHARED_MENU_TABLE, 13),
    "page_switch": SpriteRecord(SHARED_MENU_TABLE, 14),
    "weapon": SpriteRecord(SHARED_MENU_TABLE, 15),
    "armor": SpriteRecord(SHARED_MENU_TABLE, 16),
    "accessory": SpriteRecord(SHARED_MENU_TABLE, 17),
    "status": SpriteRecord(SHARED_MENU_TABLE, 18),
    "magic_stone_info": SpriteRecord(SHARED_MENU_TABLE, 19),
    "switch": SpriteRecord(SHARED_MENU_TABLE, 20),
    "move": SpriteRecord(SHARED_MENU_TABLE, 22),
    "front_back_change": SpriteRecord(SHARED_MENU_TABLE, 23),
}
COCK01_MENU_RECORDS = {
    "mage_magic": SpriteRecord(COCK01_MENU_TABLE, 11),
    "priest_magic": SpriteRecord(COCK01_MENU_TABLE, 12),
    "first_allied": SpriteRecord(COCK01_MENU_TABLE, 17),
    "second_allied": SpriteRecord(COCK01_MENU_TABLE, 18),
    "skill": SpriteRecord(COCK01_MENU_TABLE, 19),
}


@dataclass(frozen=True)
class SpritePatchSpec:
    texture: str
    rect: str
    source: str
    reuse_offset: tuple[int, int]
    record: SpriteRecord
    original: tuple[int, int, int, int, int, int]
    source_original: tuple[int, int, int, int]
    texture_location: tuple[int, str, int, int, int]


PAGE_INDEX = 14
SWITCH_INDEX = 20
PAGE_ORIGINAL = (64, 184, 88, 16, 88, 16)
SWITCH_ORIGINAL = (100, 184, 48, 16, 48, 16)
PATCH_SPECS = {
    ("cock_00", "switch"): SpritePatchSpec(
        "cock_00",
        "switch",
        "page_switch",
        (36, 0),
        SHARED_MENU_RECORDS["switch"],
        SWITCH_ORIGINAL,
        PAGE_ORIGINAL[:4],
        (0, "0x20700", 4, 256, 256),
    ),
}


def _texture(manifest: dict, name: str) -> dict:
    item = next((entry for entry in manifest["part"] if entry["name"] == name), None)
    if item is None:
        raise ValueError(f"{name} texture is missing")
    return item


def _check_original_record(
    elf: bytes, record: SpriteRecord, expected: tuple[int, ...], name: str
) -> None:
    offset = record.table - FILE_BASE + record.index * 12
    if struct.unpack_from("<6h", elf, offset) != expected:
        raise ValueError(
            f"{name} original sprite record differs at {offset + FILE_BASE:#x}"
        )


def elf_writes(elf: bytes, manifest: dict) -> dict[int, bytes]:
    for item in manifest["part"]:
        for rect in item["rects"]:
            if "reuse" in rect and (item["name"], rect["name"]) not in PATCH_SPECS:
                raise ValueError(
                    f"{item['name']}/{rect['name']} has no verified sprite-table patch"
                )

    writes = {}
    for (texture_name, rect_name), spec in PATCH_SPECS.items():
        item = _texture(manifest, texture_name)
        by_name = {rect["name"]: rect for rect in item["rects"]}
        rect = by_name.get(rect_name)
        if rect is None:
            raise ValueError(f"{texture_name}/{rect_name} translated crop is missing")
        if rect.get("reuse") != {
            "name": spec.source,
            "x": spec.reuse_offset[0],
            "y": spec.reuse_offset[1],
        }:
            raise ValueError(
                f"{rect_name} must reuse {spec.source} at {spec.reuse_offset}"
            )
        if (
            item["chunk"],
            item["offset"],
            item["slot"],
            item["width"],
            item["height"],
        ) != spec.texture_location:
            raise ValueError(f"{texture_name} texture location changed")

        source = by_name.get(spec.source)
        if source is None:
            raise ValueError(f"{texture_name} reuse source {spec.source} is missing")
        for name, original, expected in (
            (spec.source, source, spec.source_original),
            (spec.rect, rect, spec.original[:4]),
        ):
            if (
                tuple(original[key] for key in ("x", "y", "width", "height"))
                != expected
            ):
                raise ValueError(f"{name} original-disc rect changed")
        _check_original_record(elf, spec.record, spec.original, spec.rect)
        source_record = SHARED_MENU_RECORDS[spec.source]
        _check_original_record(
            elf,
            source_record,
            (*spec.source_original, *spec.source_original[2:]),
            spec.source,
        )

        target = translated_rect(item, rect)
        # Preserve destination dimensions and its caller-supplied screen position.
        # Only source x changes; the original PNG rect stays for honest comparisons.
        if target["y"] != rect["y"]:
            raise ValueError(f"{spec.rect} sprite patch only supports moving source x")
        if target["x"] != rect["x"]:
            offset = spec.record.table - FILE_BASE + spec.record.index * 12
            writes[offset] = struct.pack("<h", target["x"])
    return writes
