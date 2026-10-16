"""Decode and repack Atlus TMX0 textures."""
import struct
import tempfile
from dataclasses import dataclass
from pathlib import Path

try:
    from PIL import Image
except ImportError:
    Image = None

TMX_TAG = b"TMX0"
PSMCT32 = 0x00
PSMCT16 = 0x02
PSMT8 = 0x13
PSMT4 = 0x14


@dataclass(frozen=True)
class TmxImage:
    path: Path
    user_id: int
    comment: str
    width: int
    height: int
    pixel_format: int
    palette_format: int
    palettes: list[tuple[tuple[int, int, int, int], ...]]
    pixels: tuple[int, ...]


def decode_psmt4(data: bytes, width: int, height: int) -> tuple[int, ...]:
    expected = width * height // 2
    if len(data) != expected:
        raise ValueError(f"pixel data is {len(data)} bytes, expected {expected}")
    pixels = []
    for byte in data:
        pixels.append(byte & 0x0F)
        pixels.append((byte >> 4) & 0x0F)
    return tuple(pixels[:width * height])


def decode_psmt8(data: bytes, width: int, height: int) -> tuple[int, ...]:
    expected = width * height
    if len(data) != expected:
        raise ValueError(f"pixel data is {len(data)} bytes, expected {expected}")
    return tuple(data)


def decode_ct32(data: bytes) -> tuple[tuple[int, int, int, int], ...]:
    if len(data) != 1024:
        raise ValueError(f"PSMT8 PSMCT32 palette is {len(data)} bytes, expected 1024")
    return tuple(tuple(data[i:i + 4]) for i in range(0, 1024, 4))


def decode_ct32_16(data: bytes) -> tuple[tuple[int, int, int, int], ...]:
    if len(data) != 64:
        raise ValueError(f"PSMT4 PSMCT32 palette is {len(data)} bytes, expected 64")
    return tuple(tuple(data[i:i + 4]) for i in range(0, 64, 4))


def decode_ct16(data: bytes) -> tuple[tuple[int, int, int, int], ...]:
    if len(data) != 32:
        raise ValueError(f"PSMT4 PSMCT16 palette is {len(data)} bytes, expected 32")
    colors = []
    for value in struct.unpack("<16H", data):
        colors.append((
            (value & 0x1F) << 3,
            ((value >> 5) & 0x1F) << 3,
            ((value >> 10) & 0x1F) << 3,
            255,
        ))
    return tuple(colors)


def read_tmx(path: Path) -> TmxImage:
    data = path.read_bytes()
    if len(data) < 0x40:
        raise ValueError("file is shorter than the TMX0 header")
    flag, user_id, declared_size, tag = struct.unpack_from("<HHI4s", data, 0)
    if flag != 2 or tag != TMX_TAG:
        raise ValueError("not an Atlus TMX0 texture")
    if declared_size != len(data):
        raise ValueError(f"declared size {declared_size} does not match file size {len(data)}")

    fields = struct.unpack_from("<BBHHBBHBBII28s", data, 0x10)
    (
        palette_count,
        palette_format,
        width,
        height,
        pixel_format,
        mipmap_count,
        _mipmap_kl,
        _reserved,
        _wrap_modes,
        _texture_id,
        _clut_id,
        raw_comment,
    ) = fields
    if mipmap_count:
        raise ValueError("mipmapped TMX textures are not supported")
    if width <= 0 or height <= 0:
        raise ValueError(f"invalid dimensions {width}x{height}")
    # A 4-bit image with a 32-bit palette is 64 palette bytes, then the pixels.
    # Bytes after the pixels are not drawn. The 512×256 poem keeps 960 of them.
    # Treating that tail as palette and starting at byte 1024 drops the first
    # three rows and 384 columns, so the last 128 pixels of each row sit on the
    # left. The game draws the texture from its top-left. The gray is flat;
    # the letters are only in the alpha.
    padding = 0
    if pixel_format == PSMT4 and palette_format == PSMCT16:
        if width % 2:
            raise ValueError(f"invalid PSMT4 dimensions {width}x{height}")
        palette_size = 32
        pixel_size = width * height // 2
        decode_palette = decode_ct16
        decode_pixels = decode_psmt4
    elif pixel_format == PSMT8 and palette_format == PSMCT32:
        palette_size = 1024
        pixel_size = width * height
        decode_palette = decode_ct32
        decode_pixels = decode_psmt8
    elif pixel_format == PSMT4 and palette_format == PSMCT32 and palette_count == 8 and width == 1024 and height == 1024:
        # Message glyphs only: eight palettes, the pixels, then 960 zero bytes.
        padding = 960
        palette_size = 64
        pixel_size = width * height // 2
        decode_palette = decode_ct32_16
        decode_pixels = decode_psmt4
    else:
        raise ValueError(
            f"unsupported pixel format 0x{pixel_format:02X} / palette 0x{palette_format:02X}"
        )

    palette_offset = 0x40
    pixel_offset = palette_offset + palette_count * palette_size
    if pixel_offset + pixel_size + padding != len(data):
        raise ValueError(
            f"payload plus padding ends at 0x{pixel_offset + pixel_size + padding:X}, file ends at 0x{len(data):X}"
        )
    if padding and any(data[-padding:]):
        raise ValueError("message glyph TMX padding is not zero")

    palettes = [
        decode_palette(data[palette_offset + i * palette_size:palette_offset + (i + 1) * palette_size])
        for i in range(palette_count)
    ]
    pixels = decode_pixels(data[pixel_offset:pixel_offset + pixel_size], width, height)
    comment = raw_comment.split(b"\0", 1)[0].decode("shift_jis", errors="replace")
    return TmxImage(path, user_id, comment, width, height, pixel_format, palette_format, palettes, pixels)


