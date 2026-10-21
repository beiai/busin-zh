"""Runtime replacement for the three fixed character names.

This patch intentionally covers normal memory-card save/load data and party
rebuilds only. PCSX2 savestates are outside this path and are not repaired here.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass

from busin.disc import FILE_BASE
from busin.patch.asm import (
    A0, A1, A2, A3, Asm, GP, RA, SP, T0, T1, T2, T3, T4, T5, T6, T7, V0, V1, ZR,
)
from busin.runtime_names import NAME_CELLS, load_names, source_codes

# The first twelve words at 0x4409F0 are the memory-card handler table.  The
# zero-filled gap before the state words is unused by that table and is a safe
# executable RWX area for this small trampoline.
CODE_BASE = 0x440B00
DATA_BASE = 0x440D00
CODE_LIMIT = DATA_BASE
DATA_LIMIT = 0x440DE8
CHECKSUM_HOOK = 0x10B948
PARTY_HOOK = 0x1A8310
CHECKSUM_SIZE = 0x47A30
PARTY_ARRAYS = ((0x4836E0, 15), (0x4847C0, 14))
RECORD_SIZE = 0x120
CHECKSUM_SOURCE = 0x1080000C
CHECKSUM_SOURCE_DELAY = 0x0000382D
PARTY_SOURCE = 0x27BDFFD0
PARTY_SOURCE_DELAY = 0x00042600

assert (CODE_BASE, DATA_BASE, CODE_LIMIT, DATA_LIMIT) == (
    0x440B00, 0x440D00, 0x440D00, 0x440DE8)
assert all(size > 0 for _base, size in PARTY_ARRAYS)


@dataclass(frozen=True)
class NameTable:
    source: bytes
    target: bytes


def _name_table(table: dict[str, int]) -> NameTable:
    source_words: list[int] = []
    target_words: list[int] = []
    for name in load_names():
        source = source_codes(name.source)
        if len(source) > NAME_CELLS:
            raise ValueError(f"{name.ident} source exceeds {NAME_CELLS} cells")
        try:
            target = [table[char] for char in name.text]
        except KeyError as exc:
            raise ValueError(f"{name.ident} translation {name.text!r} has no UI glyph") from exc
        if len(target) > NAME_CELLS:
            raise ValueError(f"{name.ident} translation exceeds {NAME_CELLS} cells")
        source_words.extend(source + [0xFFFF] * (NAME_CELLS - len(source)))
        target_words.extend(target + [0xFFFF] * (NAME_CELLS - len(target)))
    return NameTable(
        struct.pack(f"<{len(source_words)}H", *source_words),
        struct.pack(f"<{len(target_words)}H", *target_words),
    )


def _li(a: Asm, rt: int, value: int) -> None:
    low = value & 0xFFFF
    high = (value >> 16) + (low >> 15)
    a.lui(rt, high)
    a.addiu(rt, rt, low)


def _build_sanitize(a: Asm, names: NameTable) -> None:
    # Inputs are a0=start and a1=byte size.  The routine is a leaf and only
    # uses caller-saved registers, so the trampolines can restore the original
    # arguments around it.
    a.label("sanitize")
    a.addiu(T0, A0, 0x22)
    a.addu(T1, A0, A1)
    a.addiu(T1, T1, -(NAME_CELLS * 2))
    a.label("outer")
    a.sltu(V0, T1, T0)
    a.bne(V0, ZR, "done")
    a.nop()
    # Require the known player-record subrecord signature before replacing a
    # name. This keeps exact text matches elsewhere in the save block alone.
    a.lh(V0, -2, T0)
    a.addiu(V1, ZR, 1)
    a.bne(V0, V1, "next")
    a.nop()
    a.lh(V0, -0x22, T0)
    a.addiu(V1, ZR, -1)
    a.bne(V0, V1, "next")
    a.nop()
    a.lh(V0, -0x1A, T0)
    _li(a, V1, 0x4646)
    a.bne(V0, V1, "next")
    a.nop()
    a.lh(V0, -4, T0)
    a.bne(V0, ZR, "next")
    a.nop()
    a.lh(V0, -6, T0)
    a.bne(V0, ZR, "next")
    a.nop()
    a.addiu(A2, ZR, 0)
    a.label("pattern")
    a.sltiu(V0, A2, 3)
    a.beq(V0, ZR, "next")
    a.nop()
    a.sll(A3, A2, 4)
    _li(a, V1, DATA_BASE)
    a.addu(A3, A3, V1)
    a.addiu(T3, ZR, 0)
    a.label("compare")
    a.sltiu(V0, T3, NAME_CELLS)
    a.beq(V0, ZR, "match")
    a.nop()
    a.sll(V0, T3, 1)
    a.addu(V1, A3, V0)
    a.lh(T4, 0, V1)
    a.addu(V1, T0, V0)
    a.lh(T5, 0, V1)
    a.bne(T4, T5, "next_pattern")
    a.addiu(T3, T3, 1)
    a.beq(ZR, ZR, "compare")
    a.nop()
    a.label("match")
    a.sll(V0, A2, 4)
    _li(a, V1, DATA_BASE + len(names.source))
    a.addu(A3, V0, V1)
    a.addiu(T3, ZR, 0)
    a.label("copy")
    a.sltiu(V0, T3, NAME_CELLS)
    a.beq(V0, ZR, "next")
    a.nop()
    a.sll(V0, T3, 1)
    a.addu(V1, A3, V0)
    a.lh(T4, 0, V1)
    a.addu(V1, T0, V0)
    a.sh(T4, 0, V1)
    a.addiu(T3, T3, 1)
    a.beq(ZR, ZR, "copy")
    a.nop()
    a.label("next_pattern")
    a.addiu(A2, A2, 1)
    a.beq(ZR, ZR, "pattern")
    a.nop()
    a.label("next")
    a.addiu(T0, T0, 2)
    a.beq(ZR, ZR, "outer")
    a.nop()
    a.label("done")
    a.jr(RA)
    a.nop()


def _build_checksum_wrapper(a: Asm) -> None:
    a.label("checksum_wrapper")
    a.addiu(SP, SP, -24)
    a.sw(A0, 0, SP)
    a.sw(A1, 4, SP)
    a.sd(RA, 8, SP)
    a.beq(A0, ZR, "checksum_zero")
    a.nop()
    _li(a, V0, CHECKSUM_SIZE)
    a.bne(A1, V0, "checksum_restore")
    a.nop()
    a.jal(DATA_BASE)  # patched below to the sanitizer entry
    a.nop()
    a.label("checksum_restore")
    a.lw(A0, 0, SP)
    a.lw(A1, 4, SP)
    a.addiu(A3, ZR, 0)
    a.ld(RA, 8, SP)
    a.addiu(SP, SP, 24)
    a.j(CHECKSUM_HOOK + 8)
    a.nop()
    a.label("checksum_zero")
    a.addiu(V0, ZR, 0)
    a.ld(RA, 8, SP)
    a.addiu(SP, SP, 24)
    a.jr(RA)
    a.nop()


def _build_party_wrapper(a: Asm) -> None:
    a.label("party_wrapper")
    a.addiu(SP, SP, -24)
    a.sw(A0, 0, SP)
    a.sd(RA, 8, SP)
    for base, count in PARTY_ARRAYS:
        _li(a, A0, base)
        _li(a, A1, count * RECORD_SIZE)
        a.jal(DATA_BASE)  # patched below to the sanitizer entry
        a.nop()
    a.lw(A0, 0, SP)
    a.ld(RA, 8, SP)
    a.addiu(SP, SP, 24)
    a.addiu(SP, SP, -0x30)
    a.sll(A0, A0, 24)
    a.j(PARTY_HOOK + 8)
    a.nop()


def exact_patterns(table: dict[str, int]) -> tuple[bytes, bytes]:
    names = _name_table(table)
    return names.source, names.target


def replace_exact_names(data: bytes, start: int, size: int, table: dict[str, int],
                         require_signature: bool = True) -> bytes:
    """Reference implementation used by tests for the runtime scanner."""
    source, target = exact_patterns(table)
    out = bytearray(data)
    end = start + size
    for at in range(start + 0x22, end - NAME_CELLS * 2 + 1, 2):
        if require_signature:
            if struct.unpack_from("<H", out, at - 2)[0] != 1:
                continue
            if struct.unpack_from("<H", out, at - 0x22)[0] != 0xFFFF:
                continue
            if struct.unpack_from("<H", out, at - 0x1A)[0] != 0x4646:
                continue
            if struct.unpack_from("<H", out, at - 4)[0] != 0:
                continue
            if struct.unpack_from("<H", out, at - 6)[0] != 0:
                continue
        found = None
        for index in range(3):
            offset = index * NAME_CELLS * 2
            if out[at:at + NAME_CELLS * 2] == source[offset:offset + NAME_CELLS * 2]:
                found = offset
                break
        if found is not None:
            out[at:at + NAME_CELLS * 2] = target[found:found + NAME_CELLS * 2]
    return bytes(out)


def memory_checksum(block: bytes) -> int:
    if len(block) % 4:
        raise ValueError("memory-card block size is not word aligned")
    return sum(struct.unpack_from("<I", block, at)[0] for at in range(0, len(block), 4)) & 0xFFFFFFFF


def recompute_memory_checksum(block: bytes) -> bytes:
    out = bytearray(block)
    struct.pack_into("<I", out, 0, 0)
    struct.pack_into("<I", out, 0, memory_checksum(out))
    return bytes(out)


def build_machine(table: dict[str, int]) -> tuple[list[int], dict[str, int], bytes]:
    names = _name_table(table)
    a = Asm()
    _build_checksum_wrapper(a)
    _build_party_wrapper(a)
    _build_sanitize(a, names)
    words = a.done()
    labels = {name: CODE_BASE + 4 * index for name, index in a.labels.items()}
    code = struct.pack(f"<{len(words)}I", *words)
    if CODE_BASE + len(code) > CODE_LIMIT:
        raise ValueError(f"runtime name patch needs {len(code)} bytes, limit {CODE_LIMIT - CODE_BASE}")
    data = names.source + names.target
    if DATA_BASE + len(data) > DATA_LIMIT:
        raise ValueError("runtime name tables do not fit before the memory-card state words")
    # The wrappers call the sanitizer by absolute address.  Build after labels
    # are known, then replace those temporary calls with the real entry.
    calls = []
    for index, word in enumerate(words):
        if word == 0x0C000000 | (DATA_BASE >> 2):
            calls.append(index)
    if len(calls) != 3:
        raise ValueError(f"expected three sanitizer calls, found {len(calls)}")
    encoded = list(words)
    call_word = 0x0C000000 | (labels["sanitize"] >> 2)
    for index in calls:
        encoded[index] = call_word
    return encoded, labels, data


def elf_writes(elf: bytes, table: dict[str, int]) -> dict[int, bytes]:
    words, labels, data = build_machine(table)
    blob = struct.pack(f"<{len(words)}I", *words) + data
    writes = {CODE_BASE - FILE_BASE: blob}
    hooks = (
        (CHECKSUM_HOOK, CHECKSUM_SOURCE, 0x08000000 | (labels["checksum_wrapper"] >> 2)),
        (CHECKSUM_HOOK + 4, CHECKSUM_SOURCE_DELAY, 0),
        (PARTY_HOOK, PARTY_SOURCE, 0x08000000 | (labels["party_wrapper"] >> 2)),
        (PARTY_HOOK + 4, PARTY_SOURCE_DELAY, 0),
    )
    for va, source, patched in hooks:
        off = va - FILE_BASE
        have = struct.unpack_from("<I", elf, off)[0]
        if have != source:
            raise ValueError(f"runtime-name hook {va:#x} is {have:#x}, expected {source:#x}")
        writes[off] = struct.pack("<I", patched)
    spans = sorted((off, off + len(blob)) for off, blob in writes.items())
    if any(end > nxt for (_start, end), (nxt, _end) in zip(spans, spans[1:])):
        raise ValueError("runtime-name ELF writes overlap")
    return writes


def source_hook_words() -> tuple[tuple[int, int, int], ...]:
    return (
        (CHECKSUM_HOOK, CHECKSUM_SOURCE, CHECKSUM_SOURCE_DELAY),
        (PARTY_HOOK, PARTY_SOURCE, PARTY_SOURCE_DELAY),
    )


def self_test(table: dict[str, int]) -> None:
    words, labels, data = build_machine(table)
    assert CODE_BASE <= labels["sanitize"] < CODE_BASE + 4 * len(words)
    assert CODE_BASE + 4 * len(words) <= CODE_LIMIT
    assert len(data) == 3 * NAME_CELLS * 2 * 2
    assert words.count(0x0C000000 | (labels["sanitize"] >> 2)) == 3
