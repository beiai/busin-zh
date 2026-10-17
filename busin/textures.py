"""Export every distinct TMX texture and palette in PACKDATA."""
import hashlib
import struct
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from busin.paths import BIN, BUILD
from busin.tmx import (
    PSMCT16, PSMCT32, PSMT4, PSMT8, decode_ct16, decode_ct32, decode_ct32_16,
    decode_psmt4, decode_psmt8,
)
from busin.ui import container_extent, read_container_range

try:
    from PIL import Image
except ImportError:
    Image = None

TMX_TAG = b"TMX0"
OUT = BUILD / "textures"
MAX_DIMENSION = 4096


@dataclass(frozen=True)
class Texture:
    comment: str
    width: int
    height: int
    pixel_format: int
    palette_format: int
    palettes: tuple[tuple[tuple[int, int, int, int], ...], ...]
    pixels: tuple[int, ...] | tuple[tuple[int, int, int, int], ...]


@dataclass(frozen=True)
class ExportReport:
    output: Path
    textures: int
    pngs: int
    duplicates: int
    failures: dict[str, int]


def decode_color16(data: bytes) -> tuple[tuple[int, int, int, int], ...]:
    if len(data) % 2:
        raise ValueError("16-bit color data has an odd size")
    colors = []
    for value in struct.unpack(f"<{len(data) // 2}H", data):
        colors.append((
            (value & 0x1F) << 3,
            ((value >> 5) & 0x1F) << 3,
            ((value >> 10) & 0x1F) << 3,
            255,
        ))
    return tuple(colors)


def decode_ct16_256(data: bytes) -> tuple[tuple[int, int, int, int], ...]:
    if len(data) != 512:
        raise ValueError(f"PSMT8 PSMCT16 palette is {len(data)} bytes, expected 512")
    return decode_color16(data)


def decode_raw32(data: bytes, width: int, height: int) -> tuple[tuple[int, int, int, int], ...]:
    expected = width * height * 4
    if len(data) < expected:
        raise ValueError(f"pixel data is {len(data)} bytes, expected {expected}")
    return tuple(tuple(data[index:index + 4]) for index in range(0, expected, 4))


def decode_raw24(data: bytes, width: int, height: int) -> tuple[tuple[int, int, int, int], ...]:
    expected = width * height * 3
    if len(data) < expected:
        raise ValueError(f"pixel data is {len(data)} bytes, expected {expected}")
    return tuple(tuple(data[index:index + 3]) + (255,) for index in range(0, expected, 3))


def decode_raw16(data: bytes, width: int, height: int) -> tuple[tuple[int, int, int, int], ...]:
    expected = width * height * 2
    if len(data) < expected:
        raise ValueError(f"pixel data is {len(data)} bytes, expected {expected}")
    return decode_color16(data[:expected])


def decode_texture(blob: bytes) -> Texture:
    if len(blob) < 0x40:
        raise ValueError("file is shorter than the TMX0 header")
    flag, _user_id, declared_size, tag = struct.unpack_from("<HHI4s", blob, 0)
    if flag != 2 or tag != TMX_TAG:
        raise ValueError("not an Atlus TMX0 texture")
    if declared_size != len(blob):
        raise ValueError("declared size does not match the texture")
    fields = struct.unpack_from("<BBHHBBHBBII28s", blob, 0x10)
    (
        palette_count, palette_format, width, height, pixel_format,
        mipmap_count, _mipmap_kl, _reserved, _wrap_modes, _texture_id, _clut_id,
        raw_comment,
    ) = fields
    if mipmap_count:
        raise ValueError("mipmapped texture")
    if not 0 < width <= MAX_DIMENSION or not 0 < height <= MAX_DIMENSION:
        raise ValueError(f"invalid dimensions {width}x{height}")
    body = blob[0x40:]
    comment = raw_comment.split(b"\0", 1)[0].decode("shift_jis", errors="replace")

    if pixel_format == PSMT4:
        if width % 2:
            raise ValueError("odd PSMT4 width")
        if palette_format == PSMCT16:
            palette_size, decode_palette, palette_len = 32, decode_ct16, 16
        elif palette_format == PSMCT32:
            palette_size, decode_palette, palette_len = 64, decode_ct32_16, 16
        else:
            raise ValueError(f"unsupported PSMT4 palette 0x{palette_format:02X}")
        count = palette_count or 1
        pixel_size = width * height // 2
        pixel_at = count * palette_size
        if pixel_at + pixel_size > len(body):
            raise ValueError("truncated PSMT4 pixel data")
        palettes = tuple(
            decode_palette(body[index * palette_size:(index + 1) * palette_size])
            for index in range(count)
        )
        if any(len(palette) != palette_len for palette in palettes):
            raise ValueError("PSMT4 palette size")
        return Texture(comment, width, height, pixel_format, palette_format, palettes,
                       decode_psmt4(body[pixel_at:pixel_at + pixel_size], width, height))

    if pixel_format == PSMT8:
        if palette_format == PSMCT16:
            palette_size, decode_palette, palette_len = 512, decode_ct16_256, 256
        elif palette_format == PSMCT32:
            palette_size, decode_palette, palette_len = 1024, decode_ct32, 256
        else:
            raise ValueError(f"unsupported PSMT8 palette 0x{palette_format:02X}")
        count = palette_count or 1
        pixel_size = width * height
        pixel_at = count * palette_size
        if pixel_at + pixel_size > len(body):
            raise ValueError("truncated PSMT8 pixel data")
        palettes = tuple(
            decode_palette(body[index * palette_size:(index + 1) * palette_size])
            for index in range(count)
        )
        if any(len(palette) != palette_len for palette in palettes):
            raise ValueError("PSMT8 palette size")
        return Texture(comment, width, height, pixel_format, palette_format, palettes,
                       decode_psmt8(body[pixel_at:pixel_at + pixel_size], width, height))

    if palette_count:
        raise ValueError(f"direct texture has {palette_count} palettes")
    if pixel_format == PSMCT32:
        pixels = decode_raw32(body, width, height)
    elif pixel_format == 0x01:
        pixels = decode_raw24(body, width, height)
    elif pixel_format == PSMCT16:
        pixels = decode_raw16(body, width, height)
    else:
        raise ValueError(
            f"unsupported pixel format 0x{pixel_format:02X} / palette 0x{palette_format:02X}"
        )
    return Texture(comment, width, height, pixel_format, palette_format, (), pixels)