def save_png(image: TmxImage, palette_index: int, output: Path) -> None:
    if Image is None:
        raise RuntimeError("Pillow is required to export PNG files")
    palette = image.palettes[palette_index]
    bitmap = Image.new("RGBA", (image.width, image.height))
    bitmap.putdata([palette[pixel] for pixel in image.pixels])
    output.parent.mkdir(parents=True, exist_ok=True)
    bitmap.save(output)


def self_test() -> None:
    repack_self_test()

    palette = b"".join(struct.pack("<H", value) for value in range(16))
    body = struct.pack(
        "<BBHHBBHBBII28s",
        1,
        PSMCT16,
        2,
        2,
        PSMT4,
        0,
        0,
        0,
        0,
        0,
        0,
        b"test.tim",
    )
    pixels = bytes((0x21, 0x43))
    payload = body + palette + pixels
    header = struct.pack("<HHI4s", 2, 7, 16 + len(payload), TMX_TAG) + bytes(4)
    sample = header + payload

    with tempfile.TemporaryDirectory() as tmp:
        temp = Path(tmp) / "sample.tmx"
        temp.write_bytes(sample)
        decoded = read_tmx(temp)
        assert decoded.user_id == 7
        assert decoded.comment == "test.tim"
        assert decoded.pixel_format == PSMT4
        assert decoded.palette_format == PSMCT16
        assert (decoded.width, decoded.height) == (2, 2)
        assert decoded.pixels == (1, 2, 3, 4)
        assert decoded.palettes[0][15] == (120, 0, 0, 255)

        palette8 = bytearray(1024)
        palette8[7 * 4:7 * 4 + 4] = bytes((10, 20, 30, 128))
        body8 = struct.pack(
            "<BBHHBBHBBII28s",
            1, PSMCT32, 2, 2, PSMT8, 0, 0, 0, 0, 0, 0, b"title.tga",
        )
        payload8 = body8 + bytes(palette8) + bytes((7, 1, 2, 3))
        header8 = struct.pack("<HHI4s", 2, 9, 16 + len(payload8), TMX_TAG) + bytes(4)
        temp.write_bytes(header8 + payload8)
        decoded8 = read_tmx(temp)
        assert decoded8.user_id == 9
        assert decoded8.comment == "title.tga"
        assert decoded8.pixel_format == PSMT8
        assert decoded8.palette_format == PSMCT32
        assert decoded8.pixels == (7, 1, 2, 3)
        assert decoded8.palettes[0][7] == (10, 20, 30, 128)
        assert decoded8.palettes[0][0] == (0, 0, 0, 0)

        body_glyph = struct.pack(
            "<BBHHBBHBBII28s",
            8, PSMCT32, 1024, 1024, PSMT4, 0, 0, 0, 0, 0, 0, b"font.tim",
        )
        glyph_pixels = bytes((0x0F, 0x10)) + bytes(524286)
        glyph_clut = b"".join(bytes((212, 212, 212, 120 - index * 8)) for index in range(16)) * 8
        glyph = body_glyph + glyph_clut + glyph_pixels + bytes(960)
        header_glyph = struct.pack("<HHI4s", 2, 11, 16 + len(glyph), TMX_TAG) + bytes(4)
        temp.write_bytes(header_glyph + glyph)
        decoded_glyph = read_tmx(temp)
        assert decoded_glyph.pixel_format == PSMT4
        assert decoded_glyph.palette_format == PSMCT32
        assert decoded_glyph.pixels[:4] == (15, 0, 0, 1)
        assert decoded_glyph.palettes[0][0] == (212, 212, 212, 120)
        preview = Path(tmp) / "preview.png"
        save_png(decoded_glyph, 0, preview)
        with Image.open(preview) as bitmap:
            assert bitmap.getpixel((0, 0)) == (212, 212, 212, 0)
            assert bitmap.getpixel((1, 0)) == (212, 212, 212, 120)
        temp.write_bytes(header_glyph + glyph[:-1])
        try:
            read_tmx(temp)
        except ValueError:
            pass
        else:
            raise AssertionError("truncated glyph TMX was accepted")


