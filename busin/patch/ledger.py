"""Typed bookkeeping for byte patches and the disc sectors they declare."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from busin.disc import ELF_LBA, PACK_LBA

AREA_PREFIX = "prefix"
AREA_TAIL = "moved-tail"
AREA_APPEND = "append"


def ranges(values: Iterable[int]) -> tuple[tuple[int, int], ...]:
    """Compress consecutive LBAs into inclusive ranges."""
    ordered = sorted(set(values))
    result: list[tuple[int, int]] = []
    for value in ordered:
        if result and value == result[-1][1] + 1:
            result[-1] = (result[-1][0], value)
        else:
            result.append((value, value))
    return tuple(result)


def expand(items: Iterable[tuple[int, int]]) -> set[int]:
    return {value for start, end in items for value in range(start, end + 1)}


@dataclass(frozen=True)
class BytePatch:
    target: str
    category: str
    offset: int
    data: bytes
    reason: str = ""


class PatchLedger:
    """Collect patches without losing their origin.

    Byte patches are used to build the image.  Sector declarations describe
    which original or appended sectors a category is allowed to touch; the
    post-build audit compares them with the real image.
    """

    def __init__(self):
        self._bytes: dict[str, list[BytePatch]] = {}
        self._sectors: dict[tuple[str, str], set[int]] = {}

    def add_bytes(self, target: str, category: str, offset: int, data: bytes,
                  reason: str = "") -> None:
        if not data:
            raise ValueError(f"empty {category} patch at {offset:#x}")
        self._bytes.setdefault(target, []).append(
            BytePatch(target, category, offset, data, reason))

    def add_elf(self, category: str, offset: int, data: bytes, reason: str = "") -> None:
        self.add_bytes("elf", category, offset, data, reason)
        self.declare_elf(category, offset, data)

    def add_pack(self, category: str, offset: int, data: bytes, reason: str = "") -> None:
        self.add_bytes("pack", category, offset, data, reason)
        self.declare_pack(category, offset, data)

    def declare_user_sectors(self, area: str, category: str, sectors: Iterable[int]) -> None:
        if area not in (AREA_PREFIX, AREA_TAIL, AREA_APPEND):
            raise ValueError(f"unknown patch area {area}")
        values = set(sectors)
        if not values or any(value < 0 for value in values):
            raise ValueError(f"{category} has invalid sectors")
        key = (area, category)
        self._sectors[key] = self._sectors.get(key, set()) | values

    def declare_elf(self, category: str, offset: int, data: bytes) -> None:
        first = offset // 2048
        last = (offset + len(data) - 1) // 2048
        self.declare_user_sectors(
            AREA_TAIL, category, (ELF_LBA + value for value in range(first, last + 1)))

    def declare_pack(self, category: str, offset: int, data: bytes) -> None:
        first = offset // 2048
        last = (offset + len(data) - 1) // 2048
        self.declare_user_sectors(
            AREA_PREFIX, category, (PACK_LBA + value for value in range(first, last + 1)))

    def declare_append(self, category: str, start: int, count: int) -> None:
        if count <= 0:
            raise ValueError(f"{category} has no appended sectors")
        self.declare_user_sectors(
            AREA_APPEND, category, range(start, start + count))

    def validate(self) -> None:
        by_target: dict[str, list[BytePatch]] = {}
        for target, patches in self._bytes.items():
            for patch in patches:
                by_target.setdefault(target, []).append(patch)
        for target, patches in by_target.items():
            ordered = sorted((patch.offset, patch.offset + len(patch.data), patch)
                             for patch in patches)
            for (_start, end, left), (start, _end, right) in zip(ordered, ordered[1:]):
                if end > start:
                    raise ValueError(
                        f"{left.category} and {right.category} overlap in {target} at {start:#x}")

    def writes(self, target: str) -> dict[int, bytes]:
        patches = self._bytes.get(target, ())
        result: dict[int, bytes] = {}
        for patch in patches:
            if patch.target != target:
                continue
            if patch.offset in result:
                raise ValueError(f"duplicate {target} patch at {patch.offset:#x}")
            result[patch.offset] = patch.data
        self.validate()
        return result

    def spans(self, target: str) -> list[tuple[int, bytes]]:
        result = [(patch.offset, patch.data) for patch in self._bytes.get(target, ())
                  if patch.target == target]
        self.validate()
        return sorted(result)

    def sector_declarations(self, area: str) -> dict[str, set[int]]:
        return {
            category: values
            for (patch_area, category), values in self._sectors.items()
            if patch_area == area
        }

    def report(self) -> dict:
        self.validate()
        sector_patches = []
        for (area, category), values in sorted(self._sectors.items()):
            compressed = ranges(values)
            sector_patches.append({
                "area": area,
                "category": category,
                "sectors": [list(item) for item in compressed],
                "count": len(values),
            })
        byte_patches = {}
        for target, patches in sorted(self._bytes.items()):
            summary = {}
            for patch in patches:
                item = summary.setdefault(patch.category, {"patches": 0, "bytes": 0})
                item["patches"] += 1
                item["bytes"] += len(patch.data)
            byte_patches[target] = summary
        return {
            "byte_patches": byte_patches,
            "sector_patches": sector_patches,
        }
