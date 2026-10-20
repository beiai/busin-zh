import copy
import json
import struct

from PIL import Image
import pytest

from busin.disc import BIN, FILE_BASE, chunk_row, read_pack
from busin.patch.picture_rects import MENU_TABLE, SWITCH_INDEX, elf_writes
from busin.paths import DATA
from busin.picture_text import status
from busin.picture_text.registry import RENDERERS
from busin.ui import (
    PART_HANDLES,
    PART_SOURCE_COPIES,
    _part_span,
    entry_slices,
    export_pictures,
    load_images,
    translated_rect,
)
from busin.tmx import PSMCT16, PSMT4, TmxImage, read_tmx_bytes


def manifest():
    return load_images(DATA / "picture" / "images.json")


def menu(doc):
    return next(item for item in doc["part"] if item["name"] == "cock_00")


def rect(item, name):
    return next(r for r in item["rects"] if r["name"] == name)


def pixels(path):
    with Image.open(path) as bitmap:
        return bitmap.convert("RGBA").tobytes()


def test_status_labels_use_terms_and_declared_grayscale_palette(tmp_path):
    status.self_test()
    doc = manifest()
    item = next(item for item in doc["part"] if item["name"] == "status00")
    expected = status.translations()
    labels = [r for r in item["rects"] if r["name"] in status.SOURCES]
    assert {r["name"]: r["text"] for r in labels} == expected
    assert {r["name"]: r["source"] for r in labels} == status.SOURCES
    image = TmxImage(
        tmp_path / "status00.tmx",
        0,
        "status00.tim",
        256,
        256,
        PSMT4,
        PSMCT16,
        [status.PALETTE_COLORS for _index in range(16)],
        (0,) * (256 * 256),
    )
    allowed = set(status.PALETTE_COLORS)
    for label in labels:
        output = tmp_path / f"{label['name']}.zh.png"
        status.render_initial(image, label, output)
        with Image.open(output) as bitmap:
            assert bitmap.size == (label["width"], label["height"])
            colors = set(bitmap.convert("RGBA").get_flattened_data())
        assert colors <= allowed
        assert status.PALETTE_COLORS[status.CORE_INDEX] in colors
        assert status.PALETTE_COLORS[15] not in colors

    bad = dict(labels[0])
    bad["text"] = "智力"
    with pytest.raises(ValueError, match="source or translation changed"):
        status.render_initial(image, bad, tmp_path / "bad.zh.png")
    palettes = [status.PALETTE_COLORS for _index in range(16)]
    palettes[status.PALETTE] = tuple(
        (0, 0, 0, 0xFF) for _index in range(16)
    )
    changed = TmxImage(
        image.path,
        image.user_id,
        image.comment,
        image.width,
        image.height,
        image.pixel_format,
        image.palette_format,
        palettes,
        image.pixels,
    )
    with pytest.raises(ValueError, match="palette 2 colors changed"):
        status.render_initial(changed, labels[0], tmp_path / "changed.zh.png")


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
    doc["whole"], doc["part"] = [], [item]
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


