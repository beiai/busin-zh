"""Delay-slot MIPS interpreter for the font patch, and the checks that run it."""
import struct

from busin.disc import BIN, ELF_LBA, FILE_BASE, read_user
from busin.patch.asm import A0, A1, A2, A3, Asm, GP, RA, S1, SP, T0, T1, T2, T3, T5, V0, V1
from busin.patch.font_paging import (
    BLOCK_PENDING, BLOCK_TMX, CAVE, CHUNK_BYTES, CODE_OFF, DATA_OFF, GP_TMX, LOAD_BRIDGE,
    LOAD_EPILOGUE, LOAD_MISS, MARKER_ADDR, NINE_EPILOGUE, OPEN_PREAMBLE, OPEN_SAMPLE,
    OPEN_SUBMIT, OPEN_TMX, OPEN_UPLOAD_TAIL, SLOT2_MARK, TEX0, TOKMAP_BASE, TOKMAP_READY,
    build_bridge, build_cave, build_opening, hooks,
)

MASK32 = 0xFFFFFFFF
MASK64 = (1 << 64) - 1
QUEUE = 0x437800
BUCKETS = 0x200000
CURSOR = 0x300000
OBJ = 0x500000
CALLEE_SAVED = (RA, SP, *range(16, 24), 28, 30)  # ra, sp, s0-s7, gp, fp
CALLER_SAVED = (*range(1, 16), 24, 25)  # at, v0-v1, a0-a3, t0-t9
RETURN = 0x0BAD1F00


def sx(value):
    value &= MASK32
    return value - 0x100000000 if value & 0x80000000 else value


