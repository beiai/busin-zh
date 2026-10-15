"""Unpack scene manifest entries from PACKDATA and repack edited PNGs for the build."""
import json
import struct
import tempfile
from pathlib import Path

from busin import text as decode_msg
from busin.disc import BIN, children, entry, read_user
from busin.paths import DATA, BUILD
from busin.tmx import PSMCT16, PSMT4, TMX_TAG, read_tmx, repack, save_png

try:
    from PIL import Image
except ImportError:
    Image = None


def load_manifest(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("container") != "PACKDATA.CIG":
        raise ValueError("manifest container must be PACKDATA.CIG")
    files = data.get("files")
    if not isinstance(files, list) or not files:
        raise ValueError("manifest files must be a non-empty list")
    seen = {}
    names = set()
    for item in files:
        name = item.get("name")
        offsets = item.get("offsets")
        palette = item.get("palette")
        if not isinstance(name, str) or Path(name).name != name or name in ("", ".", ".."):
            raise ValueError(f"invalid texture name {name!r}")
        if name in names:
            raise ValueError(f"duplicate texture name {name}")
        names.add(name)
        if ("palette" in item) == ("size" in item):
            raise ValueError(f"{name} needs exactly one of palette or size")
        if "size" in item:
            size = item["size"]
            if type(size) is not int or size <= 0:
                raise ValueError(f"{name} size must be a positive integer")
        elif type(palette) is not int or not 0 <= palette <= 15:
            raise ValueError(f"{name} palette must be an integer from 0 to 15")
        if "word_count_header" in item and ("size" not in item or type(item["word_count_header"]) is not bool):
            raise ValueError(f"{name} word_count_header needs a raw block and boolean value")
        if not isinstance(offsets, list) or not offsets:
            raise ValueError(f"{name} needs at least one offset")
        if len(offsets) != len(set(offsets)):
            raise ValueError(f"{name} has duplicate offsets")
        for offset in offsets:
            if type(offset) is not int or offset < 0:
                raise ValueError(f"{name} offset must be a non-negative integer")
            owner = seen.get(offset)
            if owner is not None:
                raise ValueError(f"offset {offset:#x} is claimed by {owner} and {name}")
            seen[offset] = name
    return data


def png_name(item: dict) -> str:
    return f"{item['name']}.pal{item['palette']:02d}.png"


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


def entry_slices(read, item: dict) -> list[bytes]:
    if "size" in item:
        blobs = [read(offset, item["size"]) for offset in item["offsets"]]
    else:
        blobs = [read_tmx_at(read, offset) for offset in item["offsets"]]
    if any(len(blob) != len(blobs[0]) for blob in blobs):
        raise ValueError(f"{item['name']} offsets have different sizes")
    return blobs


def ranges_overlap(items: list[tuple[str, int, int]]) -> None:
    ordered = sorted(items, key=lambda item: item[1])
    for (left_name, left, left_size), (right_name, right, _right_size) in zip(ordered, ordered[1:]):
        if left + left_size > right:
            raise ValueError(f"{left_name} and {right_name} overlap")


def collect(read, manifest: dict) -> list[tuple[dict, list[bytes]]]:
    found = []
    spans = []
    for item in manifest["files"]:
        blobs = entry_slices(read, item)
        found.append((item, blobs))
        spans.extend((item["name"], offset, len(blobs[0])) for offset in item["offsets"])
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


def unpack_reader(read, manifest: dict, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for item, blobs in collect(read, manifest):
        if "size" in item:
            msg_path = out_dir / f"{item['name']}.msg"
            msg_path.write_bytes(blobs[0])
            report = decode_msg.describe(blobs[0], item.get("word_count_header", False))
            try:
                report["path"] = msg_path.name
            except ValueError:
                report["path"] = msg_path.name
            json_path = out_dir / f"{item['name']}.json"
            json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            print(
                f"{msg_path}: {report['size']} bytes, "
                f"{report['terminated_segments']} segments, {report['clean_messages']} clean"
            )
            continue
        tmx_path = out_dir / f"{item['name']}.tmx"
        tmx_path.write_bytes(blobs[0])
        image = read_tmx(tmx_path)
        if item["palette"] >= len(image.palettes):
            raise ValueError(f"{item['name']} has no palette {item['palette']}")
        png_path = out_dir / png_name(item)
        save_png(image, item["palette"], png_path)
        print(
            f"{tmx_path}: {image.width}x{image.height}, "
            f"comment={image.comment!r}, palette={item['palette']}"
        )


def replacement_spans(read, root: Path) -> list[tuple[int, bytes]]:
    """Repacked TMX bytes for PNG files sitting next to a manifest. No PNG means no spans."""
    spans = []
    if not root.is_dir():
        return spans
    for manifest_path in sorted(root.rglob("manifest.json")):
        manifest = load_manifest(manifest_path)
        for item in manifest["files"]:
            if "size" in item:
                continue
            png_path = manifest_path.parent / png_name(item)
            if not png_path.is_file():
                continue
            updated = repack_bytes(read_tmx_at(read, item["offsets"][0]), png_path, item["palette"])
            spans.extend((offset, updated) for offset in item["offsets"])
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
    manifest = load_manifest(manifest_path)
    out_dir = scene_output(manifest_path)
    with BIN.open("rb") as source:
        lba, size = container_extent(source, manifest["container"])
        unpack_reader(
            lambda offset, count: read_container_range(source, lba, size, offset, count),
            manifest,
            out_dir,
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
    manifest = {
        "container": "PACKDATA.CIG",
        "files": [
            {"name": "a", "offsets": [0], "palette": 0},
            {"name": "b", "offsets": [len(first)], "palette": 0},
            {"name": "note", "offsets": [len(first) + len(second)], "size": len(raw), "word_count_header": True},
        ],
    }
    try:
        load_manifest_conflict()
    except ValueError as error:
        if "claimed" not in str(error):
            raise
    else:
        raise AssertionError("duplicate offsets were accepted")

    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        manifest_path = folder / "manifest.json"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        assert load_manifest(manifest_path) == manifest
        source = folder / "source.bin"
        preview = folder / "preview"
        source.write_bytes(blob)
        read = file_reader(source, len(blob))
        unpack_reader(read, manifest, preview)
        image = read_tmx(preview / "a.tmx")
        assert image.comment == "a.tim" and image.pixels == (1, 2, 3, 4)
        assert (preview / "note.msg").read_bytes() == raw
        note = json.loads((preview / "note.json").read_text(encoding="utf-8"))
        assert note["path"] == "note.msg" and note["clean_messages"] == 1
        assert note["messages"][0]["offset"] == 2
        assert note["messages"][1]["clean"] is False

        assert replacement_spans(read, folder) == []

        palette = image.palettes[0]
        changed = Image.new("RGBA", (2, 2))
        changed.putdata([palette[5], palette[2], palette[3], palette[4]])
        changed.save(folder / "a.pal00.png")
        spans = replacement_spans(read, folder)
        assert [(offset, len(data)) for offset, data in spans] == [(0, len(first))]
        decoded = read_tmx_bytes(spans[0][1])
        png = changed.convert("RGBA")
        assert [decoded.palettes[0][pixel] for pixel in decoded.pixels] == list(png.get_flattened_data())
    assert scene_output(DATA / "screen" / "title" / "manifest.json") == BUILD / "screen" / "title"


def load_manifest_conflict() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "manifest.json"
        path.write_text(json.dumps({
            "container": "PACKDATA.CIG",
            "files": [
                {"name": "a", "offsets": [0], "palette": 0},
                {"name": "b", "offsets": [0], "palette": 0},
            ],
        }), encoding="utf-8")
        load_manifest(path)


def read_tmx_bytes(data: bytes):
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "slice.tmx"
        path.write_bytes(data)
        return read_tmx(path)
