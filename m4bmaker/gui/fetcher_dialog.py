from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QMessageBox,
    QComboBox,
    QDoubleSpinBox,
    QGroupBox,
)
from PySide6.QtCore import Qt
import urllib.request
from tempfile import NamedTemporaryFile
from pathlib import Path

from m4bmaker.gui import prefs
from m4bmaker.providers import fetch_audnexus_data, fetch_google_books_data, fetch_itunes_data


class MetadataFetcherDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Fetch Metadata")
        self.setMinimumWidth(500)

        self._meta_result = None
        self._chapters_result = None
        self._cover_result = None
        
        self._data_cache = {
            "audible": {"meta": None, "chapters": None, "cover": None},
            "google": {"meta": None, "chapters": None, "cover": None},
            "itunes": {"meta": None, "chapters": None, "cover": None},
        }

        layout = QVBoxLayout(self)

        # IDs Group
        id_group = QGroupBox("Identifiers")
        id_layout = QGridLayout(id_group)

        id_layout.addWidget(QLabel("Audible ASIN:"), 0, 0)
        self.asin_edit = QLineEdit()
        id_layout.addWidget(self.asin_edit, 0, 1)
        test_asin_btn = QPushButton("Test")
        test_asin_btn.clicked.connect(self._test_audible)
        id_layout.addWidget(test_asin_btn, 0, 2)

        id_layout.addWidget(QLabel("Google Books ID:"), 1, 0)
        self.google_edit = QLineEdit()
        id_layout.addWidget(self.google_edit, 1, 1)
        test_google_btn = QPushButton("Test")
        test_google_btn.clicked.connect(self._test_google)
        id_layout.addWidget(test_google_btn, 1, 2)

        id_layout.addWidget(QLabel("iTunes ID:"), 2, 0)
        self.itunes_edit = QLineEdit()
        id_layout.addWidget(self.itunes_edit, 2, 1)
        test_itunes_btn = QPushButton("Test")
        test_itunes_btn.clicked.connect(self._test_itunes)
        id_layout.addWidget(test_itunes_btn, 2, 2)

        layout.addWidget(id_group)

        # Mapping Group
        map_group = QGroupBox("Apply From Source")
        map_layout = QGridLayout(map_group)

        map_layout.addWidget(QLabel("General Metadata (Title/Author):"), 0, 0)
        self.meta_combo = QComboBox()
        self.meta_combo.addItems(["None", "Audible", "Google Books", "iTunes"])
        map_layout.addWidget(self.meta_combo, 0, 1)

        map_layout.addWidget(QLabel("Cover Art:"), 1, 0)
        self.cover_combo = QComboBox()
        self.cover_combo.addItems(["None", "Audible", "Google Books", "iTunes"])
        map_layout.addWidget(self.cover_combo, 1, 1)

        map_layout.addWidget(QLabel("Chapters:"), 2, 0)
        self.chap_combo = QComboBox()
        self.chap_combo.addItems(["None", "Audible"]) # Google/iTunes don't provide chapters
        map_layout.addWidget(self.chap_combo, 2, 1)
        


        layout.addWidget(map_group)

        # Bottom Buttons
        btn_box = QHBoxLayout()
        btn_box.addStretch()
        apply_btn = QPushButton("Fetch & Apply")
        cancel_btn = QPushButton("Cancel")
        apply_btn.clicked.connect(self._apply)
        cancel_btn.clicked.connect(self.reject)
        btn_box.addWidget(apply_btn)
        btn_box.addWidget(cancel_btn)
        layout.addLayout(btn_box)

    def _test_audible(self):
        asin = self.asin_edit.text().strip()
        if not asin:
            QMessageBox.warning(self, "Error", "Enter ASIN first.")
            return
        meta, chap, cov = fetch_audnexus_data(asin, 0.0)
        self._data_cache["audible"] = {"meta": meta, "chapters": chap, "cover": cov}
        self._show_test_result("Audible", meta, chap, cov)

    def _test_google(self):
        gid = self.google_edit.text().strip()
        api_key = prefs.get("google_books_api_key")
        if not api_key:
            QMessageBox.warning(self, "Error", "No Google Books API Key set in Tools -> API Settings.")
            return
        if not gid:
            QMessageBox.warning(self, "Error", "Enter Google Books ID first.")
            return
        meta, chap, cov = fetch_google_books_data(gid, str(api_key))
        self._data_cache["google"] = {"meta": meta, "chapters": chap, "cover": cov}
        self._show_test_result("Google Books", meta, chap, cov)

    def _test_itunes(self):
        iid = self.itunes_edit.text().strip()
        if not iid:
            QMessageBox.warning(self, "Error", "Enter iTunes ID first.")
            return
        meta, chap, cov = fetch_itunes_data(iid)
        self._data_cache["itunes"] = {"meta": meta, "chapters": chap, "cover": cov}
        self._show_test_result("iTunes", meta, chap, cov)

    def _show_test_result(self, source, meta, chap, cov):
        if not meta and not chap and not cov:
            QMessageBox.warning(self, "Result", f"No data found for {source}.")
            return
        lines = [f"Data found for {source}:"]
        if meta:
            lines.append(f"- Title: {meta.title}")
            lines.append(f"- Author: {meta.author}")
        if chap:
            lines.append(f"- Chapters: {len(chap)} found")
        if cov:
            lines.append("- Cover Art: Available")
        QMessageBox.information(self, "Result", "\n".join(lines))

    def _download_cover(self, url: str) -> Path | None:
        if not url:
            return None
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "m4bmaker/1.0"})
            with urllib.request.urlopen(req, timeout=10) as response:
                data = response.read()
            # Save to temp file
            temp = NamedTemporaryFile(delete=False, suffix=".jpg")
            temp.write(data)
            temp.close()
            return Path(temp.name)
        except Exception as e:
            print(f"Failed to download cover: {e}")
            return None

    def _apply(self):
        # Fetch missing data if they didn't test
        meta_src = self.meta_combo.currentText()
        cov_src = self.cover_combo.currentText()
        chap_src = self.chap_combo.currentText()

        src_map = {"Audible": "audible", "Google Books": "google", "iTunes": "itunes"}
        
        # Helper to fetch if needed
        def get_data(src_name):
            if src_name == "None": return None
            key = src_map[src_name]
            if self._data_cache[key]["meta"] is None and self._data_cache[key]["cover"] is None:
                if key == "audible": self._test_audible()
                elif key == "google": self._test_google()
                elif key == "itunes": self._test_itunes()
            return self._data_cache[key]

        meta_data = get_data(meta_src)
        if meta_data: self._meta_result = meta_data["meta"]

        chap_data = get_data(chap_src)
        if chap_data: self._chapters_result = chap_data["chapters"]

        cov_data = get_data(cov_src)
        if cov_data and cov_data["cover"]:
            self._cover_result = self._download_cover(cov_data["cover"])

        self.accept()

    def get_results(self):
        return self._meta_result, self._chapters_result, self._cover_result
