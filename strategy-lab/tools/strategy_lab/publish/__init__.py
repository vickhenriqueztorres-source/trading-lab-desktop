"""Strategy Lab publishing pipeline package (R-PUB-1..5)."""

from strategy_lab.publish.builder import build_manifest, load_candidates_file
from strategy_lab.publish.differ import compute_diff, format_diff_report, prompt_confirmation
from strategy_lab.publish.preflight import run_preflight
from strategy_lab.publish.signer import load_private_key, sign_manifest
from strategy_lab.publish.uploader import upload_manifest

__all__ = [
    "build_manifest",
    "compute_diff",
    "format_diff_report",
    "load_candidates_file",
    "load_private_key",
    "prompt_confirmation",
    "run_preflight",
    "sign_manifest",
    "upload_manifest",
]
