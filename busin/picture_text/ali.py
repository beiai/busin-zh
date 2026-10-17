"""Render and patch the Chinese command labels in the ali menu textures."""
import struct
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from busin.paths import BUILD, DATA
from busin.picture_text.render import (
    CORE_INDEX, FontSpec, OUTLINE_INDEX, TEXT_INDICES, load_font,
    outline_indices, parse_generated, render_line, replace_psmt4_pixels,
)
from busin.tmx import PSMCT16, PSMT4, decode_ct16, decode_psmt4, encode_psmt4

IMAGES = DATA / "picture" / "images.json"
WIDTH = 256
PALETTE_COUNT = 16
PALETTE_SIZE = 32
PIXEL_OFFSET = 0x40 + PALETTE_COUNT * PALETTE_SIZE
COLS = 2
CELL_WIDTH = WIDTH // COLS
CELL_HEIGHT = 32
PAL01_TEXT_RGB = (24, 24, 24)
PAL01_OUTLINE_RGB = (144, 144, 144)
PREVIEW_PALETTES = (0, 1, 2)

# PACKDATA offsets are checked against the TMX name, size, and format.
SOURCES = {
    "ali_00": (0x101B8800, 33344),
    "ali_01": (0x101C0A40, 33344),
    "ali_02": (0x101C8C80, 16960),
    "ali_03": (0x101CCEC0, 33344),
}
BUTTON_ROWS = {
    "ali_00": 8,
    "ali_01": 8,
    "ali_02": 4,
    "ali_03": 6,
}
HEIGHTS = {
    "ali_00": 256,
    "ali_01": 256,
    "ali_02": 128,
    "ali_03": 256,
}
SOURCE_TEXT = {
    "ali_00": (
        "一斉逃走", "前後列交代", "集中攻撃", "範囲攻撃",
        "フロントガード", "後衛防御", "援護射撃", "牽制射撃",
        "魔法協力", "ラッシュ", "散開", "呪文集中陣",
        "立体殺法", "フレイクサイレンス", "Wラッシュ", "マジックキャンセル",
    ),
    "ali_01": (
        "マジックキャンセル", "スタンスマッシュ", "フェイクアタック", "ホールドアタック",
        "ジグザグフィンガーアタック", "ワープアタック", "バックアタック", "セイクリッドクロス",
        "スレイクラッシュ", "マジックソード", "ゲイルスラッシュ", "アレイドアクション",
        "", "", "", "",
    ),
    "ali_02": (
        "死神の刃", "魂砕き", "邪魂治癒", "封じの声",
        "詐欺の逃走", "各自行動", "", "",
    ),
    "ali_03": (
        "防御系アレイド", "攻撃系アレイド", "魔法系アレイド", "援護系アレイド",
        "攻撃", "魔法", "アイテム", "ディスペル",
        "逃げる", "位置交代", "身を守る", "各自行動",
    ),
}
BLANK_SOURCES = (("ali_01", 15), ("ali_02", 7))


@dataclass(frozen=True)
class Part:
    source: str
    text: str


@dataclass(frozen=True)
class Spec:
    name: str
    font_file: str
    variation: str
    size: int
    parts: tuple[Part, ...]


def load_spec(data: dict) -> Spec:
    parsed = parse_generated(data)
    name = parsed.name
    if name not in SOURCES:
        raise ValueError(f"unknown ali picture {name!r}")
    if parsed.renderer != "ali_buttons":
        raise ValueError(f"{name} renderer is {parsed.renderer!r}")
    if parsed.font != FontSpec("NotoSansSC-VF.ttf", "Medium", 18):
        raise ValueError(f"{name} must use NotoSansSC-VF.ttf Medium 18")
    expected = SOURCE_TEXT[name]
    if len(parsed.labels) != len(expected):
        raise ValueError(f"{name} needs {len(expected)} labels")
    parts = []
    for index, (label, source) in enumerate(zip(parsed.labels, expected)):
        text = label.text
        if label.source != source:
            raise ValueError(f"{name} label {index} source is {label.source!r}")
        if bool(text) != bool(source):
            raise ValueError(f"{name} label {index} must keep the empty slot empty")
        parts.append(Part(source, text))
    return Spec(name, parsed.font.file, parsed.font.variation, parsed.font.size, tuple(parts))


