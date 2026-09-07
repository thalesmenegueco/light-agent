# mini-agent.spec
# PyInstaller build configuration for mini-agent (a console CLI app).
#
# Build:
#     pip install -r requirements-build.txt
#     pyinstaller --clean --noconfirm mini-agent.spec
#
# Output: dist/mini-agent (Linux/macOS) or dist/mini-agent.exe (Windows).
#
# Skills are imported as regular Python modules (skills/__init__.py), so
# PyInstaller's analysis bundles them automatically -- there is no on-disk
# skills/ folder to locate at runtime, and no data files to collect. At
# runtime a frozen binary writes config and logs to the per-user config dir
# (see config.get_base_dir / logging_setup.get_log_file), never next to the
# executable.

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[],
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
    a.binaries,
    a.datas,
    [],
    name='mini-agent',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
