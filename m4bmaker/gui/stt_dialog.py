"""Dialogs for STT Model Settings and Alignment Progress."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from m4bmaker.gui import prefs
from m4bmaker.stt.models import (
    DEFAULT_MODEL,
    SUPPORTED_MODELS,
    delete_model,
    download_model,
    get_installed_model_size_mb,
    get_models_dir,
    is_model_downloaded,
)

_log = logging.getLogger(__name__)


class ModelDownloadWorker(QThread):
    progress = Signal(float, str)
    finished_success = Signal(str)
    error = Signal(str)

    def __init__(self, model_name: str) -> None:
        super().__init__()
        self.model_name = model_name

    def run(self) -> None:
        try:
            download_model(
                self.model_name,
                progress_fn=lambda frac, msg: self.progress.emit(frac, msg),
            )
            self.finished_success.emit(self.model_name)
        except Exception as exc:
            _log.error("Failed downloading model %s: %s", self.model_name, exc)
            self.error.emit(str(exc))


class SttSettingsDialog(QDialog):
    """Configuration dialog for Smart Chapter Alignment and STT models."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Smart Chapter Alignment Settings")
        self.setMinimumWidth(500)
        self._download_worker: Optional[ModelDownloadWorker] = None
        self._setup_ui()
        self._update_model_status()

    def _setup_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(12)

        # ── Group 1: Speech-to-Text Model ────────────────────────────────────
        model_group = QGroupBox("Speech-to-Text Model")
        model_layout = QVBoxLayout(model_group)

        row_model = QHBoxLayout()
        row_model.addWidget(QLabel("Active Model:"))
        self._model_combo = QComboBox()
        for key, info in SUPPORTED_MODELS.items():
            self._model_combo.addItem(f"{key} - {info['description']}", key)

        cur_model = str(prefs.get("stt_model") or DEFAULT_MODEL)
        idx = self._model_combo.findData(cur_model)
        if idx >= 0:
            self._model_combo.setCurrentIndex(idx)
        self._model_combo.currentIndexChanged.connect(self._on_model_selection_changed)
        row_model.addWidget(self._model_combo, 1)
        model_layout.addLayout(row_model)

        # Status & Action row
        status_row = QHBoxLayout()
        self._status_lbl = QLabel("Checking status...")
        self._status_lbl.setStyleSheet("font-weight: bold;")
        status_row.addWidget(self._status_lbl, 1)

        self._dl_btn = QPushButton("Download Model")
        self._dl_btn.clicked.connect(self._on_download_clicked)
        status_row.addWidget(self._dl_btn)

        self._del_btn = QPushButton("Delete")
        self._del_btn.setStyleSheet("color: #d9534f;")
        self._del_btn.clicked.connect(self._on_delete_clicked)
        status_row.addWidget(self._del_btn)
        model_layout.addLayout(status_row)

        self._progress_bar = QProgressBar()
        self._progress_bar.setVisible(False)
        model_layout.addWidget(self._progress_bar)

        # Storage directory row
        dir_row = QHBoxLayout()
        dir_row.addWidget(QLabel("Models Folder:"))
        self._dir_edit = QLineEdit(str(get_models_dir()))
        self._dir_edit.setReadOnly(True)
        dir_row.addWidget(self._dir_edit, 1)
        browse_btn = QPushButton("Browse…")
        browse_btn.clicked.connect(self._on_browse_models_dir)
        dir_row.addWidget(browse_btn)
        model_layout.addLayout(dir_row)

        main_layout.addWidget(model_group)

        # ── Group 2: Alignment Parameters ────────────────────────────────────
        param_group = QGroupBox("Alignment Parameters")
        form = QFormLayout(param_group)

        # Search window radius
        window_box = QHBoxLayout()
        self._window_slider = QSlider(Qt.Orientation.Horizontal)
        self._window_slider.setRange(1, 10)
        saved_window = int(prefs.get("stt_window_min") or 3)
        self._window_slider.setValue(saved_window)

        self._window_lbl = QLabel(f"± {saved_window} min")
        self._window_slider.valueChanged.connect(
            lambda v: self._window_lbl.setText(f"± {v} min")
        )
        window_box.addWidget(self._window_slider, 1)
        window_box.addWidget(self._window_lbl)
        form.addRow("Search Window:", window_box)

        # Silence snapping
        self._snap_check = QCheckBox("Snap to nearest preceding silence pause")
        self._snap_check.setChecked(bool(prefs.get("stt_silence_snap") if prefs.get("stt_silence_snap") is not None else True))
        form.addRow("", self._snap_check)

        # Noise threshold
        self._noise_spin = QSpinBox()
        self._noise_spin.setRange(-60, -15)
        self._noise_spin.setSuffix(" dB")
        self._noise_spin.setValue(int(prefs.get("stt_noise_db") or -35))
        form.addRow("Silence Noise Floor:", self._noise_spin)

        # Min silence duration
        self._min_silence_spin = QSpinBox()
        self._min_silence_spin.setRange(100, 2000)
        self._min_silence_spin.setSingleStep(50)
        self._min_silence_spin.setSuffix(" ms")
        self._min_silence_spin.setValue(int(float(prefs.get("stt_min_silence_s") or 0.3) * 1000))
        form.addRow("Min Silence Length:", self._min_silence_spin)

        # ── Pre-Chapter Lead-in (Chapter 0) ───────────────────────────────────
        self._auto_pre_check = QCheckBox("Auto-create preceding chapter if Chapter 1 starts after audio")
        self._auto_pre_check.setChecked(
            bool(prefs.get("stt_auto_pre_chapter") if prefs.get("stt_auto_pre_chapter") is not None else True)
        )
        form.addRow("", self._auto_pre_check)

        self._pre_title_edit = QLineEdit(str(prefs.get("stt_pre_chapter_title") or "Chapter 0"))
        form.addRow("Lead-in Chapter Title:", self._pre_title_edit)

        self._pre_thresh_spin = QDoubleSpinBox()
        self._pre_thresh_spin.setRange(0.5, 60.0)
        self._pre_thresh_spin.setSingleStep(0.5)
        self._pre_thresh_spin.setSuffix(" s")
        self._pre_thresh_spin.setValue(float(prefs.get("stt_pre_chapter_threshold_s") or 3.0))
        form.addRow("Lead-in Min Threshold:", self._pre_thresh_spin)

        main_layout.addWidget(param_group)

        # ── Buttons ──────────────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        save_btn = QPushButton("Save Settings")
        save_btn.setDefault(True)
        save_btn.clicked.connect(self._on_save)
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(save_btn)
        main_layout.addLayout(btn_row)

    def _selected_model(self) -> str:
        return str(self._model_combo.currentData())

    def _update_model_status(self) -> None:
        model = self._selected_model()
        downloaded = is_model_downloaded(model)
        if downloaded:
            sz = get_installed_model_size_mb(model)
            self._status_lbl.setText(f"✓ Ready ({sz} MB on disk)")
            self._status_lbl.setStyleSheet("color: #4caf50; font-weight: bold;")
            self._dl_btn.setText("Redownload")
            self._del_btn.setEnabled(True)
        else:
            approx = SUPPORTED_MODELS.get(model, {}).get("approx_size_mb", 75)
            self._status_lbl.setText(f"Not downloaded (~{approx} MB)")
            self._status_lbl.setStyleSheet("color: #777777;")
            self._dl_btn.setText("Download")
            self._del_btn.setEnabled(False)

    def _on_model_selection_changed(self) -> None:
        self._update_model_status()

    def _on_browse_models_dir(self) -> None:
        cur = str(get_models_dir())
        chosen = QFileDialog.getExistingDirectory(self, "Select Models Storage Directory", cur)
        if chosen:
            prefs.set("stt_models_dir", chosen)
            self._dir_edit.setText(chosen)
            self._update_model_status()

    def _on_download_clicked(self) -> None:
        model = self._selected_model()
        self._dl_btn.setEnabled(False)
        self._del_btn.setEnabled(False)
        self._progress_bar.setVisible(True)
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(0)
        self._status_lbl.setText(f"Connecting to Hugging Face for {model}…")
        self._status_lbl.setStyleSheet("color: #2196f3;")

        self._download_worker = ModelDownloadWorker(model)
        self._download_worker.progress.connect(self._on_download_progress)
        self._download_worker.finished_success.connect(self._on_download_success)
        self._download_worker.error.connect(self._on_download_error)
        self._download_worker.start()

    def _on_download_progress(self, frac: float, msg: str) -> None:
        self._progress_bar.setValue(int(frac * 100))
        self._status_lbl.setText(msg)

    def _on_download_success(self, model: str) -> None:
        self._progress_bar.setVisible(False)
        self._dl_btn.setEnabled(True)
        self._update_model_status()
        from m4bmaker.gui.worker import ModelWarmupWorker

        self._warmup_worker = ModelWarmupWorker(model)
        self._warmup_worker.start()
        QMessageBox.information(self, "Model Ready", f"Successfully downloaded model '{model}'!")

    def _on_download_error(self, err_msg: str) -> None:
        self._progress_bar.setVisible(False)
        self._dl_btn.setEnabled(True)
        self._update_model_status()
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Critical)
        box.setWindowTitle("Download Failed")
        box.setText(f"Could not download model '{self._selected_model()}'.")
        box.setInformativeText(
            "Note: Faster-Whisper models are open-source and do NOT require an API key or login.\n\n"
            f"You can also download files manually into:\n{get_models_dir()}"
        )
        box.setDetailedText(err_msg)
        box.exec()

    def _on_delete_clicked(self) -> None:
        model = self._selected_model()
        confirm = QMessageBox.question(
            self,
            "Delete Model",
            f"Are you sure you want to remove the model files for '{model}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if confirm == QMessageBox.StandardButton.Yes:
            from m4bmaker.stt.aligner import clear_model_cache

            clear_model_cache()
            delete_model(model)
            self._update_model_status()

    def _on_save(self) -> None:
        prefs.set("stt_model", self._selected_model())
        win_min = self._window_slider.value()
        prefs.set("stt_window_min", win_min)
        prefs.set("stt_search_window_s", float(win_min * 60))
        snap = self._snap_check.isChecked()
        prefs.set("stt_silence_snap", snap)
        prefs.set("stt_snap_silence", snap)
        noise = self._noise_spin.value()
        prefs.set("stt_noise_db", noise)
        prefs.set("stt_silence_thresh_db", noise)
        min_sil = round(self._min_silence_spin.value() / 1000.0, 3)
        prefs.set("stt_min_silence_s", min_sil)
        prefs.set("stt_silence_min_dur_s", min_sil)
        prefs.set("stt_auto_pre_chapter", self._auto_pre_check.isChecked())
        prefs.set("stt_pre_chapter_title", self._pre_title_edit.text().strip() or "Chapter 0")
        prefs.set("stt_pre_chapter_threshold_s", round(float(self._pre_thresh_spin.value()), 2))
        self.accept()
