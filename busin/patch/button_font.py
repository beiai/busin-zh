"""Draw 确定, 返回 and 切换 with the small font.

0x177418 ends by tail-calling the sprite drawer. Indices 9, 10 and 14
take a detour once the Chinese pages are parsed: two 0x108820 glyphs,
same place as the 32×16 stamp. The four vertex color bytes are 0xFF so
the gray coverage ink covers the bar; the keyboard keeps 0x80. Other indices still
enter 0x169990. The cock stamps stay, for that fallback and for callers
that never reach this tail.

The drawer cannot live at 0x5A4E00 (BSS, cleared, then used). The two
alignment gaps below hold only the dispatch. The drawer itself is the
sector tail of the Chinese page buffer, which is left allocated.
"""
import struct

from busin.disc import FILE_BASE
from busin.patch.asm import (
    A0, A1, A2, A3, GP, RA, SP, T0, T1, T2, T3, T4, T5, T6, T7, ZR, Asm,
)
from busin.patch.ui_pages import ALLOC_BYTES, BUFFER, GP_BASE, STATE, UI_BYTES

HOOK = 0x1775A4
HOOK_WORD = 0x0805A664  # j 0x169990
DISPATCH = 0x5A2F3C  # .lit4 ends here; .sdata starts at 0x5A2F80
DISPATCH_ROOM = 17
TAIL = 0x5A1AEC  # .rodata ends at 0x5A1AE9; .lit4 starts at 0x5A1B00
TAIL_ROOM = 5
DRAW = 0x108820
SPRITE = 0x169990
READY = 5
CHARS = "确定返回切换"
assert tuple(CHARS) == ("确", "定", "返", "回", "切", "换")
assert UI_BYTES == 0x30A80 and UI_BYTES % 64 == 0
assert ALLOC_BYTES - UI_BYTES == 0x580
assert (0x3 << 16) | (UI_BYTES & 0xFFFF) == UI_BYTES
assert GP_BASE + STATE == 0x5A3108
assert BUFFER == 0x4377F8


def _j(va):
    return 0x08000000 | (va >> 2)


def build_dispatch():
    """Index and load-state check. Pointer arithmetic is at TAIL."""
    a = Asm()
    a.addiu(T4, A3, -9)
    a.beq(T4, ZR, "font")
    a.addiu(T4, A3, -10)
    a.beq(T4, ZR, "font")
    a.addiu(T4, A3, -14)
    a.bne(T4, ZR, "sprite")
    a.nop()
    a.label("font")
    a.i(36, GP, T4, STATE)  # lbu, state byte
    a.addiu(T5, ZR, READY)
    a.bne(T4, T5, "sprite")
    a.lui(T4, BUFFER >> 16)
    a.lw(T4, BUFFER & 0xFFFF, T4)
    a.j(TAIL)
    a.nop()
    a.label("sprite")
    a.j(SPRITE)
    a.nop()
    words = a.done()
    if len(words) > DISPATCH_ROOM:
        raise ValueError(f"button dispatch is {len(words)} words")
    return words


def build_tail():
    a = Asm()
    a.lui(T5, UI_BYTES >> 16)
    a.ori(T5, T5, UI_BYTES & 0xFFFF)
    a.addu(T4, T4, T5)
    a.jr(T4)
    a.nop()
    words = a.done()
    if len(words) != TAIL_ROOM:
        raise ValueError(f"button tail is {len(words)} words")
    return words


def build_blob(codes):
    """Position-independent drawer. codes are the 0x0418-page numbers."""
    if any(char not in codes or not 0x0418 <= codes[char] < 0x8000 for char in CHARS):
        raise ValueError("button glyphs are not on the Chinese pages")
    a = Asm()
    a.addiu(SP, SP, -0x30)
    a.sd(RA, 0x10, SP)
    a.sh(A1, 0x18, SP)
    a.addiu(A2, A2, -2)
    a.sh(A2, 0x1A, SP)
    a.addiu(T4, A3, -9)
    a.beq(T4, ZR, "confirm")
    a.addiu(T4, A3, -10)
    a.beq(T4, ZR, "back")
    a.nop()
    a.addiu(T6, ZR, codes["切"])
    a.beq(ZR, ZR, "emit")
    a.addiu(T7, ZR, codes["换"])
    a.label("confirm")
    a.addiu(T6, ZR, codes["确"])
    a.beq(ZR, ZR, "emit")
    a.addiu(T7, ZR, codes["定"])
    a.label("back")
    a.addiu(T6, ZR, codes["返"])
    a.addiu(T7, ZR, codes["回"])
    a.label("emit")
    a.sh(T7, 0x1C, SP)
    a.addiu(T0, ZR, 0)
    a.addiu(T1, ZR, 0x1C)
    a.addiu(T2, ZR, 0xFF)
    a.addiu(T3, ZR, 0xFF)
    a.sb(T2, 0, SP)
    a.sb(T2, 8, SP)
    a.ori(A3, ZR, 0xFE79)
    a.jal(DRAW)
    a.addu(A0, T6, ZR)
    a.lh(A1, 0x18, SP)
    a.lh(A2, 0x1A, SP)
    a.addiu(A1, A1, 16)
    a.addiu(T0, ZR, 0)
    a.addiu(T1, ZR, 0x1C)
    a.addiu(T2, ZR, 0xFF)
    a.addiu(T3, ZR, 0xFF)
    a.ori(A3, ZR, 0xFE79)
    a.jal(DRAW)
    a.lh(A0, 0x1C, SP)
    a.ld(RA, 0x10, SP)
    a.jr(RA)
    a.addiu(SP, SP, 0x30)
    words = a.done()
    if len(words) * 4 > ALLOC_BYTES - UI_BYTES:
        raise ValueError("button drawer does not fit the sector tail")
    return struct.pack(f"<{len(words)}I", *words)