def stem_of(comment: str, base: int) -> str:
    text = comment.strip()
    if text.lower().endswith((".tga", ".tim")):
        text = text.rsplit(".", 1)[0]
    cleaned = []
    for char in text:
        if char in '<>:"/\\|?*' or ord(char) < 32:
            cleaned.append("_")
        else:
            cleaned.append(char)
    stem = "".join(cleaned).strip(" .")
    return stem[:80] if stem else f"blank_{base:08x}"


def save_texture(texture: Texture, stem: str, output: Path) -> int:
    if Image is None:
        raise RuntimeError("Pillow is required to export PNG files")
    output.mkdir(parents=True, exist_ok=True)
    if texture.palettes:
        count = len(texture.palettes)
        for index, palette in enumerate(texture.palettes):
            name = f"{stem}.png" if count == 1 else f"{stem}.pal{index:02d}.png"
            bitmap = Image.new("RGBA", (texture.width, texture.height))
            bitmap.putdata([palette[pixel] for pixel in texture.pixels])
            bitmap.save(output / name)
        return count
    bitmap = Image.new("RGBA", (texture.width, texture.height))
    bitmap.putdata(texture.pixels)
    bitmap.save(output / f"{stem}.png")
    return 1


def clean_build_output(output: Path) -> None:
    if output.resolve().is_relative_to(BUILD.resolve()):
        for old in output.glob("*.png"):
            old.unlink()


def export_pack_bytes(pack: bytes, output: Path) -> ExportReport:
    output.mkdir(parents=True, exist_ok=True)
    seen = set()
    names = set()
    textures = pngs = duplicates = 0
    failures = Counter()
    start = 0
    while True:
        at = pack.find(TMX_TAG, start)
        if at < 0:
            break
        start = at + 1
        base = at - 8
        if base < 0 or base + 0x40 > len(pack):
            continue
        flag, _user_id, size, tag = struct.unpack_from("<HHI4s", pack, base)
        if flag != 2 or tag != TMX_TAG or size < 0x40 or base + size > len(pack):
            continue
        blob = pack[base:base + size]
        digest = hashlib.sha256(blob).digest()
        if digest in seen:
            duplicates += 1
            continue
        seen.add(digest)
        try:
            texture = decode_texture(blob)
            stem = stem_of(texture.comment, base)
            if stem in names:
                stem = f"{stem}__{base:08x}"
            names.add(stem)
            pngs += save_texture(texture, stem, output)
            textures += 1
        except (ValueError, RuntimeError, IndexError, OSError) as error:
            failures[str(error).splitlines()[0][:120]] += 1
            continue
        if textures % 250 == 0:
            print(textures, flush=True)
    return ExportReport(output, textures, pngs, duplicates, dict(failures))


def export_textures(output: Path | None = None) -> ExportReport:
    output = output or OUT
    clean_build_output(output)
    with BIN.open("rb") as source:
        lba, size = container_extent(source, "PACKDATA.CIG")
        pack = read_container_range(source, lba, size, 0, size)
    return export_pack_bytes(pack, output)


def main(argv=None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="busin export-textures")
    parser.add_argument("--output", type=Path, help="output folder; default build/textures")
    args = parser.parse_args(argv)
    report = export_textures(args.output)
    print(
        f"{report.output}: {report.textures} textures, {report.pngs} PNGs, "
        f"{report.duplicates} duplicates, {sum(report.failures.values())} failed"
    )
    for message, count in sorted(report.failures.items(), key=lambda item: (-item[1], item[0])):
        print(f"  {count} {message}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
