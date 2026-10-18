"""Audit every sector-level difference between the source disc and a build."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from busin.disc import BIN, read_user
from busin.patch.font_paging import BLOCK_FONT_LBA, BLOCK_FONT_SECTORS, SHIFT, SHIFT_FROM
from busin.patch.ledger import ranges
from busin.patch.ui_pages import UI_SECTORS
from busin.paths import BUILD, ROOT


def _label(path: Path) -> str:
    return path.relative_to(ROOT).as_posix() if path.is_relative_to(ROOT) else path.as_posix()


def _expanded(items) -> set[int]:
    return {value for start, end in items for value in range(start, end + 1)}


def _write_set(report: dict, area: str) -> dict[str, set[int]]:
    result: dict[str, set[int]] = {}
    for item in report["change_plan"]["sector_patches"]:
        if item["area"] == area:
            values = _expanded(item["sectors"])
            if not values or any(start > end for start, end in item["sectors"]):
                raise ValueError(f"invalid {item['category']} sector ranges")
            result[item["category"]] = values
    return result


def _classified(actual: set[int], expected: dict[str, set[int]]) -> dict[str, int]:
    return {category: len(actual & values) for category, values in sorted(expected.items())}


def _missing_declared(actual: set[int], expected: dict[str, set[int]]) -> dict[str, int]:
    return {category: len(values - actual) for category, values in sorted(expected.items())
            if values - actual}


def audit_image(report_path=None, image_path=None, output_path=None) -> dict:
    report_path = Path(report_path or BUILD / "image" / "report.json")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    image_path = Path(image_path or ROOT / report["image"])
    output_path = Path(output_path or report_path.with_name("change-audit.json"))

    with image_path.open("rb") as image_file:
        actual_hash = hashlib.file_digest(image_file, "sha256").hexdigest()
    if actual_hash != report["image_sha256"]:
        raise ValueError("image hash does not match report.json")
    for label, wanted in report["inputs"].items():
        input_path = Path(label)
        if not input_path.is_absolute():
            input_path = ROOT / input_path
        if not input_path.is_file():
            raise ValueError(f"report input {label} is missing")
        with input_path.open("rb") as input_file:
            have = hashlib.file_digest(input_file, "sha256").hexdigest()
        if have != wanted:
            raise ValueError(f"report input {label} has changed since the build")

    with BIN.open("rb") as source, image_path.open("rb") as target:
        source_hash = hashlib.file_digest(source, "sha256").hexdigest()
        expected_source = report["inputs"].get(_label(BIN))
        if source_hash != expected_source:
            raise ValueError("source disc hash does not match report.json")
        source_sectors = source.seek(0, 2) // 2352
        image_sectors = target.seek(0, 2) // 2352
        wanted_sectors = source_sectors + SHIFT + BLOCK_FONT_SECTORS + UI_SECTORS
        if image_sectors != wanted_sectors:
            raise ValueError(f"image has {image_sectors} sectors, expected {wanted_sectors}")

        changed_prefix = {
            lba for lba in range(SHIFT_FROM)
            if read_user(source, lba, 1) != read_user(target, lba, 1)
        }
        changed_tail = {
            lba for lba in range(SHIFT_FROM, source_sectors)
            if read_user(source, lba, 1) != read_user(target, lba + SHIFT, 1)
        }

    prefix_plan = _write_set(report, "prefix")
    tail_plan = _write_set(report, "moved-tail")
    append_plan = _write_set(report, "append")
    unexpected_prefix = changed_prefix - set().union(*prefix_plan.values()) if prefix_plan else changed_prefix
    unexpected_tail = changed_tail - set().union(*tail_plan.values()) if tail_plan else changed_tail

    appended = (
        set(range(SHIFT_FROM, SHIFT_FROM + SHIFT))
        | set(range(BLOCK_FONT_LBA, image_sectors))
    )
    expected_append = set().union(*append_plan.values()) if append_plan else set()
    append_missing = expected_append - appended
    append_extra = appended - expected_append

    result = {
        "report": str(report_path),
        "image": str(image_path),
        "image_sha256": actual_hash,
        "sectors": {
            "source": source_sectors,
            "image": image_sectors,
            "prefix_changed": len(changed_prefix),
            "moved_tail_changed": len(changed_tail),
            "appended": len(appended),
        },
        "classified": {
            "prefix": _classified(changed_prefix, prefix_plan),
            "moved_tail": _classified(changed_tail, tail_plan),
        },
        "declared_but_unchanged": {
            "prefix": _missing_declared(changed_prefix, prefix_plan),
            "moved_tail": _missing_declared(changed_tail, tail_plan),
        },
        "unexpected": {
            "prefix": [list(item) for item in ranges(unexpected_prefix)],
            "moved_tail": [list(item) for item in ranges(unexpected_tail)],
            "append_missing": [list(item) for item in ranges(append_missing)],
            "append_extra": [list(item) for item in ranges(append_extra)],
        },
    }
    result["ok"] = not any(result["unexpected"].values())
    output_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    if not result["ok"]:
        raise ValueError(f"undeclared image changes: {result['unexpected']}")
    return result