def _word(elf, va):
    return struct.unpack_from("<I", elf, va - FILE_BASE)[0]


def check_source(elf):
    if _word(elf, HOOK) != HOOK_WORD:
        raise ValueError(f"hint tail {HOOK:#x} is {_word(elf, HOOK):#x}")
    for va, count in ((DISPATCH, DISPATCH_ROOM), (TAIL, TAIL_ROOM)):
        blob = elf[va - FILE_BASE:va - FILE_BASE + count * 4]
        if any(blob):
            raise ValueError(f"button gap at {va:#x} is not empty")


def elf_writes(elf):
    check_source(elf)
    dispatch, tail = build_dispatch(), build_tail()
    return {
        HOOK - FILE_BASE: struct.pack("<I", _j(DISPATCH)),
        DISPATCH - FILE_BASE: struct.pack(f"<{len(dispatch)}I", *dispatch),
        TAIL - FILE_BASE: struct.pack(f"<{len(tail)}I", *tail),
    }


def _simm(word):
    imm = word & 0xFFFF
    return imm - 0x10000 if imm & 0x8000 else imm


def _put(reg, index, value):
    if index:
        reg[index] = value & 0xFFFFFFFF


def _load(mem, addr, size):
    value = 0
    for index in range(size):
        value |= mem.get(addr + index, 0) << (8 * index)
    return value


def _store(mem, addr, size, value):
    for index in range(size):
        mem[addr + index] = (value >> (8 * index)) & 0xFF


def _exec(word, reg, mem):
    op = word >> 26
    rs = (word >> 21) & 31
    rt = (word >> 16) & 31
    rd = (word >> 11) & 31
    if word == 0:
        return
    if op == 0 and (word & 63) == 33:
        _put(reg, rd, reg[rs] + reg[rt])
    elif op == 9:
        _put(reg, rt, reg[rs] + _simm(word))
    elif op == 13:
        _put(reg, rt, reg[rs] | (word & 0xFFFF))
    elif op == 15:
        _put(reg, rt, (word & 0xFFFF) << 16)
    elif op == 35:
        _put(reg, rt, _load(mem, reg[rs] + _simm(word), 4))
    elif op == 36:
        _put(reg, rt, _load(mem, reg[rs] + _simm(word), 1))
    elif op == 33:
        value = _load(mem, reg[rs] + _simm(word), 2)
        _put(reg, rt, value - 0x10000 if value & 0x8000 else value)
    elif op in (40, 41, 63):
        _store(mem, reg[rs] + _simm(word), {40: 1, 41: 2, 63: 8}[op], reg[rt])
    elif op == 55:
        _put(reg, rt, _load(mem, reg[rs] + _simm(word), 8))
    else:
        raise AssertionError(f"unhandled {word:#010x}")


def _run(code, pc, reg, mem, stop):
    for _step in range(40):
        if pc in stop:
            return pc
        word = code[pc]
        op = word >> 26
        if op in (2, 3) or (op == 0 and (word & 63) == 8):
            _exec(code[pc + 4], reg, mem)
            if op == 3:
                reg[RA] = pc + 8
                pc = (word & 0x03FFFFFF) << 2
            elif op == 2:
                pc = (word & 0x03FFFFFF) << 2
            else:
                pc = reg[(word >> 21) & 31]
        elif op in (4, 5):
            rs = (word >> 21) & 31
            rt = (word >> 16) & 31
            taken = (reg[rs] == reg[rt]) == (op == 4)
            _exec(code[pc + 4], reg, mem)
            pc = pc + 4 + (_simm(word) << 2) if taken else pc + 8
        else:
            _exec(word, reg, mem)
            pc += 4
        if pc in stop:
            return pc
    raise AssertionError(f"did not reach {sorted(hex(item) for item in stop)}")


