"""Six Chinese 16x16 UI font pages after the block-font area.

The unrolled chunk-0 parser occupied 76 instructions at 0x108674. This
replacement fills that hole: the same twelve textures, then a state that
reads the Chinese pages. ponytail: 76 instructions exactly. Another texture
needs a different gap.
"""
import struct

from busin.disc import FILE_BASE
from busin.patch.asm import (
    A0, A1, A2, A3, GP, RA, S0, S1, SP, T0, T1, T2, T3, V0, V1, ZR, Asm,
)
from busin.patch.font_paging import BLOCK_FONT_LBA, BLOCK_FONT_SECTORS

PAGE_BYTES = 0x81C0
PAGE_COUNT = 6
FIRST_CODE = 0x0401
LAST_CODE = 0x400 + PAGE_COUNT * 0x100 - 1
PAGE_LIMIT = 4 + PAGE_COUNT
UI_BYTES = PAGE_BYTES * PAGE_COUNT
UI_SECTORS = (UI_BYTES + 2047) // 2048
UI_LBA = BLOCK_FONT_LBA + BLOCK_FONT_SECTORS
ALLOC_BYTES = UI_SECTORS * 2048
ALLOC_LO = ALLOC_BYTES & 0xFFFF
ALLOC_HI = (ALLOC_BYTES >> 16) + (ALLOC_LO >> 15)
assert (ALLOC_HI << 16) + ALLOC_LO - (0x10000 if ALLOC_LO & 0x8000 else 0) == ALLOC_BYTES
assert (UI_BYTES, UI_SECTORS, UI_LBA, ALLOC_BYTES, LAST_CODE, PAGE_LIMIT) == (
    0x30A80, 98, 289619, 0x31000, 0x09FF, 10)
# Absolute LBA fits lui $a0, 4 / addiu $a0, 0x6B53.
assert (4 << 16) + 0x6B53 == UI_LBA
# 0x403C78 a3 is the single request object. lui $a3, 0x5E / ori $a3, 0xC578.
assert (0x5E << 16) | 0xC578 == 0x5EC578

PARSE = 0x3FD8D0
ALLOC = 0x101740
READ = 0x403C78
STATUS = 0x105CB0
FREE = 0x407A30
LINK = 0x3F5B08
RESUME = 0x1087A4
EPILOGUE = 0x108810
PARSER = 0x108674
LOADER = PARSER + 32 * 4
HELPER = 0x4377B0
OFFSETS = 0x437780
PAGES = HELPER + 12 * 4
BUFFER = PAGES + PAGE_COUNT * 4
OLD = 0x43DC30
GP_BASE = 0x5A9AF0
CHUNK0 = -0x69E4
CHUNK1 = -0x69B8
READ_STATE = -0x4B94
GP_DEST = -0x69DC
assert (LOADER, PAGES, BUFFER, GP_BASE + CHUNK0, GP_BASE + GP_DEST) == (
    0x1086F4, 0x4377E0, 0x4377F8, 0x5A310C, 0x5A3114)
assert BUFFER + 8 == 0x437800
assert -0x8000 <= PAGES - OLD < 0x8000

OFFSET_WORDS = (
    0x00000, 0x081C0, 0x10380, 0x18540, 0x20700, 0x28980,
    0x30CC0, 0x38F00, 0x41140, 0x49380, 0x51560, 0x51DE0,
)
DRAW_SITES = (0x108890, 0x108A1C, 0x108C94, 0x108DEC, 0x108F3C, 0x109094)
DRAW_KEEP = (0x0040A02D, 0x0040B02D, 0x0040882D, 0x0040882D, 0x0040882D, 0x0040882D)
SOURCE = {
    0x10859C: 0x28420004,
    0x1085B8: 0x24030004,
    0x108618: 0x1000007D,
    0x108674: 0x0C0FF634,
    0x1087A0: 0xAF829640,
    0x108800: 0x24020003,
    0x113574: 0x2C420003,
}
for site in DRAW_SITES:
    SOURCE[site] = 0x3C030044
    SOURCE[site + 4] = 0x2463DC30
    SOURCE[site + 12] = 0x00052880
    SOURCE[site + 20] = 0x00A32821
    SOURCE[site + 28] = 0x8CA80000


def _li(a, rt, addr):
    low = addr & 0xFFFF
    high = (addr >> 16) + (low >> 15)
    a.lui(rt, high)
    a.addiu(rt, rt, low)


def _parser():
    a = Asm()
    _li(a, S1, OFFSETS)
    a.addiu(S0, ZR, 0)
    a.label("loop")
    a.lw(A0, CHUNK0, GP)
    a.lw(T0, 0, S1)
    a.jal(PARSE)
    a.addu(A0, A0, T0)
    a.sltiu(T1, S0, 4)
    a.beq(T1, ZR, "gpdest")
    a.sll(T2, S0, 2)
    _li(a, T3, OLD)
    a.beq(ZR, ZR, "store")
    a.addu(T3, T3, T2)
    a.label("gpdest")
    a.addiu(T2, S0, -4)
    a.sll(T2, T2, 2)
    a.addiu(T3, GP, GP_DEST)
    a.addu(T3, T3, T2)
    a.label("store")
    a.sw(V0, 0, T3)
    a.addiu(S0, S0, 1)
    a.addiu(S1, S1, 4)
    a.sltiu(T1, S0, 12)
    a.bne(T1, ZR, "loop")
    a.nop()
    a.lw(A0, CHUNK1, GP)
    a.lw(V1, CHUNK0, GP)
    a.lui(A1, 5)
    a.addiu(A1, A1, 0x1EE0)
    a.jal(LINK)
    a.addu(A1, V1, A1)
    a.j(RESUME)
    a.nop()
    return a.done()


