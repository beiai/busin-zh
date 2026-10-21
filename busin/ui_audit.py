"""Audit directly drawn UI strings collected from the project IDA database."""
import json
import re
from pathlib import Path

from busin.paths import BUILD, DATA


def compact(text):
    text = re.sub(r"\{\d+\}", "", text)
    return text.replace(" ", "").replace("/", "")


def known_sources():
    known = set()
    for path in sorted((DATA / "interface").glob("*.json")):
        for row in json.loads(path.read_text(encoding="utf-8")):
            known.add(compact(row.get("source", "")))
    return known


def _drop_suffixes(rows, universe=None):
    universe = rows if universe is None else universe
    out = []
    for row in rows:
        text = compact(row["text"])
        if any(
            other is not row
            and other.get("function") == row.get("function")
            and len(compact(other["text"])) > len(text)
            and compact(other["text"]).endswith(text)
            for other in universe
        ):
            continue
        out.append(row)
    return out


def run_audit(path=None):
    path = Path(path) if path else BUILD / "ui-audit" / "direct-draw-strings.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    known = known_sources()
    rows = []
    for item in data.get("strings", []):
        row = dict(item)
        row["known"] = compact(row["text"]) in known
        rows.append(row)
    pending = _drop_suffixes([row for row in rows if not row["known"]], rows)
    pending.sort(key=lambda row: (int(row["va"], 16), row["text"]))
    output = BUILD / "ui-audit" / "untranslated-candidates.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(pending, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {
        "input": path,
        "output": output,
        "caller_functions": data.get("caller_functions"),
        "strings": len(rows),
        "pending": pending,
    }


def format_report(report):
    lines = [
        f"direct-draw callers: {report['caller_functions']}",
        f"direct-draw strings: {report['strings']}",
        f"pending untranslated: {len(report['pending'])}",
    ]
    for row in report["pending"]:
        lines.append(f"{row['va']}  {row['text']}  draw={row['function']} ref={row['from']}")
    return "\n".join(lines)


def self_test():
    rows = [
        {"va": "0x10", "text": "戻りますか?", "function": "0x20", "from": "0x24"},
        {"va": "0x18", "text": "ますか?", "function": "0x20", "from": "0x28"},
        {"va": "0x30", "text": "はい  いいえ", "function": "0x40", "from": "0x44"},
    ]
    assert compact("はい{2}いいえ") == compact(rows[2]["text"])
    assert [row["va"] for row in _drop_suffixes(rows)] == ["0x10", "0x30"]
