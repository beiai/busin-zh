"""Unpack picture-list images and repack edited whole images or crops."""
import json
import struct
import tempfile
from pathlib import Path

from busin import text as decode_msg
from busin.disc import BIN, ELF_LBA, FILE_BASE, children, chunk_row, entry, read_pack, read_user
from busin.paths import DATA, BUILD
from busin.picture_text import DEFAULT_TEXT_STYLE
from busin.tmx import (
    PSMCT16, PSMCT32, PSMT4, PSMT8, TMX_TAG, encode_psmt4, map_png_to_indices,
    TmxImage, read_tmx, read_tmx_bytes, repack_bytes, save_png,
)

try:
    from PIL import Image
except ImportError:
    Image = None


WHOLE_KEYS = {"name", "dir", "chunk", "offset", "width", "height", "palette", "size", "word_count_header"}
PART_KEYS = {"name", "dir", "chunk", "offset", "slot", "width", "height", "rects"}
# PACKDATA keeps translated parent textures in duplicate chunks. Keep every
# disc location in code and check all copies before repacking.
PART_SOURCE_COPIES = {
    "bar_00": ((0, "0x30cc0"), (12, "0x2ca40")),
    "guild_00": ((12, "0x24800"),),
}
# Each slot belongs to a separate texture-handle row. Keep the game addresses
# here and check the manifest row index against the verified loader position.
PART_HANDLES = {
    "bar_00": (0x442650, 0),
    "ali_00": (0x513D50, 0),
    "ali_01": (0x513D50, 1),
    "ali_02": (0x513D50, 2),
    "ali_03": (0x513D50, 3),
    "yado3": (0x442650, 0),
}
RECT_KEYS = {"name", "x", "y", "width", "height", "palette"}
RECT_TEXT_KEYS = RECT_KEYS | {"source", "text"}
PICTURE_KEYS = {"name", "dir", "chunk", "offset", "width", "height"}


def _folder_name(name: str, folder) -> None:
    if not isinstance(folder, str) or folder in ("", ".", "..") or Path(folder).name != folder:
        raise ValueError(f"{name} dir is not a folder name")


def _picture_name(name) -> str:
    if not isinstance(name, str) or Path(name).name != name or name in ("", ".", ".."):
        raise ValueError(f"invalid texture name {name!r}")
    return name


def _place(item: dict, name: str) -> tuple[int, int]:
    chunk = item.get("chunk")
    offset = item.get("offset")
    if type(chunk) is not int or chunk < 0 or not isinstance(offset, str):
        raise ValueError(f"{name} chunk")
    try:
        relative = int(offset, 0)
    except ValueError:
        raise ValueError(f"{name} offset") from None
    if relative < 0:
        raise ValueError(f"{name} offset")
    return chunk, relative


def _pixels(item: dict, name: str) -> None:
    width, height = item.get("width"), item.get("height")
    if type(width) is not int or type(height) is not int or width <= 0 or height <= 0:
        raise ValueError(f"{name} size")


def _rect(rect: dict, name: str, width: int, height: int) -> str:
    label = _picture_name(rect.get("name"))
    x, y, box_width, box_height = rect["x"], rect["y"], rect["width"], rect["height"]
    palette = rect["palette"]
    if (type(x) is not int or type(y) is not int or type(box_width) is not int or type(box_height) is not int
            or x < 0 or y < 0 or box_width <= 0 or box_height <= 0
            or x + box_width > width or y + box_height > height):
        raise ValueError(f"{name} rect {label}")
    if type(palette) is not int or not 0 <= palette <= 255:
        raise ValueError(f"{name} rect {label} palette")
    return label


def _claim(seen: dict, chunk: int, relative: int, name: str) -> None:
    owner = seen.get((chunk, relative))
    if owner is not None:
        raise ValueError(f"offset {relative:#x} is claimed by {owner} and {name}")
    seen[(chunk, relative)] = name


def translated_rect(item: dict, rect: dict) -> dict:
    """Keep original-disc crops intact; resolve reused translated crop positions."""
    if "reuse" not in rect:
        return rect
    reuse = rect["reuse"]
    name = f"{item['name']}/{rect['name']}"
    if (not isinstance(reuse, dict) or set(reuse) != {"name", "x", "y"}
            or not isinstance(reuse["name"], str)
            or any(type(reuse[axis]) is not int or reuse[axis] < 0 for axis in ("x", "y"))):
        raise ValueError(f"{name} invalid reuse")
    source = next((r for r in item["rects"] if r["name"] == reuse["name"]), None)
    if source is None:
        raise ValueError(f"{name} reuse source {reuse['name']} is missing from this texture")
    if "reuse" in source:
        raise ValueError(f"{name} reuse source must not itself reuse another crop")
    if source["palette"] != rect["palette"]:
        raise ValueError(f"{name} reuse palette differs from {source['name']}")
    if (reuse["x"] + rect["width"] > source["width"]
            or reuse["y"] + rect["height"] > source["height"]):
        raise ValueError(f"{name} reuse is outside {source['name']}")
    return {**rect, "x": source["x"] + reuse["x"], "y": source["y"] + reuse["y"]}


