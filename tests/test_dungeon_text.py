import json
import struct

import pytest

from busin import dungeon_text as dungeon
from busin.disc import BIN

pytestmark = pytest.mark.skipif(not BIN.exists(), reason="没有原盘")


@pytest.fixture
def resource():
    rows, used, raw = dungeon.read_source()
    doc = json.loads(dungeon.PATH.read_text(encoding="utf-8"))
    return rows, used, raw, doc


def test_source_roundtrip_and_collection(resource, tmp_path):
    from busin.disasm import load_elf
    from busin.ui_text import collect, dump_manuscript, dump_ui

    rows, used, raw, doc = resource
    assert len(rows) == 213 and used == 6784
    assert sum(codes == [0xFFFE] for _, codes, _ in rows) == 31
    visible = [(ident, codes) for ident, codes, _ in rows if codes != [0xFFFE]]
    assert len(doc) == 182 and all(row["source"] for row in doc)
    assert doc[0]["source"] == "特に変わったところはない。"
    assert doc[9]["source"] == "ハコが置いてある。/中には何もない。"
    assert [row["id"] for row in doc] == [ident for ident, _ in visible]
    untranslated = [dict(row, text=None) for row in doc]
    assert dungeon.encode(rows, dungeon.translations(rows, untranslated), {}) == (raw, used)
    spans, info = dungeon.pack_patches({}, untranslated)
    assert spans == []
    assert info == {"total": 182, "translated": 0, "pending": 182,
                    "original_bytes": 6784, "bytes": 6784, "capacity": 8192, "free_bytes": 1408}
    collected = next(r for name, _, r in collect(load_elf()) if name == "dungeon")
    assert collected == visible
    dump_ui(tmp_path / "ui")
    dump_manuscript(tmp_path / "src")
    assert "ハコが置いてある。/中には何もない。" in (tmp_path / "ui/dungeon.txt").read_text(encoding="utf-8")
    manuscript = (tmp_path / "src/dungeon.txt").read_text(encoding="utf-8")
    assert "000  特に変わったところはない。  width=13x1" in manuscript
    assert "{d}" not in manuscript
    for ident, codes, _ in rows:
        if codes == [0xFFFE]:
            assert not any(line.startswith(f"{ident}  ") for line in manuscript.splitlines())


def test_missing_extra_or_reordered_entries_fail(resource):
    rows, _, _, doc = resource
    for invalid in (doc[:-1], [*doc, {"id": "142", "source": "", "text": None}],
                    [doc[1], doc[0], *doc[2:]]):
        with pytest.raises(ValueError, match="dungeon"):
            dungeon.translations(rows, invalid)


def test_translation_uses_shared_glyphs_and_preserves_actions(resource, monkeypatch):
    from busin.disasm import load_elf
    from busin.patch.ledger import PatchLedger
    from busin.ui_font import ui_font_pages
    from busin.ui_text import ui_patches_with_table

    rows, _, raw, doc = resource
    doc = [dict(row, text=None) for row in doc]
    doc[9]["text"] = "箱子。/里面没有东西。"
    read_text = type(dungeon.PATH).read_text

    def read_doc(path, *args, **kwargs):
        return json.dumps(doc, ensure_ascii=False) if path == dungeon.PATH else read_text(path, *args, **kwargs)

    monkeypatch.setattr(type(dungeon.PATH), "read_text", read_doc)
    _, extra, info, table = ui_patches_with_table(load_elf())
    assert info["dungeon"]["translated"] == 1
    assert set("箱子。里面没有东西").issubset(table)
    assert all(extra[c] == table[c] for c in "箱子。里面没有东西")
    assert ui_font_pages(extra)
    spans, stats = dungeon.pack_patches(table)
    assert stats == info["dungeon"]
    assert spans[0][0] == dungeon.START * 2048
    changed = spans[0][1]
    assert changed != raw
    # parse() intentionally accepts only original glyphs; inspect the encoded BE words here.
    words = struct.unpack(f">{len(changed) // 2}H", changed)
    pos = 1
    for index, (_, original, action) in enumerate(rows):
        end = words.index(0xFFFF, pos)
        body = list(words[pos:end])
        assert words[end + 1] == action
        if index == 9:
            assert body == [table[c] if c != "/" else 0xFFFD for c in doc[9]["text"]] + [0xFFFE]
        else:
            assert body == original
        pos = end + 2
    assert not any(words[pos:])
    ledger = PatchLedger()
    ledger.add_pack("interface.dungeon", *spans[0])
    assert ledger.spans("pack") == spans
    assert ledger.report()["byte_patches"]["pack"]["interface.dungeon"]["bytes"] == 8192
    from busin.disc import PACK_LBA, read_user
    from busin.image import splice_pack

    sectors = {}
    with BIN.open("rb") as handle:
        splice_pack(handle, sectors, ledger.spans("pack"))
        start = PACK_LBA + dungeon.START
        assert set(sectors) == set(range(start, start + dungeon.SECTORS))
        assert b"".join(sectors[lba] for lba in sorted(sectors)) == changed
        assert read_user(handle, start, dungeon.SECTORS) == raw


