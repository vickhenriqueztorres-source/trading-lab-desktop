# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

block_cipher = None
project_root = Path(SPECPATH).parent.resolve()

a = Analysis(
    [str(project_root / "apps" / "launcher" / "__main__.py")],
    pathex=[str(project_root)],
    binaries=[],
    datas=[
        (str(project_root / "apps"), "apps"),
        (str(project_root / "packages"), "packages"),
    ],
    hiddenimports=[
        "PySide6",
        "PySide6.QtCore",
        "PySide6.QtGui",
        "PySide6.QtWidgets",
        "cryptography",
        "cryptography.hazmat.primitives.asymmetric.ed25519",
        "websockets",
        "sqlite3",
        "json",
        "apps.auth_agent",
        "apps.auth_agent.runner",
        "apps.core",
        "apps.core.runner",
        "apps.deriv_worker",
        "apps.deriv_worker.server",
        "apps.iqoption_worker",
        "apps.simulated_worker",
        "apps.simulated_worker.runner",
        "apps.ui",
        "apps.ui.app",
        "apps.ui.runner",
        "apps.ui.components",
        "apps.ui.i18n",
        "apps.ui.theme",
        "packages.domain",
        "packages.protocol",
        "packages.security",
        "packages.persistence",
        "packages.observability",
        "packages.strategy_catalog",
        "packages.strategies",
        "packages.portfolio_allocation",
        "packages.signal_arbitration",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "scipy", "numpy", "pandas", "IPython", "notebook"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="TradingLab",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version=str(project_root / "build_scripts" / "version_info.txt"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="TradingLab",
)
