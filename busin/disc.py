"""Read a Mode 2 Form 1 BIN and rebuild its sector checksums."""
import struct
from pathlib import PurePosixPath

from busin.paths import BIN, BUILD

SYNC = bytes.fromhex("00ffffffffffffffffffff00")
PACK_LBA = 324
ELF_LBA = 262407
ELF_SIZE = 4914016
FILE_BASE = 0xFF000
CHUNK_TABLE = 0x33BEF0
assert FILE_BASE + CHUNK_TABLE == 0x43AEF0  # runtime table read by 0x105CB8


def read_user(file, lba, count):
    if lba < 0 or count < 0:
        raise ValueError("negative sector range")
    data = bytearray()
    for sector in range(lba, lba + count):
        file.seek(sector * 2352)
        raw = file.read(2352)
        if (len(raw) != 2352 or raw[:12] != SYNC or raw[15] != 2
                or raw[16:20] != raw[20:24] or raw[18] & 32):
            raise ValueError(f"sector {sector} is not a complete Mode 2 Form 1 sector")
        data.extend(raw[24:2072])
    return bytes(data)


def read_pack(file, offset, size):
    start, skip = divmod(offset, 2048)
    return read_user(file, PACK_LBA + start, (skip + size + 2047) // 2048)[skip:skip + size]


def chunk_row(file, chunk):
    """<start sector, sector count> of a PACKDATA chunk, from the source ELF."""
    at = CHUNK_TABLE + chunk * 16
    return struct.unpack_from("<QQ", read_user(file, ELF_LBA + at // 2048, 1), at % 2048)


def entry(record):
    if len(record) < 34 or 33 + record[32] > len(record):
        raise ValueError("invalid ISO directory record")
    extent = struct.unpack_from("<I", record, 2)[0]
    size = struct.unpack_from("<I", record, 10)[0]
    if (extent != struct.unpack_from(">I", record, 6)[0]
            or size != struct.unpack_from(">I", record, 14)[0]):
        raise ValueError("ISO directory endian copies disagree")
    name = record[33:33 + record[32]].decode("ascii").split(";")[0]
    return name, extent, size


def children(file, parent):
    data = read_user(file, parent[1], (parent[2] + 2047) // 2048)[:parent[2]]
    pos = 0
    records = []
    while pos < len(data):
        size = data[pos]
        if not size:
            pos = (pos // 2048 + 1) * 2048
            continue
        if size < 34 or pos + size > len(data) or pos % 2048 + size > 2048:
            raise ValueError("invalid ISO directory record boundary")
        records.append(entry(data[pos:pos + size]))
        pos += size
    return records


def extract(resource):
    """Copy one ISO path into build/iso. Returns the output path."""
    from pathlib import Path
    path = PurePosixPath(str(resource).replace("\\", "/"))
    if path.is_absolute() or not path.parts or any(part in ("..", ".") or ":" in part for part in path.parts):
        raise ValueError("resource must be a relative ISO path without '..' or drive letters")
    output = BUILD / "iso" / Path(*path.parts)
    with BIN.open("rb") as source:
        pvd = read_user(source, 16, 1)
        if pvd[:7] != b"\x01CD001\x01":
            raise ValueError("missing ISO9660 primary volume descriptor")
        current = entry(pvd[156:156 + pvd[156]])
        for component in path.parts:
            current = next(e for e in children(source, current) if e[0].upper() == component.upper())
        output.parent.mkdir(parents=True, exist_ok=True)
        remaining = current[2]
        with output.open("wb") as target:
            sector = current[1]
            while remaining:
                count = min(64, (remaining + 2047) // 2048)
                data = read_user(source, sector, count)[:remaining]
                target.write(data)
                remaining -= len(data)
                sector += count
    assert output.stat().st_size == current[2]
    return output


def _tables():
    edc, f, b = [], [0] * 256, [0] * 256
    for i in range(256):
        value = i
        for _ in range(8):
            value = (value >> 1) ^ (0xD8018001 if value & 1 else 0)
        edc.append(value)
        j = ((i << 1) ^ (0x11D if i & 0x80 else 0)) & 255
        f[i] = j
        b[i ^ j] = i
    return edc, f, b


EDC, ECC_F, ECC_B = _tables()


def edc_of(data):
    value = 0
    for byte in data:
        value = EDC[(value ^ byte) & 255] ^ (value >> 8)
    return value & 0xFFFFFFFF


def _ecc(src, major, minor, major_mult, minor_inc, dest):
    size = major * minor
    for maj in range(major):
        index = (maj >> 1) * major_mult + (maj & 1)
        ecc_a = ecc_b = 0
        for _ in range(minor):
            temp = src[index]
            index += minor_inc
            if index >= size:
                index -= size
            ecc_a ^= temp
            ecc_b ^= temp
            ecc_a = ECC_F[ecc_a]
        ecc_a = ECC_B[ECC_F[ecc_a] ^ ecc_b]
        dest[maj] = ecc_a
        dest[maj + major] = ecc_a ^ ecc_b


def finish_sector(raw, lba):
    out = bytearray(raw)
    minute, rem = divmod(lba + 150, 75 * 60)
    second, frame = divmod(rem, 75)
    def bcd(n):
        return ((n // 10) << 4) | (n % 10)
    out[12:15] = bytes((bcd(minute), bcd(second), bcd(frame)))
    out[15] = 2
    out[2072:2076] = edc_of(out[16:2072]).to_bytes(4, "little")
    _ecc(out[12:], 86, 24, 2, 86, out[2076:])
    _ecc(out[12:], 52, 43, 86, 88, out[2248:])
    return bytes(out)


