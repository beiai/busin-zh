import pytest

from busin.disc import BIN
from busin.paths import BUILD


@pytest.mark.skipif(not BIN.exists() or not (BUILD / "image" / "report.json").exists(),
                    reason="没有原盘或构建报告")
def test_current_image_has_no_undeclared_changes(tmp_path):
    from busin.image_audit import audit_image
    result = audit_image(output_path=tmp_path / "audit.json")
    assert result["ok"]
    assert result["sectors"]["prefix_changed"] == 475
    assert result["sectors"]["moved_tail_changed"] == 116
    assert result["sectors"]["appended"] == 6014
    assert result["unexpected"] == {
        "prefix": [], "moved_tail": [], "append_missing": [], "append_extra": []
    }
