"""Font-page trampoline, upload patch, hook table, and the addresses they use.

Do not hook the return of 0x105CB8. The read is still outstanding there.
The hooks stay at the two script-machine completion points.
"""
import struct

from busin.disc import CHUNK_TABLE, FILE_BASE, PACK_LBA
from busin.patch.asm import (
    A0, A1, A2, A3, Asm, GP, RA, SP, S1,
    T0, T1, T2, T3, T4, T5, T6, T7, V0, V1, ZR,
)

SHIFT_FROM = 262145
SHIFT = 516
COMBINED_START = SHIFT_FROM - PACK_LBA
assert COMBINED_START == 261821  # old end of PACKDATA, in PACKDATA sectors
PACK_SECTORS = COMBINED_START + SHIFT
# Original PVD volume space, equal to the BIN length in sectors. The patch
# inserts SHIFT sectors at SHIFT_FROM; block fonts append after that image.
BLOCK_FONT_LBA = 283703 + SHIFT
assert BLOCK_FONT_LBA == 284219
BLOCK_SLOTS = 18
SLOT_SECTORS = 300
BLOCK_FONT_SECTORS = BLOCK_SLOTS * SLOT_SECTORS
# 16 KiB token table, the live page pointer, and the ready flag. All sit in the 249 KiB gap.
# Empty in the saves checked so far. InitHeap's size argument is -1;
# whether another scene writes this gap is unknown.
TOKMAP_BASE = 0x01FA1A00
TOKMAP_COUNT = 4096
BLOCK_TMX = TOKMAP_BASE + TOKMAP_COUNT * 4
BLOCK_PENDING = BLOCK_TMX + 4
# Nonzero once the table has been written. A later block sets this before its own map.
TOKMAP_READY = BLOCK_PENDING + 4
assert (BLOCK_TMX, BLOCK_PENDING, TOKMAP_READY, BLOCK_FONT_SECTORS) == (
    0x01FA5A00, 0x01FA5A04, 0x01FA5A08, 5400)
assert BLOCK_FONT_LBA == 0x4563B
SEVEN_LBA = BLOCK_FONT_LBA
NINE_LBA = BLOCK_FONT_LBA + 2 * SLOT_SECTORS
assert (SEVEN_LBA, NINE_LBA) == (0x4563B, 0x45893) and SEVEN_LBA >> 16 == NINE_LBA >> 16
# Last table word. Slot 2's copy is 2; the fill only writes 0..1763, so it stays.
MARKER_ADDR = TOKMAP_BASE + (TOKMAP_COUNT - 1) * 4
SLOT2_MARK = 2
assert MARKER_ADDR == 0x01FA59FC
assert 0x5F0000 - 0x3A88 == 0x5EC578
assert BLOCK_TMX + 4 <= 0x01FE0000
CHUNK_SECTOR = 33518
CHUNK_BYTES = 0x81000
TMX_BYTES = 0x80600
PAGE_CELLS = 1764
MAP_SECTORS = 8
READ_SECTORS = CHUNK_BYTES // 2048 + MAP_SECTORS
READ_BYTES = READ_SECTORS * 2048
assert (READ_SECTORS, READ_BYTES) == (266, 0x85000) and READ_SECTORS <= SLOT_SECTORS
PAGE1_OFF = 0x81000
CODE_OFF = 0x101600
DATA_OFF = 0x101E00
COMBINED = 0x102000
CAVE = 0x5A4D84
GP_TMX = 0x5A4550
OPEN_TMX = 0x5A4B54
OPEN_SAMPLE = 0x3BD998
OPEN_SUBMIT = 0x3BD9A0
OPEN_PREAMBLE = 0x3BD9A8
OPEN_UPLOAD_TAIL = 0x3BD990
BODY_TABLE = CHUNK_TABLE + 317 * 16
OPEN_TABLE = CHUNK_TABLE + 278 * 16
assert (BODY_TABLE, OPEN_TABLE) == (0x33D2C0, 0x33D050)
TEX0 = 0x2002A006A9441508
BODY_SUBMITS = (0x2A3D84, 0x2A3E58, 0x2A3F9C, 0x2A4104, 0x2A42A0, 0x2A4470, 0x2A46B4)
OPEN_SUBMITS = (0x3BDAB8, 0x3BDBDC, 0x3BDD24)
P_FILESZ = 0x44
# Cave ends at VA 0x5A4E00. Bytes at and after that are BSS and get used.
PATCHED_FILESZ = 0x4A4E00
# Five unused words between .text and .vutext. The jr delay is the VU word addi $zero, $zero, 0.
LOAD_BRIDGE = 0x42E3EC
# Two padding nops after jr ra, before the syscall stubs.
LOAD_MISS = 0x425B34
LOAD_HOOK = 0x288A20
LOAD_EPILOGUE = 0x288A88
NINE_HOOK = 0x288728
NINE_EPILOGUE = 0x2887FC
INSTALL_OFF = 0
# Source words checked before patching but not overwritten by a hook.
GUARDS = {0x3BD998: 0x30A6FFFF, 0x3BD984: 0xDFBF0020}


