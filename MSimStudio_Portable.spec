# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['launch_app.py'],
    pathex=[],
    binaries=[('msim_compiler/bin/ms14_codec.exe', 'msim_compiler/bin'), ('msim_compiler/bin/msim_db_bridge.exe', 'msim_compiler/bin'), ('msim_compiler/bin/msim_eda_accel.dll', 'msim_compiler/bin')],
    datas=[('msim_compiler/templates', 'msim_compiler/templates'), ('msim_compiler/config', 'msim_compiler/config')],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='MSimStudio_Portable',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version='build/version_info.txt',
    icon=['msim_compiler/config/app_icon.ico'],
)
