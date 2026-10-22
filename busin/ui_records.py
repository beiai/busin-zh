"""Readers for fixed UI text records in the original executable."""

from dataclasses import dataclass
import struct

from busin.disc import FILE_BASE

QUEST_TITLE_TABLE = 0x569EE8
QUEST_TITLE_COUNT = 24
QUEST_TITLE_START = 0x569C30
QUEST_DETAIL_TABLE = 0x56AE70
QUEST_DETAIL_COUNT = 24
QUEST_DETAIL_START = 0x56A2B0
PLACE_POINTER_RANGES = (
    (0x569BD0, QUEST_TITLE_START),
    (QUEST_TITLE_TABLE, QUEST_TITLE_TABLE + QUEST_TITLE_COUNT * 4),
    (QUEST_DETAIL_TABLE, QUEST_DETAIL_TABLE + QUEST_DETAIL_COUNT * 8),
)

BLURB_WIDTH = 17
# Item-name draw callers copy 7-9 cells; five source cells are not the display limit.
NAME_DISPLAY_CELLS = 7
BLURB_ROWS = 3
BLURB_STRIDE = BLURB_WIDTH * BLURB_ROWS * 2

# 0x1FDD50 indexes these 60 25-cell magic-effect rows. A full row has no terminator.
MAGIC_BASE = 0x4B0106
MAGIC_COUNT = 60
MAGIC_CELLS = 25
NAME_END = MAGIC_BASE - 6

# 0x148D1C..0x148D2C scales an alliance-detail index by 0x8C from 0x4C1306.
ALLIANCE_BASE = 0x4C1306
ALLIANCE_STRIDE = 0x8C
ALLIANCE_COUNT = 40


@dataclass(frozen=True)
class BlurbTable:
    base: int
    count: int
    getter: int

    def contains(self, va: int) -> bool:
        return self.base <= va < self.end and (va - self.base) % BLURB_STRIDE == 0

    @property
    def end(self) -> int:
        return self.base + self.count * BLURB_STRIDE


# 0x12F368..0x12F458 index these tables by 102 bytes. 0x12F2A0 copies
# three rows of 17 cells; zero cells are padding, not record delimiters.
BLURB_TABLES = (
    BlurbTable(0x4B0CC6, 120, 0x12F368),
    BlurbTable(0x4B3CC6, 100, 0x12F398),
    BlurbTable(0x4B64C6, 100, 0x12F3F8),
    BlurbTable(0x4B8CC6, 100, 0x12F3C8),
    BlurbTable(0x4BB4C6, 100, 0x12F428),
    BlurbTable(0x4BDCC6, 100, 0x12F458),
)


def half(elf, va: int) -> int:
    return struct.unpack_from("<H", elf, va - FILE_BASE)[0]


def word(elf, va: int) -> int:
    return struct.unpack_from("<I", elf, va - FILE_BASE)[0]


def expect_words(elf, checks, label: str) -> None:
    for va, expected in checks:
        if word(elf, va) != expected:
            raise AssertionError(f"{label} at {va:#x}")


def read_ffff(elf, va: int, limit: int = 96, extra=()):
    out = []
    for index in range(limit):
        value = half(elf, va + index * 2)
        if value == 0xFFFF:
            return out
        if value != 0xFFFE and value not in extra and value >= 0x400:
            return out
        out.append(value)
    return out


def text_run(run) -> bool:
    body = [code for code in run if code not in (0x6E, 0xFFFE, 0)]
    return any(code >= 0x70 for code in body)


def strip_pad(run):
    # 0x1D (=) and 0x6E (_) pad item records. They are real glyphs elsewhere.
    while run and run[0] in (0x1D, 0x6E):
        run = run[1:]
    while run and run[-1] == 0x6E:
        run = run[:-1]
    return run


def grid(elf, va: int, width: int, rows: int):
    return [half(elf, va + index * 2) for index in range(width * rows)]