def load_manifest_spec(name: str) -> Spec:
    from busin.ui import load_images

    matches = [
        item for item in load_images(IMAGES)["generated"]
        if item.get("name") == name
    ]
    if len(matches) != 1:
        raise ValueError(f"{name} generated entry appears {len(matches)} times")
    return load_spec(matches[0])


def _pixel_size(name: str) -> int:
    return WIDTH * HEIGHTS[name] // 2


def _validate_blob(name: str, blob: bytes) -> tuple[int, ...]:
    offset, size = SOURCES[name]
    if len(blob) != size:
        raise ValueError(f"{name} is {len(blob)} bytes, expected {size}")
    flag, _user_id, declared, tag = struct.unpack_from("<HHI4s", blob, 0)
    fields = struct.unpack_from("<BBHHBBHBBII28s", blob, 0x10)
    (
        palette_count, palette_format, width, height, pixel_format,
        mipmap_count, _mipmap_kl, _reserved, _wrap_modes, _texture_id, _clut_id,
        raw_comment,
    ) = fields
    comment = raw_comment.split(b"\0", 1)[0].decode("shift_jis")
    expected = (PALETTE_COUNT, PSMCT16, WIDTH, HEIGHTS[name], PSMT4, 0)
    if (flag, declared, tag) != (2, size, b"TMX0"):
        raise ValueError(f"{name} TMX header")
    if (palette_count, palette_format, width, height, pixel_format, mipmap_count) != expected:
        raise ValueError(f"{name} TMX format")
    if comment != f"{name}.tim":
        raise ValueError(f"{name} comment is {comment!r}")
    palette1 = decode_ct16(blob[0x40 + PALETTE_SIZE:0x40 + 2 * PALETTE_SIZE])
    if palette1[CORE_INDEX][:3] != PAL01_TEXT_RGB:
        raise ValueError(f"{name} palette 1 text is {palette1[CORE_INDEX][:3]}")
    if palette1[OUTLINE_INDEX][:3] != PAL01_OUTLINE_RGB:
        raise ValueError(f"{name} palette 1 outline is {palette1[OUTLINE_INDEX][:3]}")
    pixels = decode_psmt4(blob[PIXEL_OFFSET:PIXEL_OFFSET + _pixel_size(name)], WIDTH, HEIGHTS[name])
    for index, source in enumerate(SOURCE_TEXT[name]):
        values = _cell(pixels, index, name)
        if any(value in TEXT_INDICES for value in values) != bool(source):
            raise ValueError(f"{name} source part {index} text shape")
    return pixels