def _loader():
    a = Asm()
    a.addiu(V0, ZR, 4)
    a.bne(S0, V0, "leave")
    _li(a, S1, PAGES)
    a.lw(T0, BUFFER - PAGES, S1)
    a.bne(T0, ZR, "wait")
    a.lui(A0, ALLOC_HI)
    a.jal(ALLOC)
    a.addiu(A0, A0, ALLOC_LO)
    a.beq(V0, ZR, "leave")
    a.lui(A3, 0x5E)
    a.sw(V0, BUFFER - PAGES, S1)
    a.sb(ZR, READ_STATE, GP)
    a.ori(A3, A3, 0xC578)
    a.lui(A0, UI_LBA >> 16)
    a.addiu(A0, A0, 0x6B53)
    a.addiu(A1, ZR, UI_SECTORS)
    a.jal(READ)
    a.r(A2, V0, ZR, 0x2D)
    a.beq(ZR, ZR, "leave")
    a.nop()
    a.label("wait")
    a.jal(STATUS)
    a.addiu(S0, ZR, 0)
    a.addiu(V1, ZR, 2)
    a.bne(V0, V1, "leave")
    a.ori(T1, ZR, PAGE_BYTES)
    a.label("fill")
    a.lw(A0, BUFFER - PAGES, S1)
    # 0x3FD8D0 does not save $t1, so the stride does not survive the first parse.
    a.ori(T1, ZR, PAGE_BYTES)
    a.mult_rd(T0, S0, T1)
    a.jal(PARSE)
    a.addu(A0, A0, T0)
    a.sll(T0, S0, 2)
    a.addu(T0, S1, T0)
    a.sw(V0, 0, T0)
    a.addiu(T2, ZR, PAGE_COUNT - 1)
    a.bne(S0, T2, "fill")
    a.addiu(S0, S0, 1)
    # Keep the page buffer allocated while its parsed handles are in use.
    a.nop()
    a.nop()
    a.nop()
    a.nop()
    a.label("leave")
    # 0x108810 reloads s0 only. ra still points at the last jal unless we reload it.
    a.ld(RA, 0x20, SP)
    a.j(EPILOGUE)
    a.ld(S1, 0x10, SP)
    return a.done()


def _helper():
    """Page in $a1. Pointer comes back in $v1. $v0 and $a1 stay put."""
    a = Asm()
    _li(a, V1, OLD)
    a.sltiu(T0, A1, 4)
    a.beq(T0, ZR, "high")
    a.sll(T1, A1, 2)
    a.beq(ZR, ZR, "load")
    a.addu(V1, V1, T1)
    a.label("high")
    a.addiu(T1, T1, -16)
    a.addiu(V1, V1, PAGES - OLD)
    a.addu(V1, V1, T1)
    a.label("load")
    a.jr(31)
    a.lw(V1, 0, V1)
    return a.done()


def machine():
    parser, loader, helper = _parser(), _loader(), _helper()
    if (len(parser), len(loader), len(helper)) != (32, 44, 12):
        raise ValueError(f"ui page code is {len(parser)}+{len(loader)}+{len(helper)}")
    return parser + loader, helper


def _word(elf, va):
    return struct.unpack_from("<I", elf, va - FILE_BASE)[0]


def check_source(elf):
    for va, word in SOURCE.items():
        have = _word(elf, va)
        if have != word:
            raise ValueError(f"ui page site {va:#x} is {have:#x}, expected {word:#x}")
    for va, word in zip(DRAW_SITES, DRAW_KEEP):
        have = _word(elf, va + 8)
        if have != word:
            raise ValueError(f"draw site {va:#x} cell move is {have:#x}")
    gap = elf[OFFSETS - FILE_BASE:0x437800 - FILE_BASE]
    if any(gap):
        raise ValueError("ui font gap at 0x437780 is not empty")


