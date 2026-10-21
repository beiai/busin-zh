import json
import struct
from pathlib import Path

from busin.disc import FILE_BASE
from busin.paths import DATA
from busin.patch.ui_names import elf_writes
from busin.runtime_names import (
    NAME_CELLS, inspect_memory, load_names, source_codes, translate_display,
)


def _write_names(blob, addresses, names):
    for address, name in zip(addresses, names):
        codes = source_codes(name)
        packed = codes + [0xFFFF] * (NAME_CELLS - len(codes))
        struct.pack_into(f"<{NAME_CELLS}H", blob, address, *packed)


def test_party_names_match_terms():
    terms = json.loads((DATA / "terms.json").read_text(encoding="utf-8"))
    names = load_names()
    assert [(name.source, name.text) for name in names] == [
        ("グレッグ", "格雷格"),
        ("リカルド", "里卡多"),
        ("サラ", "萨拉"),
    ]
    assert all(terms[name.source]["zh"] == name.text for name in names)


def test_name_mapping_is_exact():
    names = load_names()
    assert translate_display("グレッグ", names) == "格雷格"
    assert translate_display("A", names) == "A"
    assert translate_display("グレッグさん", names) == "グレッグさん"
    assert translate_display("リカルド", names) == "里卡多"


def test_template_writes_keep_the_eight_cell_field():
    names = load_names()
    elf = bytearray(FILE_BASE + max(name.template for name in names) + NAME_CELLS * 2)
    _write_names(elf, [name.template - FILE_BASE for name in names], [name.source for name in names])
    table = {char: 0x4000 + index for index, char in enumerate("格雷格里卡多萨拉")}
    writes = elf_writes(bytes(elf), table)
    for name in names:
        got = struct.unpack_from(f"<{NAME_CELLS}H", writes[name.template - FILE_BASE])
        want = tuple([table[char] for char in name.text] + [0xFFFF] * (NAME_CELLS - len(name.text)))
        assert got == want


def test_memory_report_marks_unmapped_names():
    names = load_names()
    memory = bytearray(max(name.display for name in names) + NAME_CELLS * 2)
    _write_names(memory, [name.display for name in names], [name.source for name in names])
    report = inspect_memory(bytes(memory))
    assert [row["status"] for row in report] == ["source"] * 3
    unknown = source_codes("オルフェ")
    struct.pack_into(
        f"<{NAME_CELLS}H", memory, names[0].display,
        *(unknown + [0xFFFF] * (NAME_CELLS - len(unknown))),
    )
    report = inspect_memory(bytes(memory))
    assert report[0] == {
        "id": "gregg",
        "display": "オルフェ",
        "source": "グレッグ",
        "text": "格雷格",
        "status": "unknown",
    }
