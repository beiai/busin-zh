"""Plan translation coverage, font slots, and PACKDATA budgets without writing an image."""
from __future__ import annotations

from busin.disc_source import SourceDisc
from busin.font import load_plan
from busin.message_pack import (
    PLAN_PATH, SHARED_CHUNKS, assign_ids, load_messages, load_texts, text_chars,
)
from busin.patch.font_paging import BLOCK_FONT_SECTORS, BLOCK_SLOTS, CHUNK_SLOTS, PAGE_CELLS, SHIFT
from busin.patch.ui_pages import UI_SECTORS
from busin.text import BEAT_CHUNK, PACKED_CHUNKS, counted_chunks


_SPECIAL = {294: "script counter shares a message start; handle after translation"}
# These untranslated blocks become packed chunks once every message has a translation.
_FUTURE_PACKED = {283, 286, 287, 288, 289, 290, 300, 304, 305}


def _mode(chunk, counted):
    if chunk in PACKED_CHUNKS:
        return "packed"
    if chunk == BEAT_CHUNK:
        return "ending"
    if chunk in counted:
        return "counted"
    return "inline"


def build_disc_plan() -> dict:
    font_plan = load_plan(PLAN_PATH)
    texts = load_texts()
    _ids, page, _stats, by_chunk = assign_ids(font_plan, texts)
    grouped = {}
    for chunk, slot in CHUNK_SLOTS.items():
        grouped.setdefault(slot, set()).update(by_chunk.get(chunk, set()))

    with SourceDisc() as disc:
        source_sectors = disc.sector_count()
        counted = set(counted_chunks())
        _messages, packs, _blocks = load_messages(disc.file, texts, _ids)
        packed = {item["chunk"]: item for item in packs}
        rows = []
        for _name, doc in texts:
            chunk = doc.get("chunk")
            messages = doc.get("messages", [])
            translated = sum(message.get("text") is not None for message in messages)
            mode = _mode(chunk, counted)
            slot = 0 if chunk in SHARED_CHUNKS else CHUNK_SLOTS.get(chunk)
            chars = len(by_chunk.get(chunk, set()))
            original, new = None, None
            if chunk is not None:
                original = disc.chunk_row(chunk)[1]
                if chunk in packed:
                    new = packed[chunk]["sectors"]
                elif mode != "packed":
                    new = original
            intended = "packed" if chunk in _FUTURE_PACKED else mode
            rows.append({
                "chunk": chunk,
                "intended_mode": intended,
                "file": _name,
                "mode": mode,
                "messages": len(messages),
                "translated": translated,
                "pending": len(messages) - translated,
                "font_slot": slot,
                "characters": chars,
                "capacity": PAGE_CELLS - chars if slot is not None else None,
                "sectors": {"original": original, "planned": new,
                            "freed": original - new if original is not None and new is not None else None},
                "note": _SPECIAL.get(chunk, ""),
            })

    slot_rows = []
    for slot in range(BLOCK_SLOTS):
        owners = sorted(chunk for chunk, owner in CHUNK_SLOTS.items() if owner == slot)
        if slot == 0:
            characters = len(page)
        else:
            characters = len(grouped.get(slot, set()))
        slot_rows.append({
            "slot": slot,
            "chunks": owners,
            "characters": characters,
            "free_cells": PAGE_CELLS - characters,
            "state": "shared" if slot == 0 else "used" if owners else "reserved",
        })

    from busin.disasm import load_elf
    from busin.ui_text import ui_patches
    _ui_writes, _ui_extra, ui = ui_patches(load_elf())

    return {
        "messages": {
            "total": sum(item["messages"] for item in rows),
            "translated": sum(item["translated"] for item in rows),
            "pending": sum(item["pending"] for item in rows),
        },
        "font": {
            "page_cells": PAGE_CELLS,
            "slots": slot_rows,
            "reserved_slots": [item["slot"] for item in slot_rows if item["state"] == "reserved"],
        },
        "interface": ui,
        "layout": {
            "source_sectors": source_sectors,
            "inserted_sectors": SHIFT,
            "block_font_sectors": BLOCK_FONT_SECTORS,
            "ui_font_sectors": UI_SECTORS,
            "image_sectors": source_sectors + SHIFT + BLOCK_FONT_SECTORS + UI_SECTORS,
        },
        "chunks": rows,
    }


def write_text_plan(report: dict) -> str:
    lines = [
        "Disc plan",
        f"  messages: {report['messages']['translated']} translated / {report['messages']['total']} total"
        f" ({report['messages']['pending']} pending)",
        f"  interface: {report['interface']['strings']} strings, {report['interface']['glyphs']} glyphs",
        f"  sectors: {report['layout']['source_sectors']} source -> {report['layout']['image_sectors']} image",
        "Font slots:",
    ]
    for item in report["font"]["slots"]:
        if item["state"] != "reserved":
            lines.append(
                f"  slot {item['slot']:>2}: {item['characters']:>4} chars,"
                f" {item['free_cells']:>4} free, chunks {','.join(map(str, item['chunks'])) or '-'}"
            )
    reserved = report["font"]["reserved_slots"]
    lines.append(f"  reserved: {','.join(map(str, reserved)) or 'none'}")
    lines.append("Chunks:")
    lines.append("  chunk  mode      done/total    slot  chars  sectors")
    for item in report["chunks"]:
        slot = "shared" if item["font_slot"] == 0 else "-" if item["font_slot"] is None else str(item["font_slot"])
        sectors = "-"
        if item["sectors"]["original"] is not None:
            sectors = str(item["sectors"]["original"])
            if item["sectors"]["planned"] != item["sectors"]["original"]:
                sectors += f"->{item['sectors']['planned']}"
        mode = item["mode"]
        if item["intended_mode"] != mode:
            mode += "->" + item["intended_mode"]
        lines.append(
            f"  {item['chunk']:>5}  {mode:<17} "
            f"{item['translated']:>5}/{item['messages']:<5} {slot:>5} {item['characters']:>5}  {sectors}"
        )
        if item["note"]:
            lines.append(f"         {item['note']}")
    return "\n".join(lines) + "\n"
