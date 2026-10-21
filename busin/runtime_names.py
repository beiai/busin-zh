"""Exact mappings for fixed character names copied from the character templates."""
from __future__ import annotations

import json
import struct
import zipfile
from dataclasses import dataclass
from pathlib import Path

from busin.disc import FILE_BASE
from busin.paths import DATA

NAME_CELLS = 8
STATE_MEMBER = "eeMemory.bin"
STATE_MEMORY_SIZE = 33554432


@dataclass(frozen=True)
class RuntimeName:
    ident: str
    source: str
    text: str
    template: int
    display: int


def load_names(path: Path | None = None) -> list[RuntimeName]:
    path = path or DATA / "interface" / "party.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list) or not raw:
        raise ValueError("party names must be a non-empty list")
    names = []
    seen = set()
    seen_addresses = set()
    for item in raw:
        if not isinstance(item, dict) or set(item) != {"id", "source", "text", "template", "display"}:
            raise ValueError(f"invalid party name record: {item!r}")
        try:
            template = int(item["template"], 16)
            display = int(item["display"], 16)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid party name address: {item!r}") from exc
        name = RuntimeName(item["id"], item["source"], item["text"], template, display)
        if not name.ident or name.ident in seen:
            raise ValueError(f"duplicate party name id {name.ident!r}")
        if not name.source or not name.text or name.source == name.text:
            raise ValueError(f"invalid party name mapping {item!r}")
        if len(name.text) > NAME_CELLS:
            raise ValueError(f"{name.ident} translation exceeds {NAME_CELLS} cells")
        if template % 2 or display % 2:
            raise ValueError(f"{name.ident} name address is not halfword aligned")
        if template in seen_addresses or display in seen_addresses:
            raise ValueError(f"{name.ident} reuses a name address")
        seen.add(name.ident)
        seen_addresses.update((template, display))
        names.append(name)
    return names


def _codes() -> dict[str, int]:
    raw = json.loads((DATA / "font" / "small.json").read_text(encoding="utf-8"))
    return {char: int(code, 16) for code, char in raw.items()}


def source_codes(text: str) -> list[int]:
    table = _codes()
    try:
        return [table[char] for char in text]
    except KeyError as exc:
        raise ValueError(f"party name {text!r} has no UI glyph") from exc


def _read_name(words: list[int]) -> str:
    raw = json.loads((DATA / "font" / "small.json").read_text(encoding="utf-8"))
    chars = {int(code, 16): char for code, char in raw.items()}
    out = []
    for word in words:
        if word == 0xFFFF:
            break
        if word not in chars:
            raise ValueError(f"party name uses unknown UI code {word:#x}")
        out.append(chars[word])
    return "".join(out)


def template_words(elf: bytes, name: RuntimeName) -> list[int]:
    offset = name.template - FILE_BASE
    if offset < 0 or offset + NAME_CELLS * 2 > len(elf):
        raise ValueError(f"{name.ident} template is outside the ELF")
    words = list(struct.unpack_from(f"<{NAME_CELLS}H", elf, offset))
    source = _read_name(words)
    if source != name.source:
        raise ValueError(f"{name.ident} template is {source!r}, expected {name.source!r}")
    source_width = len(source_codes(name.source))
    if any(word != 0xFFFF for word in words[source_width:]):
        raise ValueError(f"{name.ident} template padding is not 0xFFFF")
    return words


def display_name(memory: bytes, name: RuntimeName) -> str:
    offset = name.display
    if offset < 0 or offset + NAME_CELLS * 2 > len(memory):
        raise ValueError(f"{name.ident} display is outside EE RAM")
    return _read_name(list(struct.unpack_from(f"<{NAME_CELLS}H", memory, offset)))


def translate_display(value: str, names: list[RuntimeName]) -> str:
    for name in names:
        if value == name.source:
            return name.text
    return value


def inspect_memory(memory: bytes) -> list[dict]:
    names = load_names()
    report = []
    for name in names:
        shown = display_name(memory, name)
        if shown == name.text:
            status = "translated"
        elif shown == name.source:
            status = "source"
        else:
            status = "unknown"
        report.append({
            "id": name.ident,
            "display": shown,
            "source": name.source,
            "text": name.text,
            "status": status,
        })
    return report


def check_state(state: Path) -> list[dict]:
    with zipfile.ZipFile(state) as source:
        bad = source.testzip()
        if bad is not None:
            raise ValueError(f"savestate CRC failed: {bad}")
        memory = source.read(STATE_MEMBER)
    if len(memory) != STATE_MEMORY_SIZE:
        raise ValueError("unexpected EE RAM size")
    return inspect_memory(memory)


def self_test() -> None:
    names = load_names()
    assert [(name.source, name.text) for name in names] == [
        ("グレッグ", "格雷格"), ("リカルド", "里卡多"), ("サラ", "萨拉")
    ]
    assert translate_display("A", names) == "A"
    assert translate_display("グレッグさん", names) == "グレッグさん"
