import copy
import json
import struct

from PIL import Image
import pytest

from busin.disc import BIN, FILE_BASE, chunk_row, read_pack
from busin.patch.picture_rects import MENU_TABLE, SWITCH_INDEX, elf_writes
from busin.paths import DATA
from busin.picture_text.registry import RENDERERS
from busin.ui import (
    _part_span,
    entry_slices,
    export_pictures,
    load_images,
    translated_rect,
)
from busin.tmx import read_tmx_bytes


def manifest():
    return load_images(DATA / "picture" / "images.json")


def menu(doc):
    return next(item for item in doc["part"] if item["name"] == "cock_00")


def rect(item, name):
    return next(r for r in item["rects"] if r["name"] == name)


def pixels(path):
    with Image.open(path) as bitmap:
        return bitmap.convert("RGBA").tobytes()


def test_every_declared_picture_text_has_a_registered_renderer():
    doc = manifest()
    declared = {
        (item["name"], rect["name"])
        for item in doc["part"]
        for rect in item["rects"]
        if "text" in rect
    }
    assert declared == set(RENDERERS)


@pytest.mark.parametrize(
    "case, message",
    [
        ("missing", "missing from this texture"),
        ("self", "must not itself reuse"),
        ("chain", "must not itself reuse"),
        ("palette", "palette differs"),
        ("outside_x", "outside"),
        ("outside_y", "outside"),
        ("negative", "invalid reuse"),
        ("bool", "invalid reuse"),
        ("extra", "invalid reuse"),
        ("unrelated_overlap", "translated rects page_switch and switch overlap"),
    ],
)
def test_invalid_reuse_is_rejected_at_load(tmp_path, case, message):
    doc = manifest()
    item = menu(doc)
    parent, child = rect(item, "page_switch"), rect(item, "switch")
    if case == "missing":
        child["reuse"]["name"] = "not_in_this_texture"
    elif case == "self":
        child["reuse"]["name"] = "switch"
    elif case == "chain":
        parent["reuse"] = {"name": "switch", "x": 0, "y": 0}
    elif case == "palette":
        child["palette"] = 1
    elif case == "outside_x":
        child["reuse"]["x"] = 41
    elif case == "outside_y":
        child["reuse"]["y"] = 1
    elif case == "negative":
        child["reuse"]["x"] = -1
    elif case == "bool":
        child["reuse"]["x"] = True
    elif case == "extra":
        child["reuse"]["unused"] = 0
    else:
        del child["reuse"]
        item["rects"] = [parent, child]
        parent["x"] = child["x"]
    path = tmp_path / "images.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        load_images(path)


@pytest.fixture
def original_menu(tmp_path):
    if not BIN.exists():
        pytest.skip("没有原盘")
    doc = manifest()
    item = menu(doc)
    item["rects"] = [r for r in item["rects"] if r["name"] in {"page_switch", "switch"}]
    doc["whole"], doc["generated"], doc["part"] = [], [], [item]
    with BIN.open("rb") as source:
        read = lambda at, size: read_pack(source, at, size)
        row = lambda chunk: chunk_row(source, chunk)
        _, blob = entry_slices(read, item, row)
        yield doc, item, read, row, blob


def test_export_reuses_suffix_regardless_of_order_and_preserves_originals(
    tmp_path, original_menu
):
    doc, item, read, row, blob = original_menu
    item["rects"].reverse()  # The derived crop deliberately precedes its source.
    before = copy.deepcopy(item)
    export_pictures(read, doc, tmp_path, row)
    assert item == before
    folder = tmp_path / "menu"
    with Image.open(folder / "page_switch.zh.png") as parent:
        assert parent.size == (88, 16)
        expected = parent.convert("RGBA").crop((32, 0, 80, 16))
        assert expected.tobytes() == pixels(folder / "switch.zh.png")
        assert parent.convert("RGB").crop((64, 0, 88, 16)).getbbox() is None
    image = read_tmx_bytes(blob)
    with Image.open(folder / "switch.png") as original:
        expected = [
            image.palettes[2][image.pixels[y * 256 + x]]
            for y in range(184, 200)
            for x in range(100, 148)
        ]
        assert list(original.get_flattened_data()) == expected
    child = rect(item, "switch")
    assert (child["x"], translated_rect(item, child)["x"]) == (100, 96)


def test_normal_export_preserves_a_manual_source_and_refreshes_derived_crops(
    tmp_path, original_menu
):
    doc, item, read, row, _ = original_menu
    export_pictures(read, doc, tmp_path, row)
    parent_path = tmp_path / "menu" / "page_switch.zh.png"
    with Image.open(parent_path) as bitmap:
        edited = bitmap.convert("RGBA")
    edited.putpixel((32, 0), (248, 248, 248, 255))
    edited.save(parent_path)
    export_pictures(read, doc, tmp_path, row)
    assert pixels(parent_path) == edited.tobytes()
    assert (
        pixels(tmp_path / "menu" / "switch.zh.png")
        == edited.crop((32, 0, 80, 16)).tobytes()
    )