@pytest.mark.parametrize("field,value", [("id", "999"), ("source", "wrong"),
                                        ("text", ""), ("text", 123), ("text", "{d}"),
                                        ("text", "a\nb")])
def test_bad_manuscript_fails(resource, field, value):
    rows, _, _, doc = resource
    doc[0][field] = value
    with pytest.raises(ValueError, match="dungeon 000"):
        dungeon.translations(rows, doc)


@pytest.mark.parametrize("kind", ["count", "end", "terminator", "control", "padding", "size"])
def test_malformed_resource_fails(resource, kind):
    _, _, original, _ = resource
    raw = bytearray(original)
    if kind == "count":
        raw[1] = 0
    elif kind == "end":
        struct.pack_into(">H", raw, 0x1C, 0)
    elif kind == "terminator":
        raw[0x1E:] = b"\0" * (len(raw) - 0x1E)
    elif kind == "control":
        struct.pack_into(">H", raw, 2, 0xFFFC)
    elif kind == "padding":
        raw[-1] = 1
    else:
        raw.pop()
    with pytest.raises(ValueError, match="dungeon"):
        dungeon.parse(raw)


def test_layout_and_capacity_failures(resource, monkeypatch):
    rows, _, _, doc = resource
    table = {"字": 0x401}
    doc[0]["text"] = "字" * 14
    with pytest.raises(ValueError, match=r"dungeon 000: width 14, limit 13, exceeds by 1 cells \(16 pixels\)"):
        dungeon.encode(rows, dungeon.translations(rows, doc), table)
    doc[0]["text"] = "字/字"
    with pytest.raises(ValueError, match="dungeon 000: 2 lines, limit 1"):
        dungeon.encode(rows, dungeon.translations(rows, doc), table)
    doc[0]["text"] = "字"
    with pytest.raises(ValueError, match="dungeon 000: no cell"):
        dungeon.encode(rows, dungeon.translations(rows, doc), {})
    # Use the real source with a smaller budget to exercise the overflow guard.
    monkeypatch.setattr(dungeon, "CAPACITY", resource[1] - 1)
    with pytest.raises(ValueError, match="chunk 264 uses .* exceeds by .* bytes"):
        dungeon.encode(rows, [None] * len(rows), table)


def test_disc_position_and_loader_guards(resource, monkeypatch):
    from busin.disasm import load_elf

    elf = bytearray(load_elf())
    struct.pack_into("<I", elf, 0x286418 - 0xFF000, 0)
    with pytest.raises(ValueError, match="instruction changed"):
        dungeon.read_source(elf)
    monkeypatch.setattr(dungeon, "chunk_row", lambda *args: (dungeon.START + 1, dungeon.SECTORS))
    with pytest.raises(ValueError, match="disc position changed"):
        dungeon.read_source()


def test_build_report_and_translated_image(resource):
    import hashlib

    from busin.disc import read_pack
    from busin.paths import BUILD

    report_path = BUILD / "image/report.json"
    if not report_path.exists():
        pytest.skip("没有构建报告")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["git"] and report["image_sha256"]
    assert report["inputs"]["data/interface/dungeon.json"] == hashlib.sha256(dungeon.PATH.read_bytes()).hexdigest()
    from busin.disasm import load_elf
    from busin.ui_text import ui_patches_with_table

    _, _, _, table = ui_patches_with_table(load_elf())
    spans, info = dungeon.pack_patches(table)
    assert report["ui_text"]["dungeon"] == info
    assert info["total"] == info["translated"] == 182 and info["pending"] == 0
    with (BUILD / "image/busin-zh.bin").open("rb") as handle:
        assert read_pack(handle, dungeon.START * 2048, dungeon.CAPACITY) == spans[0][1]
