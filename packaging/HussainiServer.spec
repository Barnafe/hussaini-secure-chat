# -*- mode: python ; coding: utf-8 -*-
# PyInstaller recipe for HussainiServer.exe (console window that shows the server log).
# Build:  pyinstaller --noconfirm --clean packaging/HussainiServer.spec
import os

ROOT = os.path.abspath(os.path.join(SPECPATH, '..'))

a = Analysis(
    [os.path.join(ROOT, 'run_server.py')],
    pathex=[ROOT],
    binaries=[],
    datas=[],
    hiddenimports=['bcrypt', 'cryptography', 'sqlite3'],
    hookspath=[],
    runtime_hooks=[],
    excludes=['tkinter', '_tkinter', 'pytest', 'yaml'],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='HussainiServer',
    debug=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=True,
    icon=os.path.join(ROOT, 'client', 'assets', 'icon.ico'),
)
