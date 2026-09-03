# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
from PyInstaller.utils.hooks import collect_all

root = Path(SPEC).resolve().parents[1]
datas = [
    (str(root / 'frontend'), 'frontend'),
    (str(root / 'wordpress-plugin'), 'wordpress-plugin'),
    (str(root / 'codexifyr-migrator-importer.zip'), '.'),
    (str(root / 'tools'), 'tools'),
]

# pywebview backends/assets need hidden imports on Windows.
wv_datas, wv_bins, wv_hidden = collect_all('webview')

a = Analysis(
    [str(root / 'desktop_app.py')],
    pathex=[str(root)],
    binaries=wv_bins,
    datas=datas + wv_datas,
    hiddenimports=wv_hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Codexifyr WebLab',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    icon=str(root / 'frontend' / 'assets' / 'favicon.ico'),
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    name='desktop',
)
