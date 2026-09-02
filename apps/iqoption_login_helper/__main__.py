from __future__ import annotations

import argparse
import json
from pathlib import Path

from apps.launcher.iqoption_login import IQOptionPracticeLoginDialog
from packages.brokers.iqoption.credentials import IQOptionCredentialVault


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--vault-dir", type=Path, required=True)
    arguments = parser.parse_args()

    from PySide6.QtWidgets import QApplication, QDialog

    application = QApplication.instance() or QApplication([])
    dialog = IQOptionPracticeLoginDialog(IQOptionCredentialVault(arguments.vault_dir))
    accepted = dialog.exec() == QDialog.DialogCode.Accepted
    response = {"status": "cancelled"}
    if accepted:
        response = {"status": "saved", "account_mode": dialog.selected_account_mode}
    print(json.dumps(response), flush=True)
    application.quit()
    return 0 if accepted else 1


if __name__ == "__main__":
    raise SystemExit(main())
