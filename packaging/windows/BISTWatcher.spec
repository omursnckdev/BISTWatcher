# PyInstaller spec for the Windows desktop app. Build from the repository root:
#     pyinstaller packaging/windows/BISTWatcher.spec --noconfirm
from pathlib import Path

ROOT = Path(SPECPATH).parents[1]

a = Analysis(
    [str(ROOT / "packaging" / "windows" / "launcher.py")],
    pathex=[str(ROOT / "src")],
    datas=[
        (str(ROOT / "config"), "config"),
        (str(ROOT / "src" / "bist_quant" / "gui" / "icon.png"), "bist_quant/gui"),
    ],
    hiddenimports=["tzdata", "openpyxl", "matplotlib.backends.backend_qtagg"],
    excludes=["tkinter", "pytest", "IPython", "PyQt5", "PyQt6", "PySide2"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="BISTWatcher",
    console=False,
    icon=str(ROOT / "packaging" / "windows" / "bistwatcher.ico"),
)
coll = COLLECT(exe, a.binaries, a.datas, name="BISTWatcher")
