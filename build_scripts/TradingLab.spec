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
        "apps.deriv_login_helper",
        "apps.deriv_login_helper.__main__",
        "apps.iqoption_login_helper",
        "apps.iqoption_login_helper.__main__",
        "apps.iqoption_connection_worker",
        "apps.iqoption_connection_worker.__main__",
        "apps.iqoption_connection_worker.server",
        "apps.iqoption_worker",
        "apps.iqoption_worker.process",
        "apps.simulated_worker",
        "apps.simulated_worker.server",
        "apps.ui",
        "apps.ui.app",
        "apps.ui.runner",
        "apps.ui.components",
        "apps.ui.i18n",
        "apps.ui.theme",
        "packages.brokers",
        "packages.brokers.port",
        "packages.brokers.iqoption_adapter",
        "packages.domain",
        "packages.protocol",
        "packages.security",
        "packages.persistence",
        "packages.observability",
        "packages.strategy_catalog",
        "packages.strategies",
        "packages.strategies.demo_test_strategy",
        "packages.strategies.iqoption_rsi",
        "packages.portfolio_allocation",
        "packages.signal_arbitration",
        "packages.sprt",
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

# The bundled workspace also exposes Poppler on PATH. Its ICU 78 libraries use
# version-suffixed exports, while Qt 6 on supported Windows 10/11 imports the
# unversioned system ICU API. PyInstaller's recursive dependency scan can pick
# the Poppler DLLs accidentally, causing QtCore to fail with a missing procedure.
FOREIGN_ICU_DLLS = {"icuuc.dll", "icudt78.dll"}
a.binaries = [
    binary
    for binary in a.binaries
    if Path(binary[0]).name.lower() not in FOREIGN_ICU_DLLS
]

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
