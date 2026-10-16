"""Render Source Han Sans Medium into 16×16 UI cells."""
import hashlib
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from busin.font import seat_baseline
from busin.paths import BUILD

CELL = 16
EM = 16
BASELINE = CELL - 1
SOLID = 128
SAMPLES = ("姓名？", "姓 名", "种 族", "阵 营", "职 业", "等级", "性 格",
           "平假", "片假", "英数", "符号", "空格", "完成", "切换页面", "确定", "返回")
FONTS = (
    ("primary", Path("C:/Windows/Fonts/NotoSansSC-VF.ttf"), "Medium",
     "763146584cf0710223441356b4395e279021b0806c196614377a7a0174ae074a"),
)


def _checked_fonts():
    fonts = []
    for role, path, version, expected in FONTS:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != expected:
            raise ValueError(f"{role} font SHA-256 does not match {version}: {path.name}")
        fonts.append({"role": role, "file": path.name, "version": version, "sha256": digest})
    return fonts


def _primary():
    font = ImageFont.truetype(str(FONTS[0][1]), EM)
    font.set_variation_by_name("Medium")
    if font.getname() != ("Noto Sans SC", "Medium"):
        raise ValueError("primary font is not Source Han Sans Medium")
    ascent, descent = font.getmetrics()
    if (ascent, descent) != (EM, 4):
        raise ValueError(f"primary em is {ascent}+{descent}, expected 16+4")
    return font


def _placed(font, char):
    return seat_baseline(font, char, CELL)


def load_ui_glyphs(wanted):
    fonts = _checked_fonts()
    font = _primary()
    missing = font.getmask(chr(0x10FFFF), mode="L")
    glyphs = {}
    raised = {}
    for char in sorted(set(wanted)):
        bitmap = font.getmask(char, mode="L")
        if bitmap.size == missing.size and bytes(bitmap) == bytes(missing):
            raise ValueError(f"font is missing {char!r}")
        glyphs[char], raised[char] = _placed(font, char)
    return glyphs, raised, fonts


def render_ui_glyphs():
    wanted = sorted(set("".join(SAMPLES)) - {" "})
    glyphs, raised, fonts = load_ui_glyphs(wanted)
    out = BUILD / "font" / "ui-glyphs"
    out.mkdir(parents=True, exist_ok=True)
    columns = 16
    atlas = Image.new("RGBA", (columns * CELL, (len(wanted) + columns - 1) // columns * CELL))
    report = {"fonts": fonts, "em_size": EM, "cell_size": CELL,
              "font_ascent": EM, "font_descent": 4, "baseline": BASELINE,
              "samples": SAMPLES, "glyphs": []}
    packed = bytearray()
    for index, char in enumerate(wanted):
        mask = glyphs[char]
        png = Image.new("RGBA", (CELL, CELL), "white")
        png.putalpha(mask)
        filename = f"u{ord(char):04x}.png"
        png.save(out / filename)
        atlas.paste(png, (index % columns * CELL, index // columns * CELL))
        raw = mask.tobytes()
        assert len(raw) == CELL * CELL
        packed.extend(raw)
        report["glyphs"].append({"character": char, "preview_cell": index, "png": filename,
                                  "raised": raised[char], "ink_box": mask.getbbox(),
                                  "bitmap_sha256": hashlib.sha256(raw).hexdigest()})
    atlas.save(out / "cells.png")
    (out / "cells-l8.raw").write_bytes(packed)
    preview = Image.new("RGB", (160, len(SAMPLES) * 24 + 8), "#303030")
    for row, sample in enumerate(SAMPLES):
        for col, char in enumerate(sample):
            if char != " ":
                preview.paste("#e8e8e8", (8 + col * CELL, 4 + row * 24), glyphs[char])
    preview.save(out / "text.png")
    preview.resize((preview.width * 3, preview.height * 3), Image.Resampling.NEAREST).save(out / "text-large.png")
    sheet = Image.new("RGB", (8 * 88, (len(wanted) + 7) // 8 * 88), "#303030")
    draw = ImageDraw.Draw(sheet)
    for index, char in enumerate(wanted):
        x, y = index % 8 * 88, index // 8 * 88
        draw.rectangle((x, y, x + 63, y + 63), outline="#555555")
        sheet.paste("#e8e8e8", (x, y), glyphs[char].resize((64, 64), Image.Resampling.NEAREST))
        draw.text((x, y + 66), f"U+{ord(char):04X}", fill="white")
    sheet.save(out / "cells-large.png")
    output_names = [f"u{ord(char):04x}.png" for char in wanted]
    output_names.extend(("cells.png", "cells-l8.raw", "text.png", "text-large.png", "cells-large.png"))
    report["outputs_sha256"] = {name: hashlib.sha256((out / name).read_bytes()).hexdigest()
                                for name in sorted(output_names)}
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return out


def _solid_rows(mask):
    return [y for y in range(CELL) if any(mask.getpixel((x, y)) >= SOLID for x in range(CELL))]


def self_test():
    wanted = set("".join(SAMPLES)) - {" "}
    glyphs, raised, _fonts = load_ui_glyphs(wanted | set("()pqyQ/{}"))
    assert raised["姓"] == 0 and raised["名"] == 0
    assert raised["p"] == raised["q"] == raised["y"] == 3
    assert raised["("] == raised[")"] == raised["Q"] == raised["{"] == raised["}"] == 2
    assert raised["/"] == 1
    assert _solid_rows(glyphs["p"])[0] == 3 and _solid_rows(glyphs["p"])[-1] == CELL - 1
    assert any(0 < value < 255 for value in glyphs["名"].get_flattened_data())
    name = glyphs["姓"].getbbox()
    assert name[0] == 0 and name[2] == CELL
    try:
        load_ui_glyphs({chr(0x10FFFF)})
    except ValueError as error:
        assert "font is missing" in str(error)
    else:
        raise AssertionError("missing glyph was silently accepted")