def test_force_zh_regenerates_the_programmatic_source_and_derived_crop(
    tmp_path, original_menu
):
    doc, item, read, row, _ = original_menu
    export_pictures(read, doc, tmp_path, row)
    parent_path = tmp_path / "menu" / "page_switch.zh.png"
    with Image.open(parent_path) as bitmap:
        edited = bitmap.convert("RGBA")
    edited.putpixel((32, 0), (248, 248, 248, 255))
    edited.save(parent_path)
    export_pictures(read, doc, tmp_path, row, force_zh=True)
    assert pixels(parent_path) != edited.tobytes()
    with Image.open(parent_path) as bitmap:
        assert (
            pixels(tmp_path / "menu" / "switch.zh.png")
            == bitmap.convert("RGBA").crop((32, 0, 80, 16)).tobytes()
        )


def test_repack_uses_translated_rect_and_leaves_other_pixels_untouched(
    tmp_path, original_menu
):
    doc, item, read, row, blob = original_menu
    export_pictures(read, doc, tmp_path, row)
    original = read_tmx_bytes(blob)
    first = _part_span(read, item, tmp_path, row)
    item["rects"].reverse()
    assert _part_span(read, item, tmp_path, row) == first
    packed = read_tmx_bytes(first[1])
    expected = list(original.pixels)
    with Image.open(tmp_path / "menu" / "page_switch.zh.png") as bitmap:
        translated = bitmap.convert("RGBA")
        for y in range(184, 200):
            for x in range(64, 152):
                expected[y * 256 + x] = packed.palettes[2].index(
                    translated.getpixel((x - 64, y - 184))
                )
    assert list(packed.pixels) == expected


@pytest.mark.parametrize(
    "case", ["stale", "missing_child", "missing_parent", "parent_size"]
)
def test_build_rejects_broken_derived_images(tmp_path, original_menu, case):
    doc, item, read, row, _ = original_menu
    export_pictures(read, doc, tmp_path, row)
    folder = tmp_path / "menu"
    if case == "stale":
        path = folder / "switch.zh.png"
        with Image.open(path) as bitmap:
            edited = bitmap.convert("RGBA")
        edited.putpixel((47, 15), (248, 248, 248, 255))
        edited.save(path)
        message = "reused crop switch disagrees with page_switch"
    elif case == "missing_child":
        (folder / "switch.zh.png").unlink()
        message = "translated crop is missing"
    elif case == "missing_parent":
        (folder / "page_switch.zh.png").unlink()
        message = "source translated crop is missing"
    else:
        Image.new("RGBA", (87, 16)).save(folder / "page_switch.zh.png")
        message = "source page_switch size changed"
    with pytest.raises(ValueError, match=message):
        _part_span(read, item, tmp_path, row)


@pytest.mark.skipif(not BIN.exists(), reason="没有原盘")
def test_sprite_patch_changes_only_source_x_and_checks_original_record():
    from busin.disasm import load_elf

    doc, elf = manifest(), load_elf()
    offset = MENU_TABLE - FILE_BASE + SWITCH_INDEX * 12
    writes = elf_writes(elf, doc)
    assert writes == {offset: struct.pack("<h", 96)}
    patched = bytearray(elf)
    for at, data in writes.items():
        patched[at : at + len(data)] = data
    assert struct.unpack_from("<6h", patched, offset) == (96, 184, 48, 16, 48, 16)
    assert (
        patched[:offset] == elf[:offset] and patched[offset + 2 :] == elf[offset + 2 :]
    )
    for at in (offset, MENU_TABLE - FILE_BASE + 14 * 12):
        changed = bytearray(elf)
        changed[at] ^= 1
        with pytest.raises(ValueError, match="original sprite record differs"):
            elf_writes(changed, doc)
    child = rect(menu(doc), "switch")
    child["name"] = "unverified"
    with pytest.raises(ValueError, match="no verified sprite-table patch"):
        elf_writes(elf, doc)


@pytest.mark.skipif(not BIN.exists(), reason="没有原盘")
def test_switch_must_keep_its_verified_reuse_declaration():
    from busin.disasm import load_elf

    doc, elf = manifest(), load_elf()
    child = rect(menu(doc), "switch")
    del child["reuse"]
    child["x"] = 200
    with pytest.raises(ValueError, match="switch must reuse page_switch"):
        elf_writes(elf, doc)


@pytest.mark.skipif(not BIN.exists(), reason="没有原盘")
def test_template_renderer_checks_the_game_palette(tmp_path):
    from busin.picture_text.menu_text import render_initial

    doc = manifest()
    item = next(item for item in doc["part"] if item["name"] == "cock_01")
    label = rect(item, "first_allied")
    with BIN.open("rb") as source:
        read = lambda at, size: read_pack(source, at, size)
        row = lambda chunk: chunk_row(source, chunk)
        _, blob = entry_slices(read, item, row)
    image = read_tmx_bytes(blob)
    render_initial(image, label, tmp_path / "label.zh.png")
    image.palettes[10] = tuple((0, 0, 0, 0xFF) for _index in range(16))
    with pytest.raises(ValueError, match="palette 10 colors changed"):
        render_initial(image, label, tmp_path / "label.zh.png")