# Slot 0 is the shared page (opening and chunk 282). Its map is the identity table.
# Chunk 285 stays in slot 2. Chunks 312–316 share one character set and one slot.
CHUNK_SLOTS = {
    285: 2,
    291: 1,
    292: 8,
    293: 9,
    296: 3,
    297: 4,
    299: 5,
    301: 6,
    302: 7,
    306: 10,
    308: 11,
    309: 14,
    310: 12,
    298: 15,
    312: 13,
    313: 13,
    314: 13,
    315: 13,
    316: 13,
}
assert CHUNK_SLOTS[285] == SLOT2_MARK
assert all(0 < slot < BLOCK_SLOTS for slot in CHUNK_SLOTS.values())


def token_map():
    """Identity cells 0..1763, then blanks. Shared-page tokens are 0x4000 plus the cell."""
    return struct.pack(f"<{TOKMAP_COUNT}I", *(index if index < PAGE_CELLS else 0 for index in range(TOKMAP_COUNT)))


def j(target):
    return 0x08000000 | (target >> 2)


def jal(target):
    return 0x0C000000 | (target >> 2)


def build_bridge():
    """Enter the pad with selector 4 when the message font is resident, otherwise the epilogue.

    The pad lives at the font pointer plus 0x101600, so this cannot jump to a fixed address.
    """
    pad = build_cave()[1]["pad"]
    t0, v1 = 8, 3
    lw = (35 << 26) | (GP << 21) | (t0 << 16) | ((GP_TMX - 0x5A9AF0) & 0xFFFF)
    off = (LOAD_MISS - (LOAD_BRIDGE + 8)) >> 2
    if not -0x8000 <= off < 0x8000:
        raise ValueError("load bridge cannot reach the miss stub")
    beq = (4 << 26) | (t0 << 21) | (off & 0xFFFF)
    addiu = (9 << 26) | (v1 << 16) | 4
    return [lw, beq, addiu, j(pad), 0]


def hooks(labels):
    """(va, source word, patched word) for every ELF instruction the patch replaces."""
    if INSTALL_OFF == 0:
        build_pad()
    table = [
        (0x2A3C60, 0x30A6FFFF, j(labels["sample"])),
        (0x2A3CA0, 0x27BDFFD0, j(labels["clear"])),
        (0x2A3CA4, 0xFFB10010, 0x27BDFFD0),
        (0x2A3CD4, 0xDFBF0020, 0xDFB90020),
        (0x2A3CE0, 0x080FF86C, jal(labels["preamble"])),
        (LOAD_HOOK, 0x10000019, j(LOAD_BRIDGE)),
        (NINE_HOOK, 0x10000034, j(labels["nine"])),
        (LOAD_MISS, 0, j(LOAD_EPILOGUE)),
        (LOAD_MISS + 4, 0, 0),
    ]
    table += [(va, 0x0C0FFCEC, jal(labels["submit"])) for va in BODY_SUBMITS]
    table += [(va, 0x0C0FFCEC, jal(OPEN_SUBMIT)) for va in OPEN_SUBMITS]
    table.append((OPEN_UPLOAD_TAIL, 0x080FF86C, j(OPEN_PREAMBLE)))
    table += [(LOAD_BRIDGE + 4 * index, 0, word) for index, word in enumerate(build_bridge())]
    return table