def slot(
    ident,
    va,
    mode,
    raw,
    width,
    slack=None,
    rows=1,
    room=None,
    slash=False,
    ffff=False,
    kind=None,
):
    return {
        "id": ident,
        "va": va,
        "mode": mode,
        "raw": raw,
        "width": width,
        "slack": slack,
        "rows": rows,
        "room": room,
        "slash": slash,
        "ffff": ffff,
        "type": kind,
    }


def kept_spans(elf, va, limit: int = 3):
    found, current, start = [], [], None
    for index in range(32):
        value = half(elf, va + index * 2)
        at = va + index * 2
        if value in (0, 0xFFFF):
            if current:
                found.append((start, current, at))
                current, start = [], None
        elif value == 0xFFFE or value < 0x400:
            if start is None:
                start = at
            current.append(value)
        elif current:
            found.append((start, current, at))
            current, start = [], None
    if current:
        found.append((start, current, va + 64))
    kept = []
    for start, raw, term in found:
        # The name getters start after the record marker. Stripping it from the
        # source must also advance the write address, or the first letter is lost.
        while raw and raw[0] in (0x1D, 0x6E):
            start += 2
            raw = raw[1:]
        stripped = strip_pad(list(raw))
        if text_run(stripped):
            kept.append((start, stripped, raw, term))
        if len(kept) == limit:
            break
    return kept


