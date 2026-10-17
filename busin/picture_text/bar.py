"""Render and patch the Chinese labels in the bar_00 menu texture."""
import json
import struct
import tempfile
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from busin.disc import chunk_row, read_pack
from busin.paths import BUILD, DATA
from busin.picture_text.render import (
    CORE_INDEX, FontSpec, Label, MAX_EDGE_CROP, OUTLINE_INDEX, TEXT_INDICES,
    load_font, outline_indices, render_line as render_text_line,
    replace_psmt4_pixels,
)
from busin.tmx import PSMCT16, PSMT4, decode_ct16, decode_psmt4, encode_psmt4

IMAGES = DATA / "picture" / "images.json"
TEXTURE = "bar_00.tim"
SIZE = 0x8240
WIDTH = 256
HEIGHT = 256
PALETTE_COUNT = 16
PALETTE_SIZE = 32
PIXEL_OFFSET = 0x40 + PALETTE_COUNT * PALETTE_SIZE
PIXEL_SIZE = WIDTH * HEIGHT // 2
COLS = 2
ROWS = 8
CELL_WIDTH = WIDTH // COLS
CELL_HEIGHT = HEIGHT // ROWS
BLANK_CELL = (COLS - 1, ROWS - 1)
PAL01_TEXT_RGB = (24, 24, 24)
PAL01_OUTLINE_RGB = (144, 144, 144)
PREVIEW_PALETTES = (0, 1, 2)

# PACKDATA chunk and chunk-relative offset of every identical source copy.
SOURCES = (
    (0, 0x30CC0),
    (12, 0x2CA40),
    (19, 0x00000),
)
SOURCE_TEXT = (
    "仲間に入る", "仲間を外す", "ステータス", "依頼リスト",
    "パーティー", "外に出る", "魔法", "アイテム",
    "装備", "AAの確認", "隊列の変更", "ドロップ練習",
    "戻る", "依頼の確認", "仲間からはなす", "",
)


@dataclass(frozen=True)
class Part:
    source: str
    text: str


@dataclass(frozen=True)
class Spec:
    font_file: str
    variation: str
    size: int
    parts: tuple[Part, ...]


def load_spec(data: dict) -> Spec:
    from busin.picture_text.render import parse_generated

    parsed = parse_generated(data)
    if parsed.name != "bar_00" or parsed.renderer != "bar_buttons":
        raise ValueError("bar_00 generated entry")
    if parsed.font.file != "NotoSansSC-VF.ttf" or parsed.font.variation != "Medium":
        raise ValueError("bar_00 must use NotoSansSC-VF.ttf Medium")
    if len(parsed.labels) != len(SOURCE_TEXT):
        raise ValueError(f"bar_00 needs {len(SOURCE_TEXT)} labels")
    parts = []
    for index, label in enumerate(parsed.labels):
        source, text = label.source, label.text
        if source != SOURCE_TEXT[index]:
            raise ValueError(f"bar_00 label {index} source is {source!r}")
        if "/" in text:
            raise ValueError(f"bar_00 label {index} text")
        if bool(text) != bool(source):
            raise ValueError(f"bar_00 label {index} must keep the empty slot empty")
        parts.append(Part(source, text))
    return Spec(parsed.font.file, parsed.font.variation, parsed.font.size, tuple(parts))


def load_manifest_spec(name: str = "bar_00") -> Spec:
    from busin.ui import load_images

    matches = [
        item for item in load_images(IMAGES).get("generated", [])
        if item.get("name") == name
    ]
    if len(matches) != 1:
        raise ValueError(f"{name} generated entry appears {len(matches)} times")
    return load_spec(matches[0])


def _font(spec: Spec):
    font = load_font(FontSpec(spec.font_file, spec.variation, spec.size))
    if font.getname() != ("Noto Sans SC", spec.variation):
        raise ValueError(f"bar_00 font is {font.getname()}")
    return font


def render_line(text: str, spec: Spec, label: str | None = None) -> Image.Image:
    return render_text_line(text, _font(spec), CELL_WIDTH, CELL_HEIGHT, label)


