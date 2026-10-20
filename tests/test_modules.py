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
    assert terms["攻撃力"]["zh"] == "攻击"
    assert terms["防御力"]["zh"] == "防御"
    assert terms["回避力"]["zh"] == "闪避"
    assert terms["信頼"]["zh"] == "信任" and terms["信頼度"]["zh"] == "信任度"
    assert terms["名品"]["zh"] == "精品"
    assert terms["エルフ"]["zh"] == "精灵" and terms["精霊"]["zh"] == "灵"
    assert terms["妖精"]["zh"] == "妖精"
    assert terms["ピクシー"]["zh"] == "小妖精" and terms["フェアリー"]["zh"] == "妖精"
    assert terms["合成柔材"]["zh"] == "合成材料" and terms["合成素材"]["zh"] == "合成材料"
    assert terms["死者の頭蓋"]["zh"] == "死者头骨" and terms["竜の心臓"]["zh"] == "龙之心"
    assert terms["スライムゼリー"]["zh"] == "史莱姆凝胶"


def test_trust_terms_are_used_in_interface_translations():
    terms = json.loads((DATA / "terms.json").read_text(encoding="utf-8"))
    assert terms["信頼"]["zh"] == "信任" and terms["信頼度"]["zh"] == "信任度"
    found = False

    def walk(item):
        nonlocal found
        if isinstance(item, dict):
            source = item.get("source")
            text = item.get("text")
            if isinstance(source, str) and isinstance(text, (str, list)):
                translated = "\n".join(
                    part for part in (text if isinstance(text, list) else [text])
                    if isinstance(part, str)
                )
                assert "信赖" not in translated, (item.get("id"), translated)
                normalized = source.replace("\n", "").replace("/", "")
                if "信頼度" in normalized:
                    found = True
                    assert "信任度" in translated, (item.get("id"), translated)
            for value in item.values():
                walk(value)
        elif isinstance(item, list):
            for value in item:
                walk(value)

    for path in DATA.rglob("*.json"):
        walk(json.loads(path.read_text(encoding="utf-8")))
    assert found


def test_elemental_spirit_term_is_used_in_interface_translations():
    found = False

    def walk(item):
        nonlocal found
        if isinstance(item, dict):
            source = item.get("source")
            text = item.get("text")
            if isinstance(source, str) and "精霊" in source and isinstance(text, (str, list)):
                found = True
                translated = "\n".join(
                    part for part in (text if isinstance(text, list) else [text])
                    if isinstance(part, str)
                )
                assert ("灵" in translated or "魂" in translated), (item.get("id"), translated)
                assert "精灵" not in translated, (item.get("id"), translated)
            for value in item.values():
                walk(value)
        elif isinstance(item, list):
            for value in item:
                walk(value)

    for path in (DATA / "interface").glob("*.json"):
        walk(json.loads(path.read_text(encoding="utf-8")))
    assert found


def test_material_terms_are_used_in_translations():
    def translated(item):
        text = item.get("text")
        if isinstance(text, list):
            return "\n".join(part for part in text if isinstance(part, str))
        return text if isinstance(text, str) else ""

    def walk(item):
        if isinstance(item, dict):
            source = item.get("source")
            text = translated(item)
            if text and isinstance(source, str):
                if "合成柔材" in source or "合成素材" in source or "柔材" in source or "素材" in source:
                    assert "材料" in text and "柔材" not in text and "素材" not in text, (item.get("id"), text)
                if "死者の頭蓋" in source:
                    assert "死者头骨" in text and "头盖骨" not in text, (item.get("id"), text)
                if "竜の心臓" in source:
                    assert "龙之心" in text and "龙的心脏" not in text, (item.get("id"), text)
                if "スライムゼリー" in source:
                    assert "史莱姆凝胶" in text and "软泥凝胶" not in text, (item.get("id"), text)
            for value in item.values():
                walk(value)
        elif isinstance(item, list):
            for value in item:
                walk(value)

    for path in DATA.rglob("*.json"):
        walk(json.loads(path.read_text(encoding="utf-8")))


def test_fairy_terms_are_used_in_translations():
    def translated(item):
        text = item.get("text")
        if isinstance(text, list):
            return "\n".join(part for part in text if isinstance(part, str))
        return text if isinstance(text, str) else ""

    def walk(item):
        if isinstance(item, dict):
            source = item.get("source")
            text = translated(item)
            if text:
                if isinstance(source, str) and "ピクシー" in source:
                    assert "小妖精" in text and "精灵" not in text, (item.get("id"), text)
                if isinstance(source, str) and "フェアリー" in source:
                    assert "妖精" in text and "精灵" not in text, (item.get("id"), text)
            for value in item.values():
                walk(value)
        elif isinstance(item, list):
            for value in item:
                walk(value)

    for path in DATA.rglob("*.json"):
        walk(json.loads(path.read_text(encoding="utf-8")))


def test_parameter_terms_are_used_in_translations():
    old_terms = ("攻击力", "防御力", "回避力", "回避率")
    rate_count = 0
    attack_capability_found = False
    for path in DATA.rglob("*.json"):
        doc = json.loads(path.read_text(encoding="utf-8"))

        def walk(item):
            nonlocal rate_count, attack_capability_found
            if isinstance(item, dict):
                source = item.get("source")
                text = item.get("text")
                if isinstance(source, str) and "text" in item:
                    parts = text if isinstance(text, list) else [text]
                    translated = "\n".join(
                        part for part in parts if isinstance(part, str)
                    )
                    assert not any(term in translated for term in old_terms), (
                        path,
                        item.get("id"),
                        translated,
                    )
                    normalized = source.replace("\n", "").replace("/", "")
                    if "攻撃能力" in normalized:
                        attack_capability_found = True
                        assert "攻击能力" in translated
                    rate_count += translated.count("闪避率")
                for value in item.values():
                    walk(value)
            elif isinstance(item, list):
                for value in item:
                    walk(value)

        walk(doc)
    assert attack_capability_found
    assert rate_count == 3


def test_ui_codes():
    from busin.ui_text import self_test
    self_test()


@pytest.mark.skipif(not BIN.exists(), reason="没有原盘")
def test_ui_strings():
    from busin.ui_text import disc_self_test
    disc_self_test()


def test_bar():
    from busin.picture_text.bar import self_test
    self_test()


@pytest.mark.skipif(not BIN.exists(), reason="没有原盘")
def test_bar_disc():
    from busin.picture_text.bar import disc_self_test
    disc_self_test()



def test_ali():
    from busin.picture_text.ali import self_test
    self_test()


@pytest.mark.skipif(not BIN.exists(), reason="没有原盘")
def test_ali_disc():
    from busin.picture_text.ali import disc_self_test
    disc_self_test()


def test_cock_text():
    from busin.picture_text.cock import self_test
    self_test()


def test_menu_text():
    from busin.picture_text.menu_text import self_test
    self_test()


def test_plaque():
    from busin.plaque import self_test
    self_test()


@pytest.mark.skipif(not BIN.exists(), reason="没有原盘")
def test_plaque_disc():
    from busin.plaque import disc_self_test
    disc_self_test()


def test_ui_page_boundaries():
    from busin.patch.ui_pages import self_test
    self_test()


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