def name_slots(elf):
    # The normal-name getters advance past the '=' marker, then index 64-byte records.
    for getter, base in (
        (0x12EEB0, 0x4A4E06),
        (0x12EF10, 0x4A7386),
        (0x12EF70, 0x4A9886),
    ):
        expect_words(
            elf,
            (
                (getter + 20, 0x3C030000 | ((base + 0x8000) >> 16)),
                (getter + 28, 0x24630000 | (base & 0xFFFF)),
                (getter + 32, 0x00021180),
            ),
            "name reader",
        )
    slots = []
    for va in range(0x4A4000, NAME_END, 0x40):
        spans = kept_spans(elf, va)
        for index, (start, stripped, raw, term) in enumerate(spans):
            ident = f"{va:06X}" if index == 0 else f"{va:06X}.{index + 1}"
            source_room = (term - start) // 2
            if source_room != len(raw):
                raise AssertionError(f"{ident} room")
            available = (va + 64 - start) // 2
            if index + 1 < len(spans):
                # Short runs can be separated by one zero cell inside a name record.
                available = min(available, (spans[index + 1][0] - start) // 2 - 1)
            width = len(stripped)
            if len(spans) > 1 and all(len(span[1]) == 1 for span in spans):
                available = 1
            slack = min(NAME_DISPLAY_CELLS, available) - width
            if slack < 0:
                slack = 0
            slots.append(
                slot(
                    ident,
                    start,
                    "span",
                    stripped,
                    width,
                    slack=slack,
                    room=min(max(len(raw), width + slack), available),
                )
            )
    return slots


def monster_slots(elf):
    slots = []
    for va in range(0x4F3400, 0x4F5400, 0x40):
        if half(elf, va) == 0:
            continue
        raw, term, bad = [], None, False
        for index in range(20):
            code = half(elf, va + index * 2)
            if code == 0:
                term = va + index * 2
                break
            if code >= 0x400:
                bad = True
                break
            raw.append(code)
        else:
            term = va + 40
        stripped = strip_pad(list(raw))
        if bad or term is None or not text_run(stripped):
            continue
        slots.append(
            slot(
                f"{va:06X}", va, "span", stripped, len(stripped), room=(term - va) // 2
            )
        )
    return slots


def packed_slots(elf, start, end):
    slots = []
    va = start
    while va < end:
        value = half(elf, va)
        if value in (0, 0xFFFF) or value >= 0x400:
            va += 2
            continue
        raw, at, ok = [], va, True
        while at < end:
            code = half(elf, at)
            if code in (0, 0xFFFF):
                break
            if code == 0xFFFE or code < 0x400:
                raw.append(code)
                at += 2
                continue
            ok = False
            break
        stripped = strip_pad(list(raw))
        if ok and text_run(stripped) and half(elf, at) in (0, 0xFFFF):
            slots.append(
                slot(f"{va:06X}", va, "span", stripped, len(stripped), room=len(raw))
            )
            va = at + 2
        else:
            va += 2
    return slots


def quest_title_slots(elf):
    # The title painter indexes this pointer table and treats FFFE as a line break.
    # Read through the pointers: the preceding name pointer's high half looks like 'v'.
    expect_words(
        elf,
        (
            (0x3DD24C, 0x2C620000 | QUEST_TITLE_COUNT),
            (0x3DD264, 0x3C020000 | ((QUEST_TITLE_TABLE + 0x8000) >> 16)),
            (0x3DD268, 0x00031880),
            (0x3DD26C, 0x24420000 | (QUEST_TITLE_TABLE & 0xFFFF)),
            (0x3DD274, 0x2404FFFF),
            (0x3DD278, 0x8C700000),
            (0x3DD290, 0x2404FFFE),
        ),
        "quest title reader",
    )
    pointers = [
        word(elf, QUEST_TITLE_TABLE + index * 4) for index in range(QUEST_TITLE_COUNT)
    ]
    if pointers[0] != QUEST_TITLE_START:
        raise AssertionError(f"quest title pointer {pointers[0]:#x}")
    slots = []
    for va, end in zip(pointers, [*pointers[1:], QUEST_TITLE_TABLE]):
        if va % 2 or not QUEST_TITLE_START <= va < end <= QUEST_TITLE_TABLE:
            raise AssertionError(f"quest title pointer {va:#x}..{end:#x}")
        codes = read_ffff(elf, va, limit=(end - va) // 2)
        if (
            not codes
            or va + len(codes) * 2 >= end
            or half(elf, va + len(codes) * 2) != 0xFFFF
        ):
            raise AssertionError(f"quest title terminator at {va:#x}")
        slots.append(
            slot(
                f"{va:06X}", va, "span", codes, len(codes), room=len(codes), slash=True
            )
        )
    return slots


def quest_detail_targets(elf):
    # The detail painter indexes two pointers per quest and treats FFFE as a line break.
    expect_words(
        elf,
        (
            (0x3DD6B4, 0x3C020057),
            (0x3DD6C0, 0x2443AE70),
            (0x3DD6C4, 0x00851021),
            (0x3DD6C8, 0x00431021),
            (0x3DD6E8, 0x2404FFFE),
        ),
        "quest detail reader",
    )
    targets = []
    for index in range(QUEST_DETAIL_COUNT):
        pair = [
            word(elf, QUEST_DETAIL_TABLE + (index * 2 + side) * 4)
            for side in range(2)
        ]
        if pair[0] != pair[1] or pair[0] % 2 or not QUEST_DETAIL_START <= pair[0] < QUEST_DETAIL_TABLE:
            raise AssertionError(f"quest detail pointer {pair[0]:#x}, {pair[1]:#x}")
        if pair[0] in targets:
            raise AssertionError(f"duplicate quest detail pointer {pair[0]:#x}")
        targets.append(pair[0])
    return targets


def place_slots(elf):
    # These mixed-data ranges contain floats, pointers and rewards, not glyphs.
    expect_words(elf, (
        (0x569A50, 0x40000000), (0x569A58, 0x40800000),
        (0x569A60, 0x3F800000),
        (0x56A188, 0), (0x56A190, 0x569F78),
        (0x56A1F0, 0), (0x56A1F4, 5), (0x56A2A8, 40000),
    ), "place non-text data")
    slots = []
    for start, end in (
        (0x526CF0, 0x527400),
        (0x529C20, 0x52A000),
        (0x567F90, 0x5682D0),
        (0x569940, 0x569A50),
        (0x569A68, PLACE_POINTER_RANGES[0][0]),
    ):
        slots.extend(packed_slots(elf, start, end))
    slots.extend(quest_title_slots(elf))
    slots.extend(packed_slots(elf, PLACE_POINTER_RANGES[1][1], 0x56A188))
    slots.extend(packed_slots(elf, QUEST_DETAIL_START, 0x56AF00))
    by_va = {item["va"]: item for item in slots}
    for va in quest_detail_targets(elf):
        if va not in by_va:
            raise AssertionError(f"quest detail text at {va:#x}")
        by_va[va]["slash"] = True
    slots.extend(alliance_slots(elf))
    return slots



def alliance_slots(elf):
    expect_words(
        elf,
        (
            (0x148D1C, 0x2404008C),
            (0x148D20, 0x3C05004C),
            (0x148D2C, 0x24A51306),
        ),
        "alliance detail reader",
    )
    slots = []
    for row in range(ALLIANCE_COUNT):
        start = ALLIANCE_BASE + row * ALLIANCE_STRIDE
        end = start + ALLIANCE_STRIDE
        va = start
        while va < end:
            value = half(elf, va)
            if value in (0, 0xFFFF) or value >= 0x400:
                va += 2
                continue
            raw, at, ok = [], va, True
            while at < end:
                code = half(elf, at)
                if code in (0, 0xFFFF):
                    break
                if code == 0xFFFE or code < 0x400:
                    raw.append(code)
                    at += 2
                    continue
                ok = False
                break
            stripped = strip_pad(list(raw))
            if ok and text_run(stripped):
                slots.append(slot(f"{va:06X}", va, "span", stripped, len(stripped), room=len(raw)))
            va = (at if at > va else va + 2) + 2
    return slots


def blurb_record(va: int) -> bool:
    return any(table.contains(va) for table in BLURB_TABLES)


def magic_slots(elf):
    slots = []
    for index in range(MAGIC_COUNT):
        va = MAGIC_BASE + index * MAGIC_CELLS * 2
        codes = grid(elf, va, MAGIC_CELLS, 1)
        stripped = strip_pad(list(codes))
        if text_run(stripped):
            slots.append(
                slot(f"{va:06X}", va, "span", stripped, len(stripped), room=MAGIC_CELLS)
            )
    return slots


def blurb_slots(elf):
    # The copy routine advances 34 bytes per source row and stops after three rows.
    expect_words(
        elf, ((0x12F34C, 0x28420003), (0x12F350, 0x24840022)), "blurb row copy"
    )
    slots = magic_slots(elf)
    for table in BLURB_TABLES:
        # Check the actual reader's stride and signed lui/addiu address pair.
        high = (table.base + 0x8000) >> 16
        expect_words(
            elf,
            (
                (table.getter + 20, 0x24050000 | BLURB_STRIDE),
                (table.getter + 28, 0x3C020000 | high),
                (table.getter + 32, 0x24420000 | (table.base & 0xFFFF)),
            ),
            "blurb reader",
        )
        if [half(elf, table.base - 6 + index * 2) for index in range(3)] != [
            0,
            0,
            0x1D,
        ]:
            raise AssertionError(f"blurb table header at {table.base - 6:#x}")
        for index in range(table.count):
            va = table.base + index * BLURB_STRIDE
            codes = grid(elf, va, BLURB_WIDTH, BLURB_ROWS)
            if any(code >= 0x400 for code in codes):
                raise AssertionError(f"blurb grid cell at {va:#x}")
            if any(code not in (0, 0x6E) for code in codes):
                slots.append(
                    slot(
                        f"{va:06X}",
                        va,
                        "flow",
                        codes,
                        BLURB_WIDTH,
                        rows=BLURB_ROWS,
                        kind="grid",
                    )
                )
    # 0x2854E4 starts at 0x4C04C6; the marker at 0x4C04C4 is structural.
    first_link = read_ffff(elf, 0x4C04C6, limit=12)
    if len(first_link) != 12 or half(elf, 0x4C04C6 + len(first_link) * 2) != 0:
        raise AssertionError("first link record")
    slots.append(
        slot("4C04C6", 0x4C04C6, "span", first_link, len(first_link), room=len(first_link))
    )
    return slots
