from __future__ import annotations

import argparse
import json
from pathlib import Path

from apps.launcher.deriv_login import DerivDemoLoginDialog
from packages.brokers.deriv.credentials import DerivCredentialVault


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--vault-dir", type=Path, required=True)
    arguments = parser.parse_args()

    from PySide6.QtWidgets import QApplication, QDialog

    application = QApplication.instance() or QApplication([])
    dialog = DerivDemoLoginDialog(DerivCredentialVault(arguments.vault_dir))
    accepted = dialog.exec() == QDialog.DialogCode.Accepted
    print(json.dumps({"status": "saved" if accepted else "cancelled"}), flush=True)
    application.quit()
    return 0 if accepted else 1


if __name__ == "__main__":
    raise SystemExit(main())