def elf_writes(words, labels):
    """ELF file offset -> bytes, for every change the patch makes to the executable."""
    opening = build_opening(labels["nobase"])
    writes = {
        P_FILESZ: struct.pack("<I", PATCHED_FILESZ),
        BODY_TABLE: struct.pack("<QQ", COMBINED_START, SHIFT),
        OPEN_TABLE: struct.pack("<QQ", COMBINED_START, SHIFT),
        CAVE - FILE_BASE: struct.pack(f"<{len(words)}I", *words),
        OPEN_SAMPLE - FILE_BASE: struct.pack(f"<{len(opening)}I", *opening),
    }
    for va, _source, patched in hooks(labels):
        writes[va - FILE_BASE] = struct.pack("<I", patched)
    spans = sorted((off, off + len(data)) for off, data in writes.items())
    if any(end > nxt for (_, end), (nxt, _) in zip(spans, spans[1:])):
        raise ValueError("ELF patch writes overlap")
    return writes

def build_pad():
    # Code and cache share this allocation; code must end before DATA_OFF.
    global INSTALL_OFF
    a = Asm()
    a.addiu(T7, V1, 0)
    a.bne(T7, ZR, "not_sample")
    a.andi(T0, A1, 0xFFFF)
    a.sltiu(T2, T0, 0x4000)
    a.bne(T2, ZR, "sample_page0")
    a.andi(A2, T0, 0xFFFF)
    # ponytail: one identity fill for the whole session. A block map must set
    # TOKMAP_READY before it writes, or the next page-1 sample restores 0..1763.
    a.lui(T4, TOKMAP_BASE >> 16)
    a.lw(T2, TOKMAP_READY & 0xFFFF, T4)
    a.bne(T2, ZR, "mapped")
    a.addiu(T1, T0, -0x4000)
    a.addiu(T4, T4, TOKMAP_BASE & 0xFFFF)
    a.addiu(T2, ZR, 0)
    a.addiu(T5, ZR, 1764)
    a.label("fill")
    a.sw(T2, 0, T4)
    a.addiu(T2, T2, 1)
    a.addiu(T4, T4, 4)
    a.bne(T2, T5, "fill")
    a.nop()
    a.lui(T4, TOKMAP_BASE >> 16)
    a.addiu(T2, ZR, 1)
    a.sw(T2, TOKMAP_READY & 0xFFFF, T4)
    a.label("mapped")
    a.sltiu(T2, T1, TOKMAP_COUNT)
    a.beq(T2, ZR, "blank")
    a.sll(T2, T1, 2)
    a.lui(T4, TOKMAP_BASE >> 16)
    a.addiu(T4, T4, TOKMAP_BASE & 0xFFFF)
    a.addu(T4, T4, T2)
    a.lw(A2, 0, T4)
    a.beq(ZR, ZR, "page1")
    a.andi(A2, A2, 0xFFFF)
    a.label("blank")
    a.addiu(A2, ZR, 0)
    a.label("page1")
    a.beq(ZR, ZR, "sample_store")
    a.addiu(T3, ZR, 1)
    a.label("sample_page0")
    a.addiu(T3, ZR, 0)
    a.label("sample_store")
    # ponytail: body pointer wins when both fonts are resident. The opening crawl leaves it at 0.
    a.lui(T4, 0x5A)
    a.lw(T5, 0x4550, T4)
    a.bne(T5, ZR, "sample_have")
    a.nop()
    a.lw(T5, 0x4B54, T4)
    a.label("sample_have")
    a.beq(T5, ZR, "ret")
    a.lui(T4, 0x10)
    a.addiu(T4, T4, 0x1E00)
    a.addu(T4, T5, T4)
    a.sw(T3, 0, T4)
    a.addiu(T3, ZR, 1)
    a.beq(ZR, ZR, "ret")
    a.sw(T3, 4, T4)
    a.label("ret")
    a.lui(T5, 0x2A)
    a.addiu(T5, T5, 0x3C68)
    a.jr(T5)
    a.addiu(V0, ZR, 42)
    a.label("not_sample")
    a.addiu(T0, ZR, 3)
    a.beq(T7, T0, "preamble")
    a.addiu(T0, ZR, 4)
    a.beq(T7, T0, "install")
    a.addiu(T0, ZR, 5)
    a.beq(T7, T0, "install")
    a.addiu(T0, ZR, 2)
    a.bne(T7, T0, "do_submit")
    a.lui(T4, 0x5A)
    a.lw(T4, 0x4550, T4)
    a.beq(T4, ZR, "back_clear")
    a.lui(T5, 0x10)
    a.addiu(T5, T5, 0x1E00)
    a.addu(T4, T4, T5)
    a.andi(T7, A0, 0xFFFF)
    a.addiu(T0, ZR, 27)
    a.sw(ZR, 0, T4)
    a.sw(ZR, 4, T4)
    a.bne(T7, T0, "clear28")
    a.sw(ZR, 24, T4)
    a.sw(ZR, 16, T4)
    a.sw(ZR, 20, T4)
    a.beq(ZR, ZR, "back_clear")
    a.sw(ZR, 28, T4)
    a.label("clear28")
    a.beq(ZR, ZR, "back_clear")
    a.sw(ZR, 28, T4)
    a.label("back_clear")
    a.lui(T5, 0x2A)
    a.addiu(T5, T5, 0x3CA8)
    a.jr(T5)
    a.nop()
    a.label("do_submit")
    a.addiu(SP, SP, -80)
    a.sd(RA, 16, SP)
    a.sd(A0, 24, SP)
    a.sd(A1, 32, SP)
    a.sd(A2, 40, SP)
    a.lui(T4, 0x5A)
    a.sd(A3, 48, SP)
    a.lw(T5, 0x4550, T4)
    a.bne(T5, ZR, "sub_have")
    a.nop()
    a.lw(T5, 0x4B54, T4)
    a.label("sub_have")
    a.addu(T4, T5, ZR)
    a.beq(T4, ZR, "sprite")
    a.lui(T5, 0x10)
    a.addiu(T5, T5, 0x1E00)
    a.addu(T5, T4, T5)
    a.lw(T6, 4, T5)
    a.beq(T6, ZR, "sprite")
    a.lw(T1, 32, A2)
    a.lui(T2, 0xA944)
    a.lw(T6, 0, T5)
    a.ori(T2, T2, 0x1508)
    a.bne(T1, T2, "sprite")
    a.andi(T7, A1, 0xFFFF)
    a.addiu(T2, ZR, 27)
    a.beq(T7, T2, "use27")
    a.addiu(T2, ZR, 28)
    a.bne(T7, T2, "noslot")
    a.addiu(T3, T5, 24)
    a.beq(ZR, ZR, "have_slot")
    a.nop()
    a.label("use27")
    a.addiu(T3, T5, 16)
    a.beq(ZR, ZR, "have_slot")
    a.nop()
    a.label("noslot")
    a.addiu(T3, ZR, 0)
    a.label("have_slot")
    a.beq(T3, ZR, "need_up")
    a.andi(T1, T1, 0x3FFF)
    a.lw(T2, 0, T3)
    a.sll(T0, T6, 16)
    a.addu(T0, T0, T1)
    # Page and DBP match, but a new block page must still upload once.
    a.bne(T2, T0, "need_up")
    a.lw(T2, 4, T3)
    a.lui(A2, BLOCK_TMX >> 16)
    a.lw(A2, BLOCK_TMX & 0xFFFF, A2)
    a.bne(T6, ZR, "keep_live")
    a.nop()
    a.addiu(A2, ZR, 0)
    a.label("keep_live")
    a.beq(T2, A2, "sprite")
    a.nop()
    a.label("need_up")
    a.lw(T2, 8, A0)
    a.lw(T0, 0, T2)
    a.lw(T2, 4, T2)
    a.subu(T2, T2, T0)
    a.sltiu(T2, T2, 336)
    a.bne(T2, ZR, "fail")
    a.lw(T2, 0, A0)
    a.sll(T0, T7, 5)
    a.addu(T2, T2, T0)
    a.lw(T0, 16, T2)
    a.sw(T0, 56, SP)
    a.sw(T3, 60, SP)
    a.sw(T6, 64, SP)
    a.sw(T1, 68, SP)
    a.bne(T6, ZR, "pix1")
    a.sw(T5, 72, SP)
    a.addiu(A2, T4, 0x240)
    a.beq(ZR, ZR, "call_up")
    a.andi(A3, T1, 0x3FFF)
    a.label("pix1")
    a.lui(T7, BLOCK_TMX >> 16)
    a.lw(T7, BLOCK_TMX & 0xFFFF, T7)
    a.beq(T7, ZR, "pix1_combined")
    a.nop()
    a.beq(ZR, ZR, "pix1_have")
    a.addiu(A2, T7, 0x240)
    a.label("pix1_combined")
    a.lui(T2, 8)
    a.addiu(T2, T2, 0x1000)
    a.addu(T2, T4, T2)
    a.addiu(A2, T2, 0x240)
    a.label("pix1_have")
    a.andi(A3, T1, 0x3FFF)
    a.label("call_up")
    a.addiu(T0, ZR, 16)
    a.addiu(T1, ZR, 0)
    a.addiu(T2, ZR, 0)
    a.addiu(T3, ZR, 1024)
    a.sw(T3, 0, SP)
    a.addiu(T6, ZR, 0x14)
    a.jal(0x3FD3C0)
    a.sb(T6, 8, SP)
    a.ld(A0, 24, SP)
    a.ld(A1, 32, SP)
    a.lw(T2, 0, A0)
    a.andi(T7, A1, 0xFFFF)
    a.sll(T0, T7, 5)
    a.addu(T2, T2, T0)
    a.lw(T2, 16, T2)
    a.lw(T0, 56, SP)
    a.beq(T2, T0, "fail")
    a.lw(T5, 72, SP)
    a.lw(T3, 60, SP)
    a.beq(T3, ZR, "sprite")
    a.lw(T1, 68, SP)
    a.lw(T6, 64, SP)
    a.sll(T6, T6, 16)
    a.addu(T1, T1, T6)
    a.sw(T1, 0, T3)
    a.lui(T7, BLOCK_TMX >> 16)
    a.lw(T7, BLOCK_TMX & 0xFFFF, T7)
    a.bne(T6, ZR, "store_base")
    a.nop()
    a.addiu(T7, ZR, 0)
    a.label("store_base")
    a.beq(ZR, ZR, "sprite")
    a.sw(T7, 4, T3)
    a.label("sprite")
    a.ld(A0, 24, SP)
    a.ld(A1, 32, SP)
    a.ld(A2, 40, SP)
    a.jal(0x3FF3B0)
    a.ld(A3, 48, SP)
    a.ld(RA, 16, SP)
    a.jr(RA)
    a.addiu(SP, SP, 80)
    a.label("fail")
    a.ld(RA, 16, SP)
    a.jr(RA)
    a.addiu(SP, SP, 80)
    # Selector 3: the original page-0 upload has returned. Register this bucket
    # only when both pixel REFs are on its chain.
    a.label("preamble")
    a.addiu(SP, SP, -32)
    a.sw(24, 16, SP)
    a.sw(A0, 20, SP)
    a.sw(A1, 24, SP)
    a.jal(0x3FE1B0)
    a.sw(A2, 28, SP)
    a.lw(A0, 20, SP)
    a.lw(A1, 24, SP)
    a.lw(A2, 28, SP)
    a.lw(T0, 0, A0)
    a.andi(T1, A1, 0xFFFF)
    a.sll(T1, T1, 5)
    a.addu(T0, T0, T1)
    a.addiu(T2, A2, 0x240)
    a.lui(T4, 8)
    a.addiu(T4, T4, -16)
    a.addu(T3, T2, T4)
    a.addiu(T6, ZR, 0)
    a.addiu(T7, ZR, 0)
    a.label("walk")
    a.sltiu(T1, T7, 512)
    a.beq(T1, ZR, "walk_done")
    a.addiu(T7, T7, 1)
    a.lw(T5, 0, T0)
    a.andi(T4, T5, 0xFFFF)
    a.srl(T5, T5, 24)
    a.addiu(T1, ZR, 0x70)
    a.beq(T5, T1, "walk_done")
    a.lw(T1, 4, T0)
    a.addiu(T1, ZR, 0x20)
    a.bne(T5, T1, "not_next")
    a.lw(T1, 4, T0)
    a.beq(T1, ZR, "walk_done")
    a.nop()
    a.addiu(T0, T1, 0)
    a.beq(ZR, ZR, "walk")
    a.nop()
    a.label("not_next")
    a.addiu(T1, ZR, 0x30)
    a.bne(T5, T1, "not_ref")
    a.lw(T1, 4, T0)
    a.addiu(T5, ZR, 32767)
    a.bne(T4, T5, "try_one")
    a.nop()
    a.bne(T1, T2, "try_one")
    a.nop()
    a.ori(T6, T6, 1)
    a.label("try_one")
    a.addiu(T5, ZR, 1)
    a.bne(T4, T5, "step_ref")
    a.nop()
    a.bne(T1, T3, "step_ref")
    a.nop()
    a.ori(T6, T6, 2)
    a.label("step_ref")
    a.addiu(T0, T0, 16)
    a.beq(ZR, ZR, "walk")
    a.nop()
    a.label("not_ref")
    a.addiu(T1, ZR, 0x10)
    a.bne(T5, T1, "walk_done")
    a.nop()
    a.sll(T4, T4, 4)
    a.addiu(T0, T0, 16)
    a.addu(T0, T0, T4)
    a.beq(ZR, ZR, "walk")
    a.nop()
    a.label("walk_done")
    a.lw(A1, 24, SP)
    a.lui(T5, 0x5A)
    a.lw(T4, 0x4550, T5)
    a.bne(T4, ZR, "pre_have")
    a.nop()
    a.lw(T4, 0x4B54, T5)
    a.label("pre_have")
    a.beq(T4, ZR, "pre_ret")
    a.lui(T5, 0x10)
    a.addiu(T5, T5, 0x1E00)
    a.addu(T4, T4, T5)
    a.andi(T5, A1, 0xFFFF)
    a.addiu(T0, ZR, 27)
    a.beq(T5, T0, "slot27")
    a.addiu(T0, ZR, 28)
    a.bne(T5, T0, "pre_ret")
    a.addiu(T4, T4, 24)
    a.beq(ZR, ZR, "store_slot")
    a.nop()
    a.label("slot27")
    a.addiu(T4, T4, 16)
    a.label("store_slot")
    a.addiu(T1, ZR, 3)
    a.bne(T6, T1, "wipe")
    a.addiu(T0, ZR, 0x1508)
    a.sw(T0, 0, T4)
    a.beq(ZR, ZR, "pre_ret")
    a.sw(ZR, 4, T4)
    a.label("wipe")
    a.sw(ZR, 0, T4)
    a.sw(ZR, 4, T4)
    a.label("pre_ret")
    a.lw(24, 16, SP)
    a.addiu(RA, 24, 0)
    a.jr(RA)
    a.addiu(SP, SP, 32)
    # ponytail: a different slot allocates another page and leaves the previous one.
    # Free it with the script block when a third resident page would stay.
    a.label("install")
    a.lui(T0, BLOCK_TMX >> 16)
    a.lw(T1, BLOCK_PENDING & 0xFFFF, T0)
    a.bne(T1, ZR, "install_publish")
    a.nop()
    # Descriptor is 0x5978B0 + index * 0xA8 + selection * 0x18. Chunk is the u16 at +0x10.
    a.lh(T6, -0x47E2, GP)
    a.lui(T4, 0x0052)
    a.addiu(T4, T4, 0x5B98)
    a.addu(T4, T4, T6)
    a.lbu(T5, 0, T4)
    a.sll(T4, T6, 7)
    a.sll(T7, T6, 5)
    a.addu(T4, T4, T7)
    a.sll(T7, T6, 3)
    a.addu(T4, T4, T7)
    a.sll(T7, T5, 4)
    a.sll(T5, T5, 3)
    a.addu(T5, T7, T5)
    a.addu(T4, T4, T5)
    a.lui(T5, 0x0059)
    a.addiu(T5, T5, 0x78B0)
    a.addu(T4, T4, T5)
    a.lh(T6, 0x10, T4)
    a.addiu(T7, ZR, 0)
    for index, (chunk, slot) in enumerate(CHUNK_SLOTS.items()):
        a.addiu(T5, ZR, chunk)
        a.bne(T6, T5, f"slot_miss_{index}")
        a.nop()
        a.beq(ZR, ZR, "install_slot")
        a.addiu(T7, ZR, slot)
        a.label(f"slot_miss_{index}")
    a.label("install_slot")
    a.lw(T1, MARKER_ADDR & 0xFFFF, T0)
    a.bne(T1, T7, "install_switch")
    a.lw(T2, BLOCK_TMX & 0xFFFF, T0)
    a.bne(T2, ZR, "install_leave")
    a.nop()
    a.label("install_switch")
    # The allocator clobbers T7. Pending is 0 on this path, so it holds the slot across the call.
    a.sw(T7, BLOCK_PENDING & 0xFFFF, T0)
    a.label("install_alloc")
    a.lui(A0, 8)
    a.jal(0x101740)
    a.addiu(A0, A0, 0x5000)
    a.beq(V0, ZR, "install_fail")
    a.addu(T2, V0, ZR)
    a.sb(ZR, -0x4B94, GP)
    # 0x403C78 clobbers caller-saved registers, so the pointer is stored first.
    a.lui(T0, BLOCK_TMX >> 16)
    a.lw(T7, BLOCK_PENDING & 0xFFFF, T0)
    a.sw(T2, BLOCK_PENDING & 0xFFFF, T0)
    a.sll(T5, T7, 8)
    a.sll(T4, T7, 5)
    a.addu(T5, T5, T4)
    a.sll(T4, T7, 3)
    a.addu(T5, T5, T4)
    a.sll(T4, T7, 2)
    a.addu(T5, T5, T4)
    a.lui(A0, SEVEN_LBA >> 16)
    a.ori(A0, A0, SEVEN_LBA & 0xFFFF)
    a.addu(A0, A0, T5)
    a.addiu(A1, ZR, READ_SECTORS)
    a.addu(A2, T2, ZR)
    a.lui(A3, 0x5F)
    a.jal(0x403C78)
    a.addiu(A3, A3, -0x3A88)
    a.beq(V0, ZR, "install_fail")
    a.nop()
    a.lh(T7, -0x47E4, GP)
    a.addiu(T1, ZR, 5)
    a.addiu(T2, ZR, 8)
    a.beql(T7, T2, "install_rewind")
    a.addiu(T1, ZR, 7)
    a.label("install_rewind")
    a.beq(ZR, ZR, "install_leave")
    a.sh(T1, -0x47E4, GP)
    a.label("install_fail")
    a.lui(T0, BLOCK_TMX >> 16)
    a.sw(ZR, BLOCK_PENDING & 0xFFFF, T0)
    a.addiu(T1, ZR, 2)
    a.beq(ZR, ZR, "install_leave")
    a.sb(T1, -0x4B94, GP)
    a.label("install_publish")
    # The page is the first 0x81000 bytes. The next 16 KiB is the cell table.
    a.lui(T2, 8)
    a.addiu(T2, T2, 0x1000)
    a.addu(T2, T1, T2)
    a.addiu(T4, T0, TOKMAP_BASE & 0xFFFF)
    a.addiu(T5, ZR, 0)
    a.addiu(T3, ZR, TOKMAP_COUNT)
    a.label("copy_map")
    a.lw(T6, 0, T2)
    a.sw(T6, 0, T4)
    a.addiu(T2, T2, 4)
    a.addiu(T4, T4, 4)
    a.addiu(T5, T5, 1)
    a.bne(T5, T3, "copy_map")
    a.nop()
    a.addiu(T2, ZR, 1)
    a.sw(T2, TOKMAP_READY & 0xFFFF, T0)
    a.sw(T1, BLOCK_TMX & 0xFFFF, T0)
    a.sw(ZR, BLOCK_PENDING & 0xFFFF, T0)
    a.label("install_leave")
    # Queue success has already rewound to 5 or 7. Nine-step is 7 or 8.
    a.lh(T7, -0x47E4, GP)
    a.sltiu(T1, T7, 7)
    a.beq(T1, ZR, "install_nine")
    a.nop()
    a.j(LOAD_EPILOGUE)
    a.nop()
    a.label("install_nine")
    a.j(NINE_EPILOGUE)
    a.nop()
    words = a.done()
    INSTALL_OFF = CODE_OFF + 4 * a.labels["install"]
    if len(words) * 4 > DATA_OFF - CODE_OFF:
        raise ValueError(f"pad is {len(words) * 4} bytes")
    return struct.pack("<" + "I" * len(words), *words)