def test_facility_rects_use_numeric_ids_and_registered_renderers():
    doc = manifest()
    expected = {
        "guild_00": (3, [f"{index:02d}" for index in range(3)]),
        "guild_a0": (16, [f"{index:02d}" for index in range(3, 19)]),
        "guild_a1": (7, [f"{index:02d}" for index in range(19, 26)]),
        "shop_00": (15, [f"{index:02d}" for index in range(15)]),
        "temple00": (8, [f"{index:02d}" for index in range(8)]),
        "bar_00": (15, [f"{index:02d}" for index in range(15)]),
        "ali_00": (16, [f"{index:02d}" for index in range(16)]),
        "ali_01": (12, [f"{index:02d}" for index in range(16, 28)]),
        "ali_02": (6, [f"{index:02d}" for index in range(28, 34)]),
        "ali_03": (12, [f"{index:02d}" for index in range(34, 46)]),
        "yado3": (2, [f"{index:02d}" for index in range(2)]),
    }
    for name, (count, names) in expected.items():
        item = next(entry for entry in doc["part"] if entry["name"] == name)
        assert len(item["rects"]) == count
        assert [entry["name"] for entry in item["rects"]] == names
        assert all(
            (entry["width"], entry["height"]) == (128, 32)
            and entry["source"] and entry["text"]
            and (item["name"], entry["name"]) in RENDERERS
            for entry in item["rects"]
        )
    assert sum(count for count, _names in expected.values()) == 112
    assert {name: (entry["chunk"], entry["offset"], entry["slot"]) for name, entry in (
        (item["name"], item) for item in doc["part"]
        if item["name"] in {"bar_00", "ali_00", "ali_01", "ali_02", "ali_03", "yado3"}
    )} == {
        "bar_00": (19, "0x0", 0),
        "ali_00": (630, "0x0", 0),
        "ali_01": (630, "0x8240", 1),
        "ali_02": (630, "0x10480", 2),
        "ali_03": (630, "0x146c0", 3),
        "yado3": (23, "0x0", 0),
    }
    assert PART_HANDLES == {
        "bar_00": (0x442650, 0),
        "ali_00": (0x513D50, 0),
        "ali_01": (0x513D50, 1),
        "ali_02": (0x513D50, 2),
        "ali_03": (0x513D50, 3),
        "yado3": (0x442650, 0),
    }
    assert PART_SOURCE_COPIES["bar_00"] == ((0, "0x30cc0"), (12, "0x2ca40"))
    shop = next(entry for entry in doc["part"] if entry["name"] == "shop_00")
    assert [entry["text"] for entry in shop["rects"]] == [
        "购买武具", "出售", "鉴定", "解除诅咒", "魔法合成",
        "购买防具", "购买武器", "购买饰品", "羊皮纸合成", "分解魔法石",
        "合成魔法材料", "返回", "离开", "分解魔法石", "合成魔法材料",
    ]
    bar = next(entry for entry in doc["part"] if entry["name"] == "bar_00")
    assert [(entry["source"], entry["text"]) for entry in bar["rects"][:2]] == [
        ("仲間に入る", "加入同伴"), ("仲間を外す", "移出同伴"),
    ]
    ali01 = next(entry for entry in doc["part"] if entry["name"] == "ali_01")
    assert ali01["rects"][0]["source"] == "マジックキャンセル"
    assert ali01["rects"][0]["text"] == "魔法取消"
    ali03 = next(entry for entry in doc["part"] if entry["name"] == "ali_03")
    assert ali03["rects"][-1] == {
        "name": "45", "x": 128, "y": 160, "width": 128, "height": 32,
        "palette": 1, "source": "各自行動", "text": "各自行动",
    }
    yado = next(entry for entry in doc["part"] if entry["name"] == "yado3")
    assert [(entry["source"], entry["text"]) for entry in yado["rects"]] == [
        ("泊まる", "住宿"), ("外に出る", "离开"),
    ]
    assert [(entry["x"], entry["y"], entry["palette"]) for entry in yado["rects"]] == [
        (0, 0, 7), (128, 0, 7),
    ]


def test_menu_banner_renderer_checks_text_size_and_palette(tmp_path):
    from busin.picture_text.menu_banner import PALETTE_COLORS, render_initial

    image = TmxImage(
        tmp_path / "facility.tmx",
        0,
        "facility.tim",
        256,
        256,
        PSMT4,
        PSMCT16,
        [PALETTE_COLORS for _index in range(16)],
        (0,) * (256 * 256),
    )
    label = {
        "name": "00", "x": 0, "y": 0, "width": 128, "height": 32,
        "palette": 1, "source": "外に出る", "text": "离开",
    }
    output = tmp_path / "00.zh.png"
    render_initial(image, label, output)
    with Image.open(output) as bitmap:
        assert bitmap.size == (128, 32)

    empty = {**label, "text": ""}
    with pytest.raises(ValueError, match="00 text"):
        render_initial(image, empty, tmp_path / "empty.zh.png")
    wrong_size = {**label, "width": 127}
    with pytest.raises(ValueError, match="00 is 127x32"):
        render_initial(image, wrong_size, tmp_path / "wrong.zh.png")
    image.palettes[1] = tuple((0, 0, 0, 0xFF) for _index in range(16))
    with pytest.raises(ValueError, match="palette 1 colors changed"):
        render_initial(image, label, tmp_path / "palette.zh.png")
    image.palettes[1] = PALETTE_COLORS
    image.palettes.extend([PALETTE_COLORS, PALETTE_COLORS])
    render_initial(image, {**label, "palette": 17}, tmp_path / "17.zh.png")


