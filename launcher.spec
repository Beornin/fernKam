# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec file for fernKam launcher.

Build steps:
    cd frontend && npm run build      # produces frontend/build — the backend
                                       # serves it from this on-disk path at
                                       # runtime, it is NOT bundled into the exe
    pip install -r requirements-launcher.txt
    pyinstaller launcher.spec

This freezes launcher.py + pywebview into fernKam.exe — just the orchestrator
(spawn backend, show it in a window, kill everything on close). The backend
itself is NOT frozen — it keeps running from backend/.venv via `fernkam
serve` and serves frontend/build straight from the repo on disk (this
machine only, see the Phase 1 packaging plan; PyInstaller-freezing
onnxruntime-gpu/insightface/CUDA is out of scope).

Relocatable exe: this spec bakes the repo root's absolute path (wherever
you're building from) into a small JSON file bundled inside the exe, so the
built fernKam.exe can be moved/copied/pinned anywhere (Desktop, Start Menu,
taskbar) and will still find backend/ at its real, fixed location. It still
only works on *this* machine/checkout — if you move the repo itself, rebuild.

Set DEBUG_CONSOLE=1 in the environment before building to get a console
window on the exe (useful for troubleshooting webview/backend startup
issues); otherwise it's windowed, matching digiKam.
"""
import json
import os

block_cipher = None
DEBUG_CONSOLE = os.environ.get("DEBUG_CONSOLE") == "1"

# Bake this build's repo root into the bundle (see launcher.py's frozen-path
# resolution) so the exe is relocatable without needing to sit next to backend/.
_repo_root = os.path.abspath(SPECPATH)
_build_config_path = os.path.join(_repo_root, "_launcher_build_config.json")
with open(_build_config_path, "w", encoding="utf-8") as _f:
    json.dump({"repo_root": _repo_root}, _f)

a = Analysis(
    ['launcher.py'],
    pathex=[],
    binaries=[],
    datas=[(_build_config_path, '.')],
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
    name='fernKam',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=DEBUG_CONSOLE,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=os.path.join(_repo_root, 'assets', 'fernkam.ico'),
)
