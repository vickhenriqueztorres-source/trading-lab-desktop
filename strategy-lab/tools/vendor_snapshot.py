"""Generate reviewable upstream/patched hashes and diff (R-VEND-1); no network."""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMMIT = "acac6e08333466ae188c7dfa7fd2a03174e34ca2"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--upstream", type=Path, required=True)
    args = parser.parse_args()
    vendor = ROOT / "vendor" / "iqoptionapi"
    original: dict[str, str] = {}
    patched: dict[str, str] = {}
    diff: list[str] = []
    for source in sorted(args.upstream.rglob("*")):
        if not source.is_file():
            continue
        name = source.relative_to(args.upstream).as_posix()
        before = source.read_bytes()
        after = (vendor / name).read_bytes()
        original[name] = hashlib.sha256(before).hexdigest()
        patched[name] = hashlib.sha256(after).hexdigest()
        if before != after:
            diff.extend(
                difflib.unified_diff(
                    before.decode("utf-8").splitlines(keepends=True),
                    after.decode("utf-8").splitlines(keepends=True),
                    fromfile="a/" + name,
                    tofile="b/" + name,
                )
            )
    manifest = {
        "repository": "https://github.com/victalejo/iqoptionapi",
        "commit": COMMIT,
        "archive_sha256": "ad86660b8d8691e966b655cb601f2f853b17975a3364684aed1abdaf7f755a38",
        "upstream_sha256": original,
        "patched_sha256": patched,
    }
    (ROOT / "vendor" / "iqoptionapi.integrity.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    (ROOT / "vendor" / "iqoptionapi.security.patch").write_text(
        "".join(diff),
        encoding="utf-8",
        newline="\n",
    )


if __name__ == "__main__":
    main()
