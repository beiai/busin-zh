import struct

import pytest

from busin.disasm import load_elf
from busin.disc import BIN, FILE_BASE
from busin.patch.asm import A0, GP, RA, SP
from busin.patch.emu import RETURN, execute, junk_regs
from busin.patch.runtime_names_patch import (
    CODE_BASE, DATA_BASE, DATA_LIMIT, HEAP_BASE, HOOK, POOL_SIZE, SEGMENT_OFFSET,
    build_machine, elf_writes, exact_patterns,
)
from busin.runtime_names import load_names, source_codes


@pytest.fixture(scope="module")
def source_elf():
    if not BIN.exists():
        pytest.skip("没有原盘")
    return load_elf()


@pytest.fixture(scope="module")
def table(source_elf):
    from busin.ui_text import ui_patches_with_table
    return ui_patches_with_table(source_elf)[3]


def _code(elf):
    return {va: struct.unpack_from("<I", elf, va - FILE_BASE)[0]
            for va in range(0x1A3778, 0x1A3B14, 4)}


def _run(elf, table, field, patched, source=0x700260, dest=0x4847C0):
    # Real template layout, with only the eight-cell name varied.
    record_at = load_names()[0].template - FILE_BASE - 2
    record = bytearray(elf[record_at:record_at + 0x120])
    record[2:18] = field
    memory = bytearray(0x800000)
    memory[source:source + len(record)] = record
    struct.pack_into("<I", memory, 0x5A9AF0 - 26124, dest)
    regs = junk_regs()
    regs[A0], regs[GP], regs[RA], regs[SP] = source, 0x5A9AF0, RETURN, 0x180000
    # Seed upper 32 bits to catch accidental word-sized save/restore.
    for i in range(16, 24):
        regs[i] |= 0x1234567800000000
    code = _code(elf)
    if patched:
        words, _labels, data = build_machine(table)
        code.update({CODE_BASE + 4 * i: word for i, word in enumerate(words)})
        code[HOOK], code[HOOK + 4] = 0x08000000 | (CODE_BASE >> 2), 0
        memory[DATA_BASE:DATA_BASE + len(data)] = data
    before = bytes(memory)

    def writable(at, size):
        return (dest <= at and at + size <= dest + 0x120
                or regs[SP] <= at and at + size <= 0x180000)

    execute(code, memory, regs, 0x1A3778, lambda pc: pc == RETURN,
            lambda *_: None, writable=writable)
    if source != dest:
        assert memory[source:source + 0x120] == before[source:source + 0x120]
    return bytes(memory[dest:dest + 0x120]), regs


@pytest.mark.parametrize("index", range(3))
@pytest.mark.parametrize("dest", [0x4836E0, 0x4847C0, 0x485D10, 0x700260])
def test_original_copy_differs_only_in_name(source_elf, table, index, dest):
    source, target = exact_patterns(table)
    field = source[index * 16:(index + 1) * 16]
    original, original_regs = _run(source_elf, table, field, False, dest=dest)
    translated, patched_regs = _run(source_elf, table, field, True, dest=dest)
    expected = original[:2] + target[index * 16:(index + 1) * 16] + original[18:]
    assert translated == expected
    assert patched_regs == original_regs


@pytest.mark.parametrize("text", ["オルフェ", "グレッグさん", "", "サラサラ"])
def test_custom_names_unchanged(source_elf, table, text):
    words = source_codes(text)
    field = struct.pack("<8H", *(words + [0xFFFF] * (8 - len(words))))
    assert _run(source_elf, table, field, True) == _run(source_elf, table, field, False)


def test_complete_padding_must_match_and_translation_is_idempotent(source_elf, table):
    source, target = exact_patterns(table)
    for index in range(3):
        field = target[index * 16:(index + 1) * 16]
        assert _run(source_elf, table, field, True) == _run(source_elf, table, field, False)
        field = source[index * 16:(index + 1) * 16 - 2] + b"\x00\x00"
        assert _run(source_elf, table, field, True) == _run(source_elf, table, field, False)


def test_own_loaded_segment_survives_bss_clear_and_reserves_heap(source_elf, table):
    elf = bytearray(source_elf)
    for offset, data in elf_writes(source_elf, table).items():
        elf[offset:offset + len(data)] = data
    count = struct.unpack_from("<H", elf, 0x2C)[0]
    assert count == 2
    memory = bytearray(0x800000)
    spans = []
    for i in range(count):
        kind, offset, va, pa, size, mem_size, flags, align = struct.unpack_from("<8I", elf, 0x34 + i * 32)
        assert kind == 1 and va == pa and offset % align == va % align
        assert flags & 1 and size <= mem_size
        assert all(va + mem_size <= start or end <= va for start, end in spans)
        spans.append((va, va + mem_size))
        memory[va:va + size] = elf[offset:offset + size]
    # Execute the startup clear's exact inclusive 16-byte bound.
    for at in range(0x5A4E00, 0x62154C + 16, 16):
        memory[at:at + 16] = bytes(16)
    words, _, data = build_machine(table)
    assert memory[CODE_BASE:CODE_BASE + len(words) * 4] == struct.pack(f"<{len(words)}I", *words)
    assert memory[DATA_BASE:DATA_LIMIT] == data
    assert struct.unpack_from("<I", elf, 0x100074 - FILE_BASE)[0] == 0x24842000
    assert HEAP_BASE >= max(end for _, end in spans)
    assert struct.unpack_from("<I", elf, 0x1000F4 - FILE_BASE)[0] == 0x3C040197
    # Keep more than the heap shift as headroom for libc allocation metadata.
    assert 0x1980000 - POOL_SIZE > HEAP_BASE - 0x62154C + 0x1000
    assert elf[SEGMENT_OFFSET:SEGMENT_OFFSET + 4] == memory[CODE_BASE:CODE_BASE + 4]
    # These earlier patch homes and hooks must be restored byte for byte.
    for start, end in ((0x3F6BF0, 0x3F6ED4), (0x440B00, 0x440F00),
                       (0x10B948, 0x10B984), (0x1A4858, 0x1A48FC), (0x1A8310, 0x1A8318)):
        assert elf[start - FILE_BASE:end - FILE_BASE] == source_elf[start - FILE_BASE:end - FILE_BASE]


def test_rejects_changed_source_or_occupied_padding(source_elf, table):
    for at in (SEGMENT_OFFSET, HOOK - FILE_BASE, 0x1074, 0x44, 0x2C):
        changed = bytearray(source_elf)
        changed[at] ^= 1
        with pytest.raises(ValueError):
            elf_writes(changed, table)


def test_report_recognizes_actual_translated_codes(source_elf, table):
    from busin.runtime_names import inspect_memory
    memory = bytearray(0x800000)
    _, target = exact_patterns(table)
    for i, name in enumerate(load_names()):
        memory[name.display:name.display + 16] = target[i * 16:(i + 1) * 16]
    assert [row["status"] for row in inspect_memory(memory)] == ["translated"] * 3


def test_disassembler_reads_new_segment_by_its_header(source_elf, table, monkeypatch):
    from busin import disasm
    elf = bytearray(source_elf)
    for offset, data in elf_writes(source_elf, table).items():
        elf[offset:offset + len(data)] = data
    monkeypatch.setattr(disasm, "load_elf", lambda _image: bytes(elf))
    text = disasm.disassemble(CODE_BASE, CODE_BASE + 4)
    assert f"{CODE_BASE:08x}: 24090000" in text
    assert "addiu" in text
