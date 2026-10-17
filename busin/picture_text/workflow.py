"""Coordinate generated picture rendering, export, and PNG repacking."""
from pathlib import Path

from busin.picture_text.render import parse_generated
from busin.tmx import read_tmx_bytes, repack_bytes, save_png


def source_spans(item: dict, read, row) -> list[tuple[int, bytes]]:
    """Return every original TMX source span used by one generated picture."""
    spec = parse_generated(item)
    if spec.renderer == "bar_buttons":
        from busin.picture_text.bar import source_spans as renderer_sources
        return renderer_sources(read, row)
    if spec.renderer == "ali_buttons":
        from busin.picture_text.ali import source_spans as renderer_sources
        return renderer_sources(read, spec.name)
    raise ValueError(f"unknown generated renderer {spec.renderer!r}")


def render_generated(item: dict, read, row) -> list[tuple[int, bytes]]:
    """Render translated TMX bytes from labels for initial export and previews."""
    spec = parse_generated(item)
    if spec.renderer == "bar_buttons":
        from busin.picture_text.bar import load_spec, spans
        return spans(read, row, load_spec(item))
    if spec.renderer == "ali_buttons":
        from busin.picture_text.ali import load_spec, spans
        return spans(read, row, load_spec(item))
    raise ValueError(f"unknown generated renderer {spec.renderer!r}")


def validate_repacked(item: dict, source: bytes, packed: bytes) -> None:
    spec = parse_generated(item)
    if len(packed) != len(source):
        raise ValueError(f"{spec.name} repacked size changed")
    if spec.renderer == "bar_buttons":
        from busin.picture_text.bar import validate_repacked as renderer_check
        renderer_check(source, packed)
    elif spec.renderer == "ali_buttons":
        from busin.picture_text.ali import validate_repacked as renderer_check
        renderer_check(spec.name, source, packed)
    else:
        raise ValueError(f"unknown generated renderer {spec.renderer!r}")


def preview_generated(item: dict, read, row, output: Path) -> Path:
    spec = parse_generated(item)
    if spec.renderer == "bar_buttons":
        from busin.picture_text.bar import load_spec, preview
        return preview(read, row, load_spec(item), output)
    if spec.renderer == "ali_buttons":
        from busin.picture_text.ali import load_spec, preview
        return preview(read, row, load_spec(item), output)
    raise ValueError(f"unknown generated renderer {spec.renderer!r}")


def export_generated_picture(item: dict, read, row, output: Path) -> None:
    """Export the original and initial translated PNG pair for one generated picture."""
    name = item["name"]
    output.mkdir(parents=True, exist_ok=True)
    sources = source_spans(item, read, row)
    if not sources:
        raise ValueError(f"{name} generated no source picture bytes")
    source = sources[0][1]
    original = output / f"{name}.png"
    save_png(read_tmx_bytes(source), item["palette"], original)
    translated = output / f"{name}.zh.png"
    if translated.is_file():
        return

    rendered = render_generated(item, read, row)
    if not rendered:
        raise ValueError(f"{name} generated no translated picture bytes")
    if [offset for offset, _data in rendered] != [offset for offset, _data in sources]:
        raise ValueError(f"{name} rendered source offsets changed")
    if [len(data) for _offset, data in rendered] != [len(data) for _offset, data in sources]:
        raise ValueError(f"{name} rendered source sizes changed")
    if item["renderer"] == "bar_buttons":
        if len({data for _offset, data in rendered}) != 1:
            raise ValueError("bar_00 rendered copies differ")
    elif len(rendered) != 1:
        raise ValueError(f"{name} rendered {len(rendered)} picture copies")
    save_png(read_tmx_bytes(rendered[0][1]), item["palette"], translated)


def repack_generated_picture(item: dict, read, row, translated: Path) -> list[tuple[int, bytes]]:
    """Repack one translated PNG into every original TMX source span."""
    name = item["name"]
    sources = source_spans(item, read, row)
    if not sources:
        raise ValueError(f"{name} generated no source picture bytes")
    found = [
        (offset, repack_bytes(source, translated, item["palette"]))
        for offset, source in sources
    ]
    for copy, ((offset, source), (packed_offset, packed)) in enumerate(zip(sources, found)):
        if offset != packed_offset:
            raise ValueError(f"{name} copy {copy} source offset changed")
        validate_repacked(item, source, packed)
    if item["renderer"] == "bar_buttons" and len({data for _offset, data in found}) != 1:
        raise ValueError("bar_00 repacked copies differ")
    return found
