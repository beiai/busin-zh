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
    assert terms["仲間"]["zh"] == "同伴" and terms["転移"]["zh"] == "传送"
    assert terms["転移の薬"]["zh"] == "传送药"
    assert terms["牽制射撃"]["zh"] == "牵制射击" and terms["フロントガード"]["zh"] == "前卫防御"
    assert terms["Wスラッシュ"]["zh"] == "双斩" and terms["ラッシュ"]["zh"] == "突击"
    assert terms["クイーンガード長"]["zh"] == "女王近卫长"
    assert terms["通行証"]["zh"] == "通行证" and terms["魔法石"]["zh"] == "魔法石"
    assert terms["合成"]["zh"] == "合成" and terms["分解"]["zh"] == "分解"
    assert terms["リーダー"]["zh"] == "领袖" and terms["宿業"]["zh"] == "业障"
    assert terms["冒険者"]["zh"] == "冒险者" and terms["魔神"]["zh"] == "魔神"
    assert terms["エルフ"]["zh"] == "精灵" and terms["精霊"]["zh"] == "灵"
    assert terms["妖精"]["zh"] == "妖精"
    assert terms["ピクシー"]["zh"] == "小妖精" and terms["フェアリー"]["zh"] == "妖精"
    assert terms["合成柔材"]["zh"] == "合成材料" and terms["合成素材"]["zh"] == "合成材料"
    assert terms["死者の頭蓋"]["zh"] == "死者头骨" and terms["竜の心臓"]["zh"] == "龙之心"
    assert terms["スライムゼリー"]["zh"] == "史莱姆凝胶"
    assert terms["瀬死"]["zh"] == "濒死" and terms["即死"]["zh"] == "即死"
    assert terms["胸あて"]["zh"] == "胸甲" and terms["小手"]["zh"] == "护手"
    assert terms["鎖かたびら"]["zh"] == "锁子甲" and terms["皮鎧"]["zh"] == "皮甲"
    assert terms["兜"]["zh"] == "头盔" and terms["ブーツ"]["zh"] == "长靴"
    assert terms["ローブ"]["zh"] == "长袍" and terms["メイス"]["zh"] == "钉锤"
    assert terms["フレイル"]["zh"] == "连枷" and terms["石弓"]["zh"] == "弩"
    assert terms["短刀"]["zh"] == "短刀" and terms["小剣"]["zh"] == "细剑"
    assert terms["長剣"]["zh"] == "长剑" and terms["両手剣"]["zh"] == "双手剑"
    assert terms["ダガー"]["zh"] == "匕首" and terms["ナイフ"]["zh"] == "小刀" and terms["ショートソード"]["zh"] == "短剑"
    assert terms["手斧"]["zh"] == "手斧" and terms["戦斧"]["zh"] == "战斧"
    assert terms["回避率"]["zh"] == "闪避率" and terms["クリティカル率"]["zh"] == "暴击率"
    assert terms["戦意"]["zh"] == "斗志" and terms["耐性"]["zh"] == "抗性"
    assert terms["攻撃回数"]["zh"] == "攻击次数" and terms["エナジードレイン"]["zh"] == "能量吸取"
    assert terms["ブレス"]["zh"] == "吐息" and terms["転移"]["zh"] == "传送"


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


def test_equipment_terms_are_used_in_translations():
    mapping = {
        "胸あて": "胸甲", "小手": "护手", "鎖かたびら": "锁子甲",
        "皮鎧": "皮甲", "兜": "头盔", "ブーツ": "长靴", "ローブ": "长袍",
        "メイス": "钉锤", "フレイル": "连枷", "石弓": "弩",
        "短刀": "短刀", "小剣": "细剑", "長剣": "长剑",
        "両手剣": "双手剑", "ダガー": "匕首", "ナイフ": "小刀",
        "なげナイフ": "飞刀", "手斧": "手斧", "戦斧": "战斧",
    }

    def translated(item):
        text = item.get("text")
        if isinstance(text, list):
            return "".join(part for part in text if isinstance(part, str))
        return text if isinstance(text, str) else ""

    def walk(item, path):
        if isinstance(item, dict):
            source = item.get("source")
            text = translated(item)
            if text and isinstance(source, str):
                compact = "".join(text.split())
                for term, wanted in mapping.items():
                    if term == "ナイフ" and "なげナイフ" in source:
                        continue
                    if term in source:
                        assert wanted in compact, (path, item.get("id"), source, text, wanted)
            for value in item.values():
                walk(value, path)
        elif isinstance(item, list):
            for value in item:
                walk(value, path)

    for path in DATA.rglob("*.json"):
        walk(json.loads(path.read_text(encoding="utf-8")), path)


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


def test_menu_banner_text():
    from busin.picture_text.menu_banner import self_test
    self_test()


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
