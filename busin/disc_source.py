"""High-level reads over one source disc."""
from __future__ import annotations

from contextlib import contextmanager

from busin.disc import BIN, chunk_row, read_pack, read_user


class SourceDisc:
    def __init__(self, path=BIN):
        self.path = path
        self.file = None

    def __enter__(self):
        self.file = self.path.open("rb")
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.file.close()
        self.file = None

    def user_sector(self, lba: int) -> bytes:
        return read_user(self.file, lba, 1)

    def user_sectors(self, lba: int, count: int) -> bytes:
        return read_user(self.file, lba, count)

    def pack(self, offset: int, size: int) -> bytes:
        return read_pack(self.file, offset, size)

    def chunk_row(self, chunk: int) -> tuple[int, int]:
        return chunk_row(self.file, chunk)

    def sector_count(self) -> int:
        return self.file.seek(0, 2) // 2352


@contextmanager
def open_source_disc(path=BIN):
    disc = SourceDisc(path)
    with disc:
        yield disc
