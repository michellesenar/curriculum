"""Where curriculum keeps its files. The one place that decides."""

import os
import sys
from pathlib import Path

APP_NAME = "Curriculum"
PROJECT_ROOT = Path(__file__).parent.parent.parent


def _data_dir() -> Path:
    """
    Per-user data directory. Override with CURRICULUM_DATA_DIR.
    macOS: ~/Library/Application Support/Curriculum
    Windows: %APPDATA%\\Curriculum
    Elsewhere: $XDG_DATA_HOME/curriculum (default ~/.local/share/curriculum)
    """
    override = os.environ.get("CURRICULUM_DATA_DIR")
    if override:
        return Path(override).expanduser()
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
        return base / APP_NAME
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    xdg = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg) if xdg else Path.home() / ".local" / "share"
    return base / APP_NAME.lower()


def _resolve(name: str, legacy: Path) -> Path:
    """
    Use the per-user location, unless the file still lives in the project
    folder from before the move and nothing exists at the new location yet.
    That keeps an existing install working without moving anyone's data.
    An explicit CURRICULUM_DATA_DIR always wins.
    """
    new = _data_dir() / name
    if os.environ.get("CURRICULUM_DATA_DIR"):
        return new
    if not new.exists() and legacy.exists():
        return legacy
    return new


DATA_DIR = _data_dir()
DB_PATH = _resolve("curriculum.db", PROJECT_ROOT / "curriculum.db")
CONFIG_PATH = _resolve("filter_topics.txt", PROJECT_ROOT / "config" / "filter_topics.txt")
