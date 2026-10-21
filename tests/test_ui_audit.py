import struct

import pytest

from busin.disc import BIN
from busin.ui_audit import _drop_suffixes, compact, self_test


def test_ui_audit_helpers():
    self_test()


@pytest.mark.skipif(not BIN.exists(), reason="没有原盘")
@pytest.mark.parametrize("kind", ["source", "terminator", "unused", "reference"])
def test_direct_ui_text_guards(kind):
    from busin.ui_text import check_direct_ui_text
    from busin.disasm import load_elf

    changed = bytearray(load_elf())
    if kind == "source":
        struct.pack_into("<H", changed, 0x552430 - 0xFF000, 0)
    elif kind == "terminator":
        struct.pack_into("<H", changed, 0x552440 - 0xFF000, 0)
    elif kind == "unused":
        struct.pack_into("<H", changed, 0x552442 - 0xFF000, 1)
    else:
        struct.pack_into("<I", changed, 0x388F54 - 0xFF000, 0)
    with pytest.raises(AssertionError):
        check_direct_ui_text(changed)


@pytest.mark.skipif(not BIN.exists(), reason="没有原盘")
def test_direct_ui_translations_are_unpadded():
    import struct

    from busin.disasm import load_elf
    from busin.ui_text import DIRECT_UI_TEXT, UI, ui_patches_with_table

    source = load_elf()
    writes, _extra, _info, table = ui_patches_with_table(source)
    reverse = {**UI, **{code: char for char, code in table.items()}}
    patched = bytearray(source)
    for offset, data in writes.items():
        patched[offset:offset + len(data)] = data
    expected = {
        "552430": "要返回城镇吗？",
        "567F78": "要打开宝箱吗？",
        "569910": "死神  ON",
        "569920": "死神  OFF",
        "59EB38": "要返回标题，可以吗？",
    }
    for item in DIRECT_UI_TEXT:
        codes = []
        for index in range(item["max_cells"] + 1):
            code = struct.unpack_from("<H", patched, item["va"] - 0xFF000 + index * 2)[0]
            if code == 0xFFFF:
                break
            codes.append(code)
        else:
            raise AssertionError(item["id"])
        text = "".join(" " if code == 0 else "/" if code == 0xFFFE else "{d}" if code == 0xFFFD else reverse[code] for code in codes)
        assert text == expected[item["id"]]
        assert len(codes) == len(expected[item["id"]].replace("{2}", "  "))