def _validate_whole_item(item, names: set, files: set, seen: dict) -> None:
    if not isinstance(item, dict) or not set(item) <= WHOLE_KEYS:
        raise ValueError("whole image")
    name = _picture_name(item.get("name"))
    if name in names:
        raise ValueError(f"duplicate texture name {name}")
    names.add(name)
    if "dir" in item:
        _folder_name(name, item["dir"])
    if ("palette" in item) == ("size" in item):
        raise ValueError(f"{name} needs exactly one of palette or size")
    if "size" in item:
        size = item["size"]
        if type(size) is not int or size <= 0 or "width" in item or "height" in item:
            raise ValueError(f"{name} size must be a positive integer")
    else:
        palette = item.get("palette")
        if type(palette) is not int or not 0 <= palette <= 15:
            raise ValueError(f"{name} palette must be an integer from 0 to 15")
        _pixels(item, name)
        _claim_file(files, item.get("dir"), name)
    if "word_count_header" in item and (
        "size" not in item or type(item["word_count_header"]) is not bool
    ):
        raise ValueError(
            f"{name} word_count_header needs a raw block and boolean value"
        )
    _claim(seen, *_place(item, name), name)


def _claim_file(files: set, folder: str | None, stem: str) -> None:
    key = (folder or "", stem)
    if key in files:
        raise ValueError(f"{key[0]}/{stem} is used twice")
    files.add(key)


def _validate_part_item(item, names: set, files: set, seen: dict) -> None:
    if not isinstance(item, dict) or set(item) != PART_KEYS:
        raise ValueError("partial image")
    name = _picture_name(item.get("name"))
    if name in names:
        raise ValueError(f"duplicate texture name {name}")
    names.add(name)
    _folder_name(name, item.get("dir"))
    slot = item["slot"]
    if type(slot) is not int or slot < 0:
        raise ValueError(f"{name} slot")
    handle = PART_HANDLES.get(name)
    if handle is not None and slot != handle[1]:
        raise ValueError(
            f"{name} slot {slot} is not handle table {handle[0]:#x} slot {handle[1]}"
        )
    _pixels(item, name)
    _claim(seen, *_place(item, name), name)
    rects = item["rects"]
    if not isinstance(rects, list) or not rects:
        raise ValueError(f"{name} rects")
    for rect in rects:
        if not isinstance(rect, dict) or set(rect) - {"reuse"} not in (
            RECT_KEYS,
            RECT_TEXT_KEYS,
        ):
            raise ValueError(f"{name} rect")
        if "text" in rect and (
            not isinstance(rect["source"], str)
            or not isinstance(rect["text"], str)
            or "\n" in rect["text"]
        ):
            raise ValueError(f"{name} rect text")
        _claim_file(
            files, item["dir"], _rect(rect, name, item["width"], item["height"])
        )
    for rect in rects:
        translated_rect(item, rect)
    _validate_translated_overlaps(item)


def _box(rect: dict) -> tuple[int, int, int, int]:
    return (rect["x"], rect["y"], rect["x"] + rect["width"], rect["y"] + rect["height"])


def _boxes_overlap(
    left: tuple[int, int, int, int], right: tuple[int, int, int, int]
) -> bool:
    return (
        left[0] < right[2]
        and right[0] < left[2]
        and left[1] < right[3]
        and right[1] < left[3]
    )


def _box_contains(
    outer: tuple[int, int, int, int], inner: tuple[int, int, int, int]
) -> bool:
    return (
        outer[0] <= inner[0]
        and outer[1] <= inner[1]
        and inner[2] <= outer[2]
        and inner[3] <= outer[3]
    )


def _validate_translated_overlaps(item: dict) -> None:
    by_name = {rect["name"]: rect for rect in item["rects"]}
    targets = [(rect, translated_rect(item, rect)) for rect in item["rects"]]
    for index, (left, left_target) in enumerate(targets):
        for right, right_target in targets[index + 1 :]:
            if not _boxes_overlap(_box(left_target), _box(right_target)):
                continue
            source = by_name.get(left.get("reuse", {}).get("name"))
            allowed = (
                source is right and _box_contains(_box(right), _box(left_target))
            ) or (
                by_name.get(right.get("reuse", {}).get("name")) is left
                and _box_contains(_box(left), _box(right_target))
            )
            if not allowed:
                raise ValueError(
                    f"{item['name']} translated rects {left['name']} and {right['name']} overlap"
                )