def execute(code, mem, reg, pc, stop, on_call, limit=20000, writable=None):
    """Run code ({address: word}) from pc, updating reg in place, until stop(pc).

    on_call(target, reg, link) decides each j (link False) and jal (link True):
    "skip" steps over it, "stop" ends the run, None follows it.
    writable(addr, size), when given, must allow every store.
    """
    hilo = [0, 0]

    def address(base, offset, size, store=False):
        addr = (base + offset) & MASK32
        # The R5900 raises an address error on unaligned word and doubleword access.
        if addr % size or addr + size > len(mem):
            raise AssertionError(f"access {addr:#x}+{size} at {pc:#x}")
        if store and writable is not None and not writable(addr, size):
            raise AssertionError(f"store {addr:#x}+{size} at {pc:#x}")
        return addr

    def perform(word):
        _perform(word)
        reg[0] = 0

    def _perform(word):
        o0, fn = word >> 26, word & 63
        a, b, c = (word >> 21) & 31, (word >> 16) & 31, (word >> 11) & 31
        im = word & 0xFFFF
        sm = im - 0x10000 if im & 0x8000 else im
        sa = (word >> 6) & 31
        if word == 0 or o0 in (1, 2, 3, 4, 5, 20, 21) or (o0 == 0 and fn in (8, 9)):
            return
        if o0 == 9:
            reg[b] = sx(reg[a] + sm)
        elif o0 == 15:
            reg[b] = sx(im << 16)
        elif o0 == 12:
            reg[b] = (reg[a] & MASK32) & im
        elif o0 == 13:
            reg[b] = sx((reg[a] & MASK32) | im)
        elif o0 == 11:
            reg[b] = 1 if (reg[a] & MASK32) < im else 0
        elif o0 == 10:
            reg[b] = int(reg[a] < sm)
        elif o0 == 33:
            reg[b] = struct.unpack_from("<h", mem, address(reg[a], sm, 2))[0]
        elif o0 == 37:
            reg[b] = struct.unpack_from("<H", mem, address(reg[a], sm, 2))[0]
        elif o0 == 35:
            reg[b] = sx(struct.unpack_from("<I", mem, address(reg[a], sm, 4))[0])
        elif o0 == 36:
            reg[b] = mem[address(reg[a], sm, 1)]
        elif o0 == 43:
            struct.pack_into("<I", mem, address(reg[a], sm, 4, True), reg[b] & MASK32)
        elif o0 == 40:
            mem[address(reg[a], sm, 1, True)] = reg[b] & 255
        elif o0 == 41:
            struct.pack_into("<H", mem, address(reg[a], sm, 2, True), reg[b] & 0xFFFF)
        elif o0 == 55:
            reg[b] = struct.unpack_from("<Q", mem, address(reg[a], sm, 8))[0]
        elif o0 == 63:
            struct.pack_into("<Q", mem, address(reg[a], sm, 8, True), reg[b] & MASK64)
        elif o0 == 0 and fn == 0:
            reg[c] = sx((reg[b] & MASK32) << sa)
        elif o0 == 0 and fn == 2:
            reg[c] = (reg[b] & MASK32) >> sa
        elif o0 == 0 and fn == 3:
            reg[c] = sx(reg[b]) >> sa
        elif o0 == 0 and fn in (33, 45):
            reg[c] = sx((reg[a] + reg[b]) & MASK32)
        elif o0 == 0 and fn == 35:
            reg[c] = sx((reg[a] - reg[b]) & MASK32)
        elif o0 == 0 and fn == 37:
            reg[c] = reg[a] | reg[b]
        elif o0 == 0 and fn == 43:
            reg[c] = int((reg[a] & MASK64) < (reg[b] & MASK64))
        elif o0 == 0 and fn == 16:
            reg[c] = sx(hilo[0])
        elif o0 == 0 and fn == 18:
            reg[c] = sx(hilo[1])
        elif o0 == 0 and fn == 24:
            prod = sx(reg[a]) * sx(reg[b])
            hilo[:] = [(prod >> 32) & MASK32, prod & MASK32]
        elif o0 == 0 and fn == 27:
            left, right = reg[a] & MASK32, reg[b] & MASK32
            hilo[:] = [left % right, left // right]
        elif o0 == 0 and fn == 0x3A:
            reg[c] = (reg[b] & MASK64) >> sa
        else:
            raise AssertionError(f"op {word:#x} at {pc:#x}")

    for _ in range(limit):
        if stop(pc):
            return
        if pc not in code:
            raise AssertionError(f"jumped outside traced code: {pc:#x}")
        op = code[pc]
        op0, fn = op >> 26, op & 63
        rs, rt = (op >> 21) & 31, (op >> 16) & 31
        imm = op & 0xFFFF
        simm = imm - 0x10000 if imm & 0x8000 else imm
        delay = code.get(pc + 4, 0)
        taken = reg[rs] == reg[rt] if op0 in (4, 20) else reg[rs] != reg[rt] if op0 in (5, 21) else None
        if op0 == 1:
            if rt not in (0, 1):
                raise AssertionError(f"unsupported REGIMM {op:#x} at {pc:#x}")
            taken = reg[rs] < 0 if rt == 0 else reg[rs] >= 0
        if op0 in (20, 21) and not taken:
            pc += 8
        elif op0 == 0 and fn == 8:
            # JR reads its target before the delay slot can change the register.
            target = reg[rs] & MASK32
            perform(delay)
            pc = target
        elif op0 in (2, 3):
            target = (op & 0x3FFFFFF) << 2
            if op0 == 3:
                reg[RA] = pc + 8
            perform(delay)
            action = on_call(target, reg, op0 == 3)
            if action == "stop":
                return
            pc = pc + 8 if action == "skip" else target
        elif op0 in (1, 4, 5, 20, 21):
            perform(delay)
            pc = pc + 4 + simm * 4 if taken else pc + 8
        else:
            perform(op)
            pc += 4
    raise AssertionError(f"code did not return, pc {pc:#x}")


def elf_code(code, va, size):
    off = va - FILE_BASE
    with BIN.open("rb") as src:
        blob = read_user(src, ELF_LBA + off // 2048, (off % 2048 + size + 2047) // 2048)
    for index in range(0, size, 4):
        code[va + index] = struct.unpack_from("<I", blob, off % 2048 + index)[0]


def junk_regs():
    """Registers the caller did not set hold values that are neither 0 nor a valid address."""
    return [0] + [0x0BAD0000 + 4 * index for index in range(1, 32)]


def clobber(reg, mem):
    """What a called function may leave behind: scratch registers and the stack below sp."""
    for index in CALLER_SAVED:
        reg[index] = 0x0DEAD000 + 4 * index
    sp = reg[SP] & MASK32
    mem[sp - 0x100:sp] = b"\xcc" * 0x100


def changed(before, after, keep):
    return [index for index in keep if after[index] != before[index]]


def pad_test(pad, mem, base):
    """Drive the pad's sample (0), submit (1) and clear (2) selectors directly."""
    data = base + DATA_OFF
    entry = base + CODE_OFF
    stack = 0x180000
    code = {entry + 4 * index: word for index, word in enumerate(struct.unpack(f"<{len(pad) // 4}I", pad))}
    page0, page1 = base + 0x240, base + 0x81240
    P0, P1 = 0x1508, 0x11508

    def writable(addr, size):
        return stack - 80 <= addr and addr + size <= stack or data <= addr and addr + size <= data + 32 \
            or TOKMAP_BASE <= addr and addr + size <= TOKMAP_READY + 4

    def cache():
        return list(struct.unpack_from("<8I", mem, data))

    def upload(bucket, pixels):
        return (QUEUE, bucket, pixels, 0x1508, 16, 0, 0, 1024, 1024, 0x14)

    def go(selector, a1=28, token=0x4000, space=4096, link=True, tex=TEX0, bucket=27):
        reg = junk_regs()
        reg[RA], reg[SP] = RETURN, stack
        reg[V1], reg[A0], reg[A1], reg[A2], reg[A3] = selector, QUEUE, a1, OBJ, 0x600000
        mem[stack - 0x100:stack] = b"\xcc" * 0x100
        if selector == 0:
            reg[A1] = token
        if selector == 2:
            reg[A0] = bucket
        before = list(reg)
        struct.pack_into("<Q", mem, OBJ + 32, tex)
        struct.pack_into("<II", mem, CURSOR, 0x400000, 0x400000 + space)
        for index in (26, 27, 28):
            struct.pack_into("<I", mem, BUCKETS + index * 32 + 16, 0x1111)
        calls = []

        def on_call(target, r, is_link):
            if not is_link:
                return None
            sp = r[SP] & MASK32
            calls.append((target, *(r[index] & MASK32 for index in (A0, A1, A2, A3, T0, T1, T2, T3)),
                          struct.unpack_from("<I", mem, sp)[0], mem[sp + 8]))
            if target == 0x3FD3C0 and link:
                struct.pack_into("<I", mem, BUCKETS + (r[A1] & 0xFFFF) * 32 + 16, 0x2222)
            clobber(r, mem)
            return "skip"

        execute(code, mem, reg, entry, lambda pc: pc in (RETURN, 0x2A3C68, 0x2A3CA8), on_call, 20000, writable)
        keep = CALLEE_SAVED + {0: (A0, A1, A3), 2: (A0, A1, A2, A3)}.get(selector, ())
        if changed(before, reg, keep):
            raise AssertionError(f"selector {selector} changed registers {changed(before, reg, keep)}")
        for call in calls:
            if call[0] == 0x3FF3B0 and call[1:5] != (QUEUE, a1, OBJ, 0x600000):
                raise AssertionError(f"sprite args {call}")
        return calls, reg[A2] & MASK32, cache()

    def targets(calls):
        return [call[0] for call in calls]

    mem[data:data + 32] = bytes(32)
    for token, cell, page in ((0x4000, 0, 1), (sx(0xDEAD46E3), 0x6E3, 1), (0x46E4, 0, 1),
                              (sx(0xBEEF05A5), 0x5A5, 0), (0x53, 0x53, 0), (0x4012, 0x12, 1),
                              (0x45FF, 0x5FF, 1), (0x4FFF, 0, 1), (0x5000, 0, 1)):
        calls, got, state = go(0, token=token)
        if calls or got != cell or state != [page, 1, 0, 0, 0, 0, 0, 0]:
            raise AssertionError(f"sample {token & MASK32:#x}: cell {got:#x} cache {state}")
    if struct.unpack_from("<I", mem, TOKMAP_READY)[0] != 1 \
            or struct.unpack_from("<I", mem, TOKMAP_BASE + 0x6E3 * 4)[0] != 0x6E3 \
            or struct.unpack_from("<I", mem, TOKMAP_BASE + 1764 * 4)[0] != 0:
        raise AssertionError("identity map was not written")
    struct.pack_into("<I", mem, TOKMAP_BASE + 0x12 * 4, 0x99)
    calls, got, state = go(0, token=0x4012)
    if calls or got != 0x99 or state[0] != 1:
        raise AssertionError(f"mapped cell {got:#x} cache {state}")
    struct.pack_into("<I", mem, TOKMAP_BASE + 0x12 * 4, 0x12)

    # Bucket 27 starts the frame and resets both slots; bucket 28 only its own.
    for bucket, kept in ((27, (2, 3)), (28, (2, 3, 4, 5))):
        mem[data:data + 32] = b"\xff" * 32
        go(2, bucket=bucket)
        want = [MASK32 if index in kept else 0 for index in range(8)]
        if cache() != want:
            raise AssertionError(f"clear {bucket} left {cache()}")

    mem[data:data + 32] = bytes(32)
    go(0, token=0x4000)
    steps = (
        ("first upload", dict(a1=28), [upload(28, page1)], [1, 1, 0, 0, 0, 0, P1, 0]),
        ("same page", dict(a1=28), [], [1, 1, 0, 0, 0, 0, P1, 0]),
        ("other bucket", dict(a1=27), [upload(27, page1)], [1, 1, 0, 0, P1, 0, P1, 0]),
        ("same page 27", dict(a1=27), [], [1, 1, 0, 0, P1, 0, P1, 0]),
        ("no slot", dict(a1=26), [upload(26, page1)], [1, 1, 0, 0, P1, 0, P1, 0]),
        ("no slot again", dict(a1=26), [upload(26, page1)], [1, 1, 0, 0, P1, 0, P1, 0]),
        ("back to page 0", dict(a1=28, token=0x10), [upload(28, page0)], [0, 1, 0, 0, P1, 0, P0, 0]),
        ("queue 335", dict(a1=28, token=0x4001, space=335), None, [1, 1, 0, 0, P1, 0, P0, 0]),
        ("queue 336", dict(a1=28, space=336), [upload(28, page1)], [1, 1, 0, 0, P1, 0, P1, 0]),
        ("unlinked", dict(a1=28, token=0x10, link=False), "upload only", [0, 1, 0, 0, P1, 0, P1, 0]),
        ("foreign TEX0", dict(a1=28, tex=0), [], [0, 1, 0, 0, P1, 0, P1, 0]),
    )
    for name, args, uploads, want in steps:
        if "token" in args:
            go(0, token=args.pop("token"))
        calls, _, state = go(1, **args)
        if uploads is None:
            ok = calls == []
        elif uploads == "upload only":
            ok = targets(calls) == [0x3FD3C0]
        else:
            ok = [call[1:] for call in calls if call[0] == 0x3FD3C0] == uploads and targets(calls)[-1:] == [0x3FF3B0] \
                and len(calls) == len(uploads) + 1
        if not ok or state != want:
            raise AssertionError(f"{name}: {calls} cache {state}")

    struct.pack_into("<I", mem, GP_TMX, 0)
    struct.pack_into("<I", mem, OPEN_TMX, base)
    mem[data:data + 32] = bytes(32)
    go(0, token=0x4012)
    calls, _, state = go(1, a1=27)
    if [call[1:] for call in calls[:1]] != [upload(27, page1)] or targets(calls) != [0x3FD3C0, 0x3FF3B0] \
            or state != [1, 1, 0, 0, P1, 0, 0, 0]:
        raise AssertionError(f"opening upload {calls} cache {state}")

    block = 0x480000
    struct.pack_into("<I", mem, BLOCK_TMX, block)
    go(0, token=0x4001)
    calls, _, state = go(1, a1=28)
    if [call[1:] for call in calls if call[0] == 0x3FD3C0] != [upload(28, block + 0x240)] \
            or state != [1, 1, 0, 0, P1, 0, P1, block]:
        raise AssertionError(f"block page {calls} cache {state}")
    calls, _, state = go(1, a1=28)
    if any(call[0] == 0x3FD3C0 for call in calls) or targets(calls) != [0x3FF3B0] or state[7] != block:
        raise AssertionError(f"block page uploaded again {calls}")
    go(0, token=0x10)
    calls, _, state = go(1, a1=28)
    if [call[1:] for call in calls if call[0] == 0x3FD3C0] != [upload(28, page0)] or state[7] != 0:
        raise AssertionError(f"page 0 followed the block pointer {calls} cache {state}")
    go(0, token=0x4001)
    calls, _, _ = go(1, a1=28)
    if [call[1:] for call in calls if call[0] == 0x3FD3C0] != [upload(28, block + 0x240)]:
        raise AssertionError(f"return to block page {calls}")
    calls, _, _ = go(1, a1=28)
    if any(call[0] == 0x3FD3C0 for call in calls) or targets(calls) != [0x3FF3B0]:
        raise AssertionError("block page did not stay up")
    struct.pack_into("<I", mem, BLOCK_TMX, 0)
    calls, _, state = go(1, a1=28)
    if [call[1:] for call in calls if call[0] == 0x3FD3C0] != [upload(28, page1)] or state[7] != 0:
        raise AssertionError(f"combined page not restored {calls} cache {state}")
    calls, _, _ = go(1, a1=28)
    if any(call[0] == 0x3FD3C0 for call in calls) or targets(calls) != [0x3FF3B0]:
        raise AssertionError("combined page uploaded again")


def chain_test(words, labels, pad, mem, base):
    """Run hook, cave, and pad. The two entries must come back with the caller's ra."""
    code = {}
    elf_code(code, 0x2A3C60, 0x140)
    for va, _source, patched in hooks(labels):
        if va in code:
            code[va] = patched
    for index in range(32):
        mem[BUCKETS + index * 32 + 3] = 0x70
    for index, word in enumerate(words):
        code[CAVE + index * 4] = word
    for index, word in enumerate(struct.unpack(f"<{len(pad) // 4}I", pad)):
        code[base + CODE_OFF + index * 4] = word

    # Page-0 upload chains for the registration walk: (qwc, tag id, address) after a NEXT
    # from the bucket head, then END. True when both page-0 REFs must be found.
    END = (0, 0x70, 0)
    chains = {
        "both": True, "reversed": True, "cnt": True, "stray": True, "long": True,
        "one": False, "second": False, "clut": False, "moved": False, "short": False, "wide": False,
        "unknown": False, "none": False, "zero-next": False, "loop": False, "too-long": False,
    }
    # The walk stops after 512 tags, counting the bucket head.
    filler = [(0, 0x30, 0), (0, 0x10, 0)] * 255

    def plant_refs(reg, how):
        bucket = reg[A1] & 0xFFFF
        pix = (reg[A2] + 0x240) & MASK32
        tail = pix + 32767 * 16
        head = BUCKETS + bucket * 32
        pkt = 0x310000
        first, second = (32767, 0x30, pix), (1, 0x30, tail)
        tags = {
            "both": [first, second],
            "reversed": [second, first],
            # CNT payload is skipped; the END inside it must not stop the walk.
            "cnt": [(2, 0x10, 0), END, END, first, second],
            "stray": [(1, 0x30, pix + 16), first, second],
            "long": [first, *filler[:509], second],
            "one": [first],
            "second": [second],
            "clut": [(4, 0x30, (reg[A2] + 0x40) & MASK32)],
            "moved": [(32767, 0x30, pix + 16), second],
            "short": [(100, 0x30, pix), second],
            "wide": [first, (32767, 0x30, tail)],
            "unknown": [(0, 0x50, 0), first, second],
            "loop": [(0, 0x20, pkt)],
            "too-long": [first, *filler, second],
        }.get(how, [])

        def tag(at, qwc, ident, addr):
            struct.pack_into("<H", mem, at, qwc)
            mem[at + 2] = 0
            mem[at + 3] = ident
            struct.pack_into("<I", mem, at + 4, addr & MASK32)

        for index, item in enumerate([*tags, END]):
            tag(pkt + 16 * index, *item)
        if how == "zero-next":
            # NEXT to address 0 ends the walk; it must not read a chain there.
            for index, item in enumerate([first, second, END]):
                tag(16 * index, *item)
        tag(head, *(END if how == "none" else (0, 0x20, 0) if how == "zero-next" else (0, 0x20, pkt)))

    stack = 0x180030

    def writable(addr, size):
        cache = base + DATA_OFF
        return stack - 0x100 <= addr and addr + size <= stack or cache <= addr and addr + size <= cache + 32 \
            or TOKMAP_BASE <= addr and addr + size <= TOKMAP_READY + 4

    def run(start, stop_at, caller, how="both", a0=7, a1=sx(0xDEAD4000), v1=None):
        reg = junk_regs()
        reg[RA] = caller
        reg[SP] = stack
        reg[28] = 0x5A9AF0
        reg[S1] = 0x1357
        reg[A0], reg[A1], reg[A2], reg[A3] = a0, a1, 0x500000, 0x600000
        if v1 is not None:
            reg[V1] = v1
        mem[stack - 0x100:stack] = b"\xcc" * 0x100
        before = list(reg)
        seen = []
        calls = []

        def stop(pc):
            if pc == stop_at or (pc == caller and pc != start):
                return True
            if pc in (0x2A3C68, 0x2A3CA8) and not seen:
                seen.append((reg[RA] & MASK32, reg[A0] & MASK32, reg[A2] & MASK32, reg[V0] & MASK32, reg[SP] & MASK32))
            return False

        def on_call(target, r, is_link):
            if not is_link:
                return "stop" if target in (0x100F88, 0x3FE1B0) else None
            if target not in (0x3FE1B0, 0x3FD3C0, 0x3FF3B0, 0x3FFD68):
                return None
            if target != 0x3FFD68:
                calls.append(target)
            if target == 0x3FE1B0:
                plant_refs(r, how)
            if target == 0x3FD3C0:
                at = BUCKETS + (r[A1] & 0xFFFF) * 32 + 16
                struct.pack_into("<I", mem, at, struct.unpack_from("<I", mem, at)[0] + 1)
            clobber(r, mem)
            return "skip"

        execute(code, mem, reg, start, stop, on_call, writable=writable)
        moved = changed(before, reg, CALLEE_SAVED[2:])
        if moved:
            raise AssertionError(f"run from {start:#x} changed saved registers {moved}")
        return reg, (seen[0] if seen else None), calls

    probe = Asm()
    probe.lui(T5, 0x2A)
    probe.jr(T5)
    probe.addiu(T5, T5, 0x3C68)
    for index, word in enumerate(probe.done()):
        code[0x110000 + index * 4] = word
    reg, seen, calls = run(0x110000, 0x2A0000, 0x11110000)
    if seen is not None or calls or (reg[T5] & MASK32) != 0x2A3C68:
        raise AssertionError("JR target was read after its delay slot")

    def slot(index):
        return struct.unpack_from("<I", mem, base + DATA_OFF + index * 4)[0]

    def returned(reg, ra):
        return (reg[RA] & MASK32) == ra and (reg[SP] & MASK32) == stack

    def submit(start, stop_at, caller, bucket):
        # v1 = 0 is the sample selector; the entry must set its own.
        reg, seen, calls = run(start, stop_at, caller, a0=QUEUE, a1=bucket, v1=0)
        if seen is not None:
            raise AssertionError(f"submit from {start:#x} left through {seen}")
        return reg, calls

    struct.pack_into("<I", mem, GP_TMX, base)
    mem[base + DATA_OFF:base + DATA_OFF + 32] = bytes(32)
    reg, seen, calls = run(0x2A3C60, None, 0x11110000)
    if seen != (0x11110000, 7, 0, 42, 0x180030) or not returned(reg, 0x11110000) or calls:
        raise AssertionError(f"sample return {seen} ra {reg[RA]:#x} {calls}")
    reg, seen, calls = run(0x2A3CA0, 0x22220000, 0x22220000, a0=28)
    if seen[0] != 0x22220000 or seen[4] != 0x180000 or not returned(reg, 0x22220000) or calls != [0x3FE1B0]:
        raise AssertionError(f"clear return {seen} ra {reg[RA]:#x} sp {reg[SP]:#x} {calls}")
    if slot(6) != 0x1508:
        raise AssertionError(f"page0 ref was not registered {slot(6):#x}")
    saved = struct.unpack_from("<Q", mem, 0x180010)[0]
    if saved != 0x1357:
        raise AssertionError(f"s1 slot {saved:#x}")
    for how, registers in chains.items():
        mem[base + DATA_OFF:base + DATA_OFF + 32] = b"\xff" * 32
        reg, _, calls = run(0x2A3CA0, 0x22220000, 0x22220000, how=how, a0=28)
        want = 0x1508 if registers else 0
        if slot(6) != want or not returned(reg, 0x22220000) or calls != [0x3FE1B0]:
            raise AssertionError(f"{how} chain left slot {slot(6):#x}, want {want:#x}")
    mem[0:48] = bytes(48)
    struct.pack_into("<I", mem, base + DATA_OFF + 16, 0x1508)
    run(0x2A3CA0, 0x22220000, 0x22220000, how="none", a0=28)
    if slot(4) != 0x1508 or slot(6) != 0:
        raise AssertionError(f"frame reset clobbered buckets {slot(4):#x} {slot(6):#x}")
    struct.pack_into("<I", mem, GP_TMX, base)
    mem[base + DATA_OFF:base + DATA_OFF + 32] = bytes(32)
    reg, calls = submit(0x2A3D84, 0x2A3D8C, 0x33330000, 28)
    if not returned(reg, 0x2A3D8C) or calls != [0x3FF3B0]:
        raise AssertionError(f"submit return ra {reg[RA]:#x} {calls}")

    # Font pointer 0: the trampoline returns at once, without sampling, uploading or drawing.
    struct.pack_into("<I", mem, GP_TMX, 0)
    reg, seen, _ = run(0x2A3C60, None, 0x11110000, a1=sx(0xDEADFFFF))
    if seen != (0x11110000, 7, 0xFFFF, 42, 0x180030) or not returned(reg, 0x11110000):
        raise AssertionError(f"nobase sample {seen} ra {reg[RA]:#x}")
    reg, seen, _ = run(0x2A3CA0, None, 0x22220000)
    if seen[0] != 0x22220000 or seen[4] != 0x180000 or not returned(reg, 0x22220000):
        raise AssertionError(f"nobase clear {seen} ra {reg[RA]:#x}")
    reg, calls = submit(0x2A3D84, 0x2A3D8C, 0x33330000, 28)
    if not returned(reg, 0x2A3D8C) or calls:
        raise AssertionError(f"nobase submit ra {reg[RA]:#x} {calls}")

    opening = build_opening(labels["nobase"])
    for index, word in enumerate(opening):
        code[OPEN_SAMPLE + index * 4] = word
    struct.pack_into("<I", mem, OPEN_TMX, base)
    mem[base + DATA_OFF:base + DATA_OFF + 32] = bytes(32)
    if code[0x3BD9B4] != 0x8F284B54:
        raise AssertionError(f"opening sampler reads {code.get(0x3BD9B4):#x}")
    reg, calls = submit(OPEN_SUBMIT, None, 0x3BDAC0, 27)
    if not returned(reg, 0x3BDAC0) or calls != [0x3FF3B0]:
        raise AssertionError(f"opening submit before sampling {calls} ra {reg[RA]:#x}")
    reg, seen, calls = run(OPEN_SAMPLE, None, 0x3BDA54)
    if seen != (0x3BDA54, 7, 0, 42, 0x180030) or calls or (slot(0), slot(1)) != (1, 1):
        raise AssertionError(f"opening trampoline {seen} {calls} {slot(0), slot(1)}")
    reg, calls = submit(OPEN_SUBMIT, None, 0x3BDAC0, 27)
    if not returned(reg, 0x3BDAC0) or calls != [0x3FD3C0, 0x3FF3B0] or slot(4) != 0x11508:
        raise AssertionError(f"opening submit {calls} slot {slot(4):#x} ra {reg[RA]:#x}")
    # The opening re-uploads page 0 into bucket 27 every frame; a page-1 record
    # left by the previous frame must not survive it.
    elf_code(code, 0x3BD950, 0x48)
    code[OPEN_UPLOAD_TAIL] = 0x08000000 | (OPEN_PREAMBLE >> 2)
    for how, want in (("both", 0x1508), ("one", 0), ("none", 0)):
        struct.pack_into("<I", mem, base + DATA_OFF + 16, 0x11508)
        reg, _, calls = run(0x3BD950, 0x44440000, 0x44440000, how=how, a0=27)
        if slot(4) != want or calls != [0x3FE1B0] or not returned(reg, 0x44440000):
            raise AssertionError(f"opening page-0 upload {how}: slot {slot(4):#x} {calls} ra {reg[RA]:#x} sp {reg[SP]:#x}")
    # Only buckets 27 and 28 have slots.
    mem[base + DATA_OFF:base + DATA_OFF + 32] = b"\xff" * 32
    run(0x3BD950, 0x44440000, 0x44440000, a0=26)
    if mem[base + DATA_OFF:base + DATA_OFF + 32] != b"\xff" * 32:
        raise AssertionError(f"bucket 26 wrote the cache {[slot(index) for index in range(8)]}")
    struct.pack_into("<I", mem, OPEN_TMX, 0)
    reg, seen, calls = run(OPEN_SAMPLE, None, 0x3BDA54)
    if seen != (0x3BDA54, 7, 0x4000, 42, 0x180030) or calls:
        raise AssertionError(f"opening miss {seen} {calls}")
    reg, calls = submit(OPEN_SUBMIT, None, 0x3BDAC0, 27)
    if not returned(reg, 0x3BDAC0) or calls:
        raise AssertionError(f"opening submit miss {calls} ra {reg[RA]:#x}")
    struct.pack_into("<I", mem, GP_TMX, base)


def layout_test():
    """Run the original engine's width/draw filters and control handlers with zero-width filler."""
    from busin.text import FILL, PAD

    code, mem = {}, bytearray(0x200000)
    stack, state = 0x180000, 0x1000
    elf_code(code, 0x29ACD8, 0xCA0)

    def no_call(target, reg, link):
        raise AssertionError(f"filler called {target:#x}")

    for start, token_reg in ((0x29ACD8, A2), (0x29AE28, A1), (0x29AED0, T0), (0x29B2F0, A1),
                             (0x29B3B0, A2), (0x29B560, A1), (0x29B920, A1)):
        reg = junk_regs()
        reg[SP], reg[RA] = stack, RETURN
        reg[A0], reg[A1], reg[A2], reg[A3] = state, state + 0x200, state + 0x202, state + 0x204
        reg[token_reg] = FILL
        mem[state:state + 0x300] = b"\xa5" * 0x300
        execute(code, mem, reg, start, lambda pc: pc == RETURN, no_call)
        assert mem[state:state + 0x300] == b"\xa5" * 0x300, f"filler changed control state at {start:#x}"

    # All five body drawing modes and the body width scan reject filler, but accept real spaces.
    for start, skipped, token_reg in ((0x29C2EC, 0x29C51C, 20), (0x29DB4C, 0x29DDC0, 20),
                                     (0x29F61C, 0x29F9E8, 20), (0x2A1284, 0x2A164C, 20),
                                     (0x2A3310, 0x2A3484, 19), (0x29D310, 0x29D39C, 20)):
        elf_code(code, start, 16)
        for token in (FILL, PAD, 0x4001):
            reg = junk_regs()
            reg[SP], reg[19], reg[token_reg] = stack, state, token
            seen = []
            execute(code, mem, reg, start, lambda pc: seen.append(pc) or pc in (skipped, start + 16), no_call)
            assert seen[-1] == (skipped if token == FILL else start + 16), f"layout filter {start:#x} token {token:#x}"

    # The opening counts translated glyphs, including explicit spaces, without odd-cell rounding.
    elf_code(code, 0x3B6CAC, 0x98)
    source = [0x4001] * 9 + [FILL] * 3 + [0xFFFE] + [0x4001, PAD, 0x4001, 0x4001] + [FILL] * 3 + [0xFFFF]
    mem[state:state + len(source) * 2] = struct.pack(f">{len(source)}H", *source)
    reg = junk_regs()
    reg[SP], reg[S1], reg[A1], reg[30] = stack, state, 0, 0
    struct.pack_into("<I", mem, stack + 0x220, stack + 0x10)
    execute(code, mem, reg, 0x3B6CAC, lambda pc: pc == 0x3B6D44, no_call)
    assert struct.unpack_from("<HH", mem, stack + 0x10) == (9, 4)

    for start, active, skipped in ((0x3B6DD0, 0x3B6E04, 0x3B6F34), (0x3B6FD0, 0x3B7000, 0x3B71E4),
                                   (0x3B7270, 0x3B7298, 0x3B7384), (0x3B7438, 0x3B7464, 0x3B753C)):
        elf_code(code, start, active - start)
        for token in (FILL, PAD, 0x4001):
            reg = junk_regs()
            reg[SP], reg[16], reg[25] = stack, token, state - 0x33E0
            seen = []
            execute(code, mem, reg, start, lambda pc: seen.append(pc) or pc in (skipped, active), no_call)
            assert seen[-1] == (skipped if token == FILL else active), f"opening filter {start:#x} token {token:#x}"


def install_test(pad, mem, base):
    """Block read completion allocates one page, queues slot 0, and publishes it on the next success."""
    gp_base = 0x5A9AF0
    status, step = gp_base - 0x4B94, gp_base - 0x47E4
    buf = 0x480000
    code = {}
    for index, word in enumerate(struct.unpack(f"<{len(pad) // 4}I", pad)):
        code[base + CODE_OFF + 4 * index] = word
    cave, labels = build_cave()
    for index, word in enumerate(cave):
        code[CAVE + 4 * index] = word
    for index, word in enumerate(build_bridge()):
        code[LOAD_BRIDGE + 4 * index] = word
    code[LOAD_MISS] = 0x08000000 | (LOAD_EPILOGUE >> 2)
    code[LOAD_MISS + 4] = 0

    def writable(addr, size):
        return status <= addr and addr + size <= status + 1 or step <= addr and addr + size <= step + 2 \
            or TOKMAP_BASE <= addr and addr + size <= TOKMAP_READY + 4

    def run(entry, alloc=buf, queued=1, limit=40000):
        reg = junk_regs()
        reg[SP], reg[GP] = 0x180000, gp_base
        saved = [reg[index] for index in (16, 17, 18)]
        calls = []

        def on_call(target, r, link):
            if target in code and link:
                raise AssertionError(f"followed a linked call {target:#x}")
            if target in (LOAD_EPILOGUE, NINE_EPILOGUE):
                calls.append((target, r[A0] & MASK32))
                return "stop"
            if target in code:
                return None
            calls.append((target, r[A0] & MASK32, r[A1] & MASK32, r[A2] & MASK32, r[A3] & MASK32))
            clobber(r, mem)
            if target == 0x101740:
                r[V0] = alloc
                return "skip"
            if target == 0x403C78:
                r[V0] = queued
                return "skip"
            raise AssertionError(f"unexpected call {target:#x}")

        execute(code, mem, reg, entry, lambda _pc: False, on_call, limit, writable)
        if [reg[index] for index in (16, 17, 18)] != saved:
            raise AssertionError("installer clobbered s0-s2")
        return calls, reg[25] & MASK32

    from busin.patch import font_paging
    install_at = base + font_paging.INSTALL_OFF

    struct.pack_into("<II", mem, BLOCK_TMX, 0, 0)
    struct.pack_into("<I", mem, TOKMAP_READY, 0)
    struct.pack_into("<I", mem, TOKMAP_BASE, 0xFFFFFFFF)
    mem[status] = 2
    struct.pack_into("<H", mem, step, 6)
    calls, _ = run(install_at, limit=200)
    if calls[:2] != [(0x101740, 0x85000, calls[0][2], calls[0][3], calls[0][4]),
                     (0x403C78, 284219, 266, buf, 0x5EC578)] or calls[-1][0] != LOAD_EPILOGUE:
        raise AssertionError(f"queue {calls}")
    if struct.unpack_from("<II", mem, BLOCK_TMX) != (0, buf) or mem[status] != 0 \
            or struct.unpack_from("<H", mem, step)[0] != 5 \
            or struct.unpack_from("<I", mem, TOKMAP_BASE)[0] != 0xFFFFFFFF:
        raise AssertionError("read was not left pending")

    struct.pack_into("<I", mem, buf + CHUNK_BYTES, 0x21)
    struct.pack_into("<I", mem, buf + CHUNK_BYTES + 0x12 * 4, 0x99)
    struct.pack_into("<H", mem, step, 6)
    mem[status] = 2
    calls, _ = run(install_at)
    if any(call[0] == 0x101740 for call in calls) or struct.unpack_from("<II", mem, BLOCK_TMX) != (buf, 0) \
            or struct.unpack_from("<H", mem, step)[0] != 6 \
            or struct.unpack_from("<I", mem, TOKMAP_BASE)[0] != 0x21 \
            or struct.unpack_from("<I", mem, TOKMAP_BASE + 0x12 * 4)[0] != 0x99 \
            or struct.unpack_from("<I", mem, TOKMAP_READY)[0] != 1:
        raise AssertionError(f"publish {calls} {struct.unpack_from('<II', mem, BLOCK_TMX)}")

    calls, _ = run(install_at)
    if [call[0] for call in calls] != [LOAD_EPILOGUE]:
        raise AssertionError(f"installed again {calls}")

    # Index 0, selection 0: descriptor 0x5978B0. Chunk 291 is slot 1.
    struct.pack_into("<H", mem, 0x5978B0 + 0x10, 291)
    struct.pack_into("<II", mem, BLOCK_TMX, buf, 0)
    struct.pack_into("<I", mem, MARKER_ADDR, 0)
    mem[status] = 2
    struct.pack_into("<H", mem, step, 6)
    slot1 = 0x500000
    calls, _ = run(install_at, alloc=slot1, limit=200)
    if calls[:2] != [(0x101740, 0x85000, calls[0][2], calls[0][3], calls[0][4]),
                     (0x403C78, 284519, 266, slot1, 0x5EC578)] or calls[-1][0] != LOAD_EPILOGUE:
        raise AssertionError(f"slot 1 queue {calls}")
    struct.pack_into("<H", mem, 0x5978B0 + 0x10, 0)

    struct.pack_into("<II", mem, BLOCK_TMX, 0, 0)
    mem[status] = 2
    struct.pack_into("<H", mem, step, 6)
    calls, _ = run(install_at, alloc=0)
    if [call[0] for call in calls] != [0x101740, LOAD_EPILOGUE] or mem[status] != 2 \
            or struct.unpack_from("<H", mem, step)[0] != 6:
        raise AssertionError(f"alloc fail {calls}")

    calls, _ = run(install_at, queued=0)
    if mem[status] != 2 or struct.unpack_from("<II", mem, BLOCK_TMX) != (0, 0) \
            or struct.unpack_from("<H", mem, step)[0] != 6:
        raise AssertionError(f"queue fail status {mem[status]}")

    struct.pack_into("<I", mem, GP_TMX, 0)
    _calls, t9 = run(LOAD_BRIDGE)
    if t9 != 0x0BAD0000 + 4 * 25 or _calls[0][0] != LOAD_EPILOGUE:
        raise AssertionError(f"missing font still installed {_calls} {t9:#x}")
    struct.pack_into("<II", mem, BLOCK_TMX, buf, 0)
    struct.pack_into("<I", mem, GP_TMX, base)
    _calls, t9 = run(LOAD_BRIDGE)
    if t9 != base + CODE_OFF or [call[0] for call in _calls] != [LOAD_EPILOGUE]:
        raise AssertionError(f"bridge {_calls} {t9:#x}")

    buf2 = 0x510000
    struct.pack_into("<H", mem, 0x5978B0 + 0x10, 285)
    struct.pack_into("<II", mem, BLOCK_TMX, buf, 0)
    struct.pack_into("<I", mem, MARKER_ADDR, 0)
    mem[status] = 2
    struct.pack_into("<H", mem, step, 8)
    calls, _ = run(install_at, alloc=buf2, limit=200)
    if calls[:2] != [(0x101740, 0x85000, calls[0][2], calls[0][3], calls[0][4]),
                     (0x403C78, 284819, 266, buf2, 0x5EC578)] or calls[-1][0] != NINE_EPILOGUE:
        raise AssertionError(f"nine queue {calls}")
    if struct.unpack_from("<II", mem, BLOCK_TMX) != (buf, buf2) or mem[status] != 0 \
            or struct.unpack_from("<H", mem, step)[0] != 7:
        raise AssertionError("nine read replaced the live page")

    struct.pack_into("<I", mem, buf2 + CHUNK_BYTES + (MARKER_ADDR - TOKMAP_BASE), SLOT2_MARK)
    struct.pack_into("<H", mem, step, 8)
    mem[status] = 2
    calls, _ = run(install_at)
    if any(call[0] == 0x101740 for call in calls) or struct.unpack_from("<II", mem, BLOCK_TMX) != (buf2, 0) \
            or struct.unpack_from("<H", mem, step)[0] != 8 \
            or struct.unpack_from("<I", mem, MARKER_ADDR)[0] != SLOT2_MARK \
            or calls[-1][0] != NINE_EPILOGUE:
        raise AssertionError(f"nine publish {calls}")

    calls, _ = run(install_at)
    if [call[0] for call in calls] != [NINE_EPILOGUE]:
        raise AssertionError(f"nine installed again {calls}")

    struct.pack_into("<I", mem, MARKER_ADDR, 0)
    struct.pack_into("<II", mem, BLOCK_TMX, buf, 0)
    mem[status] = 2
    struct.pack_into("<H", mem, step, 8)
    calls, _ = run(install_at, alloc=0)
    if [call[0] for call in calls] != [0x101740, NINE_EPILOGUE] or mem[status] != 2 \
            or struct.unpack_from("<II", mem, BLOCK_TMX) != (buf, 0) \
            or struct.unpack_from("<H", mem, step)[0] != 8:
        raise AssertionError(f"nine alloc fail {calls}")

    calls, _ = run(install_at, alloc=buf2, queued=0)
    if mem[status] != 2 or struct.unpack_from("<II", mem, BLOCK_TMX) != (buf, 0) \
            or struct.unpack_from("<H", mem, step)[0] != 8 or calls[-1][0] != NINE_EPILOGUE:
        raise AssertionError(f"nine queue fail {calls}")

    struct.pack_into("<I", mem, GP_TMX, 0)
    _calls, t9 = run(labels["nine"])
    if t9 != 0x0BAD0000 + 4 * 25 or _calls[0][0] != NINE_EPILOGUE:
        raise AssertionError(f"nine miss {_calls} {t9:#x}")
    struct.pack_into("<I", mem, GP_TMX, base)
    struct.pack_into("<I", mem, MARKER_ADDR, SLOT2_MARK)
    struct.pack_into("<II", mem, BLOCK_TMX, buf2, 0)
    struct.pack_into("<H", mem, step, 8)
    _calls, t9 = run(labels["nine"])
    if t9 != base + CODE_OFF or [call[0] for call in _calls] != [NINE_EPILOGUE]:
        raise AssertionError(f"nine bridge {_calls} {t9:#x}")

    struct.pack_into("<II", mem, BLOCK_TMX, 0, 0)
    struct.pack_into("<I", mem, MARKER_ADDR, 0)
    struct.pack_into("<I", mem, TOKMAP_READY, 0)


def self_test(words, labels, pad):
    layout_test()
    # Not 0x100000: the pad adds the 0x101E00 cache offset to this base.
    base = 0x120000
    mem = bytearray(TOKMAP_READY + 4)
    struct.pack_into("<I", mem, GP_TMX, base)
    struct.pack_into("<III", mem, QUEUE, BUCKETS, BUCKETS + 0x400, CURSOR)
    pad_test(pad, mem, base)
    install_test(pad, mem, base)
    struct.pack_into("<I", mem, GP_TMX, base)
    struct.pack_into("<I", mem, OPEN_TMX, 0)
    chain_test(words, labels, pad, mem, base)