def test_migrated_crop_files_are_complete():
    for folder, count in (("bar", 15), ("ali", 46), ("yado", 2)):
        root = DATA / "picture" / folder
        expected = {f"{index:02d}.png" for index in range(count)}
        expected |= {f"{index:02d}.zh.png" for index in range(count)}
        assert {path.name for path in root.iterdir()} == expected
        for index in range(count):
            with Image.open(root / f"{index:02d}.png") as original:
                assert original.size == (128, 32)
            with Image.open(root / f"{index:02d}.zh.png") as translated:
                assert translated.size == (128, 32)


@pytest.mark.skipif(not BIN.exists(), reason="没有原盘")
def test_bar_00_every_source_copy_is_identical():
    from busin.ui import entry_slices, part_source_items

    doc = manifest()
    item = next(entry for entry in doc["part"] if entry["name"] == "bar_00")
    sources = part_source_items(item)
    assert [(entry["chunk"], entry["offset"]) for entry in sources] == [
        (19, "0x0"), (0, "0x30cc0"), (12, "0x2ca40"),
    ]
    with BIN.open("rb") as source:
        read = lambda at, size: read_pack(source, at, size)
        row = lambda chunk: chunk_row(source, chunk)
        blobs = [entry_slices(read, entry, row)[1] for entry in sources]
        packed = [_part_span(read, entry, DATA / "picture", row)[1] for entry in sources]
    assert blobs[0] == blobs[1] == blobs[2]
    assert packed[0] == packed[1] == packed[2]


@pytest.mark.skipif(not BIN.exists(), reason="没有原盘")
def test_migrated_parents_change_only_declared_rect_pixels():
    doc = manifest()
    names = {"bar_00", "ali_00", "ali_01", "ali_02", "ali_03", "yado3"}
    with BIN.open("rb") as source:
        read = lambda at, size: read_pack(source, at, size)
        row = lambda chunk: chunk_row(source, chunk)
        for item in (entry for entry in doc["part"] if entry["name"] in names):
            _, original_blob = entry_slices(read, item, row)
            _, packed_blob = _part_span(read, item, DATA / "picture", row)
            original = read_tmx_bytes(original_blob)
            packed = read_tmx_bytes(packed_blob)
            pixel_at = 0x40 + len(original.palettes) * 32
            assert len(original_blob) == len(packed_blob)
            assert original_blob[:pixel_at] == packed_blob[:pixel_at]
            assert original.palettes == packed.palettes
            assert original.pixels != packed.pixels
            allowed = {
                y * item["width"] + x
                for rect in item["rects"]
                for y in range(rect["y"], rect["y"] + rect["height"])
                for x in range(rect["x"], rect["x"] + rect["width"])
            }
            changed = {
                index
                for index, (before, after) in enumerate(
                    zip(original.pixels, packed.pixels)
                )
                if before != after
            }
            assert changed <= allowed


@pytest.mark.skipif(not BIN.exists(), reason="没有原盘")
def test_guild_00_every_source_copy_is_identical():
    from busin.ui import entry_slices, part_source_items

    doc = manifest()
    item = next(entry for entry in doc["part"] if entry["name"] == "guild_00")
    sources = part_source_items(item)
    assert [(entry["chunk"], entry["offset"]) for entry in sources] == [
        (16, "0x0"), (12, "0x24800"),
    ]
    with BIN.open("rb") as source:
        read = lambda at, size: read_pack(source, at, size)
        row = lambda chunk: chunk_row(source, chunk)
        blobs = [entry_slices(read, entry, row)[1] for entry in sources]
    assert blobs[0] == blobs[1]
