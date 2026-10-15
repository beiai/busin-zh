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
        assert set(item) <= {"zh", "ui"} and item["zh"]
        values.add(item["zh"])
        if "ui" in item:
            values.add(item["ui"])
    assert terms["ドゥーハン"]["zh"] == "杜汉" and terms["アレイド"]["zh"] == "连携"
    assert terms["ヴィガー"]["zh"] == "维加" and terms["ヘルガ"]["zh"] == "赫尔加"
    assert terms["デス"]["zh"] == "德斯" and terms["リゲル"]["zh"] == "莉格尔"
    assert terms["プレイステーション 2"]["zh"] == "PlayStation 2"
    assert terms["剣士"]["zh"] == "剑士" and terms["戦士"]["zh"] == "战士"
    assert terms["人間"]["zh"] == "人类" and terms["ドワーフ"]["zh"] == "矮人"
    assert terms["ノーム"]["zh"] == "侏儒" and terms["侍"]["zh"] == "武士"
    assert terms["魔術師"]["zh"] == "魔法师"
    assert terms["ページ切り替え"]["ui"] == "切换"
    labels = json.loads((DATA / "screen" / "name" / "labels.json").read_text(encoding="utf-8"))
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


def test_ui_font():
    from busin.ui_font import FONTS, self_test
    if any(not path.exists() for _role, path, _version, _sha256 in FONTS):
        pytest.skip("UI font inputs are missing")
    self_test()


@pytest.mark.skipif(not BIN.exists(), reason="没有原盘")
def test_script_index():
    from busin.text import script_self_test
    script_self_test()


@pytest.mark.skipif(not BIN.exists(), reason="没有原盘")
def test_font_patch():
    from busin.image import self_test
    self_test()