def encode_psmt4(pixels: list[int]) -> bytes:
    if len(pixels) % 2:
        raise ValueError("PSMT4 pixel count must be even")
    return bytes(
        (pixels[i] & 0x0F) | ((pixels[i + 1] & 0x0F) << 4)
        for i in range(0, len(pixels), 2)
    )


def rgb_palette(raw: bytes) -> list[tuple[int, int, int]]:
    colors = []
    for value in struct.unpack("<16H", raw):
        colors.append((
            (value & 0x1F) << 3,
            ((value >> 5) & 0x1F) << 3,
            ((value >> 10) & 0x1F) << 3,
        ))
    return colors


def rgba_palette(raw: bytes) -> list[tuple[int, int, int, int]]:
    if len(raw) != 1024:
        raise ValueError(f"PSMT8 palette is {len(raw)} bytes, expected 1024")
    return [tuple(raw[i:i + 4]) for i in range(0, 1024, 4)]


def map_png_to_indices(
    png_path: Path,
    palette: list[tuple[int, ...]],
    width: int,
    height: int,
    alpha: bool,
    original: tuple[int, ...] | None = None,
) -> tuple[list[int], int]:
    if Image is None:
        raise RuntimeError("Pillow is required to read PNG files")
    image = Image.open(png_path).convert("RGBA" if alpha else "RGB")
    if image.size != (width, height):
        raise ValueError(f"PNG is {image.size[0]}x{image.size[1]}, expected {width}x{height}")

    exact = {color: index for index, color in enumerate(palette)}
    indices = []
    max_error = 0
    pixels = image.load()
    for y in range(height):
        for x in range(width):
            sample = pixels[x, y]
            index = exact.get(sample)
            if index is None:
                index, color = min(
                    enumerate(palette),
                    key=lambda item: sum((a - b) ** 2 for a, b in zip(sample, item[1])),
                )
                max_error = max(max_error, *(abs(a - b) for a, b in zip(sample, color)))
            elif original is not None:
                current = original[len(indices)]
                if current < len(palette) and palette[current] == sample:
                    index = current
            indices.append(index)
    return indices, max_error


