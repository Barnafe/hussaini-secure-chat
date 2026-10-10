# -*- mode: python ; coding: utf-8 -*-
# PyInstaller recipe for HussainiChat.exe (the desktop client; no console window).
# Build:  pyinstaller --noconfirm --clean packaging/HussainiChat.spec
import os

ROOT = os.path.abspath(os.path.join(SPECPATH, '..'))

a = Analysis(
    [os.path.join(ROOT, 'run_client.py')],
    pathex=[ROOT],
    binaries=[],
    datas=[],
    hiddenimports=['bcrypt', 'cryptography', 'tkinter', 'tkinter.ttk', 'tkinter.filedialog',
                   'tkinter.messagebox'],
    hookspath=[],
    runtime_hooks=[],
    excludes=['pytest', 'yaml', 'sqlite3', 'server'],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='HussainiChat',
    debug=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=False,
)
