"""Translate fixed names in the shared character-record copy, after save validation."""
import struct

from busin.disc import FILE_BASE
from busin.patch.asm import Asm, T0, T1, T5, T6, T7, V0, V1, ZR
from busin.runtime_names import NAME_CELLS, load_names, source_codes

# A second PT_LOAD uses ELF header padding, outside every original section.
# Startup clears through 0x621550; the heap must start after this segment.
SEGMENT_OFFSET = 0x100
CODE_BASE = 0x621600
DATA_BASE = 0x621800
DATA_LIMIT = 0x621860
HEAP_BASE = 0x622000
# The fixed pool originally ends just before the font token map. Moving the
# heap without reducing this pool would overwrite that map at runtime.
POOL_SIZE = 0x1970000
HOOK = 0x1A380C
RESUME = 0x1A381C
REPLAY = (0x95420012, 0x00A0702D, 0x0160782D, 0x00C0682D)


def exact_patterns(table):
    source, target = bytearray(), bytearray()
    for name in load_names():
        if any(char not in table for char in name.text):
            raise ValueError(f"{name.ident} translation has no UI glyph")
        for out, codes in ((source, source_codes(name.source)),
                           (target, [table[char] for char in name.text])):
            if len(codes) > NAME_CELLS:
                raise ValueError(f"{name.ident} exceeds {NAME_CELLS} cells")
            out.extend(struct.pack("<8H", *(codes + [0xFFFF] * (NAME_CELLS - len(codes)))))
    return bytes(source), bytes(target)


def build_machine(table):
    source, target = exact_patterns(table)
    if len(source) != 48 or len(target) != 48:
        raise ValueError("runtime names require exactly three eight-cell fields")
    data = b"".join(source[i:i + 16] + target[i:i + 16] for i in range(0, 48, 16))
    a = Asm()
    # The original loop still copies all eight cells before this trampoline.
    # Only registers overwritten by the original continuation are used here.
    a.label("entry")
    a.addiu(T1, ZR, 0)
    a.label("pattern")
    a.lui(T6, DATA_BASE >> 16)
    a.addiu(T6, T6, DATA_BASE & 0xFFFF)
    a.addu(T6, T6, T1)
    a.addiu(T7, T0, 2)
    a.addiu(T5, ZR, NAME_CELLS)
    a.label("compare")
    a.lh(V0, 0, T6)
    a.lh(V1, 0, T7)
    a.bne(V0, V1, "next")
    a.addiu(T6, T6, 2)
    a.addiu(T7, T7, 2)
    a.addiu(T5, T5, -1)
    a.bne(T5, ZR, "compare")
    a.nop()
    a.addiu(T7, T0, 2)
    a.addiu(T5, ZR, NAME_CELLS)
    a.label("copy")
    a.lh(V0, 0, T6)
    a.sh(V0, 0, T7)
    a.addiu(T6, T6, 2)
    a.addiu(T7, T7, 2)
    a.addiu(T5, T5, -1)
    a.bne(T5, ZR, "copy")
    a.nop()
    a.beq(ZR, ZR, "restore")
    a.nop()
    a.label("next")
    a.addiu(T1, T1, 32)
    a.sltiu(V0, T1, len(data))
    a.bne(V0, ZR, "pattern")
    a.nop()
    a.label("restore")
    a.addiu(V1, T0, 0x12)
    a.addiu(T1, ZR, -1)
    for word in REPLAY:
        a.emit(word)
    a.j(RESUME)
    a.nop()
    words = a.done()
    if CODE_BASE + len(words) * 4 > DATA_BASE or DATA_BASE + len(data) != DATA_LIMIT:
        raise ValueError("runtime name segment layout changed")
    return words, {name: CODE_BASE + i * 4 for name, i in a.labels.items()}, data


def elf_writes(elf, table):
    guards = {
        0x1A3804: 0x0521FFFA, 0x1A3808: 0,
        **{HOOK + i * 4: word for i, word in enumerate(REPLAY)},
        0x10000C: 0x3C030062, 0x100014: 0x2463154C,
        0x10006C: 0x3C040062, 0x100074: 0x2484154C,
        0x1000F4: 0x3C040198, 0x1000F8: 0x0C101D54,
    }
    for va, expected in guards.items():
        if struct.unpack_from("<I", elf, va - FILE_BASE)[0] != expected:
            raise ValueError(f"runtime-name source instruction changed at {va:#x}")
    if struct.unpack_from("<I", elf, 0x1C)[0] != 0x34:
        raise ValueError("unexpected ELF program-header offset")
    if struct.unpack_from("<HH", elf, 0x2A) != (32, 1):
        raise ValueError("expected one 32-byte ELF program header")
    if struct.unpack_from("<8I", elf, 0x34) != (1, 0x1000, 0x100000, 0x100000, 0x4A4D81, 0x52154C, 7, 0x1000):
        raise ValueError("original ELF load segment changed")
    end = SEGMENT_OFFSET + DATA_LIMIT - CODE_BASE
    if any(elf[0x54:end]):
        raise ValueError("ELF header padding for runtime names is not empty")
    words, labels, data = build_machine(table)
    blob = struct.pack(f"<{len(words)}I", *words)
    blob += bytes(DATA_BASE - CODE_BASE - len(blob)) + data
    assert CODE_BASE >= 0x621550 and DATA_LIMIT <= HEAP_BASE
    assert HEAP_BASE >> 16 == 0x62 and HEAP_BASE & 0xFFFF < 0x8000
    assert POOL_SIZE & 0xFFFF == 0
    assert 0x1980000 - POOL_SIZE > HEAP_BASE - 0x62154C + 0x1000
    assert SEGMENT_OFFSET % 16 == CODE_BASE % 16
    return {
        0x2C: struct.pack("<H", 2),
        0x54: struct.pack("<8I", 1, SEGMENT_OFFSET, CODE_BASE, CODE_BASE, len(blob), len(blob), 5, 16),
        SEGMENT_OFFSET: blob,
        0x100074 - FILE_BASE: struct.pack("<I", 0x24840000 | (HEAP_BASE & 0xFFFF)),
        0x1000F4 - FILE_BASE: struct.pack("<I", 0x3C040000 | (POOL_SIZE >> 16)),
        HOOK - FILE_BASE: struct.pack("<2I", 0x08000000 | (labels["entry"] >> 2), 0),
    }
