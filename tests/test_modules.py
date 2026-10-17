import json
from pathlib import Path

import pytest

from busin.paths import BIN, DATA, disc_path


def test_disc_path(tmp_path):
    assert disc_path(tmp_path / "no.toml") is None
    blank = tmp_path / "blank.toml"
    blank.write_bytes(b"")
    assert disc_path(blank) is None
    bad = tmp_path / "bad.toml"
    bad.write_text("disc =\n", encoding="utf-8")
    assert disc_path(bad) is None
    good = tmp_path / "good.toml"
    good.write_text('disc = "C:/game.bin"\n', encoding="utf-8")
    assert disc_path(good) == Path("C:/game.bin")
    rel = tmp_path / "rel.toml"
    rel.write_text('disc = "game.bin"\n', encoding="utf-8")
    assert disc_path(rel) == tmp_path / "game.bin"


def test_tmx():
    from busin.tmx import self_test
    self_test()


def test_text():
    from busin.text import self_test
    self_test()


def test_counted_message_can_grow():
    from busin.text import compact_counted, counted_messages

    source = (7).to_bytes(2, "big") + b"".join(word.to_bytes(2, "big") for word in (
        0x003F, 0x0040, 0xFFFF, 0x0041, 0x0042, 0x0043, 0xFFFF,
    )) + bytes(32)
    records = [
        {"source": "あい", "type": "subtitle", "text": ["甲乙丙"]},
        {"source": "うえお", "type": "subtitle", "text": ["丁戊己庚"]},
    ]
    tokens = {"甲": 0x003F, "乙": 0x0040, "丙": 0x0041, "丁": 0x0042,
              "戊": 0x0043, "己": 0x0044, "庚": 0x0045}
    packed = compact_counted(source, records, "center", tokens.__getitem__)
    messages = counted_messages(packed)
    assert [len(words) for _at, _end, words in messages] == [3, 4]
    assert int.from_bytes(packed[:2], "big") == 9


def test_texture_export():
    import struct
    import tempfile

    from PIL import Image

    from busin.textures import export_pack_bytes
    from busin.tmx import PSMCT32, PSMT8

    def tmx(comment, palette_count, palette_format, width, height, pixel_format, payload):
        body = struct.pack(
            "<BBHHBBHBBII28s", palette_count, palette_format, width, height, pixel_format,
            0, 0, 0, 0, 0, 0, comment,
        )
        data = body + payload
        return struct.pack("<HHI4s", 2, 0, 16 + len(data), b"TMX0") + bytes(4) + data

    palette0 = bytes((10, 20, 30, 40)) * 256
    palette1 = bytes((50, 60, 70, 80)) * 256
    multi = tmx(b"multi.tga", 2, PSMCT32, 2, 2, PSMT8, palette0 + palette1 + bytes((0, 1, 2, 3)))
    raw = tmx(b"raw.tga", 0, PSMCT32, 1, 2, 0x01, bytes((1, 2, 3, 4, 5, 6)))
    pack = bytes(16) + multi + bytes(24) + multi + bytes(32) + raw

    with tempfile.TemporaryDirectory() as tmp:
        output = Path(tmp) / "textures"
        output.mkdir()
        kept = output / "keep.png"
        kept.write_bytes(b"keep")
        report = export_pack_bytes(pack, output)
        assert kept.read_bytes() == b"keep"
        assert report.textures == 2
        assert report.pngs == 3
        assert report.duplicates == 1
        assert report.failures == {}
        names = {path.name for path in output.glob("*.png")}
        assert names == {"keep.png", "multi.pal00.png", "multi.pal01.png", "raw.png"}
        with Image.open(output / "multi.pal00.png") as bitmap:
            assert bitmap.getpixel((0, 0)) == (10, 20, 30, 40)
        with Image.open(output / "multi.pal01.png") as bitmap:
            assert bitmap.getpixel((0, 0)) == (50, 60, 70, 80)
        with Image.open(output / "raw.png") as bitmap:
            assert bitmap.getpixel((0, 0)) == (1, 2, 3, 255)


def test_text_edit():
    from busin.text_edit import self_test
    self_test()


@pytest.mark.skipif(not BIN.exists(), reason="没有原盘")
def test_text_edit_disc():
    from busin.text_edit import disc_self_test
    disc_self_test()


def test_font_plan():
    from busin.font import self_test
    self_test()


def test_ui_manifest():
    from busin.ui import self_test
    self_test()


def test_name_labels():
    from busin.naming import self_test
    self_test()


@pytest.mark.skipif(not BIN.exists(), reason="没有原盘")
def test_name_label_codes():
    from busin.naming import disc_self_test
    disc_self_test()


def test_terms():
    terms = json.loads((DATA / "terms.json").read_text(encoding="utf-8"))
    assert len(terms) == len(set(terms))
    values = set()
    for item in terms.values():
        assert set(item) <= {"zh", "ui", "note"} and item["zh"]
        if "note" in item:
            assert item["note"]
        values.add(item["zh"])
        if "ui" in item:
            values.add(item["ui"])
    assert terms["ドゥーハン"]["zh"] == "杜汉" and terms["アレイド"]["zh"] == "连携"
    assert terms["ヴィガー"]["zh"] == "维加" and terms["ヘルガ"]["zh"] == "赫尔加"
    assert terms["デス"]["zh"] == "德斯" and terms["リゲル"]["zh"] == "莉格尔"
    assert terms["プレイステーション 2"]["zh"] == "PS2"
    assert terms["剣士"]["zh"] == "剑士" and terms["戦士"]["zh"] == "战士"
    assert terms["人間"]["zh"] == "人类" and terms["ドワーフ"]["zh"] == "矮人"
    assert terms["ノーム"]["zh"] == "侏儒" and terms["侍"]["zh"] == "武士"
    assert terms["魔術師"]["zh"] == "魔法师"
    assert terms["ページ切り替え"]["ui"] == "切换"
    labels = json.loads((DATA / "picture" / "name" / "labels.json").read_text(encoding="utf-8"))
    texts = list(labels["strings"].values()) + list(labels["operations"].values())
    texts += [labels["confirm"], labels["back"], labels["page_wide"], labels["page_narrow"]]
    missing = [text for text in texts if text.replace(" ", "") not in values]
    assert missing == []


def test_ui_codes():
    from busin.ui_text import self_test
    self_test()


@pytest.mark.skipif(not BIN.exists(), reason="没有原盘")
def test_ui_strings():
    from busin.ui_text import disc_self_test
    disc_self_test()


def test_bar():
    from busin.bar import self_test
    self_test()


@pytest.mark.skipif(not BIN.exists(), reason="没有原盘")
def test_bar_disc():
    from busin.bar import disc_self_test
    disc_self_test()


def test_plaque():
    from busin.plaque import self_test
    self_test()


@pytest.mark.skipif(not BIN.exists(), reason="没有原盘")
def test_plaque_disc():
    from busin.plaque import disc_self_test
    disc_self_test()


def test_ui_font():
    from busin.ui_font import self_test
    self_test()


@pytest.mark.skipif(not BIN.exists(), reason="没有原盘")
def test_script_index():
    from busin.text import script_self_test
    script_self_test()


@pytest.mark.skipif(not BIN.exists(), reason="没有原盘")
def test_font_patch():
    from busin.image import self_test
    self_test()
