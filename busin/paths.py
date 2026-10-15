"""Repository paths. The source disc path lives in the untracked root config.toml."""
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
BUILD = ROOT / "build"
CONFIG = ROOT / "config.toml"
_DISC_HINT = '把日版原盘的路径写在仓库根目录的 config.toml 里：disc = "路径"。'


def disc_path(config: Path = CONFIG):
    """Path from config.toml, or None when the file, key, or value is missing."""
    if not config.is_file():
        return None
    try:
        with config.open("rb") as handle:
            data = tomllib.load(handle)
    except tomllib.TOMLDecodeError:
        return None
    raw = data.get("disc")
    if not isinstance(raw, str) or not raw.strip():
        return None
    path = Path(raw.strip())
    return path if path.is_absolute() else config.parent / path


class _Disc:
    """Opens the configured disc. exists() is false when it is not set."""

    def exists(self):
        path = disc_path()
        return path is not None and path.is_file()

    def open(self, *args, **kwargs):
        path = disc_path()
        if path is None or not path.is_file():
            raise SystemExit(_DISC_HINT)
        return path.open(*args, **kwargs)

    def is_relative_to(self, other):
        return self._file().is_relative_to(other)

    def relative_to(self, other):
        return self._file().relative_to(other)

    def as_posix(self):
        return self._file().as_posix()

    def _file(self):
        path = disc_path()
        if path is None:
            raise SystemExit(_DISC_HINT)
        return path


BIN = _Disc()