def build_cave():
    # j/jr only. jal here would replace the caller's ra before the function saves it.
    # The nine-step bridge's first load is also the delay of the nobase return.
    a = Asm()
    a.label("sample")
    a.addiu(V1, ZR, 0)
    a.label("common")
    a.addiu(24, RA, 0)
    a.label("font")
    a.lw(T0, -0x55A0, GP)
    a.beq(T0, ZR, "nobase")
    a.label("pad")
    a.lui(25, 0x10)
    a.addiu(25, 25, 0x1600)
    a.addu(25, T0, 25)
    a.jr(25)
    a.nop()
    a.label("submit")
    a.beq(ZR, ZR, "common")
    a.addiu(V1, ZR, 1)
    a.label("clear")
    a.sd(S1, 16, SP)
    a.beq(ZR, ZR, "common")
    a.addiu(V1, ZR, 2)
    a.label("preamble")
    a.addiu(V1, ZR, 3)
    a.beq(ZR, ZR, "font")
    a.addiu(24, 25, 0)
    a.label("nobase")
    a.bne(V1, ZR, "nobase_ns")
    a.addiu(T0, ZR, 2)
    a.j(0x2A3C68)
    a.andi(A2, A1, 0xFFFF)
    a.label("nobase_ns")
    a.beq(V1, T0, "nobase_clear")
    a.addiu(RA, 24, 0)
    a.jr(RA)
    a.label("nine")
    a.lw(T0, -0x55A0, GP)
    a.bnel(T0, ZR, "pad")
    a.addiu(V1, ZR, 5)
    a.j(NINE_EPILOGUE)
    a.nop()
    a.label("nobase_clear")
    a.j(0x2A3CA8)
    a.nop()
    words = a.done()
    if len(words) > 31 or a.labels["nine"] != a.labels["nobase_ns"] + 3 or a.labels["pad"] != a.labels["font"] + 2:
        raise ValueError(f"cave is {len(words)} instructions")
    return words, {name: CAVE + 4 * at for name, at in a.labels.items()}


def build_opening(nobase):
    # Replaces the 16-instruction opening sampler. Callers still jal 0x3BD998.
    # The preamble entry is reached by j from 0x3BD990, so ra is still the caller's.
    a = Asm()
    a.beq(ZR, ZR, "join")
    a.addiu(V1, ZR, 0)
    a.label("submit")
    a.beq(ZR, ZR, "join")
    a.addiu(V1, ZR, 1)
    a.label("preamble")
    a.addiu(V1, ZR, 3)
    a.label("join")
    a.addiu(24, RA, 0)
    a.lui(25, 0x5A)
    a.lw(T0, 0x4B54, 25)
    a.beq(T0, ZR, "miss")
    a.lui(25, 0x10)
    a.addiu(25, 25, 0x1600)
    a.addu(25, T0, 25)
    a.jr(25)
    a.nop()
    a.label("miss")
    a.j(nobase)
    a.addiu(V0, ZR, 42)
    words = a.done()
    if len(words) > 16:
        raise ValueError(f"opening sampler is {len(words)} instructions")
    if a.labels["submit"] != 2 or OPEN_SAMPLE + 4 * a.labels["preamble"] != OPEN_PREAMBLE:
        raise ValueError("opening entries moved")
    return words


