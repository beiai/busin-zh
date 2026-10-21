import struct

import pytest

from busin.disc import BIN, FILE_BASE

pytestmark = pytest.mark.skipif(not BIN.exists(), reason="没有原盘")


@pytest.fixture(scope="module")
def translated_elf():
    from busin.disasm import load_elf
    from busin.ui_text import UI, ui_patches

    source = load_elf()
    writes, extra, _info = ui_patches(source)
    translated = bytearray(source)
    for off, data in writes.items():
        translated[off:off + len(data)] = data
    reverse = {**UI, **{code: char for char, code in extra.items()}, 0: " "}
    return source, translated, reverse


def _text(elf, reverse, va, cells):
    return "".join(reverse[code] for code in struct.unpack_from(
        f"<{cells}H", elf, va - FILE_BASE)).rstrip()


def test_fixed_party_names_use_the_translated_ui_glyphs(translated_elf):
    from busin.runtime_names import load_names

    _source, translated, reverse = translated_elf
    for name in load_names():
        codes = struct.unpack_from("<8H", translated, name.template - FILE_BASE)
        shown = []
        for code in codes:
            if code == 0xFFFF:
                break
            shown.append(reverse[code])
        assert "".join(shown) == name.text


def test_item_name_uses_game_display_cells(translated_elf):
    from busin.ui_records import name_slots

    _source, translated, reverse = translated_elf
    slot = next(item for item in name_slots(_source) if item["id"] == "4A5100")
    assert slot["width"] == 5 and slot["slack"] == 2 and slot["room"] >= 6
    assert _text(translated, reverse, slot["va"], 7).rstrip() == "元素之灵细剑"


@pytest.mark.parametrize("getter,index,japanese,expected", [
    (0x12EEB0, 18, "ロングソード", "长剑"),
    (0x12EF10, 17, "鎖かたびら", "锁子甲"),
    (0x12EF10, 61, "邪神の小盾", "邪神小盾牌"),
    (0x12EF10, 62, "盾", "盾"),
])
def test_item_names_at_the_game_read_address(translated_elf, getter, index, japanese, expected):
    source, translated, reverse = translated_elf
    # Read the name base from the original getter's lui/addiu, including its signed low half.
    high = struct.unpack_from("<H", source, getter + 20 - FILE_BASE)[0]
    low = struct.unpack_from("<h", source, getter + 28 - FILE_BASE)[0]
    va = (high << 16) + low + 64 * index
    assert _text(source, reverse, va, 10) == japanese
    assert _text(translated, reverse, va, 10) == expected
    assert translated[va - 2 - FILE_BASE:va - FILE_BASE] == source[va - 2 - FILE_BASE:va - FILE_BASE]


def test_shield_description_stays_in_its_own_record(translated_elf):
    source, translated, reverse = translated_elf
    previous = _text(translated, reverse, 0x4B5514, 51).replace(" ", "")
    current = _text(translated, reverse, 0x4B557A, 51).replace(" ", "")
    assert previous == "蕴含魔神诅咒的盾。以高防御和高闪避为代价，夺走装备者大量体力。"
    assert current == "大盾。虽重，闪避高。"
    # The unused record and table marker are structural bytes, not translation space.
    assert translated[0x4B3CC0 - FILE_BASE:0x4B3D2C - FILE_BASE] == source[
        0x4B3CC0 - FILE_BASE:0x4B3D2C - FILE_BASE]


def test_consecutive_full_descriptions_do_not_merge(translated_elf):
    _source, translated, reverse = translated_elf
    assert _text(translated, reverse, 0x4B18BA, 51).replace(" ", "") == (
        "据说有操纵火焰的魔神寄宿的双手剑。战斗中可发动魔神之力也能打倒不死族。")
    assert _text(translated, reverse, 0x4B1920, 51).replace(" ", "") == (
        "据说有操纵雷电的魔神寄宿的双手剑。战斗中可发动魔神之力也能打倒不死族。")
    assert _text(translated, reverse, 0x4B1986, 51).startswith("据说狂战士用过的双手剑。")


