from __future__ import annotations

import argparse
import os
import signal
import sys
import threading
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import IO, Any


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="DualTrade local process-tree launcher")
    parser.add_argument(
        "--deriv-transport",
        choices=("fake-public", "fake-demo", "live-public", "live-demo", "live-real"),
        default="fake-public",
        help=("Deriv startup transport; authenticated modes require explicit account selection"),
    )
    parser.add_argument(
        "--profile-dir",
        type=Path,
        help="isolated local profile directory; installed builds default to LocalAppData",
    )
    parser.add_argument(
        "--headless-ui",
        action="store_true",
        help="run the UI projection client without a window (tests/soak only)",
    )
    parser.add_argument(
        "--workers",
        nargs="+",
        choices=("simulated", "deriv_read_only"),
        default=("simulated", "deriv_read_only"),
        help="bounded Phase 1 worker set; simulated is mandatory",
    )
    parser.add_argument(
        "--auto-shutdown-after",
        type=float,
        help="bounded test/soak duration in seconds",
    )
    parser.add_argument(
        "--verify-manifest",
        type=Path,
        help="path to release_manifest.json to verify package integrity at startup",
    )
    parser.add_argument(
        "--distribution-root",
        type=Path,
        help="distribution root directory for package integrity verification",
    )
    parser.add_argument(
        "--post-update-health-check",
        action="store_true",
        help="run post-update dry-run health check and exit with code 0 (success) or 1 (failure)",
    )
    return parser


def _duplicate_redirected_descriptor(descriptor: int) -> int:
    if os.name != "nt":
        return os.dup(descriptor)

    # A windowed PyInstaller child may expose the inherited pipe only as a
    # Win32 standard handle. A stale CRT descriptor may still be duplicable but
    # point at EOF, so never prefer os.dup() when the Python stream is absent.
    import ctypes
    import msvcrt

    standard_handle_ids = {0: -10, 1: -11, 2: -12}
    get_std_handle = ctypes.windll.kernel32.GetStdHandle
    get_std_handle.argtypes = [ctypes.c_uint32]
    get_std_handle.restype = ctypes.c_void_p
    handle = get_std_handle(standard_handle_ids[descriptor] & 0xFFFFFFFF)
    invalid_handle = ctypes.c_void_p(-1).value
    if handle in {None, 0, -1, invalid_handle}:
        raise OSError("redirected standard handle is unavailable")
    flags = os.O_RDONLY if descriptor == 0 else os.O_WRONLY
    return msvcrt.open_osfhandle(handle, flags)


def _restore_redirected_standard_streams(
    *,
    system: Any = sys,
    duplicate: Callable[[int], int] | None = None,
    open_fd: Callable[..., IO[str]] = os.fdopen,
) -> None:
    """Restore redirected pipes hidden by PyInstaller's windowed bootloader.

    The top-level launcher intentionally has no console. Internal subprocesses
    are different: their standard handles are explicit bootstrap pipes.
    PyInstaller sets the Python stream objects to ``None`` in windowed mode even
    when those handles exist, so frozen ``-m`` dispatch rebuilds only missing
    streams from the inherited descriptors.
    """

    descriptor_duplicator = duplicate or _duplicate_redirected_descriptor
    for attribute, descriptor, mode in (
        ("stdin", 0, "r"),
        ("stdout", 1, "w"),
        ("stderr", 2, "w"),
    ):
        if getattr(system, attribute, None) is not None:
            continue
        try:
            stream = open_fd(
                descriptor_duplicator(descriptor),
                mode,
                buffering=1,
                encoding="utf-8",
                newline="\n",
            )
        except OSError:
            continue
        setattr(system, attribute, stream)


def _dispatch_module(module_name: str, module_args: Sequence[str]) -> int:
    import runpy

    _restore_redirected_standard_streams()
    try:
        sys.argv = [sys.argv[0]] + list(module_args)
        runpy.run_module(module_name, run_name="__main__", alter_sys=True)
        return 0
    except SystemExit as e:
        return int(e.code) if isinstance(e.code, int) else (0 if e.code is None else 1)


def _is_frozen_executable() -> bool:
    return bool(getattr(sys, "frozen", False))


def _default_profile_dir() -> Path:
    if not _is_frozen_executable():
        return Path("data/profiles/default")
    local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
    if not local_app_data:
        raise RuntimeError("LAUNCHER_LOCAL_APP_DATA_UNAVAILABLE")
    return Path(local_app_data) / "TradingLab" / "profiles" / "default"


def _distribution_integrity_paths(
    manifest_path: Path | None,
    distribution_root: Path | None,
) -> tuple[Path | None, Path | None]:
    root = distribution_root.resolve() if distribution_root is not None else None
    manifest = manifest_path.resolve() if manifest_path is not None else None
    if root is None and manifest is not None:
        root = manifest.parent
    if root is None and _is_frozen_executable():
        root = Path(sys.executable).resolve().parent
    if manifest is None and root is not None:
        manifest = root / "release_manifest.json"
    return root, manifest


def main(argv: Sequence[str] | None = None) -> int:
    # Handle frozen executable sub-process dispatch (-m <module>)
    effective_argv = list(sys.argv[1:]) if argv is None else list(argv)
    if len(effective_argv) >= 2 and effective_argv[0] == "-m":
        return _dispatch_module(effective_argv[1], effective_argv[2:])

    parser = build_parser()
    arguments = parser.parse_args(argv)
    distribution_root, manifest = _distribution_integrity_paths(
        arguments.verify_manifest,
        arguments.distribution_root,
    )

    if arguments.post_update_health_check:
        from packages.security.integrity import ReleaseIntegrityVerifier

        if manifest is not None:
            if distribution_root is None or not manifest.is_file():
                return 1
            result = ReleaseIntegrityVerifier.verify_distribution(distribution_root, manifest)
            if not result.is_valid:
                return 1
        return 0

    profile_dir = arguments.profile_dir or _default_profile_dir()
    deriv_transport = arguments.deriv_transport
    # Keep this import out of frozen ``-m`` subprocess dispatch. Loading the
    # complete supervisor tree in every Auth/Core/UI/worker child consumed most
    # of the bounded bootstrap deadline on a cold Windows start.
    from apps.launcher.supervisor import ProcessTreeSupervisor

    workers = tuple(arguments.workers)
    if "simulated" not in workers or len(workers) != len(set(workers)):
        parser.error("simulated worker is mandatory and workers cannot repeat")
    duration = arguments.auto_shutdown_after
    if duration is not None and not 0.05 <= duration <= 86_400:
        parser.error("--auto-shutdown-after must be between 0.05 and 86400 seconds")
    supervisor = ProcessTreeSupervisor(
        profile_dir,
        workers=workers,
        ui_headless=arguments.headless_ui,
        deriv_transport=deriv_transport,
        manifest_path=manifest,
        distribution_root=distribution_root,
    )
    stop_requested = threading.Event()

    def request_stop(_signum: int, _frame: object) -> None:
        stop_requested.set()

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    if not supervisor.start_all():
        supervisor.stop_all()
        return 2
    started = time.monotonic()
    exit_code = 0
    try:
        while not stop_requested.wait(0.2):
            snapshot = supervisor.poll_health()
            if snapshot.overall_state.value == "FAILED":
                exit_code = 1
                break
            if duration is not None and time.monotonic() - started >= duration:
                break
    finally:
        if not supervisor.stop_all():
            exit_code = 1
    return exit_code