def render_cells(spec: Spec, blank: tuple[int, ...]) -> tuple[tuple[int, ...], ...]:
    if len(blank) != CELL_WIDTH * CELL_HEIGHT:
        raise ValueError("bar_00 blank cell")
    font = _font(spec)
    cells = []
    for index, part in enumerate(spec.parts):
        cell = list(blank)
        if part.text:
            mask = render_text_line(
                part.text, font, CELL_WIDTH, CELL_HEIGHT, f"part {index} {part.text}"
            )
            for position, value in enumerate(outline_indices(mask, CORE_INDEX, OUTLINE_INDEX)):
                if value:
                    cell[position] = value
        cells.append(tuple(cell))
    return tuple(cells)


def _blank_cell(pixels: tuple[int, ...]) -> tuple[int, ...]:
    x0, y0 = BLANK_CELL[0] * CELL_WIDTH, BLANK_CELL[1] * CELL_HEIGHT
    return tuple(
        pixels[(y0 + y) * WIDTH + x0 + x]
        for y in range(CELL_HEIGHT)
        for x in range(CELL_WIDTH)
    )


def _validate_blob(blob: bytes) -> tuple[int, ...]:
    if len(blob) != SIZE:
        raise ValueError(f"bar_00 is {len(blob)} bytes, expected {SIZE}")
    flag, _user_id, declared, tag = struct.unpack_from("<HHI4s", blob, 0)
    fields = struct.unpack_from("<BBHHBBHBBII28s", blob, 0x10)
    (
        palette_count, palette_format, width, height, pixel_format,
        mipmap_count, _mipmap_kl, _reserved, _wrap_modes, _texture_id, _clut_id,
        raw_comment,
    ) = fields
    comment = raw_comment.split(b"\0", 1)[0].decode("shift_jis")
    expected = (PALETTE_COUNT, PSMCT16, WIDTH, HEIGHT, PSMT4, 0)
    if (flag, declared, tag) != (2, SIZE, b"TMX0"):
        raise ValueError("bar_00 TMX header")
    if (palette_count, palette_format, width, height, pixel_format, mipmap_count) != expected:
        raise ValueError("bar_00 TMX format")
    if comment != TEXTURE:
        raise ValueError(f"bar_00 comment is {comment!r}")
    palette1 = decode_ct16(blob[0x40 + PALETTE_SIZE:0x40 + 2 * PALETTE_SIZE])
    actual_text = palette1[CORE_INDEX][:3]
    actual_outline = palette1[OUTLINE_INDEX][:3]
    if actual_text != PAL01_TEXT_RGB:
        raise ValueError(f"bar_00 palette 1 text is {actual_text}, expected {PAL01_TEXT_RGB}")
    if actual_outline != PAL01_OUTLINE_RGB:
        raise ValueError(
            f"bar_00 palette 1 outline is {actual_outline}, expected {PAL01_OUTLINE_RGB}"
        )
    pixels = decode_psmt4(blob[PIXEL_OFFSET:PIXEL_OFFSET + PIXEL_SIZE], WIDTH, HEIGHT)
    blank = _blank_cell(pixels)
    if any(value in TEXT_INDICES for value in blank):
        raise ValueError("bar_00 blank cell contains text")
    for index, source in enumerate(SOURCE_TEXT):
        x0 = (index % COLS) * CELL_WIDTH
        y0 = (index // COLS) * CELL_HEIGHT
        values = [
            pixels[(y0 + y) * WIDTH + x0 + x]
            for y in range(CELL_HEIGHT)
            for x in range(CELL_WIDTH)
        ]
        has_text = any(value in TEXT_INDICES for value in values)
        if has_text != bool(source):
            raise ValueError(f"bar_00 source part {index} text shape")
    return pixels


def patch_blob(blob: bytes, spec: Spec | None = None) -> bytes:
    spec = spec or load_manifest_spec()
    pixels = _validate_blob(blob)
    blank = _blank_cell(pixels)
    cells = render_cells(spec, blank)
    updated = list(pixels)
    for index, cell in enumerate(cells):
        x0 = (index % COLS) * CELL_WIDTH
        y0 = (index // COLS) * CELL_HEIGHT
        for y in range(CELL_HEIGHT):
            for x in range(CELL_WIDTH):
                updated[(y0 + y) * WIDTH + x0 + x] = cell[y * CELL_WIDTH + x]
    return replace_psmt4_pixels(
        blob, updated, WIDTH, HEIGHT, PALETTE_COUNT, PALETTE_SIZE, "bar_00"
    )


def _source_offsets(row) -> list[int]:
    return [row(chunk)[0] * 2048 + relative for chunk, relative in SOURCES]


def _source_blob(read, row) -> bytes:
    blobs = []
    for (chunk, relative), offset in zip(SOURCES, _source_offsets(row)):
        start, count = row(chunk)
        if start < 0 or count <= 0 or relative + SIZE > count * 2048:
            raise ValueError(f"bar_00 copy in chunk {chunk} does not fit")
        blob = read(offset, SIZE)
        _validate_blob(blob)
        blobs.append(blob)
    if len(set(blobs)) != 1:
        raise ValueError("bar_00 source copies differ")
    return blobs[0]


def source_spans(read, row) -> list[tuple[int, bytes]]:
    source = _source_blob(read, row)
    return [(offset, source) for offset in _source_offsets(row)]


def spans(read, row, spec: Spec | None = None) -> list[tuple[int, bytes]]:
    spec = spec or load_manifest_spec()
    patched = patch_blob(_source_blob(read, row), spec)
    return [(offset, patched) for offset in _source_offsets(row)]


def validate_repacked(source: bytes, packed: bytes) -> None:
    if len(packed) != len(source):
        raise ValueError("bar_00 repacked size changed")
    if packed[:PIXEL_OFFSET] != source[:PIXEL_OFFSET]:
        raise ValueError("bar_00 TMX header or palettes changed")


def _render_png(blob: bytes, palette_index: int, output: Path) -> None:
    pixels = decode_psmt4(blob[PIXEL_OFFSET:PIXEL_OFFSET + PIXEL_SIZE], WIDTH, HEIGHT)
    palette = decode_ct16(
        blob[0x40 + palette_index * PALETTE_SIZE:0x40 + (palette_index + 1) * PALETTE_SIZE]
    )
    image = Image.new("RGBA", (WIDTH, HEIGHT))
    image.putdata([palette[value] for value in pixels])
    output.parent.mkdir(parents=True, exist_ok=True)
    image.save(output)


def preview(read, row, spec: Spec | None = None, output: Path | None = None) -> Path:
    spec = spec or load_manifest_spec()
    output = output or BUILD / "picture"
    patched = patch_blob(_source_blob(read, row), spec)
    output.mkdir(parents=True, exist_ok=True)
    for palette_index in PREVIEW_PALETTES:
        _render_png(patched, palette_index, output / f"bar_00.pal{palette_index:02d}.png")
    with Image.open(output / "bar_00.pal01.png") as bitmap:
        bitmap.resize((WIDTH * 2, HEIGHT * 2), Image.Resampling.NEAREST).save(
            output / "bar_00.preview.png"
        )
    return output


def self_test() -> None:
    spec = load_manifest_spec()
    assert spec.size == 20 and spec.variation == "Medium"
    assert len(spec.parts) == 16 and spec.parts[-1] == Part("", "")

    blank = tuple(10 + ((x + y) % 6) for y in range(CELL_HEIGHT) for x in range(CELL_WIDTH))
    cells = render_cells(spec, blank)
    assert len(cells) == 16
    assert all(len(cell) == CELL_WIDTH * CELL_HEIGHT for cell in cells)
    assert cells[-1] == blank
    for cell in cells[:-1]:
        assert CORE_INDEX in cell and OUTLINE_INDEX in cell
        assert set(cell) - set(blank) <= set(TEXT_INDICES)

    raw_palette = list(range(16))
    raw_palette[CORE_INDEX] = 0x0C63
    raw_palette[OUTLINE_INDEX] = 0x4A52
    palette = struct.pack("<16H", *raw_palette)
    source_pixels = [0] * (WIDTH * HEIGHT)
    for index in range(COLS * ROWS):
        x0 = (index % COLS) * CELL_WIDTH
        y0 = (index // COLS) * CELL_HEIGHT
        for y in range(CELL_HEIGHT):
            for x in range(CELL_WIDTH):
                source_pixels[(y0 + y) * WIDTH + x0 + x] = blank[y * CELL_WIDTH + x]
        if index != 15:
            source_pixels[y0 * WIDTH + x0 + 10] = CORE_INDEX
    body = struct.pack(
        "<BBHHBBHBBII28s", PALETTE_COUNT, PSMCT16, WIDTH, HEIGHT, PSMT4,
        0, 0, 0, 0, 0, 0, TEXTURE.encode("shift_jis"),
    )
    blob = struct.pack(
        "<HHI4s", 2, 0,
        16 + len(body) + PALETTE_COUNT * PALETTE_SIZE + PIXEL_SIZE,
        b"TMX0",
    )
    blob += bytes(4) + body + palette * PALETTE_COUNT + encode_psmt4(source_pixels)
    patched = patch_blob(blob, spec)
    assert len(patched) == len(blob) and patched[:PIXEL_OFFSET] == blob[:PIXEL_OFFSET]
    decoded = decode_psmt4(patched[PIXEL_OFFSET:PIXEL_OFFSET + PIXEL_SIZE], WIDTH, HEIGHT)
    assert decoded != tuple(source_pixels) and _blank_cell(decoded) == blank
    for index in range(COLS * ROWS):
        x0 = (index % COLS) * CELL_WIDTH
        y0 = (index // COLS) * CELL_HEIGHT
        values = [
            decoded[(y0 + y) * WIDTH + x0 + x]
            for y in range(CELL_HEIGHT)
            for x in range(CELL_WIDTH)
        ]
        assert any(value in TEXT_INDICES for value in values) == (index != 15)

    with tempfile.TemporaryDirectory() as tmp:
        from busin.picture_text.workflow import export_generated_picture
        from busin.ui import generated_spans, load_images

        root = Path(tmp)
        document = load_images(IMAGES)
        item = next(entry for entry in document["generated"] if entry["name"] == "bar_00")
        document["generated"] = [item]
        (root / "images.json").write_text(json.dumps(document), encoding="utf-8")

        def reader(payload):
            def read(_offset, size):
                if size != SIZE:
                    raise ValueError(size)
                return payload
            return read

        def row(chunk):
            return chunk * 1000, 200

        export_generated_picture(item, reader(blob), row, root / "generated")
        original = root / "generated" / "bar_00.png"
        translated = root / "generated" / "bar_00.zh.png"
        initial_original = original.read_bytes()
        initial_translated = translated.read_bytes()
        assert initial_original and initial_translated and initial_original != initial_translated

        changed = bytearray(blob)
        changed[-1] ^= 1
        translated.write_bytes(b"manual edit")
        export_generated_picture(item, reader(bytes(changed)), row, root / "generated")
        assert original.read_bytes() != initial_original
        assert translated.read_bytes() == b"manual edit"

        translated.write_bytes(initial_translated)
        packed = generated_spans(reader(blob), root, row)
        rendered = spans(reader(blob), row, spec)
        assert packed == rendered
        assert len({data for _offset, data in packed}) == 1


def disc_self_test() -> None:
    from busin.disc import BIN

    spec = load_manifest_spec()
    with BIN.open("rb") as handle:
        read = lambda offset, size: read_pack(handle, offset, size)
        row = lambda chunk: chunk_row(handle, chunk)
        expected = _source_offsets(row)
        source = _source_blob(read, row)
        found = spans(read, row, spec)
    assert [offset for offset, _data in found] == expected
    assert len({data for _offset, data in found}) == 1
    assert found[0][1] != source
