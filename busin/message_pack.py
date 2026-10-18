"""Load dialogue manuscripts and repack their PACKDATA chunks."""
import json
import struct
import unicodedata

from busin.disc import CHUNK_TABLE, ELF_LBA, PACK_LBA, chunk_row, read_pack, read_user
from busin.font import assign_cells as place_cells, hash_collision_stats
from busin.patch.font_paging import BLOCK_SLOTS, CHUNK_SLOTS, PAGE_CELLS, TOKMAP_COUNT
from busin.paths import DATA
from busin.text import (
    BEAT_AT, BEAT_CHUNK, BEAT_INDEX, BEAT_SCRIPT, PACKED_CHUNKS, PAD, check_lines, compact_chunk,
    compact_counted, counted_chunks, counted_messages, encode_message, message_prose,
    repack_beats, split_tags, text_cursors, trim_beat_script,
)

PLAN_PATH = DATA / "font" / "message-plan.json"


def text_files():
    return sorted((DATA / "text").glob("*.json"))


def read_terminated(handle, at: int) -> list[int]:
    # Chunk 265's prologue is 363 words.
    raw = read_pack(handle, at, 768)
    words = []
    for index in range(0, len(raw), 2):
        word = int.from_bytes(raw[index:index + 2], "big")
        if word == 0xFFFF:
            return words
        words.append(word)
    raise ValueError(f"message at {at:#x} has no terminator within {len(raw) // 2} words")


def load_texts():
    return [(path.name, json.loads(path.read_text(encoding="utf-8"))) for path in text_files()]


def text_chars(doc):
    """Visible characters in one text file. Spaces stay the blank cell."""
    return {char for message in doc["messages"] for line in message.get("text") or []
            for _tag, char in split_tags(line) if char and char not in " \u3000"}


# Narration blocks 265–272 and chunk 282 stay on the shared page. Their character numbers are the page-1 cells.
SHARED_CHUNKS = {265, 266, 267, 268, 269, 270, 271, 272, 282}


def character_map(ids, local, mark):
    """Character number to this page's cell. The last word is the slot mark."""
    table = [0] * TOKMAP_COUNT
    for char, cell in local.items():
        index = ids[char]
        if not 0 < index < TOKMAP_COUNT - 1 or table[index]:
            raise ValueError(f"{char} has character number {index}")
        table[index] = cell
    if not 0 < mark < BLOCK_SLOTS:
        raise ValueError(f"slot mark {mark}")
    table[TOKMAP_COUNT - 1] = mark
    return struct.pack(f"<{TOKMAP_COUNT}I", *table)


def assign_ids(plan, texts):
    """Page-1 cells for the shared page. Other characters get numbers from 1764 up."""
    pinned = {char: int(item["cell"], 0) for char, item in plan["characters"].items() if item["page"] == 1}
    by_chunk = {}
    for _name, doc in texts:
        by_chunk.setdefault(doc.get("chunk"), set()).update(text_chars(doc))
    shared = set(pinned)
    for chunk in SHARED_CHUNKS:
        shared |= by_chunk.get(chunk, set())
    page = place_cells(shared, pinned)
    assert page == place_cells(shared, pinned)
    ids = dict(page)
    rest = sorted(set().union(*by_chunk.values()) - set(ids))
    number = PAGE_CELLS
    for char in rest:
        if number >= TOKMAP_COUNT - 1:
            raise ValueError(f"character number {number} does not fit the token table")
        ids[char] = number
        number += 1
    for chunk, chars in by_chunk.items():
        if chars - shared and chunk not in CHUNK_SLOTS:
            raise ValueError(f"chunk {chunk} has no font slot")
    stats = hash_collision_stats(shared, pinned)
    if stats["max_probe_length"] > 10:
        print(f"warning: hash probe length {stats['max_probe_length']}", flush=True)
    return ids, page, stats, by_chunk