def elf_writes(elf):
    """ELF file offset -> bytes. Source words must still be the original disc."""
    check_source(elf)
    code, helper = machine()
    branch = (LOADER - (0x108618 + 4)) >> 2
    if not 0 <= branch < 0x8000:
        raise ValueError(f"loader branch {branch:#x} is out of range")
    jal = 0x0C000000 | (HELPER >> 2)
    move = 0x0060402D  # daddu $a4, $v1, $zero
    writes = {
        PARSER - FILE_BASE: struct.pack(f"<{len(code)}I", *code),
        OFFSETS - FILE_BASE: struct.pack(f"<{len(OFFSET_WORDS)}I", *OFFSET_WORDS),
        HELPER - FILE_BASE: struct.pack(f"<{len(helper)}I", *helper),
        0x108618 - FILE_BASE: struct.pack("<I", 0x10000000 | branch),
        0x10859C - FILE_BASE: struct.pack("<I", 0x28420000 | PAGE_LIMIT),
        0x1085B8 - FILE_BASE: struct.pack("<I", 0x24030000 | PAGE_LIMIT),
        0x108800 - FILE_BASE: struct.pack("<I", 0x24020004),
        0x113574 - FILE_BASE: struct.pack("<I", 0x2C420005),
    }
    for site in DRAW_SITES:
        writes[site - FILE_BASE] = struct.pack("<I", jal)
        writes[site + 4 - FILE_BASE] = struct.pack("<I", 0)
        writes[site + 12 - FILE_BASE] = struct.pack("<I", 0)
        writes[site + 20 - FILE_BASE] = struct.pack("<I", 0)
        writes[site + 28 - FILE_BASE] = struct.pack("<I", move)
    spans = sorted((off, off + len(data)) for off, data in writes.items())
    if any(end > nxt for (_, end), (nxt, _) in zip(spans, spans[1:])):
        raise ValueError("ui page ELF writes overlap")
    return writes


def self_test():
    code, helper = machine()
    targets = []
    for index, word in enumerate(code):
        op = word >> 26
        if op in (2, 3):
            targets.append((word & 0x03FFFFFF) << 2)
        if op in (4, 5):
            off = word & 0xFFFF
            off -= 0x10000 if off & 0x8000 else 0
            dest = index + 1 + off
            if not 0 <= dest < len(code):
                raise AssertionError(f"branch at {PARSER + index * 4:#x} leaves the hole")
    want = {PARSE, LINK, RESUME, ALLOC, READ, STATUS, EPILOGUE}
    if FREE != 0x407A30:
        raise AssertionError("page buffer free is not 0x407A30")
    if set(targets) != want or targets.count(PARSE) != 2:
        raise AssertionError(f"ui page jumps are {sorted(hex(item) for item in targets)}")
    if code[3] != 0x8F84961C or code[32] != 0x24020004:
        raise AssertionError("parser no longer loads chunk 0 or the Chinese state")
    loader = code[32:]
    if 0x3C07005E not in loader or 0x34E7C578 not in loader or 0xDFBF0020 not in loader:
        raise AssertionError("loader dropped the request object or the return address")
    if helper[-2] >> 26 != 0 or helper[-1] >> 26 != 35:
        raise AssertionError("helper does not return the loaded pointer")
    stride = loader.index(0x02094018)
    if loader[stride - 1] != 0x340981C0 or loader.count(0x340981C0) != 2:
        raise AssertionError("page stride is not reloaded before multiply")
    if (4 << 16) + 0x6B53 != UI_LBA or (ALLOC_HI << 16) + ALLOC_LO - (
            0x10000 if ALLOC_LO & 0x8000 else 0) != ALLOC_BYTES:
        raise AssertionError("read immediates do not match the appended LBA")
    got = [_run_helper(page) for page in (0, 3, 4, 9)]
    if got != [0xA0, 0xA3, 0xB0, 0xB5]:
        raise AssertionError(f"helper pages resolve to {got}")
    print("ui page self-test: OK")


def _run_helper(page):
    words = _helper()
    reg = [0] * 32
    reg[A1] = page
    mem = {OLD + index * 4: 0xA0 + index for index in range(4)}
    mem.update({PAGES + index * 4: 0xB0 + index for index in range(PAGE_COUNT)})

    def put(index, value):
        if index:
            reg[index] = value & 0xFFFFFFFF

    def exec_one(item):
        op = item >> 26
        rs = (item >> 21) & 31
        rt = (item >> 16) & 31
        rd = (item >> 11) & 31
        imm = item & 0xFFFF
        simm = imm - 0x10000 if imm & 0x8000 else imm
        fn = item & 63
        if op == 15:
            put(rt, imm << 16)
        elif op == 9:
            put(rt, reg[rs] + simm)
        elif op == 11:
            put(rt, 1 if reg[rs] < (simm & 0xFFFFFFFF) else 0)
        elif op == 0 and fn == 0:
            put(rd, reg[rt] << ((item >> 6) & 31))
        elif op == 0 and fn == 33:
            put(rd, reg[rs] + reg[rt])
        elif op == 35:
            put(rt, mem[reg[rs] + simm])
        else:
            raise AssertionError(f"helper op {item:#x}")

    pc = 0
    for _step in range(16):
        word = words[pc]
        op = word >> 26
        if op == 0 and (word & 63) == 8:
            exec_one(words[pc + 1])
            return reg[V1]
        if op in (4, 5):
            rs = (word >> 21) & 31
            rt = (word >> 16) & 31
            imm = word & 0xFFFF
            simm = imm - 0x10000 if imm & 0x8000 else imm
            exec_one(words[pc + 1])
            pc = pc + 1 + simm if (reg[rs] == reg[rt]) == (op == 4) else pc + 2
            continue
        exec_one(word)
        pc += 1
    raise AssertionError("helper did not return")