def repack(template_path: Path, png_path: Path, output_path: Path, palette_index: int) -> int:
    image = read_tmx(template_path)
    template = template_path.read_bytes()
    if image.pixel_format == PSMT4 and image.palette_format == PSMCT32:
        raise ValueError("PNG repacking is unsupported for message atlases")
    if image.pixel_format == PSMT8:
        stride = 1024
        palette = rgba_palette(template[0x40 + palette_index * stride:0x40 + (palette_index + 1) * stride])
        indices, max_error = map_png_to_indices(
            png_path, palette, image.width, image.height, alpha=True, original=image.pixels
        )
        pixel_size = image.width * image.height
        encoded = bytes(index & 0xFF for index in indices)
        label = "PSMT8"
    elif image.pixel_format == PSMT4:
        stride = 32
        palette = rgb_palette(template[0x40 + palette_index * stride:0x40 + (palette_index + 1) * stride])
        indices, max_error = map_png_to_indices(
            png_path, palette, image.width, image.height, alpha=False, original=image.pixels
        )
        pixel_size = image.width * image.height // 2
        encoded = encode_psmt4(indices)
        label = "PSMT4"
    else:
        raise ValueError(f"unsupported pixel format 0x{image.pixel_format:02X}")

    pixel_offset = 0x40 + len(image.palettes) * stride
    payload = bytearray(template)
    payload[pixel_offset:pixel_offset + pixel_size] = encoded
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(payload)

    print(
        f"{output_path}: {image.width}x{image.height} {label}, "
        f"palette={palette_index}, max_rgb_error={max_error}"
    )
    return max_error


def repack_self_test() -> None:
    template_palette = b"".join(struct.pack("<H", value) for value in range(16))
    body = struct.pack(
        "<BBHHBBHBBII28s", 1, PSMCT16, 2, 2, PSMT4, 0, 0, 0, 0, 0, 0, b"test.tim"
    )
    pixels = bytes((0x21, 0x43))
    payload = body + template_palette + pixels
    template = struct.pack("<HHI4s", 2, 7, 16 + len(payload), b"TMX0") + bytes(4) + payload

    if Image is None:
        raise RuntimeError("Pillow is required")
    with tempfile.TemporaryDirectory() as tmp:
        temp_tmx = Path(tmp) / "template.tmx"
        temp_png = Path(tmp) / "edited.png"
        temp_out = Path(tmp) / "out.tmx"
        temp_tmx.write_bytes(template)
        palette = rgb_palette(template_palette)
        edited = Image.new("RGB", (2, 2))
        edited.putdata([palette[1], palette[2], palette[3], palette[4]])
        edited.save(temp_png)
        repack(temp_tmx, temp_png, temp_out, 0)
        assert temp_out.read_bytes() == template

        palette8 = bytearray(1024)
        colors = ((1, 2, 3, 128), (4, 5, 6, 255), (7, 8, 9, 64), (10, 11, 12, 32))
        for index, color in enumerate(colors):
            palette8[index * 4:index * 4 + 4] = bytes(color)
        body8 = struct.pack(
            "<BBHHBBHBBII28s", 1, PSMCT32, 2, 2, PSMT8, 0, 0, 0, 0, 0, 0, b"title.tga"
        )
        payload8 = body8 + bytes(palette8) + bytes((0, 1, 2, 3))
        template8 = struct.pack("<HHI4s", 2, 9, 16 + len(payload8), b"TMX0") + bytes(4) + payload8
        temp_tmx.write_bytes(template8)
        edited8 = Image.new("RGBA", (2, 2))
        edited8.putdata(colors)
        edited8.save(temp_png)
        repack(temp_tmx, temp_png, temp_out, 0)
        assert temp_out.read_bytes() == template8

        shared = bytes((9, 8, 7, 255))
        palette8 = bytearray(1024)
        palette8[0:4] = shared
        palette8[5 * 4:5 * 4 + 4] = shared
        palette8[4:8] = bytes((1, 2, 3, 255))
        payload8 = body8 + bytes(palette8) + bytes((0, 5, 0, 1))
        template8 = struct.pack("<HHI4s", 2, 9, 16 + len(payload8), b"TMX0") + bytes(4) + payload8
        temp_tmx.write_bytes(template8)
        edited8.putdata([tuple(shared), tuple(shared), tuple(shared), (1, 2, 3, 255)])
        edited8.save(temp_png)
        repack(temp_tmx, temp_png, temp_out, 0)
        assert temp_out.read_bytes() == template8

