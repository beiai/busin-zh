import pytest

from busin.disc import BIN
from busin.paths import BUILD


@pytest.mark.skipif(not BIN.exists(), reason="没有原盘")
def test_plan_reports_translation_and_font_budgets():
    from busin.build_plan import build_disc_plan
    report = build_disc_plan()
    assert report["messages"] == {"total": 25522, "translated": 7586, "pending": 17936}
    assert len(report["chunks"]) == 38
    assert report["font"]["reserved_slots"] == [16, 17]
    packed = next(item for item in report["chunks"] if item["chunk"] == 282)
    assert packed["sectors"] == {"original": 10, "planned": 8, "freed": 2}