@pytest.mark.parametrize("index,expected", [
    (0, "想克服\n恐惧"),
    (1, "请告诉我\n何谓信任"),
    (2, "让恋情\n成真的咒语"),
    (3, "我的恋人\n请想想办法"),
    (4, "我的恋人\n请务必管管"),
    (5, "请让我\n成为僧侣"),
    (6, "请让我\n成为主教"),
    (7, "魔法石店\n我开定了！"),
    (8, "武器店\n我开定了！"),
    (9, "合成剑\n我来卖啦！"),
    (10, "请去寻找\n名叫露易的女人"),
    (11, "能把这把斧\n送过去吗？"),
    (12, "请调查某男子\n的生死"),
    (13, "请告诉我\n我是什么人"),
    (14, "我想制作\n不死灵药"),
    (15, "请帮我夺回\n睡眠"),
    (16, "请帮我查\n我丈夫吗？"),
    (17, "请帮我夺回\n我队的盾"),
    (18, "我想做件\n好事看看"),
    (19, "请寻找名叫汉娜\n的女人"),
    (20, "请帮我找回\n音乐"),
    (21, "能帮我找来\n某本典籍吗？"),
    (22, "救救哥哥"),
    (23, "人家想去\n迷宫看看"),
])
def test_quest_titles_at_the_game_read_address(translated_elf, index, expected):
    source, translated, reverse = translated_elf
    # Decode the same signed lui/addiu pair and index used by the original painter.
    high = struct.unpack_from("<H", source, 0x3DD264 - FILE_BASE)[0]
    low = struct.unpack_from("<h", source, 0x3DD26C - FILE_BASE)[0]
    table = (high << 16) + low
    va = struct.unpack_from("<I", translated, table + 4 * index - FILE_BASE)[0]
    chars = []
    for at in range(va, table, 2):
        code = struct.unpack_from("<H", translated, at - FILE_BASE)[0]
        if code == 0xFFFF:
            break
        chars.append("\n" if code == 0xFFFE else reverse[code])
    else:
        pytest.fail("委托标题没有结束符")
    assert "".join(chars).rstrip() == expected


def test_quest_detail_line_breaks_at_the_game_read_address(translated_elf):
    source, translated, reverse = translated_elf
    # Decode the same signed lui/addiu pair and paired pointers used by the detail painter.
    high = struct.unpack_from("<H", source, 0x3DD6B4 - FILE_BASE)[0]
    low = struct.unpack_from("<h", source, 0x3DD6C0 - FILE_BASE)[0]
    table = (high << 16) + low
    pointers = struct.unpack_from("<II", translated, table - FILE_BASE)
    assert pointers == (0x56A2B0, 0x56A2B0)
    chars = []
    for at in range(pointers[0], 0x56AE70, 2):
        code = struct.unpack_from("<H", translated, at - FILE_BASE)[0]
        if code == 0xFFFF:
            break
        chars.append("\n" if code == 0xFFFE else reverse[code])
    else:
        pytest.fail("委托说明没有结束符")
    assert "".join(chars).rstrip() == (
        "自从在死亡面前胆怯以来，\n我当忍者的尊严也没了。\n能不能再让我\n站上一次战场？"
    )


def test_quest_and_requester_pointers_are_preserved(translated_elf):
    source, translated, _reverse = translated_elf
    for start, end in ((0x569BD0, 0x569C30), (0x569EE8, 0x569F48)):
        assert translated[start - FILE_BASE:end - FILE_BASE] == source[start - FILE_BASE:end - FILE_BASE]
    assert struct.unpack_from("<I", translated, 0x569C2C - FILE_BASE)[0] == 0x569BC0


@pytest.mark.parametrize("va,value,error", [
    (0x3DD24C, 0x2C620019, "quest title reader"),
    (0x3DD26C, 0x24429EE4, "quest title reader"),
    (0x3DD290, 0x2404FFFD, "quest title reader"),
    (0x569EE8, 0x569C2E, "quest title pointer"),
    (0x569EEC, 0x569C30, "quest title pointer"),
    (0x569EE4, 0, "quest title terminator"),
])
def test_quest_title_structure_changes_stop_packing(translated_elf, va, value, error):
    from busin.ui_records import quest_title_slots

    source, _translated, _reverse = translated_elf
    changed = bytearray(source)
    struct.pack_into("<I", changed, va - FILE_BASE, value)
    with pytest.raises(AssertionError, match=error):
        quest_title_slots(changed)


@pytest.mark.parametrize("va,reader,error", [
    (0x12EECC, "names", "name reader"),
    (0x12F37C, "blurbs", "blurb reader"),
    (0x12F350, "blurbs", "blurb row copy"),
])
def test_record_layout_changes_stop_packing(translated_elf, va, reader, error):
    from busin.ui_records import blurb_slots, name_slots

    source, _translated, _reverse = translated_elf
    changed = bytearray(source)
    changed[va - FILE_BASE] ^= 1
    with pytest.raises(AssertionError, match=error):
        (name_slots if reader == "names" else blurb_slots)(changed)
