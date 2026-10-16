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
    QTabWidget,
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
    scan_local_models,
    validate_hf_repo_ct2,
)

_log = logging.getLogger(__name__)


def _make_hint_label(text: str) -> QLabel:
    """Create a subtle hint / settings suggestion badge label."""
    lbl = QLabel(text)
    lbl.setWordWrap(True)
    lbl.setStyleSheet("color: #777777; font-size: 11px; font-style: italic; margin-bottom: 6px;")
    return lbl


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
        self.setMinimumWidth(540)
        self._download_worker: Optional[ModelDownloadWorker] = None
        self._setup_ui()
        self._refresh_model_combo()
        self._update_model_status()

    def _setup_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(12)

        self._tabs = QTabWidget(self)

        # ── Tab 1: Standard Settings (Plug & Play) ───────────────────────────
        standard_tab = QWidget()
        standard_layout = QVBoxLayout(standard_tab)
        standard_layout.setSpacing(10)

        # 1. Speech-to-Text Model Group
        model_group = QGroupBox("Speech-to-Text Model")
        model_layout = QVBoxLayout(model_group)

        row_model = QHBoxLayout()
        row_model.addWidget(QLabel("Active Model:"))
        self._model_combo = QComboBox()
        self._model_combo.currentIndexChanged.connect(self._on_model_selection_changed)
        row_model.addWidget(self._model_combo, 1)
        model_layout.addLayout(row_model)

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

        model_hint = _make_hint_label(
            "💡 Suggestion: 'tiny.en' (~75 MB) is fastest and ideal for most audiobooks. "
            "'base.en' (~145 MB) provides higher accuracy for varied accents and rare titles."
        )
        model_layout.addWidget(model_hint)
        standard_layout.addWidget(model_group)

        # 2. Alignment Parameters Group
        align_group = QGroupBox("Alignment & Search Parameters")
        align_form = QFormLayout(align_group)
        align_form.setVerticalSpacing(4)

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
        align_form.addRow("Search Window:", window_box)
        align_form.addRow(
            "",
            _make_hint_label(
                "💡 Suggestion: Recommended: ±3 min. Search radius around the estimated chapter timestamp. "
                "Increase if chapter cues diverge widely from estimates."
            ),
        )

        # Silence snapping
        self._snap_check = QCheckBox("Snap to nearest preceding silence pause")
        self._snap_check.setChecked(
            bool(prefs.get("stt_silence_snap") if prefs.get("stt_silence_snap") is not None else True)
        )
        align_form.addRow("", self._snap_check)
        align_form.addRow(
            "",
            _make_hint_label(
                "💡 Suggestion: Recommended: Enabled. Snaps chapter boundary to silence before speech starts "
                "to avoid cutting off the narrator's first words."
            ),
        )

        standard_layout.addWidget(align_group)

        # 3. Pre-Chapter Lead-in (Chapter 0) Group
        lead_group = QGroupBox("Opening Lead-in (Chapter 0)")
        lead_form = QFormLayout(lead_group)
        lead_form.setVerticalSpacing(4)

        self._auto_pre_check = QCheckBox("Auto-create preceding chapter if Chapter 1 starts after audio begins")
        self._auto_pre_check.setChecked(
            bool(prefs.get("stt_auto_pre_chapter") if prefs.get("stt_auto_pre_chapter") is not None else True)
        )
        lead_form.addRow("", self._auto_pre_check)

        self._pre_title_edit = QLineEdit(str(prefs.get("stt_pre_chapter_title") or "Chapter 0"))
        lead_form.addRow("Lead-in Chapter Title:", self._pre_title_edit)

        self._pre_thresh_spin = QDoubleSpinBox()
        self._pre_thresh_spin.setRange(0.5, 60.0)
        self._pre_thresh_spin.setSingleStep(0.5)
        self._pre_thresh_spin.setSuffix(" s")
        self._pre_thresh_spin.setValue(float(prefs.get("stt_pre_chapter_threshold_s") or 3.0))
        lead_form.addRow("Lead-in Min Threshold:", self._pre_thresh_spin)
        lead_form.addRow(
            "",
            _make_hint_label(
                "💡 Suggestion: Recommended: 3.0s threshold. If Chapter 1 begins after 3.0s of opening music "
                "or intro preamble, automatically creates Chapter 0 anchored to 0:00:00."
            ),
        )

        standard_layout.addWidget(lead_group)
        standard_layout.addStretch()
        self._tabs.addTab(standard_tab, "Standard Settings")

        # ── Tab 2: Advanced & Custom Models (Tinkerers) ──────────────────────
        adv_tab = QWidget()
        adv_layout = QVBoxLayout(adv_tab)
        adv_layout.setSpacing(10)

        # 1. Models Storage & Local Discovery
        storage_group = QGroupBox("Models Directory & Local Folder Scan")
        storage_layout = QVBoxLayout(storage_group)

        dir_row = QHBoxLayout()
        dir_row.addWidget(QLabel("Folder:"))
        self._dir_edit = QLineEdit(str(get_models_dir()))
        self._dir_edit.setReadOnly(True)
        dir_row.addWidget(self._dir_edit, 1)
        browse_btn = QPushButton("Browse…")
        browse_btn.clicked.connect(self._on_browse_models_dir)
        dir_row.addWidget(browse_btn)
        storage_layout.addLayout(dir_row)

        scan_row = QHBoxLayout()
        self._scan_btn = QPushButton("Scan Folder Now")
        self._scan_btn.setToolTip("Scan models directory for local CTranslate2 models")
        self._scan_btn.clicked.connect(self._on_scan_folder_clicked)
        scan_row.addWidget(self._scan_btn)
        scan_row.addStretch()
        storage_layout.addLayout(scan_row)

        storage_hint = _make_hint_label(
            "💡 Suggestion: Place offline CTranslate2 model folders inside this directory, "
            "then click 'Scan Folder Now' to automatically detect and add them to Active Model."
        )
        storage_layout.addWidget(storage_hint)
        adv_layout.addWidget(storage_group)

        # 2. Custom Hugging Face Model Download
        hf_group = QGroupBox("Download Custom Hugging Face Model")
        hf_layout = QVBoxLayout(hf_group)

        hf_row = QHBoxLayout()
        hf_row.addWidget(QLabel("Repo ID:"))
        self._hf_repo_edit = QLineEdit()
        self._hf_repo_edit.setPlaceholderText("e.g. Systran/faster-whisper-small or deepdml/faster-whisper-large-v3-turbo")
        hf_row.addWidget(self._hf_repo_edit, 1)
        self._hf_dl_btn = QPushButton("Download Model")
        self._hf_dl_btn.clicked.connect(self._on_download_custom_hf_clicked)
        hf_row.addWidget(self._hf_dl_btn)
        hf_layout.addLayout(hf_row)

        hf_hint = _make_hint_label(
            "💡 Suggestion: Download custom models directly from Hugging Face. Models must be in CTranslate2 "
            "format (containing 'model.bin' and 'config.json'). The repository is pre-validated before downloading."
        )
        hf_layout.addWidget(hf_hint)
        adv_layout.addWidget(hf_group)

        # 3. Custom Heading Keywords & Speech Biasing
        cue_group = QGroupBox("Chapter Heading Keywords & Cue Recognition")
        cue_layout = QVBoxLayout(cue_group)
        cue_form = QFormLayout()
        cue_form.setVerticalSpacing(4)

        self._keywords_edit = QLineEdit(str(prefs.get("stt_heading_keywords") or "chronicle, book, act, section, canto"))
        cue_form.addRow("Heading Keywords:", self._keywords_edit)
        cue_form.addRow(
            "",
            _make_hint_label(
                "💡 Suggestion: Recommended: chronicle, book, act, section, canto. Comma-separated structural words. "
                "Ensures chapter start is anchored before the heading word (e.g. 'Chronicle One')."
            ),
        )

        self._biasing_check = QCheckBox("Enable speech prompt biasing (Whisper initial_prompt)")
        self._biasing_check.setChecked(
            bool(prefs.get("stt_prompt_biasing") if prefs.get("stt_prompt_biasing") is not None else True)
        )
        cue_form.addRow("", self._biasing_check)
        cue_form.addRow(
            "",
            _make_hint_label(
                "💡 Suggestion: Recommended: Enabled. Primes Whisper with expected chapter titles to "
                "greatly improve recognition in noisy intros or over background music."
            ),
        )

        cue_layout.addLayout(cue_form)
        adv_layout.addWidget(cue_group)

        # 4. Advanced Silence Detection Tuning
        silence_group = QGroupBox("Silence Detection Tuning")
        silence_form = QFormLayout(silence_group)
        silence_form.setVerticalSpacing(4)

        self._noise_spin = QSpinBox()
        self._noise_spin.setRange(-60, -15)
        self._noise_spin.setSuffix(" dB")
        self._noise_spin.setValue(int(prefs.get("stt_noise_db") or -35))
        silence_form.addRow("Silence Noise Floor:", self._noise_spin)

        self._min_silence_spin = QSpinBox()
        self._min_silence_spin.setRange(100, 2000)
        self._min_silence_spin.setSingleStep(50)
        self._min_silence_spin.setSuffix(" ms")
        self._min_silence_spin.setValue(int(float(prefs.get("stt_min_silence_s") or 0.3) * 1000))
        silence_form.addRow("Min Silence Length:", self._min_silence_spin)
        silence_form.addRow(
            "",
            _make_hint_label(
                "💡 Suggestion: Recommended: -35 dB / 300 ms. Audio quieter than this threshold for at least "
                "this length is considered an inter-sentence or chapter pause."
            ),
        )

        adv_layout.addWidget(silence_group)
        adv_layout.addStretch()
        self._tabs.addTab(adv_tab, "Advanced & Custom Models")

        main_layout.addWidget(self._tabs)

        # ── Bottom Action Row ────────────────────────────────────────────────
        bottom_row = QHBoxLayout()

        reset_btn = QPushButton("Reset to Defaults")
        reset_btn.setToolTip("Restore all Smart Alignment settings to factory defaults")
        reset_btn.clicked.connect(self._on_reset_defaults_clicked)
        bottom_row.addWidget(reset_btn)

        bottom_row.addStretch()

        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        bottom_row.addWidget(cancel_btn)

        save_btn = QPushButton("Save Settings")
        save_btn.setDefault(True)
        save_btn.clicked.connect(self._on_save)
        bottom_row.addWidget(save_btn)

        main_layout.addLayout(bottom_row)

    # ── Model selection and combo management ─────────────────────────────────

    def _refresh_model_combo(self, select_key: Optional[str] = None) -> None:
        """Populate the Active Model dropdown with presets, custom models, and scanned models."""
        self._model_combo.blockSignals(True)
        self._model_combo.clear()

        # 1. Preset models
        for key, info in SUPPORTED_MODELS.items():
            self._model_combo.addItem(f"{key} - {info['description']}", key)

        # 2. Custom models saved in preferences
        custom_models = list(prefs.get("stt_custom_models") or [])
        added_keys = set(SUPPORTED_MODELS.keys())

        # 3. Discovered local models
        try:
            scanned = scan_local_models()
        except Exception:
            scanned = {}

        all_custom = set(custom_models) | set(scanned.keys())
        for c_key in sorted(all_custom):
            clean_name = c_key.split("/")[-1].replace("faster-whisper-", "")
            if clean_name in added_keys or c_key in added_keys:
                continue
            desc = scanned[c_key]["description"] if c_key in scanned else "Custom Model"
            self._model_combo.addItem(f"{clean_name} - {desc}", c_key)
            added_keys.add(c_key)
            added_keys.add(clean_name)

        target = select_key or str(prefs.get("stt_model") or DEFAULT_MODEL)
        idx = self._model_combo.findData(target)
        if idx < 0:
            clean_target = target.split("/")[-1].replace("faster-whisper-", "")
            idx = self._model_combo.findData(clean_target)
        if idx >= 0:
            self._model_combo.setCurrentIndex(idx)
        else:
            self._model_combo.setCurrentIndex(0)

        self._model_combo.blockSignals(False)
        self._update_model_status()

    def _selected_model(self) -> str:
        data = self._model_combo.currentData()
        return str(data) if data is not None else DEFAULT_MODEL

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

    # ── Folder Actions ───────────────────────────────────────────────────────

    def _on_browse_models_dir(self) -> None:
        cur = str(get_models_dir())
        chosen = QFileDialog.getExistingDirectory(self, "Select Models Storage Directory", cur)
        if chosen:
            prefs.set("stt_models_dir", chosen)
            self._dir_edit.setText(chosen)
            self._refresh_model_combo()

    def _on_scan_folder_clicked(self) -> None:
        scanned = scan_local_models()
        count = len(scanned)
        if count == 0:
            QMessageBox.information(
                self,
                "Scan Models Directory",
                f"No CTranslate2 models found in:\n{get_models_dir()}\n\n"
                "To use a local model, ensure the model folder contains 'model.bin' and 'config.json'.",
            )
            return

        custom_models = list(prefs.get("stt_custom_models") or [])
        added_new = 0
        for name in scanned:
            if name not in custom_models and name not in SUPPORTED_MODELS:
                custom_models.append(name)
                added_new += 1
        if added_new > 0:
            prefs.set("stt_custom_models", custom_models)

        self._refresh_model_combo()
        names_str = "\n".join(f"• {name} ({scanned[name]['approx_size_mb']} MB)" for name in scanned)
        QMessageBox.information(
            self,
            "Scan Complete",
            f"Found {count} valid CTranslate2 model(s):\n\n{names_str}\n\n"
            "They are now available in the Active Model list.",
        )

    # ── Download and Delete Actions ──────────────────────────────────────────

    def _on_download_clicked(self) -> None:
        model = self._selected_model()
        self._start_download(model)

    def _on_download_custom_hf_clicked(self) -> None:
        repo_id = self._hf_repo_edit.text().strip()
        if not repo_id:
            QMessageBox.warning(
                self,
                "Missing Repo ID",
                "Please enter a Hugging Face repository ID (e.g. 'Systran/faster-whisper-small').",
            )
            return

        # Pre-validate CTranslate2 format via Hugging Face API
        is_valid, val_msg = validate_hf_repo_ct2(repo_id)
        if not is_valid:
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Icon.Warning)
            box.setWindowTitle("Incompatible Model Format")
            box.setText(f"The repository '{repo_id}' is not in CTranslate2 format.")
            box.setInformativeText(val_msg)
            box.exec()
            return

        self._start_download(repo_id)

    def _start_download(self, model_name: str) -> None:
        self._dl_btn.setEnabled(False)
        self._del_btn.setEnabled(False)
        self._hf_dl_btn.setEnabled(False)
        self._progress_bar.setVisible(True)
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(0)
        self._status_lbl.setText(f"Connecting to Hugging Face for {model_name}…")
        self._status_lbl.setStyleSheet("color: #2196f3;")

        self._download_worker = ModelDownloadWorker(model_name)
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
        self._hf_dl_btn.setEnabled(True)
        self._hf_repo_edit.clear()

        # Add to custom models list if not a standard preset
        if model not in SUPPORTED_MODELS:
            clean = model.split("/")[-1].replace("faster-whisper-", "")
            custom_models = list(prefs.get("stt_custom_models") or [])
            if model not in custom_models and clean not in custom_models:
                custom_models.append(clean)
                prefs.set("stt_custom_models", custom_models)

        self._refresh_model_combo(select_key=model)
        self._update_model_status()

        from m4bmaker.gui.worker import ModelWarmupWorker

        self._warmup_worker = ModelWarmupWorker(model)
        self._warmup_worker.start()
        QMessageBox.information(self, "Model Ready", f"Successfully downloaded model '{model}'!")

    def _on_download_error(self, err_msg: str) -> None:
        self._progress_bar.setVisible(False)
        self._dl_btn.setEnabled(True)
        self._hf_dl_btn.setEnabled(True)
        self._update_model_status()
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Critical)
        box.setWindowTitle("Download Failed")
        box.setText(f"Could not download model '{self._selected_model()}'.")
        box.setInformativeText(
            "Note: Faster-Whisper models require CTranslate2 format.\n\n"
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
            if model not in SUPPORTED_MODELS:
                custom_models = list(prefs.get("stt_custom_models") or [])
                clean = model.split("/")[-1].replace("faster-whisper-", "")
                if model in custom_models:
                    custom_models.remove(model)
                if clean in custom_models:
                    custom_models.remove(clean)
                prefs.set("stt_custom_models", custom_models)
            self._refresh_model_combo()
            self._update_model_status()

    # ── Reset to Defaults & Save ─────────────────────────────────────────────

    def _on_reset_defaults_clicked(self) -> None:
        confirm = QMessageBox.question(
            self,
            "Reset Settings",
            "Reset all Smart Alignment settings to factory defaults?\n\n"
            "(Downloaded models will not be deleted).",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return

        prefs.reset_stt_defaults()

        # Reload values into controls
        self._window_slider.setValue(int(prefs.get("stt_window_min") or 3))
        self._snap_check.setChecked(bool(prefs.get("stt_silence_snap") if prefs.get("stt_silence_snap") is not None else True))
        self._auto_pre_check.setChecked(bool(prefs.get("stt_auto_pre_chapter") if prefs.get("stt_auto_pre_chapter") is not None else True))
        self._pre_title_edit.setText(str(prefs.get("stt_pre_chapter_title") or "Chapter 0"))
        self._pre_thresh_spin.setValue(float(prefs.get("stt_pre_chapter_threshold_s") or 3.0))
        self._keywords_edit.setText(str(prefs.get("stt_heading_keywords") or "chronicle, book, act, section, canto"))
        self._biasing_check.setChecked(bool(prefs.get("stt_prompt_biasing") if prefs.get("stt_prompt_biasing") is not None else True))
        self._noise_spin.setValue(int(prefs.get("stt_noise_db") or -35))
        self._min_silence_spin.setValue(int(float(prefs.get("stt_min_silence_s") or 0.3) * 1000))

        self._refresh_model_combo(select_key=DEFAULT_MODEL)
        self._update_model_status()
        QMessageBox.information(self, "Reset Complete", "Smart Alignment settings have been restored to factory defaults.")

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
        prefs.set("stt_heading_keywords", self._keywords_edit.text().strip())
        prefs.set("stt_prompt_biasing", self._biasing_check.isChecked())
        self.accept()
