"""Extract analysis members of a PCSX2 v9 savestate into build/p2s/<name>."""
import shutil
import struct
import zipfile
from pathlib import Path

from busin.paths import BUILD

MEMBERS = ("eeMemory.bin", "GS.bin", "Screenshot.png", "scratchpad.bin",
           "vu0memory.bin", "vu1memory.bin", "vu1micromem.bin")


def extract_state(state: Path) -> Path:
    output = BUILD / "p2s" / Path(state).stem
    with zipfile.ZipFile(state) as source:
        bad = source.testzip()
        if bad is not None:
            raise ValueError(f"savestate CRC failed: {bad}")
        gs = source.read("GS.bin")
        if len(gs) != 4194813 or struct.unpack_from("<I", gs)[0] != 9:
            raise ValueError("only the verified GSState v9 layout is supported")
        if source.getinfo("eeMemory.bin").file_size != 33554432:
            raise ValueError("unexpected EE RAM size")
        output.mkdir(parents=True, exist_ok=True)
        for name in MEMBERS:
            if name in source.namelist():
                with source.open(name) as member, (output / name).open("wb") as target:
                    shutil.copyfileobj(member, target)
        (output / "GS_state.bin").write_bytes(gs[:425])
        (output / "GS_VRAM.bin").write_bytes(gs[425:425 + 4194304])
        (output / "GS_gif_path_q.bin").write_bytes(gs[425 + 4194304:])
    assert (output / "GS_VRAM.bin").stat().st_size == 4194304
    return output
