"""Patch the fixed character-name templates with exact Chinese mappings."""
from __future__ import annotations

import struct

from busin.disc import FILE_BASE
from busin.runtime_names import NAME_CELLS, load_names, template_words


def elf_writes(elf: bytes, table: dict[str, int]) -> dict[int, bytes]:
    writes = {}
    for name in load_names():
        template_words(elf, name)
        try:
            codes = [table[char] for char in name.text]
        except KeyError as exc:
            raise ValueError(f"{name.ident} translation {name.text!r} has no UI glyph") from exc
        if len(codes) > NAME_CELLS:
            raise ValueError(f"{name.ident} translation needs {len(codes)} cells, limit {NAME_CELLS}")
        packed = codes + [0xFFFF] * (NAME_CELLS - len(codes))
        writes[name.template - FILE_BASE] = struct.pack(f"<{NAME_CELLS}H", *packed)
    return writes
