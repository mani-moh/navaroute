# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the NavaRoute Tier 2 owner-PC app (Windows .exe).

Builds a single-file exe from tools/tier2/tier2_pattn.py. The app is
stdlib only; src/ (the shared PattN client and tier-1 logic) is bundled
by adding the repo root to pathex. Built on a Windows runner by
.github/workflows/build-tier2.yml; also buildable locally with:
  pyinstaller tools/tier2/tier2_pattn.spec
"""
import os
from pathlib import Path

REPO_ROOT = Path(SPECPATH).parent.parent.parent

block_cipher = None

a = Analysis(
    [str(REPO_ROOT / "tools" / "tier2" / "tier2_pattn.py")],
    pathex=[str(REPO_ROOT)],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="NavaRouteTier2",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)
