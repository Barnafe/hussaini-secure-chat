# -*- mode: python ; coding: utf-8 -*-
# PyInstaller recipe for HussainiChat.exe (the desktop client; no console window).
# Build:  pyinstaller --noconfirm --clean packaging/HussainiChat.spec
import os

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

ROOT = os.path.abspath(os.path.join(SPECPATH, '..'))

a = Analysis(
    [os.path.join(ROOT, 'run_client.py')],
    pathex=[ROOT],
    binaries=collect_dynamic_libs('sounddevice') + collect_dynamic_libs('_sounddevice_data'),
    datas=[(os.path.join(ROOT, 'client', 'assets', 'icon.png'), os.path.join('client', 'assets')),
           (os.path.join(ROOT, 'client', 'assets', 'icon.ico'), os.path.join('client', 'assets'))]
          + collect_data_files('_sounddevice_data'),
    hiddenimports=['bcrypt', 'cryptography', 'PIL', 'PIL.Image', 'PIL.ImageTk', 'PIL._tkinter_finder', 'tkinter', 'tkinter.ttk', 'tkinter.filedialog',
                   'tkinter.messagebox', 'tkinter.simpledialog',
                   # voice/video calls (optional packages, bundled when installed at build time)
                   'numpy', 'sounddevice', '_sounddevice_data', 'cv2', 'client.media_engine', 'client.call_ui'],
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
    icon=os.path.join(ROOT, 'client', 'assets', 'icon.ico'),
)
