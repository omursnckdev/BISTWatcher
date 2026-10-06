"""Where the desktop app finds its bundled config and keeps user data.

* Bundled defaults: ``<repo>/config`` in development, ``<bundle>/config`` in the
  PyInstaller build.
* User data (preferences, holdings, price cache, KAP archive, reports):
  ``%LOCALAPPDATA%\\BISTWatcher`` on Windows, ``~/.bistwatcher`` elsewhere, or
  ``$BISTWATCHER_HOME``. YAML files placed in ``<user data>/config`` replace the
  bundled ones, so advanced settings can be edited without rebuilding.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

from bist_quant.config import DEFAULT_CONFIG_DIR

CONFIG_FILES = ("settings.yaml", "scoring.yaml", "universe.yaml", "backtest.yaml")


def bundled_config_dir() -> Path:
    base = getattr(sys, "_MEIPASS", None)
    return Path(base) / "config" if base else DEFAULT_CONFIG_DIR


def user_home() -> Path:
    env = os.environ.get("BISTWATCHER_HOME")
    if env:
        path = Path(env)
    elif sys.platform == "win32" and os.environ.get("LOCALAPPDATA"):
        path = Path(os.environ["LOCALAPPDATA"]) / "BISTWatcher"
    else:
        path = Path.home() / ".bistwatcher"
    path.mkdir(parents=True, exist_ok=True)
    return path


def config_dir(home: Path | None = None) -> Path:
    """A merged config directory: user overrides win over the bundled files."""
    home = home or user_home()
    user_cfg = home / "config"
    bundled = bundled_config_dir()
    if not user_cfg.exists() or not any((user_cfg / n).exists() for n in CONFIG_FILES):
        return bundled
    merged = home / "cache" / "config"
    merged.mkdir(parents=True, exist_ok=True)
    for name in CONFIG_FILES:
        src = user_cfg / name if (user_cfg / name).exists() else bundled / name
        shutil.copyfile(src, merged / name)
    return merged


def export_default_config(home: Path | None = None) -> Path:
    """Copy the bundled YAML files into ``<user data>/config`` (existing files are kept)."""
    home = home or user_home()
    target = home / "config"
    target.mkdir(parents=True, exist_ok=True)
    for name in CONFIG_FILES:
        if not (target / name).exists():
            shutil.copyfile(bundled_config_dir() / name, target / name)
    return target


def reports_dir(home: Path | None = None) -> Path:
    path = (home or user_home()) / "raporlar"
    path.mkdir(parents=True, exist_ok=True)
    return path