def self_test():
    dispatch, tail = build_dispatch(), build_tail()
    if _j(SPRITE) not in dispatch or _j(TAIL) not in dispatch:
        raise AssertionError("dispatch does not reach the sprite or the tail")
    code = {DISPATCH + index * 4: word for index, word in enumerate(dispatch)}
    code.update({TAIL + index * 4: word for index, word in enumerate(tail)})
    # Branch delay slots are fetched from the instruction dictionary.
    for va in (DISPATCH + (len(dispatch) - 1) * 4, TAIL + 4 * 4):
        code.setdefault(va + 4, 0)
    pointer = 0x01000000
    blob_at = pointer + UI_BYTES

    def enter(index, state):
        reg = [0] * 32
        reg[GP] = GP_BASE
        reg[A0], reg[A1], reg[A2], reg[A3] = 0x1111, 0x40, 0x30, index
        reg[T0], reg[T1], reg[T2], reg[T3] = 0x22, 8, 0x80, 0x80
        reg[RA], reg[SP] = 0xABC0, 0x2000000
        mem = {}
        _store(mem, GP_BASE + STATE, 1, state)
        _store(mem, BUFFER, 4, pointer)
        kept = (reg[A0], reg[A1], reg[A2], reg[A3], reg[T0], reg[T1], reg[T2], reg[T3], reg[SP], reg[RA])
        landed = _run(code, DISPATCH, reg, mem, {SPRITE, blob_at})
        if (reg[A0], reg[A1], reg[A2], reg[A3], reg[T0], reg[T1], reg[T2], reg[T3], reg[SP], reg[RA]) != kept:
            raise AssertionError(f"index {index} clobbered the sprite arguments")
        return landed

    for index in (0, 8, 11, 18, 23):
        if enter(index, READY) != SPRITE:
            raise AssertionError(f"index {index} did not stay a sprite")
    for index in (9, 10, 14):
        if enter(index, READY) != blob_at or enter(index, 4) != SPRITE:
            raise AssertionError(f"index {index} took the wrong path")
    sample = {char: 0x501 + index for index, char in enumerate(CHARS)}
    blob = build_blob(sample)
    words = struct.unpack(f"<{len(blob) // 4}I", blob)
    base = 0x80000000
    body = {base + index * 4: word for index, word in enumerate(words)}
    body[base + len(words) * 4] = 0

    def draw(index):
        reg = [0] * 32
        reg[SP], reg[RA], reg[A1], reg[A2], reg[A3] = 0x1000000, 0xABC0, 0x40, 0x30, index
        mem = {}
        calls = []
        pc = base
        for _step in range(80):
            word = body[pc]
            op = word >> 26
            if op == 3:
                _exec(body[pc + 4], reg, mem)
                if ((word & 0x03FFFFFF) << 2) != DRAW:
                    raise AssertionError("blob calls something other than the small font")
                calls.append((
                    reg[A0], reg[A1], reg[A2], reg[A3], reg[T0], reg[T1], reg[T2], reg[T3],
                    _load(mem, reg[SP], 1), _load(mem, reg[SP] + 8, 1),
                ))
                ret = pc + 8
                for index_reg in range(1, 16):
                    reg[index_reg] = 0xDEAD
                reg[RA] = ret
                pc = ret
                continue
            if op == 0 and (word & 63) == 8:
                _exec(body[pc + 4], reg, mem)
                return calls, reg[(word >> 21) & 31], reg[SP]
            if op in (4, 5):
                rs = (word >> 21) & 31
                rt = (word >> 16) & 31
                taken = (reg[rs] == reg[rt]) == (op == 4)
                _exec(body[pc + 4], reg, mem)
                pc = pc + 4 + (_simm(word) << 2) if taken else pc + 8
                continue
            _exec(word, reg, mem)
            pc += 4
        raise AssertionError("blob did not return")

    expect = {
        9: (sample["确"], sample["定"]),
        10: (sample["返"], sample["回"]),
        14: (sample["切"], sample["换"]),
    }
    for index, pair in expect.items():
        calls, back, sp = draw(index)
        if back != 0xABC0 or sp != 0x1000000 or len(calls) != 2:
            raise AssertionError(f"index {index} returned to {back:#x} via {calls}")
        for nth, glyph in enumerate(pair):
            got = calls[nth]
            if got != (glyph, 0x40 + nth * 16, 0x2E, 0xFE79, 0, 0x1C, 0xFF, 0xFF, 0xFF, 0xFF):
                raise AssertionError(f"index {index} glyph {nth} is {got}")
    print("button font self-test: OK")
