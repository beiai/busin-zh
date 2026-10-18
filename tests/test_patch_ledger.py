import pytest

from busin.patch.ledger import PatchLedger, expand, ranges


def test_ranges_round_trip():
    values = {1, 2, 3, 7, 10, 11}
    compressed = ranges(values)
    assert compressed == ((1, 3), (7, 7), (10, 11))
    assert expand(compressed) == values


def test_ledger_keeps_categories_and_rejects_overlap():
    ledger = PatchLedger()
    ledger.add_elf("font", 0x100, b"abc")
    ledger.add_elf("ui", 0x200, b"de")
    assert ledger.writes("elf") == {0x100: b"abc", 0x200: b"de"}
    assert ledger.report()["byte_patches"]["elf"]["font"] == {"patches": 1, "bytes": 3}

    ledger.add_elf("collision", 0x102, b"f")
    with pytest.raises(ValueError, match="font and collision overlap"):
        ledger.writes("elf")


def test_ledger_declares_pack_sectors():
    ledger = PatchLedger()
    ledger.add_pack("picture", 1, bytes(5))
    declared = ledger.sector_declarations("prefix")["picture"]
    assert declared == {324}