def load_images(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if set(data) != {"container", "whole", "part"}:
        raise ValueError("image list needs container, whole, and part")
    if data["container"] != "PACKDATA.CIG":
        raise ValueError("image container must be PACKDATA.CIG")
    whole = data["whole"]
    part = data["part"]
    if not isinstance(whole, list) or not whole:
        raise ValueError("whole images must be a non-empty list")
    if not isinstance(part, list):
        raise ValueError("part must be a list")
    names, files, seen = set(), set(), {}
    for item in whole:
        _validate_whole_item(item, names, files, seen)
    for item in part:
        _validate_part_item(item, names, files, seen)
    return data


def picture_dir(base: Path, item: dict) -> Path:
    folder = item.get("dir")
    if not folder:
        return base
    return base / folder


def part_source_items(item: dict) -> list[dict]:
    """Return the manifest item at its primary and verified duplicate locations."""
    copies = PART_SOURCE_COPIES.get(item["name"], ())
    return [item, *(
        {**item, "chunk": chunk, "offset": offset}
        for chunk, offset in copies
    )]


def png_name(item: dict) -> str:
    return f"{item['name']}.pal{item['palette']:02d}.png"


def zh_name(stem: str) -> str:
    return f"{stem}.zh.png"


def keep_zh(original: Path) -> None:
    zh = original.with_name(zh_name(original.stem))
    if zh.exists():
        return
    zh.write_bytes(original.read_bytes())


def scene_output(manifest_path: Path) -> Path:
    folder = manifest_path.parent.resolve()
    try:
        relative = folder.relative_to(DATA.resolve())
    except ValueError:
        raise ValueError(f"manifest must be inside {DATA}") from None
    return BUILD / relative


def declared_size(header: bytes) -> int:
    if len(header) < 16:
        raise ValueError("short TMX header")
    flag, _user_id, size, tag = struct.unpack_from("<HHI4s", header, 0)
    if flag != 2 or tag != TMX_TAG or size < 0x40:
        raise ValueError("offset is not a TMX0 texture")
    return size


def read_tmx_at(read, offset: int) -> bytes:
    size = declared_size(read(offset, 16))
    data = read(offset, size)
    if len(data) != size or declared_size(data[:16]) != size:
        raise ValueError(f"TMX at {offset:#x} is truncated")
    return data


def locate_pack(item: dict, row, size: int | None = None) -> tuple[int, int, int]:
    start, count = row(item["chunk"])
    relative = int(item["offset"], 0)
    if (type(start) is not int or type(count) is not int or start < 0 or count <= 0
            or relative < 0 or (size is not None and relative + size > count * 2048)):
        raise ValueError(f"{item['name']} does not fit chunk {item['chunk']}")
    return start * 2048 + relative, relative, count


def check_size(item: dict, blob: bytes) -> None:
    width, height = struct.unpack_from("<HH", blob, 0x12)
    if (width, height) != (item["width"], item["height"]):
        raise ValueError(f"{item['name']} is {width}x{height}")


def entry_slices(read, item: dict, row) -> tuple[int, bytes]:
    if "size" in item:
        offset, _relative, _count = locate_pack(item, row, item["size"])
        return offset, read(offset, item["size"])
    offset, relative, count = locate_pack(item, row)
    blob = read_tmx_at(read, offset)
    if relative + len(blob) > count * 2048:
        raise ValueError(f"{item['name']} does not fit chunk {item['chunk']}")
    check_size(item, blob)
    return offset, blob


def ranges_overlap(items: list[tuple[str, int, int]]) -> None:
    ordered = sorted(items, key=lambda item: item[1])
    for (left_name, left, left_size), (right_name, right, _right_size) in zip(ordered, ordered[1:]):
        if left + left_size > right:
            raise ValueError(f"{left_name} and {right_name} overlap")


def collect(read, manifest: dict, row) -> list[tuple[dict, bytes]]:
    found = []
    spans = []
    for item in manifest["whole"]:
        offset, blob = entry_slices(read, item, row)
        found.append((item, blob))
        spans.append((item["name"], offset, len(blob)))
    ranges_overlap(spans)
    return found


def file_reader(path: Path, limit: int):
    def read(offset: int, size: int) -> bytes:
        if offset < 0 or size < 0 or offset + size > limit:
            raise ValueError(f"read {offset:#x}+{size} is outside the container")
        with path.open("rb") as source:
            source.seek(offset)
            data = source.read(size)
        if len(data) != size:
            raise ValueError(f"short read at {offset:#x}")
        return data
    return read


def unpack_reader(read, manifest: dict, out_dir: Path, row) -> None:
    for item, blob in collect(read, manifest, row):
        dest = picture_dir(out_dir, item)
        dest.mkdir(parents=True, exist_ok=True)
        if "size" in item:
            msg_path = dest / f"{item['name']}.msg"
            msg_path.write_bytes(blob)
            report = decode_msg.describe(blob, item.get("word_count_header", False))
            try:
                report["path"] = msg_path.name
            except ValueError:
                report["path"] = msg_path.name
            json_path = dest / f"{item['name']}.json"
            json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            print(
                f"{msg_path}: {report['size']} bytes, "
                f"{report['terminated_segments']} segments, {report['clean_messages']} clean"
            )
            continue
        tmx_path = dest / f"{item['name']}.tmx"
        tmx_path.write_bytes(blob)
        image = read_tmx(tmx_path)
        if item["palette"] >= len(image.palettes):
            raise ValueError(f"{item['name']} has no palette {item['palette']}")
        png_path = dest / png_name(item)
        save_png(image, item["palette"], png_path)
        print(
            f"{tmx_path}: {image.width}x{image.height}, "
            f"comment={image.comment!r}, palette={item['palette']}"
        )


def replacement_spans(read, root: Path, row) -> list[tuple[int, bytes]]:
    """Repacked TMX bytes for whole-image .zh.png files. A missing file leaves that picture alone."""
    path = root / "images.json"
    if not path.is_file():
        return []
    spans = []
    for item in load_images(path)["whole"]:
        if "size" in item:
            continue
        png_path = picture_dir(root, item) / zh_name(item["name"])
        if not png_path.is_file():
            continue
        offset, blob = entry_slices(read, item, row)
        spans.append((offset, repack_bytes(blob, png_path, item["palette"])))
    return spans


def _part_palette(image, rect: dict) -> tuple[list[tuple[int, ...]], bool]:
    if rect["palette"] >= len(image.palettes):
        raise ValueError(f"{rect['name']} has no palette {rect['palette']}")
    palette = image.palettes[rect["palette"]]
    if image.pixel_format == PSMT4:
        return [color[:3] for color in palette], False
    if image.pixel_format == PSMT8:
        return palette, True
    raise ValueError(f"{rect['name']} uses unsupported pixel format 0x{image.pixel_format:02X}")


def _encode_part_pixels(image: TmxImage, pixels: list[int]) -> bytes:
    if image.pixel_format == PSMT4:
        if image.palette_format != PSMCT16 or image.width % 2:
            raise ValueError(f"{image.comment} has an unsupported 4-bit layout")
        return encode_psmt4(pixels)
    if image.pixel_format == PSMT8 and image.palette_format == PSMCT32:
        return bytes(pixels)
    raise ValueError(f"{image.comment} has an unsupported pixel layout")


def _part_span(read, item: dict, root: Path, row) -> tuple[int, bytes] | None:
    """Paint all translated crops into one copy of their parent texture."""
    offset, blob = entry_slices(read, item, row)
    image = read_tmx_bytes(blob)
    folder = picture_dir(root, item)
    # Derived PNGs are previews of the same pixels, not independent edits.
    # Reject a stale child before palette mapping could hide a disagreement.
    for rect in item["rects"]:
        if "reuse" in rect:
            expected = _reused_bitmap(item, rect, folder)
            path = folder / zh_name(rect["name"])
            if not path.is_file():
                raise ValueError(f"{item['name']} translated crop is missing: {path}")
            with Image.open(path) as bitmap:
                if bitmap.size != expected.size or bitmap.convert("RGBA").tobytes() != expected.tobytes():
                    raise ValueError(
                        f"{item['name']} reused crop {rect['name']} disagrees with "
                        f"{rect['reuse']['name']}; run export-pictures to regenerate it")
    replacements = {}
    changed = False
    for rect in item["rects"]:
        zh = folder / zh_name(rect["name"])
        if not zh.is_file():
            if "text" in rect:
                raise ValueError(f"{item['name']} translated crop is missing: {zh}")
            continue
        target = translated_rect(item, rect)
        x, y, width, height = target["x"], target["y"], target["width"], target["height"]
        original = image.pixels
        values = tuple(
            original[(y + row_at) * image.width + x + col]
            for row_at in range(height)
            for col in range(width)
        )
        palette, alpha = _part_palette(image, rect)
        indices, _error = map_png_to_indices(
            zh, palette, width, height, alpha=alpha, original=values)
        for index, (value, old) in enumerate(zip(indices, values)):
            at = (y + index // width) * image.width + x + index % width
            owner = replacements.get(at)
            if owner is not None and owner[0] != value:
                raise ValueError(
                    f"{item['name']} rects {owner[1]} and {rect['name']} disagree at pixel {at}")
            replacements[at] = (value, rect["name"])
            changed = changed or value != old
    if not changed:
        return None
    pixels = list(image.pixels)
    for at, (value, _name) in replacements.items():
        pixels[at] = value
    palette_size = 32 if image.pixel_format == PSMT4 else 1024
    pixel_size = len(image.pixels) // 2 if image.pixel_format == PSMT4 else len(image.pixels)
    pixel_at = 0x40 + len(image.palettes) * palette_size
    out = bytearray(blob)
    out[pixel_at:pixel_at + pixel_size] = _encode_part_pixels(image, pixels)
    if len(out) != len(blob) or out[:pixel_at] != blob[:pixel_at]:
        raise ValueError(f"{item['name']} part repack changed its header")
    return offset, bytes(out)


def part_spans(read, root: Path, row) -> list[tuple[int, bytes]]:
    """Repack translated part crops into every verified parent TMX copy."""
    path = root / "images.json"
    if not path.is_file():
        return []
    spans = []
    locations = []
    for item in load_images(path)["part"]:
        sources = part_source_items(item)
        primary_blob = entry_slices(read, sources[0], row)[1]
        for copy_index, source_item in enumerate(sources[1:], 1):
            copy_blob = entry_slices(read, source_item, row)[1]
            if copy_blob != primary_blob:
                raise ValueError(f"{item['name']} source copy {copy_index} differs")
        for copy_index, source_item in enumerate(sources):
            found = _part_span(read, source_item, root, row)
            if found is None:
                continue
            offset, data = found
            spans.append(found)
            label = item["name"] if copy_index == 0 else f"{item['name']} copy {copy_index}"
            locations.append((label, offset, len(data)))
    ranges_overlap(locations)
    return spans


def container_extent(source, name: str) -> tuple[int, int]:
    pvd = read_user(source, 16, 1)
    if pvd[:7] != b"\x01CD001\x01":
        raise ValueError("missing ISO9660 primary volume descriptor")
    root = entry(pvd[156:156 + pvd[156]])
    found = next((record for record in children(source, root) if record[0].upper() == name.upper()), None)
    if found is None:
        raise ValueError(f"ISO root has no {name}")
    return found[1], found[2]


def read_container_range(source, lba: int, size: int, offset: int, count: int) -> bytes:
    if offset < 0 or count < 0 or offset + count > size:
        raise ValueError(f"read {offset:#x}+{count} is outside the container")
    start = offset // 2048
    skip = offset % 2048
    sectors = (skip + count + 2047) // 2048
    data = read_user(source, lba + start, sectors)[skip:skip + count]
    if len(data) != count:
        raise ValueError(f"short container read at {offset:#x}")
    return data


def unpack_manifest(manifest_path: Path) -> None:
    manifest = load_images(manifest_path)
    out_dir = scene_output(manifest_path)
    with BIN.open("rb") as source:
        lba, size = container_extent(source, manifest["container"])
        unpack_reader(
            lambda offset, count: read_container_range(source, lba, size, offset, count),
            manifest,
            out_dir,
            lambda chunk: chunk_row(source, chunk),
        )


def save_crop(image, rect: dict, output: Path) -> None:
    if rect["palette"] >= len(image.palettes):
        raise ValueError(f"{rect['name']} has no palette {rect['palette']}")
    x, y, width, height = rect["x"], rect["y"], rect["width"], rect["height"]
    if x + width > image.width or y + height > image.height:
        raise ValueError(f"{rect['name']} is outside the picture")
    palette = image.palettes[rect["palette"]]
    bitmap = Image.new("RGBA", (width, height))
    bitmap.putdata([
        palette[image.pixels[(y + row) * image.width + x + col]]
        for row in range(height)
        for col in range(width)
    ])
    output.parent.mkdir(parents=True, exist_ok=True)
    bitmap.save(output)


def _reused_bitmap(item: dict, rect: dict, folder: Path):
    translated_rect(item, rect)
    reuse = rect["reuse"]
    source = next(r for r in item["rects"] if r["name"] == reuse["name"])
    path = folder / zh_name(source["name"])
    if not path.is_file():
        raise ValueError(f"{item['name']} reuse source translated crop is missing: {path}")
    with Image.open(path) as bitmap:
        if bitmap.size != (source["width"], source["height"]):
            raise ValueError(f"{item['name']} reuse source {source['name']} size changed")
        x, y = reuse["x"], reuse["y"]
        return bitmap.convert("RGBA").crop((x, y, x + rect["width"], y + rect["height"]))


def export_pictures(read, manifest: dict, root: Path, row, text_style: str = DEFAULT_TEXT_STYLE, force_zh: bool = False) -> None:
    """Write comparison PNGs. The original is replaced; a .zh.png is added only when missing."""
    for item in manifest["whole"]:
        if "size" in item:
            continue
        _offset, blob = entry_slices(read, item, row)
        image = read_tmx_bytes(blob)
        if item["palette"] >= len(image.palettes):
            raise ValueError(f"{item['name']} has no palette {item['palette']}")
        dest = picture_dir(root, item)
        dest.mkdir(parents=True, exist_ok=True)
        original = dest / f"{item['name']}.png"
        save_png(image, item["palette"], original)
        keep_zh(original)
    for item in manifest["part"]:
        _offset, blob = entry_slices(read, item, row)
        image = read_tmx_bytes(blob)
        dest = picture_dir(root, item)
        dest.mkdir(parents=True, exist_ok=True)
        for rect in item["rects"]:
            original = dest / f"{rect['name']}.png"
            save_crop(image, rect, original)
            translated = dest / zh_name(original.stem)
            if "reuse" in rect:
                continue
            if "text" in rect:
                if (force_zh or not translated.is_file()
                        or translated.read_bytes() == original.read_bytes()):
                    from busin.picture_text.registry import render_initial
                    render_initial(item, rect, image, translated, text_style)
            else:
                keep_zh(original)
        # Render all sources first so manifest order cannot affect derived crops.
        for rect in item["rects"]:
            if "reuse" in rect:
                _reused_bitmap(item, rect, dest).save(dest / zh_name(rect["name"]))


def export_manifest(path: Path | None = None, text_style: str = DEFAULT_TEXT_STYLE, force_zh: bool = False) -> None:
    path = path or (DATA / "picture" / "images.json")
    manifest = load_images(path)
    with BIN.open("rb") as source:
        lba, size = container_extent(source, manifest["container"])
        export_pictures(
            lambda offset, count: read_container_range(source, lba, size, offset, count),
            manifest,
            path.parent,
            lambda chunk: chunk_row(source, chunk),
            text_style,
            force_zh,
        )


def make_tmx(comment: bytes, pixels: bytes, user_id: int) -> bytes:
    palette = b"".join(struct.pack("<H", value) for value in range(16))
    body = struct.pack(
        "<BBHHBBHBBII28s", 1, PSMCT16, 2, 2, PSMT4, 0, 0, 0, 0, 0, 0, comment
    )
    payload = body + palette + pixels
    return struct.pack("<HHI4s", 2, user_id, 16 + len(payload), TMX_TAG) + bytes(4) + payload


def self_test() -> None:
    if Image is None:
        raise RuntimeError("Pillow is required")
    first = make_tmx(b"a.tim", bytes((0x21, 0x43)), 1)
    second = make_tmx(b"b.tim", bytes((0x21, 0x43)), 2)
    raw = bytes.fromhex("0005003F0040FFFF031EFFFF")
    blob = first + second + raw
    def row(chunk):
        if chunk != 0:
            raise ValueError(chunk)
        return 0, (len(blob) + 2047) // 2048

    manifest = {
        "container": "PACKDATA.CIG",
        "whole": [
            {"name": "a", "chunk": 0, "offset": "0x0", "width": 2, "height": 2, "palette": 0},
            {"name": "b", "chunk": 0, "offset": hex(len(first)), "width": 2, "height": 2, "palette": 0},
            {"name": "note", "chunk": 0, "offset": hex(len(first) + len(second)), "size": len(raw), "word_count_header": True},
        ],
        "part": [],
    }
    try:
        load_manifest_conflict()
    except ValueError as error:
        if "claimed" not in str(error):
            raise
    else:
        raise AssertionError("duplicate offsets were accepted")
    try:
        load_stem_clash()
    except ValueError as error:
        if "used twice" not in str(error):
            raise
    else:
        raise AssertionError("duplicate picture names were accepted")

    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        manifest_path = folder / "images.json"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        assert load_images(manifest_path) == manifest
        source = folder / "source.bin"
        preview = folder / "preview"
        source.write_bytes(blob)
        read = file_reader(source, len(blob))
        unpack_reader(read, manifest, preview, row)
        image = read_tmx(preview / "a.tmx")
        assert image.comment == "a.tim" and image.pixels == (1, 2, 3, 4)
        assert (preview / "note.msg").read_bytes() == raw
        note = json.loads((preview / "note.json").read_text(encoding="utf-8"))
        assert note["path"] == "note.msg" and note["clean_messages"] == 1
        assert note["messages"][0]["offset"] == 2
        assert note["messages"][1]["clean"] is False

        assert replacement_spans(read, folder, row) == []

        palette = image.palettes[0]
        changed = Image.new("RGBA", (2, 2))
        changed.putdata([palette[5], palette[2], palette[3], palette[4]])
        changed.save(folder / "a.zh.png")
        spans = replacement_spans(read, folder, row)
        assert [(offset, len(data)) for offset, data in spans] == [(0, len(first))]
        decoded = read_tmx_bytes(spans[0][1])
        png = changed.convert("RGBA")
        assert [decoded.palettes[0][pixel] for pixel in decoded.pixels] == list(png.get_flattened_data())

        part_root = folder / "parts"
        part_doc = {
            "container": "PACKDATA.CIG",
            "whole": [
                {"name": "a", "dir": "title", "chunk": 0, "offset": "0x0", "width": 2, "height": 2, "palette": 0},
            ],
            "part": [{
                "name": "tile", "dir": "town", "chunk": 0, "offset": hex(len(first)), "slot": 0,
                "width": 2, "height": 2,
                "rects": [
                    {"name": "left", "x": 0, "y": 0, "width": 1, "height": 1, "palette": 0},
                    {"name": "right", "x": 1, "y": 0, "width": 1, "height": 1, "palette": 0},
                ],
            }],
        }
        part_root.mkdir()
        (part_root / "town").mkdir()
        (part_root / "images.json").write_text(json.dumps(part_doc), encoding="utf-8")
        assert part_spans(read, part_root, row) == []
        part_palette = image.palettes[0]
        Image.new("RGB", (1, 1), part_palette[3][:3]).save(part_root / "town" / "left.zh.png")
        Image.new("RGB", (1, 1), part_palette[4][:3]).save(part_root / "town" / "right.zh.png")
        part_found = part_spans(read, part_root, row)
        assert [(offset, len(data)) for offset, data in part_found] == [(len(first), len(second))]
        part_image = read_tmx_bytes(part_found[0][1])
        assert part_image.pixels == (3, 4, 3, 4)
        assert part_found[0][1][:0x50] == second[:0x50]
        missing_doc = json.loads(json.dumps(part_doc))
        missing_doc["part"][0]["rects"][0]["source"] = "原文"
        missing_doc["part"][0]["rects"][0]["text"] = "译文"
        missing_root = folder / "missing-part"
        missing_root.mkdir()
        (missing_root / "images.json").write_text(json.dumps(missing_doc), encoding="utf-8")
        try:
            part_spans(read, missing_root, row)
        except ValueError as error:
            assert "translated crop is missing" in str(error)
        else:
            raise AssertionError("a missing translated part crop was accepted")

        overlap_doc = json.loads(json.dumps(part_doc))
        overlap_doc["part"][0]["rects"][0]["width"] = 2
        overlap_doc["part"][0]["rects"][1] = {
            "name": "other", "x": 1, "y": 0, "width": 1, "height": 1, "palette": 0,
        }
        overlap_root = folder / "overlap"
        overlap_root.mkdir()
        (overlap_root / "images.json").write_text(json.dumps(overlap_doc), encoding="utf-8")
        (overlap_root / "town").mkdir()
        Image.new("RGB", (2, 1), part_palette[3][:3]).save(overlap_root / "town" / "left.zh.png")
        Image.new("RGB", (1, 1), part_palette[5][:3]).save(overlap_root / "town" / "other.zh.png")
        try:
            part_spans(read, overlap_root, row)
        except ValueError as error:
            assert "translated rects" in str(error) and "overlap" in str(error)
        else:
            raise AssertionError("overlapping part crops with different indices were accepted")

        export_root = folder / "out"
        export_doc = {
            "container": "PACKDATA.CIG",
            "whole": [
                {"name": "a", "dir": "title", "chunk": 0, "offset": "0x0", "width": 2, "height": 2, "palette": 0},
            ],
            "part": [{
                "name": "tile", "dir": "town", "chunk": 0, "offset": hex(len(first)), "slot": 0,
                "width": 2, "height": 2,
                "rects": [{"name": "top", "x": 0, "y": 0, "width": 2, "height": 2, "palette": 0}],
            }],
        }
        export_pictures(read, export_doc, export_root, row)
        original = export_root / "title" / "a.png"
        translated = export_root / "title" / "a.zh.png"
        crop = export_root / "town" / "top.png"
        assert translated.read_bytes() == original.read_bytes()
        assert (export_root / "town" / "top.zh.png").read_bytes() == crop.read_bytes()
        kept = b"kept-zh"
        translated.write_bytes(kept)
        (export_root / "town" / "top.zh.png").write_bytes(kept)
        mutated = bytearray(blob)
        mutated[len(first) - 1] ^= 0x11
        mutated[len(first) + len(second) - 1] ^= 0x11
        source.write_bytes(mutated)
        before = original.read_bytes()
        before_crop = crop.read_bytes()
        export_pictures(file_reader(source, len(mutated)), export_doc, export_root, row)
        assert original.read_bytes() != before and crop.read_bytes() != before_crop
        assert translated.read_bytes() == kept
        assert (export_root / "town" / "top.zh.png").read_bytes() == kept
        translated.unlink()
        export_pictures(file_reader(source, len(mutated)), export_doc, export_root, row)
        assert translated.read_bytes() == original.read_bytes()
    listed = load_images(DATA / "picture" / "images.json")
    assert {item["dir"] for item in listed["whole"]} == {"title"}
    assert {item["dir"] for item in listed["part"]} == {
        "town", "menu", "status", "guild", "shop", "temple", "bar", "ali", "yado",
    }
    assert [item["name"] for item in listed["part"]] == [
        "Tw_map02", "Tw_map01", "cock_00", "cock_01", "status00",
        "guild_00", "guild_a0", "guild_a1", "shop_00", "temple00",
        "bar_00", "ali_00", "ali_01", "ali_02", "ali_03", "yado3",
    ]
    assert {rect["palette"] for item in listed["part"] for rect in item["rects"]} == {
        1, 2, 3, 6, 7, 10, 12, 17, 21,
    }
    cock01_rects = {
        rect["name"]: rect for item in listed["part"] if item["name"] == "cock_01"
        for rect in item["rects"]
    }
    assert {name: (cock01_rects[name]["source"], cock01_rects[name]["text"])
            for name in ("first_allied", "second_allied", "skill", "front_back_change")} == {
        "first_allied": ("1st アレイド", "第1连携"),
        "second_allied": ("2nd アレイド", "第2连携"),
        "skill": ("スキル", "技能"),
        "front_back_change": ("前後列交代", "前后切换"),
    }
    assert all(PICTURE_KEYS <= set(item) for item in [*listed["whole"], *listed["part"]])
    assert scene_output(DATA / "picture" / "images.json") / "title" == BUILD / "picture" / "title"
    if BIN.exists():
        with BIN.open("rb") as src:
            disc_row = lambda chunk: chunk_row(src, chunk)
            part_found = part_spans(
                lambda offset, size: read_pack(src, offset, size),
                DATA / "picture", disc_row,
            )
            part_offsets = {offset for offset, _data in part_found}
            menu_offsets = {
                locate_pack(item, disc_row)[0]
                for item in listed["part"] if item["dir"] == "menu"
            }
            if not menu_offsets <= part_offsets:
                raise AssertionError("translated menu parent pictures were not repacked")
            source_items = {
                locate_pack(source_item, disc_row)[0]: source_item
                for item in listed["part"]
                for source_item in part_source_items(item)
            }
            for offset, data in part_found:
                _offset, source = entry_slices(
                    lambda at, size: read_pack(src, at, size),
                    source_items[offset],
                    disc_row,
                )
                if data == source or len(data) != len(source):
                    raise AssertionError("part parent repack is empty or changed size")
            for item in [*listed["whole"], *listed["part"]]:
                offset, relative, count = locate_pack(item, disc_row)
                header = read_pack(src, offset, 0x40)
                comment = header[0x24:0x40].split(b"\x00", 1)[0]
                stem = item["name"].encode()
                assert comment in (stem + b".tga", stem + b".tim")
                assert struct.unpack_from("<HH", header, 0x12) == (item["width"], item["height"])
                assert relative + struct.unpack_from("<I", header, 4)[0] <= count * 2048
            at = 0x442810 - FILE_BASE
            table = read_user(src, ELF_LBA + at // 2048, 1)[at % 2048:at % 2048 + 7 * 28]

            def line(row):
                palette = struct.unpack_from("<h", row, 4)[0]
                x, y = row[6], row[7]
                width, height = struct.unpack_from("<hh", row, 8)
                return (x, y, width, height, palette)

            by_name = {item["name"]: item for item in listed["part"]}
            expect = set()
            for index in range(6):
                row = table[index * 28:(index + 1) * 28]
                assert struct.unpack_from("<h", row, 2)[0] == by_name["Tw_map01"]["slot"]
                expect.add(line(row))
            got = {
                (rect["x"], rect["y"], rect["width"], rect["height"], rect["palette"])
                for rect in by_name["Tw_map01"]["rects"]
            }
            assert got == expect
            row = table[6 * 28:7 * 28]
            assert struct.unpack_from("<h", row, 2)[0] == by_name["Tw_map02"]["slot"]
            assert {
                (rect["x"], rect["y"], rect["width"], rect["height"], rect["palette"])
                for rect in by_name["Tw_map02"]["rects"]
            } == {line(row)}

            # Record indexes live with the one module that patches them.
            from busin.patch.picture_rects import (
                COCK01_MENU_RECORDS,
                SHARED_MENU_RECORDS,
            )

            def sprite_table(at, count):
                at -= FILE_BASE
                size = count * 12
                sectors = (at % 2048 + size + 2047) // 2048
                blob = read_user(src, ELF_LBA + at // 2048, sectors)[
                    at % 2048 : at % 2048 + size
                ]
                return lambda index: struct.unpack_from("<6h", blob, index * 12)

            tables = {
                0x583EA0: sprite_table(0x583EA0, 24),
                0x583DAC: sprite_table(0x583DAC, 20),
            }
            indexes_by_texture = {
                "cock_00": {
                    name: record
                    for name, record in SHARED_MENU_RECORDS.items()
                    if name != "front_back_change"
                },
                "cock_01": {
                    **COCK01_MENU_RECORDS,
                    "front_back_change": SHARED_MENU_RECORDS["front_back_change"],
                },
            }
            for name, indexes in indexes_by_texture.items():
                rects = {rect["name"]: rect for rect in by_name[name]["rects"]}
                if name == "cock_01":
                    # The battle hint uses immediate UVs, not a sprite-table row.
                    angle = rects.pop("angle_switch")
                    assert tuple(angle[k] for k in ("x", "y", "width", "height", "palette")) == (
                        152, 72, 104, 16, 21,
                    )
                assert set(rects) == set(indexes)
                for label, record in indexes.items():
                    values = tables[record.table](record.index)
                    rect = rects[label]
                    assert values == (
                        rect["x"],
                        rect["y"],
                        rect["width"],
                        rect["height"],
                        rect["width"],
                        rect["height"],
                    )
                    expected_palette = (
                        2
                        if name == "cock_00"
                        else 17 if label in {"mage_magic", "priest_magic"} else 10
                    )
                    assert rect["palette"] == expected_palette

def load_manifest_conflict() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "images.json"
        path.write_text(json.dumps({
            "container": "PACKDATA.CIG",
            "whole": [
                {"name": "a", "chunk": 0, "offset": "0x0", "width": 2, "height": 2, "palette": 0},
                {"name": "b", "chunk": 0, "offset": "0x0", "width": 2, "height": 2, "palette": 0},
            ],
            "part": [],
        }), encoding="utf-8")
        load_images(path)


def load_stem_clash() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "images.json"
        path.write_text(json.dumps({
            "container": "PACKDATA.CIG",
            "whole": [
                {"name": "top", "dir": "town", "chunk": 0, "offset": "0x10", "width": 2, "height": 2, "palette": 0},
            ],
            "part": [{
                "name": "tile", "dir": "town", "chunk": 0, "offset": "0x0", "slot": 0,
                "width": 2, "height": 2,
                "rects": [{"name": "top", "x": 0, "y": 0, "width": 2, "height": 2, "palette": 0}],
            }],
        }), encoding="utf-8")
        load_images(path)
