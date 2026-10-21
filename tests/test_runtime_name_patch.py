import struct
from pathlib import Path

from busin.patch.runtime_names_patch import (
    CHECKSUM_HOOK, CHECKSUM_SIZE, CODE_BASE, DATA_BASE, PARTY_HOOK, PARTY_ARRAYS,
    build_machine, elf_writes,
    exact_patterns, memory_checksum, recompute_memory_checksum, replace_exact_names,
)
from busin.patch.asm import A0, A1, RA, SP, V0
from busin.patch.emu import RETURN, execute, junk_regs
from busin.runtime_names import NAME_CELLS, load_names, source_codes


def _table():
    chars = []
    for name in load_names():
        chars.extend(name.text)
    return {char: 0x601 + index for index, char in enumerate(dict.fromkeys(chars))}


def _codes(text):
    return source_codes(text) + [0xFFFF] * (NAME_CELLS - len(source_codes(text)))


def _record(name_text):
    record = bytearray(0x120)
    record[0:8] = bytes([0xFF]) * 8
    record[8:28] = bytes([0x46, 0x46]) * 10
    struct.pack_into("<H", record, 0x20, 1)
    struct.pack_into("<8H", record, 0x22, *_codes(name_text))
    return record


def _block_with_record(name_text, offset=0x100):
    block = bytearray(0x47A30)
    block[offset:offset + 0x120] = _record(name_text)
    return block


def test_exact_name_replacement_only_touches_complete_fields():
    table = _table()
    names = load_names()
    block = _block_with_record("グレッグさん")
    block += _record("オルフェ")
    block += _record("グレッグ")
    before = bytes(block)
    after = replace_exact_names(before, 0, len(before), table)
    assert after[:0x47A30] == before[:0x47A30]
    assert after[-0x120:] != before[-0x120:]
    source, target = exact_patterns(table)
    assert after[-0x120 + 0x22:-0x120 + 0x22 + 16] == target[:16]
    assert [name.text for name in names] == ["格雷格", "里卡多", "萨拉"]


def test_already_translated_and_unknown_names_are_unchanged():
    table = _table()
    source, target = exact_patterns(table)
    block = bytearray(0x47A30)
    block[0x100:0x220] = _record("オルフェ")
    block[0x300:0x420] = _record("グレッグ")
    before = bytes(block)
    once = replace_exact_names(before, 0, len(before), table)
    twice = replace_exact_names(once, 0, len(once), table)
    assert once != before
    assert twice == once
    assert once[0x100:0x220] == before[0x100:0x220]


def test_memory_block_checksum_is_recomputed_without_changing_size():
    table = _table()
    block = _block_with_record("リカルド")
    repaired = replace_exact_names(block, 0, len(block), table)
    saved = recompute_memory_checksum(repaired)
    assert len(saved) == len(block)
    stored = struct.unpack_from("<I", saved, 0)[0]
    check = bytearray(saved)
    struct.pack_into("<I", check, 0, 0)
    assert memory_checksum(check) == stored



def test_generated_scanner_matches_reference():
    table = _table()
    words, labels, data = build_machine(table)
    block = _block_with_record("サラ", offset=0x200)
    block[0x600:0x720] = _record("リカルド")
    expected = replace_exact_names(block, 0, len(block), table)
    memory = bytearray(0x800000)
    buffer = 0x700000
    memory[buffer:buffer + len(block)] = block
    memory[DATA_BASE:DATA_BASE + len(data)] = data
    code = {CODE_BASE + 4 * index: word for index, word in enumerate(words)}
    regs = junk_regs()
    regs[RA], regs[SP] = RETURN, 0x180000
    regs[A0], regs[A1] = buffer, len(block)
    execute(code, memory, regs, labels["sanitize"], lambda pc: pc == RETURN,
            lambda _target, _regs, _link: None, limit=2000000)
    assert bytes(memory[buffer:buffer + len(block)]) == expected



def _machine_code(table):
    words, labels, data = build_machine(table)
    code = {CODE_BASE + 4 * index: word for index, word in enumerate(words)}
    return code, labels, data


def _run(code, memory, regs, entry, stop):
    execute(code, memory, regs, entry, stop,
            lambda _target, _regs, _link: None, limit=2000000)


def test_checksum_wrapper_repairs_then_returns_the_original_sum():
    table = _table()
    block = bytearray(_block_with_record("グレッグ"))
    repaired = replace_exact_names(block, 0, len(block), table)
    expected = memory_checksum(repaired)
    code, labels, data = _machine_code(table)
    elf = Path("build/iso/SLPM_620.98").read_bytes()
    for index in range(0, 0x3C, 4):
        code[CHECKSUM_HOOK + index] = struct.unpack_from("<I", elf, CHECKSUM_HOOK - 0xFF000 + index)[0]
    memory = bytearray(0x800000)
    buffer = 0x700000
    memory[buffer:buffer + len(block)] = block
    memory[DATA_BASE:DATA_BASE + len(data)] = data
    regs = junk_regs()
    regs[RA], regs[SP] = RETURN, 0x180000
    regs[A0], regs[A1] = buffer, CHECKSUM_SIZE
    _run(code, memory, regs, labels["checksum_wrapper"], lambda pc: pc == RETURN)
    assert regs[V0] == expected
    assert bytes(memory[buffer:buffer + len(block)]) == repaired


def test_party_wrapper_repairs_both_arrays_and_restores_the_argument():
    table = _table()
    code, labels, data = _machine_code(table)
    memory = bytearray(0x800000)
    memory[DATA_BASE:DATA_BASE + len(data)] = data
    for index, (base, _count) in enumerate(PARTY_ARRAYS):
        name = load_names()[index].source
        record = _record(name)
        memory[base + 0x40:base + 0x160] = record
    regs = junk_regs()
    regs[RA], regs[SP] = RETURN, 0x180000
    regs[A0] = 0x12
    _run(code, memory, regs, labels["party_wrapper"], lambda pc: pc == PARTY_HOOK + 8)
    assert regs[A0] == 0x12000000
    source, target = exact_patterns(table)
    assert memory[0x4836E0 + 0x62:0x4836E0 + 0x72] == target[:16]
    assert memory[0x4847C0 + 0x62:0x4847C0 + 0x72] == target[16:32]


def test_machine_fits_and_has_two_entry_holes():
    words, labels, data = build_machine(_table())
    assert CODE_BASE + len(words) * 4 <= DATA_BASE
    assert labels["checksum_wrapper"] == CODE_BASE
    assert labels["party_wrapper"] > labels["checksum_wrapper"]
    assert len(data) == 3 * NAME_CELLS * 2 * 2
    assert labels["sanitize"] < DATA_BASE


def test_hook_source_words_and_writes_do_not_overlap():
    elf_path = Path("build/iso/SLPM_620.98")
    if not elf_path.exists():
        return
    elf = elf_path.read_bytes()
    writes = elf_writes(elf, _table())
    assert struct.unpack_from("<I", elf, CHECKSUM_HOOK - 0xFF000)[0] == 0x1080000C
    assert struct.unpack_from("<I", elf, PARTY_HOOK - 0xFF000)[0] == 0x27BDFFD0
    assert CHECKSUM_HOOK - 0xFF000 in writes
    assert PARTY_HOOK - 0xFF000 in writes
    spans = sorted((off, off + len(data)) for off, data in writes.items())
    assert all(end <= next_start for (_start, end), (next_start, _end) in zip(spans, spans[1:]))
