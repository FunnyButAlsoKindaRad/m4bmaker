from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QMessageBox,
)
from PySide6.QtCore import Qt
import urllib.request
import json
from m4bmaker.gui import prefs

class ApiPrefsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("API Settings")
        self.setMinimumWidth(400)

        layout = QVBoxLayout(self)

        layout.addWidget(QLabel("Google Books API Key:"))
        self.key_input = QLineEdit()
        self.key_input.setText(str(prefs.get("google_books_api_key") or ""))
        layout.addWidget(self.key_input)

        test_btn = QPushButton("Test Key")
        test_btn.clicked.connect(self._test_key)
        layout.addWidget(test_btn, 0, Qt.AlignmentFlag.AlignRight)

        btn_box = QHBoxLayout()
        save_btn = QPushButton("Save")
        cancel_btn = QPushButton("Cancel")
        save_btn.clicked.connect(self._save)
        cancel_btn.clicked.connect(self.reject)
        btn_box.addWidget(save_btn)
        btn_box.addWidget(cancel_btn)
        layout.addLayout(btn_box)

    def _test_key(self):
        key = self.key_input.text().strip()
        if not key:
            QMessageBox.warning(self, "Error", "Please enter a key first.")
            return

        try:
            req = urllib.request.Request(
                f"https://www.googleapis.com/books/v1/volumes?q=test&key={key}",
                headers={"User-Agent": "m4bmaker/1.0"}
            )
            with urllib.request.urlopen(req, timeout=10) as res:
                if res.status == 200:
                    QMessageBox.information(self, "Success", "API Key works perfectly!")
        except urllib.error.HTTPError as e:
            QMessageBox.warning(self, "Failed", f"API Key failed: HTTP {e.code} - {e.reason}")
        except Exception as e:
            QMessageBox.warning(self, "Failed", f"API Key failed: {e}")

    def _save(self):
        prefs.set("google_books_api_key", self.key_input.text().strip())
        self.accept()