def chunk_table(handle):
    data = read_user(handle, ELF_LBA + CHUNK_TABLE // 2048, 8)
    blob = data[CHUNK_TABLE % 2048:]
    rows = []
    for index in range(512):
        start, count = struct.unpack_from("<QQ", blob, index * 16)
        if count:
            rows.append((index, int(start), int(count)))
    return rows


def load_messages(handle, texts, cells):
    """Pack offset -> (source token, translated token) for every word that changes.

    Packed chunks and counted narration blocks are rewritten as whole spans and
    returned separately.
    """
    changed, starts, errors, packs, blocks = {}, set(), [], [], []
    cursors = text_cursors()
    rows = chunk_table(handle)
    counted = set(counted_chunks())
    for name, doc in texts:
        chunk = doc.get("chunk")
        if chunk in PACKED_CHUNKS:
            try:
                packs.append(pack_script(handle, doc, cells, cursors[chunk], rows))
            except ValueError as error:
                errors.append(f"{name}: {error}")
            continue
        if chunk in counted and chunk != BEAT_CHUNK:
            try:
                blocks.append(pack_counted(handle, doc, cells, rows))
            except ValueError as error:
                errors.append(f"{name}: {error}")
            continue
        if chunk == BEAT_CHUNK and any(message.get("text") is not None for message in doc["messages"]):
            continue
        base = int(doc.get("base", "0"), 0)
        for message in doc["messages"]:
            if message.get("text") is None:
                continue
            at = base + int(message["offset"], 0)
            try:
                if at in starts:
                    raise ValueError("offset is translated twice")
                starts.add(at)
                original = read_terminated(handle, at)
                prose = message_prose(original, doc["align"])
                if prose != message["source"].split("\n"):
                    raise ValueError(f"disc is {prose}")
                check_lines(original, message["text"], doc["align"], message.get("type"))
                encoded = encode_message(original, message["text"], doc["align"], lambda char: PAD + cells[char])
            except ValueError as error:
                errors.append(f"{name} {message['offset']}: {error}")
                continue
            for index, (was, word) in enumerate(zip(original, encoded)):
                if word != was:
                    changed[at + 2 * index] = (was, word)
    if errors:
        raise ValueError("\n".join(errors))
    return changed, packs, blocks


def ending_spans(handle, texts, cells):
    """Rewrite chunk 272's one-glyph tail and drop the unused chunk 281 commands."""
    doc = next((item for _name, item in texts if item.get("chunk") == BEAT_CHUNK), None)
    if doc is None or all(message.get("text") is None for message in doc["messages"]):
        return []
    rows = chunk_table(handle)
    start, count = next((item[1], item[2]) for item in rows if item[0] == BEAT_CHUNK)
    data = read_user(handle, PACK_LBA + start, count)
    disc = counted_messages(data)
    head = [item for item in doc["messages"] if int(item["offset"], 0) < BEAT_AT]
    beats = [item for item in doc["messages"] if int(item["offset"], 0) >= BEAT_AT]
    if len(head) != BEAT_INDEX or len(beats) > 40 or len(disc) != BEAT_INDEX + 40:
        raise ValueError("chunk 272 beat layout does not match the disc")
    have = [int(item["offset"], 0) for item in beats]
    want = [at for at, _end, _words in disc[BEAT_INDEX:BEAT_INDEX + len(beats)]]
    if have != want:
        raise ValueError("chunk 272 beat offsets are not the first slots")
    sector = bytearray(data)
    token_of = lambda char: PAD + cells[char]
    for record, (at, _end, words) in zip(head, disc[:BEAT_INDEX]):
        if record["source"].split("\n") != message_prose(words, doc["align"]):
            raise ValueError(f"chunk 272 message {at:#x} source does not match the disc")
        if record.get("text") is None:
            continue
        check_lines(words, record["text"], doc["align"], record["type"])
        encoded = encode_message(words, record["text"], doc["align"], token_of)
        cursor = at
        for word in encoded:
            sector[cursor:cursor + 2] = word.to_bytes(2, "big")
            cursor += 2
        sector[cursor:cursor + 2] = b"\xff\xff"
    tokens = []
    for record, (at, _end, words) in zip(beats, disc[BEAT_INDEX:]):
        if record["source"] != "\n".join(message_prose(words, doc["align"])):
            raise ValueError(f"chunk 272 beat {at:#x} source does not match the disc")
        text = record.get("text")
        if text is None or len(text) != 1 or len(text[0]) != 1:
            raise ValueError(f"chunk 272 beat {at:#x} is not one character")
        check_lines(words, text, doc["align"], record["type"])
        encoded = encode_message(words, text, doc["align"], token_of)
        if len(encoded) != 1:
            raise ValueError(f"chunk 272 beat {at:#x} did not stay one word")
        tokens.append(encoded[0])
    sector = repack_beats(bytes(sector), tokens)
    script_start, script_count = next((item[1], item[2]) for item in rows if item[0] == BEAT_SCRIPT)
    script = read_user(handle, PACK_LBA + script_start, script_count)
    return [
        (start * 2048, sector),
        (script_start * 2048, trim_beat_script(script, len(tokens))),
    ]


def pack_counted(handle, doc, cells, rows):
    """Rewrite one counted narration block inside its existing sector allocation."""
    chunk = doc["chunk"]
    start, count = next((item[1], item[2]) for item in rows if item[0] == chunk)
    base = start * 2048
    if int(doc.get("base", "0"), 0) != base:
        raise ValueError(f"base is not sector {start}")
    data = read_user(handle, PACK_LBA + start, count)
    body = compact_counted(data, doc["messages"], doc["align"], lambda char: PAD + cells[char])
    return {"chunk": chunk, "spans": [(base, body)]}


def pack_script(handle, doc, cells, cursor, rows):
    """Rewrite one script chunk and describe the sectors it no longer claims.

    Each line is placed in order. The translation file does not store that position.
    """
    chunk = doc["chunk"]
    start, count = next((item[1], item[2]) for item in rows if item[0] == chunk)
    base = start * 2048
    if int(doc.get("base", "0"), 0) != base:
        raise ValueError(f"base is not sector {start}")
    data = read_user(handle, PACK_LBA + start, count)
    packed, _offsets = compact_chunk(data, cursor, doc["messages"], doc["align"], lambda char: PAD + cells[char])
    sectors = (len(packed) + 2047) // 2048
    if sectors >= count:
        raise ValueError(f"sector count did not shrink ({count} -> {sectors})")
    if [item[1] for item in rows if item[1] == start + count] != [start + count]:
        raise ValueError("the next chunk does not start at the old end")
    hole = start + sectors
    if any(item[0] != chunk and item[1] < start + count and item[1] + item[2] > hole for item in rows):
        raise ValueError("freed sectors overlap another chunk")
    body = packed + bytes(sectors * 2048 - len(packed))
    return {
        "chunk": chunk,
        "spans": [(base, body), (hole * 2048, bytes((count - sectors) * 2048))],
        "table": CHUNK_TABLE + chunk * 16 + 8,
        "sectors": sectors,
        "free": {"chunk": chunk, "relative": hole, "sectors": count - sectors, "lba": PACK_LBA + hole},
    }
