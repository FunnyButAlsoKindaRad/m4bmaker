"""GUI entry point — run with ``m4bmaker-gui`` or ``python -m m4bmaker.gui``."""

from __future__ import annotations

import os
import sys

# In PyInstaller frozen bundle, ensure SSL certificate bundle is located
try:
    import certifi

    ca_path = certifi.where()
    if os.path.isfile(ca_path):
        os.environ.setdefault("SSL_CERT_FILE", ca_path)
        os.environ.setdefault("REQUESTS_CA_BUNDLE", ca_path)
except Exception:
    pass

from PySide6.QtWidgets import QApplication

from m4bmaker.gui.styles import get_stylesheet
from m4bmaker.gui.window import MainWindow


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("m4bmaker")
    app.setStyleSheet(get_stylesheet())

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