def _cell(pixels: tuple[int, ...], index: int, name: str) -> tuple[int, ...]:
    x0 = (index % COLS) * CELL_WIDTH
    y0 = (index // COLS) * CELL_HEIGHT
    return tuple(
        pixels[(y0 + y) * WIDTH + x0 + x]
        for y in range(CELL_HEIGHT)
        for x in range(CELL_WIDTH)
    )


def _blank_cell(read) -> tuple[int, ...]:
    cells = []
    for name, index in BLANK_SOURCES:
        blob = read(*SOURCES[name])
        cells.append(_cell(_validate_blob(name, blob), index, name))
    if cells[0] != cells[1]:
        raise ValueError("ali blank button backgrounds differ")
    if any(value in TEXT_INDICES for value in cells[0]):
        raise ValueError("ali blank button contains text")
    return cells[0]


def _font(spec: Spec):
    font = load_font(FontSpec(spec.font_file, spec.variation, spec.size))
    if font.getname() != ("Noto Sans SC", spec.variation):
        raise ValueError(f"{spec.name} font is {font.getname()}")
    return font


def render_cells(spec: Spec, blank: tuple[int, ...]) -> tuple[tuple[int, ...], ...]:
    if len(blank) != CELL_WIDTH * CELL_HEIGHT:
        raise ValueError(f"{spec.name} blank cell")
    font = _font(spec)
    cells = []
    for index, part in enumerate(spec.parts):
        cell = list(blank)
        if part.text:
            mask = render_line(
                part.text, font, CELL_WIDTH, CELL_HEIGHT, f"{spec.name} part {index} {part.text}"
            )
            for position, value in enumerate(outline_indices(mask, CORE_INDEX, OUTLINE_INDEX)):
                if value:
                    cell[position] = value
        cells.append(tuple(cell))
    return tuple(cells)


def patch_blob(blob: bytes, spec: Spec, blank: tuple[int, ...]) -> bytes:
    pixels = _validate_blob(spec.name, blob)
    cells = render_cells(spec, blank)
    updated = list(pixels)
    for index, cell in enumerate(cells):
        x0 = (index % COLS) * CELL_WIDTH
        y0 = (index // COLS) * CELL_HEIGHT
        for y in range(CELL_HEIGHT):
            for x in range(CELL_WIDTH):
                updated[(y0 + y) * WIDTH + x0 + x] = cell[y * CELL_WIDTH + x]
    patched = replace_psmt4_pixels(
        blob, updated, WIDTH, HEIGHTS[spec.name], PALETTE_COUNT, PALETTE_SIZE, spec.name
    )
    if spec.name == "ali_03":
        preserved = PIXEL_OFFSET + BUTTON_ROWS[spec.name] * CELL_HEIGHT * WIDTH // 2
        if patched[preserved:] != blob[preserved:]:
            raise ValueError("ali_03 result panel changed")
    return patched


def _source_blob(read, name: str) -> bytes:
    blob = read(*SOURCES[name])
    _validate_blob(name, blob)
    return blob


def source_spans(read, name: str) -> list[tuple[int, bytes]]:
    offset, _size = SOURCES[name]
    return [(offset, _source_blob(read, name))]


def spans(read, row, spec: Spec | None = None) -> list[tuple[int, bytes]]:
    del row
    spec = spec or load_manifest_spec("ali_00")
    patched = patch_blob(_source_blob(read, spec.name), spec, _blank_cell(read))
    return [(SOURCES[spec.name][0], patched)]


def validate_repacked(name: str, source: bytes, packed: bytes) -> None:
    if len(packed) != len(source):
        raise ValueError(f"{name} repacked size changed")
    if packed[:PIXEL_OFFSET] != source[:PIXEL_OFFSET]:
        raise ValueError(f"{name} TMX header or palettes changed")
    if name == "ali_03":
        preserved = PIXEL_OFFSET + BUTTON_ROWS[name] * CELL_HEIGHT * WIDTH // 2
        if packed[preserved:] != source[preserved:]:
            raise ValueError("ali_03 result panel changed in the translated picture")


def _render_png(blob: bytes, palette_index: int, output: Path) -> None:
    height = len(blob) and (len(blob) - PIXEL_OFFSET) * 2 // WIDTH
    pixels = decode_psmt4(blob[PIXEL_OFFSET:PIXEL_OFFSET + WIDTH * height // 2], WIDTH, height)
    palette = decode_ct16(
        blob[0x40 + palette_index * PALETTE_SIZE:0x40 + (palette_index + 1) * PALETTE_SIZE]
    )
    image = Image.new("RGBA", (WIDTH, height))
    image.putdata([palette[value] for value in pixels])
    output.parent.mkdir(parents=True, exist_ok=True)
    image.save(output)


def preview(read, row, spec: Spec | None = None, output: Path | None = None) -> Path:
    del row
    spec = spec or load_manifest_spec("ali_00")
    output = output or BUILD / "picture"
    patched = patch_blob(_source_blob(read, spec.name), spec, _blank_cell(read))
    output.mkdir(parents=True, exist_ok=True)
    for palette_index in PREVIEW_PALETTES:
        _render_png(patched, palette_index, output / f"{spec.name}.pal{palette_index:02d}.png")
    with Image.open(output / f"{spec.name}.pal01.png") as bitmap:
        bitmap.resize((WIDTH * 2, bitmap.height * 2), Image.Resampling.NEAREST).save(
            output / f"{spec.name}.preview.png"
        )
    return output


def self_test() -> None:
    blank = tuple(10 + ((x + y) % 6) for y in range(CELL_HEIGHT) for x in range(CELL_WIDTH))
    raw_palette = list(range(16))
    raw_palette[CORE_INDEX] = 0x0C63
    raw_palette[OUTLINE_INDEX] = 0x4A52
    palette = struct.pack("<16H", *raw_palette)
    for name in SOURCES:
        spec = load_manifest_spec(name)
        assert spec.name == name and spec.variation == "Medium" and spec.size == 18
        cells = render_cells(spec, blank)
        assert len(cells) == BUTTON_ROWS[name] * COLS
        for index, cell in enumerate(cells):
            text = spec.parts[index].text
            assert (CORE_INDEX in cell and OUTLINE_INDEX in cell) == bool(text)
            if not text:
                assert cell == blank

        source_pixels = [0] * (WIDTH * HEIGHTS[name])
        for index, part in enumerate(spec.parts):
            x0 = (index % COLS) * CELL_WIDTH
            y0 = (index // COLS) * CELL_HEIGHT
            cell = blank
            if part.source:
                values = list(blank)
                values[0] = CORE_INDEX
                cell = tuple(values)
            for y in range(CELL_HEIGHT):
                for x in range(CELL_WIDTH):
                    source_pixels[(y0 + y) * WIDTH + x0 + x] = cell[y * CELL_WIDTH + x]
        body = struct.pack(
            "<BBHHBBHBBII28s", PALETTE_COUNT, PSMCT16, WIDTH, HEIGHTS[name], PSMT4,
            0, 0, 0, 0, 0, 0, f"{name}.tim".encode("shift_jis"),
        )
        blob = struct.pack(
            "<HHI4s", 2, 0,
            16 + len(body) + PALETTE_COUNT * PALETTE_SIZE + _pixel_size(name),
            b"TMX0",
        )
        blob += bytes(4) + body + palette * PALETTE_COUNT + encode_psmt4(source_pixels)
        patched = patch_blob(blob, spec, blank)
        assert len(patched) == len(blob) and patched[:PIXEL_OFFSET] == blob[:PIXEL_OFFSET]
        validate_repacked(name, blob, patched)
        if name == "ali_03":
            preserved = PIXEL_OFFSET + BUTTON_ROWS[name] * CELL_HEIGHT * WIDTH // 2
            assert patched[preserved:] == blob[preserved:]
            bad = bytearray(patched)
            bad[-1] ^= 1
            try:
                validate_repacked(name, blob, bytes(bad))
            except ValueError as error:
                assert "result panel" in str(error)
            else:
                raise AssertionError("ali_03 result panel change was accepted")
        decoded = decode_psmt4(patched[PIXEL_OFFSET:PIXEL_OFFSET + _pixel_size(name)], WIDTH, HEIGHTS[name])
        for index, part in enumerate(spec.parts):
            values = _cell(decoded, index, name)
            assert any(value in TEXT_INDICES for value in values) == bool(part.text)


def disc_self_test() -> None:
    from busin.disc import BIN, read_pack

    with BIN.open("rb") as handle:
        read = lambda offset, size: read_pack(handle, offset, size)
        blank = _blank_cell(read)
        for name in SOURCES:
            spec = load_manifest_spec(name)
            source = _source_blob(read, name)
            found = spans(read, None, spec)
            assert len(found) == 1 and found[0][0] == SOURCES[name][0]
            assert len(found[0][1]) == len(source) and found[0][1] != source
        assert blank == _blank_cell(read)
