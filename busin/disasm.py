"""Disassemble a virtual-address range of the game ELF."""
from pathlib import Path
import struct

import capstone

from busin.disc import ELF_LBA, ELF_SIZE, read_user
from busin.paths import BIN, BUILD
from busin.patch.font_paging import SHIFT


def load_elf(image=None):
    path = Path(image) if image else BIN
    lba = ELF_LBA + (SHIFT if image else 0)
    with path.open("rb") as handle:
        return read_user(handle, lba, (ELF_SIZE + 2047) // 2048)[:ELF_SIZE]


def disassemble(start, end, image=None):
    blob = load_elf(image)
    md = capstone.Cs(capstone.CS_ARCH_MIPS, capstone.CS_MODE_MIPS64 + capstone.CS_MODE_LITTLE_ENDIAN)
    md.skipdata = True
    phoff = struct.unpack_from("<I", blob, 0x1C)[0]
    phsize, phcount = struct.unpack_from("<HH", blob, 0x2A)
    for index in range(phcount):
        kind, offset, va, _pa, size = struct.unpack_from("<5I", blob, phoff + index * phsize)
        if kind == 1 and va <= start < end <= va + size:
            off = offset + start - va
            break
    else:
        raise ValueError(f"range {start:#x}..{end:#x} is outside ELF file-backed load segments")
    lines = []
    for ins in md.disasm(blob[off:off + end - start], start):
        word = int.from_bytes(ins.bytes, "little")
        lines.append(f"{ins.address:08x}: {word:08x}  {ins.mnemonic} {ins.op_str}")
    return "\n".join(lines)


def write_disassembly(start, end, image=None):
    text = disassemble(start, end, image)
    out = BUILD / "disasm" / f"{start:08x}-{end:08x}.asm"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text + "\n", encoding="utf-8")
    return out
