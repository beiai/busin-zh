"""Create and update data/text chunk files.

The build only reads those files. These commands write them when run explicitly.
"""
import json
import tempfile
from pathlib import Path

from busin.paths import DATA
from busin.text import (
    PACKED_CHUNKS, _load_chunk, _script_chunks, check_lines, counted_chunks, counted_messages,
    encode_message, message_prose, script_messages, script_shows, text_cursors,
)


def _root(root: Path | None) -> Path:
    return DATA / "text" if root is None else root


def _path(chunk: int, root: Path | None) -> Path:
    return _root(root) / f"{chunk}.json"


def _load(chunk: int, root: Path | None) -> dict:
    return json.loads(_path(chunk, root).read_text(encoding="utf-8"))


def _save(chunk: int, doc: dict, root: Path | None) -> None:
    _path(chunk, root).write_text(
        json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def _chunk_row(chunk: int) -> tuple[int, int]:
    found = next(((start, count) for index, start, count in _script_chunks() if index == chunk), None)
    if found is None:
        raise ValueError(f"chunk {chunk} is not on the disc")
    return found


def _disc_words(chunk: int) -> dict[int, list[int]]:
    """Offset to words without the terminator."""
    start, count = _chunk_row(chunk)
    data = _load_chunk(start, count)
    if chunk in counted_chunks():
        return {at: words for at, _end, words in counted_messages(data)}
    origin = text_cursors().get(chunk)
    if origin is None:
        raise ValueError(f"chunk {chunk} has no descriptor text cursor")
    messages, skipped = script_messages(data, origin)
    if skipped:
        raise ValueError(f"chunk {chunk} has an unreadable message at {skipped[0]:#x}")
    return {at: words[:-1] for at, words in messages}


def init_text(chunk: int, align: str | None = None, root: Path | None = None) -> tuple[Path, int, list[str]]:
    """Write data/text/<chunk>.json with every readable message, text left null."""
    path = _path(chunk, root)
    if path.exists():
        raise ValueError(f"{path} exists; it may hold translations")
    start, count = _chunk_row(chunk)
    data = _load_chunk(start, count)
    if chunk in counted_chunks():
        align = align or "center"
        messages = []
        for at, _end, words in counted_messages(data):
            source = message_prose(words, align)
            if any(source):
                messages.append(_entry(chunk, at, "screen", source))
        skipped = []
    else:
        align = align or "left"
        origin = text_cursors().get(chunk)
        if origin is None:
            raise ValueError(f"chunk {chunk} has no descriptor text cursor")
        found, unread = script_messages(data, origin)
        shows = script_shows(data, origin)
        messages = []
        for at, words in found:
            source = message_prose(words[:-1], align)
            if any(source):
                messages.append(_entry(chunk, at, shows[at], source))
        skipped = [f"0x{at:04X}" for at in unread]
    doc = {"chunk": chunk, "base": f"0x{start * 2048:X}", "align": align, "messages": messages}
    _save(chunk, doc, root)
    return path, len(messages), skipped


def _entry(chunk: int, at: int, show: str, source: list[str]) -> dict:
    """One message. Packed chunks omit the disc address; packing computes the position."""
    item = {"show": show, "source": "\n".join(source), "text": None}
    if chunk not in PACKED_CHUNKS:
        item = {"offset": f"0x{at:04X}", **item}
    return item


def _by_disc(chunk: int, doc: dict) -> dict[int, dict]:
    """Disc address to message. Packed chunks are aligned in file order."""
    if chunk not in PACKED_CHUNKS:
        return {int(item["offset"], 16): item for item in doc["messages"]}
    disc = list(_disc_words(chunk))
    if len(disc) != len(doc["messages"]):
        raise ValueError(f"chunk {chunk} does not match the disc")
    return {at: item for at, item in zip(disc, doc["messages"])}


def apply_text(chunk: int, files: list[str], root: Path | None = None) -> None:
    """Merge translation fragments into one chunk. Filled lines are left alone.

    A fragment names a line by its disc address. Packed chunks do not store that address.
    """
    doc = _load(chunk, root)
    index = _by_disc(chunk, doc)
    for name in files:
        frag = json.loads(Path(name).read_text(encoding="utf-8"))
        if frag.get("chunk") != chunk:
            raise ValueError(f"{name} is chunk {frag.get('chunk')}")
        for message in frag["messages"]:
            label = message["offset"]
            item = index.get(int(label, 16))
            if item is None:
                raise ValueError(f"{name} offset {label} is not in chunk {chunk}")
            if item["text"] is not None:
                raise ValueError(f"chunk {chunk} {label} is already translated")
            text = message.get("text")
            if type(text) is not list or not text or any(type(line) is not str for line in text):
                raise ValueError(f"{name} {label} text is not a list of strings")
            item["text"] = text
    _save(chunk, doc, root)


def check_text(chunk: int, offsets: list[str] | None = None, root: Path | None = None) -> None:
    """Check translated lines against the disc. Untranslated lines are skipped.

    Packed chunks are aligned in order. An offset argument is the disc address.
    """
    doc = _load(chunk, root)
    disc = _disc_words(chunk)
    want = None if not offsets else {int(item, 16) for item in offsets}
    errors = []
    narration = chunk in counted_chunks()
    if chunk in PACKED_CHUNKS:
        pairs = list(disc.items())
        if len(pairs) != len(doc["messages"]):
            raise ValueError(f"chunk {chunk} does not match the disc")
        rows = [(at, words, item) for (at, words), item in zip(pairs, doc["messages"])]
    else:
        rows = [(int(item["offset"], 16), disc.get(int(item["offset"], 16)), item) for item in doc["messages"]]
    for at, original, item in rows:
        if item["text"] is None:
            continue
        label = f"0x{at:04X}"
        if want is not None and at not in want:
            continue
        if original is None:
            errors.append(f"{label}: is not on the disc")
            continue
        if message_prose(original, doc["align"]) != item["source"].split("\n"):
            errors.append(f"{label}: source does not match the disc")
            continue
        try:
            check_lines(original, item["text"], doc["align"], item["show"], narration)
            encode_message(original, item["text"], doc["align"], lambda char: 0x4001)
        except ValueError as error:
            errors.append(f"{label}: {error}")
    if errors:
        raise ValueError("\n".join(errors))


def copy_text(source: int, targets: list[int], root: Path | None = None) -> None:
    """Copy translations onto chunks whose source lines match one by one."""
    src = _load(source, root)
    sources = [item["source"] for item in src["messages"]]
    texts = [item["text"] for item in src["messages"]]
    if any(text is None for text in texts):
        raise ValueError(f"chunk {source} still has untranslated messages")
    for chunk in targets:
        doc = _load(chunk, root)
        if [item["source"] for item in doc["messages"]] != sources:
            raise ValueError(f"chunk {chunk} source does not match chunk {source}")
        for item, text in zip(doc["messages"], texts):
            item["text"] = text
        _save(chunk, doc, root)


def _expect_error(call, text: str) -> None:
    try:
        call()
    except ValueError as error:
        if text not in str(error):
            raise AssertionError(f"expected {text!r} in {error}") from None
    else:
        raise AssertionError(f"accepted a case that should fail: {text}")


def self_test() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        blank = {
            "chunk": 1, "base": "0x0", "align": "left",
            "messages": [
                {"offset": "0x0010", "show": "screen", "source": "あ", "text": None},
                {"offset": "0x0020", "show": "window", "source": "い", "text": None},
            ],
        }
        other = {
            "chunk": 2, "base": "0x0", "align": "left",
            "messages": [
                {"offset": "0x0010", "show": "screen", "source": "あ", "text": None},
                {"offset": "0x0020", "show": "window", "source": "い", "text": None},
            ],
        }
        _save(1, blank, root)
        _save(2, other, root)
        _expect_error(lambda: copy_text(1, [2], root), "untranslated")
        frag = root / "frag.json"
        frag.write_text(json.dumps({
            "chunk": 1,
            "messages": [
                {"offset": "0x0010", "text": ["甲"]},
                {"offset": "0x0020", "text": ["乙"]},
            ],
        }, ensure_ascii=False), encoding="utf-8")
        apply_text(1, [str(frag)], root)
        filled = _load(1, root)
        if [item["text"] for item in filled["messages"]] != [["甲"], ["乙"]]:
            raise AssertionError("fragment was not applied")
        _expect_error(lambda: apply_text(1, [str(frag)], root), "already translated")
        wrong = root / "wrong.json"
        wrong.write_text(json.dumps({"chunk": 9, "messages": []}), encoding="utf-8")
        _expect_error(lambda: apply_text(1, [str(wrong)], root), "is chunk 9")
        bad = root / "bad.json"
        bad.write_text(json.dumps({
            "chunk": 1, "messages": [{"offset": "0x0030", "text": ["丙"]}],
        }), encoding="utf-8")
        _expect_error(lambda: apply_text(1, [str(bad)], root), "not in chunk")
        shape = root / "shape.json"
        shape.write_text(json.dumps({
            "chunk": 2, "messages": [{"offset": "0x0010", "text": "甲"}],
        }), encoding="utf-8")
        _expect_error(lambda: apply_text(2, [str(shape)], root), "list of strings")
        copy_text(1, [2], root)
        if [item["text"] for item in _load(2, root)["messages"]] != [["甲"], ["乙"]]:
            raise AssertionError("translation was not copied")
        drifted = _load(2, root)
        drifted["messages"][0]["source"] = "う"
        drifted["messages"][0]["text"] = None
        _save(2, drifted, root)
        _expect_error(lambda: copy_text(1, [2], root), "does not match")
        (root / "9.json").write_text("{}\n", encoding="utf-8")
        _expect_error(lambda: init_text(9, root=root), "exists")


def disc_self_test() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for chunk in (309, 269):
            _path_out, count, _skipped = init_text(chunk, root=root)
            fresh = _load(chunk, root)
            saved = json.loads((DATA / "text" / f"{chunk}.json").read_text(encoding="utf-8"))
            if fresh["align"] != saved["align"] or fresh["base"] != saved["base"]:
                raise AssertionError(f"chunk {chunk} header does not match the saved file")
            got = [(item["offset"], item["show"], item["source"]) for item in fresh["messages"]]
            expect = [(item["offset"], item["show"], item["source"]) for item in saved["messages"]]
            if got != expect or count != len(fresh["messages"]) or any(item["text"] is not None for item in fresh["messages"]):
                raise AssertionError(f"chunk {chunk} skeleton does not match the saved file")
        doc = _load(309, root)
        doc["messages"][0]["text"] = ["甲"]
        doc["messages"][0]["source"] = "not-the-disc"
        _save(309, doc, root)
        _expect_error(lambda: check_text(309, [doc["messages"][0]["offset"]], root), "does not match")
        saved = json.loads((DATA / "text" / "309.json").read_text(encoding="utf-8"))
        doc["messages"][0]["source"] = saved["messages"][0]["source"]
        doc["messages"][0]["text"] = saved["messages"][0]["text"]
        _save(309, doc, root)
        check_text(309, [doc["messages"][0]["offset"]], root)
        counted = _load(269, root)
        saved = json.loads((DATA / "text" / "269.json").read_text(encoding="utf-8"))
        counted["messages"][0]["text"] = saved["messages"][0]["text"]
        _save(269, counted, root)
        check_text(269, [counted["messages"][0]["offset"]], root)
