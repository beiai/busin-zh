"""Unpack whole images from the picture list and repack edited PNGs for the build."""
import json
import struct
import tempfile
from pathlib import Path

from busin import text as decode_msg
from busin.disc import BIN, children, chunk_row, entry, read_pack, read_user
from busin.paths import DATA, BUILD
from busin.tmx import PSMCT16, PSMT4, TMX_TAG, read_tmx, repack, save_png

try:
    from PIL import Image
except ImportError:
    Image = None


WHOLE_KEYS = {"name", "dir", "chunk", "offset", "width", "height", "palette", "size", "word_count_header"}
PART_KEYS = {"name", "dir", "chunk", "offset", "slot", "width", "height", "rects"}
RECT_KEYS = {"name", "x", "y", "width", "height", "palette"}
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
    if type(palette) is not int or not 0 <= palette <= 15:
        raise ValueError(f"{name} rect {label} palette")
    return label


def _claim(seen: dict, chunk: int, relative: int, name: str) -> None:
    owner = seen.get((chunk, relative))
    if owner is not None:
        raise ValueError(f"offset {relative:#x} is claimed by {owner} and {name}")
    seen[(chunk, relative)] = name


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
    seen = {}
    names = set()
    files = set()

    def claim_file(folder, stem: str) -> None:
        key = (folder or "", stem)
        if key in files:
            raise ValueError(f"{key[0]}/{stem} is used twice")
        files.add(key)
    for item in whole:
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
            claim_file(item.get("dir"), name)
        if "word_count_header" in item and ("size" not in item or type(item["word_count_header"]) is not bool):
            raise ValueError(f"{name} word_count_header needs a raw block and boolean value")
        _claim(seen, *_place(item, name), name)
    for item in part:
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
        _pixels(item, name)
        _claim(seen, *_place(item, name), name)
        rects = item["rects"]
        if not isinstance(rects, list) or not rects:
            raise ValueError(f"{name} rects")
        for rect in rects:
            if not isinstance(rect, dict) or set(rect) != RECT_KEYS:
                raise ValueError(f"{name} rect")
            claim_file(item["dir"], _rect(rect, name, item["width"], item["height"]))
    return data


def picture_dir(base: Path, item: dict) -> Path:
    folder = item.get("dir")
    if not folder:
        return base
    return base / folder


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


def repack_bytes(template: bytes, png_path: Path, palette: int) -> bytes:
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        source = folder / "template.tmx"
        output = folder / "repacked.tmx"
        source.write_bytes(template)
        repack(source, png_path, output, palette)
        data = output.read_bytes()
    if len(data) != len(template):
        raise ValueError(f"{png_path.name} repacked to {len(data)} bytes, template is {len(template)}")
    return data


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


def export_pictures(read, manifest: dict, root: Path, row) -> None:
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
            keep_zh(original)


def export_manifest(path: Path | None = None) -> None:
    path = path or (DATA / "picture" / "images.json")
    manifest = load_images(path)
    with BIN.open("rb") as source:
        lba, size = container_extent(source, manifest["container"])
        export_pictures(
            lambda offset, count: read_container_range(source, lba, size, offset, count),
            manifest,
            path.parent,
            lambda chunk: chunk_row(source, chunk),
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
    assert {item["dir"] for item in listed["part"]} == {"town"}
    assert [item["name"] for item in listed["part"]] == ["Tw_map02"]
    assert {rect["palette"] for rect in listed["part"][0]["rects"]} == {1}
    assert all(PICTURE_KEYS <= set(item) for item in [*listed["whole"], *listed["part"]])
    assert scene_output(DATA / "picture" / "images.json") / "title" == BUILD / "picture" / "title"
    if BIN.exists():
        with BIN.open("rb") as src:
            disc_row = lambda chunk: chunk_row(src, chunk)
            for item in [*listed["whole"], *listed["part"]]:
                offset, relative, count = locate_pack(item, disc_row)
                header = read_pack(src, offset, 0x40)
                comment = header[0x24:0x40].split(b"\x00", 1)[0]
                stem = item["name"].encode()
                assert comment in (stem + b".tga", stem + b".tim")
                assert struct.unpack_from("<HH", header, 0x12) == (item["width"], item["height"])
                assert relative + struct.unpack_from("<I", header, 4)[0] <= count * 2048


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


def read_tmx_bytes(data: bytes):
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "slice.tmx"
        path.write_bytes(data)
        return read_tmx(path)
