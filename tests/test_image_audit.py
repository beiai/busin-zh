import pytest

from busin.disc import BIN
from busin.paths import BUILD


@pytest.mark.skipif(not BIN.exists() or not (BUILD / "image" / "report.json").exists(),
                    reason="没有原盘或构建报告")
def test_current_image_has_no_undeclared_changes(tmp_path):
    from busin.image_audit import audit_image
    result = audit_image(output_path=tmp_path / "audit.json")
    assert result["ok"]
    assert result["sectors"]["prefix_changed"] == 543
    assert result["sectors"]["moved_tail_changed"] == 116
    assert result["sectors"]["appended"] == 6014
    assert result["classified"]["moved_tail"]["elf.picture_rects"] == 1
    assert result["classified"]["prefix"]["picture.part"] == 177
    assert result["unexpected"] == {
        "prefix": [], "moved_tail": [], "append_missing": [], "append_extra": []
    }


@pytest.mark.skipif(not BIN.exists() or not (BUILD / "image" / "report.json").exists(),
                    reason="没有原盘或构建报告")
def test_current_image_uses_translated_switch_rect():
    import json
    import struct

    from busin.disasm import load_elf
    from busin.disc import FILE_BASE
    from busin.patch.picture_rects import MENU_TABLE, SWITCH_INDEX

    report = json.loads((BUILD / "image/report.json").read_text(encoding="utf-8"))
    assert report["change_plan"]["byte_patches"]["elf"]["elf.picture_rects"] == {
        "patches": 1, "bytes": 2,
    }
    elf = load_elf(BUILD / "image/busin-zh.bin")
    offset = MENU_TABLE - FILE_BASE + SWITCH_INDEX * 12
    assert struct.unpack_from("<6h", elf, offset) == (96, 184, 48, 16, 48, 16)
