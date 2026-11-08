"""Dialogs for STT Model Settings and Alignment Progress."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QAction, QIcon, QPixmap
from PySide6.QtWidgets import (
    QApplication,
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
from m4bmaker.gui.icons import load_svg_icon
from m4bmaker.gui.svg_icons import CHECK_SVG, PASTE_SVG, QUESTION_MARK_SVG
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


class InfoIcon(QLabel):
    """Question mark info icon displaying an informative hoverable tooltip card."""

    def __init__(self, tooltip: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFixedSize(16, 16)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(tooltip)
        self._update_icon(hover=False)

    def _update_icon(self, hover: bool) -> None:
        color = "#2196f3" if hover else "#9a9a9a"
        icon = load_svg_icon(QUESTION_MARK_SVG, color, 14)
        self.setPixmap(icon.pixmap(14, 14))

    def enterEvent(self, event: object) -> None:
        self._update_icon(hover=True)
        super().enterEvent(event)

    def leaveEvent(self, event: object) -> None:
        self._update_icon(hover=False)
        super().leaveEvent(event)


def _make_field_label(text: str, tooltip: str) -> QWidget:
    """Label combined with a hoverable info icon for QFormLayout rows."""
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(4)
    lbl = QLabel(text)
    icon = InfoIcon(tooltip)
    lay.addWidget(lbl)
    lay.addWidget(icon)
    lay.addStretch()
    return w


def _make_checkbox_with_info(checkbox: QCheckBox, tooltip: str) -> QWidget:
    """Checkbox combined with a hoverable info icon."""
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(6)
    lay.addWidget(checkbox)
    lay.addWidget(InfoIcon(tooltip))
    lay.addStretch()
    return w


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
        model_layout.setSpacing(8)

        row_model = QHBoxLayout()
        row_model.addWidget(QLabel("Active Model:"))
        row_model.addWidget(
            InfoIcon(
                "'tiny.en' (~75 MB) is fastest and ideal for most audiobooks.\n"
                "'base.en' (~145 MB) provides higher accuracy for varied accents and rare titles."
            )
        )
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
        self._progress_bar.setObjectName("sttDownloadProgress")
        self._progress_bar.setMinimumHeight(20)
        self._progress_bar.setMaximumHeight(20)
        self._progress_bar.setTextVisible(False)
        self._progress_bar.setVisible(False)
        model_layout.addWidget(self._progress_bar)

        standard_layout.addWidget(model_group)

        # 2. Alignment Parameters Group
        align_group = QGroupBox("Alignment & Search Parameters")
        align_form = QFormLayout(align_group)
        align_form.setVerticalSpacing(8)

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
        align_form.addRow(
            _make_field_label(
                "Search Window:",
                "Search radius around the estimated chapter timestamp.\n"
                "Increase if chapter cues diverge widely from estimates.\n\n"
                "Recommended: ±3 min",
            ),
            window_box,
        )

        # Audiobook Language
        self._lang_combo = QComboBox()
        self._lang_combo.addItem("Auto-Detect", "auto")
        self._lang_combo.addItem("English", "en")
        self._lang_combo.addItem("Japanese (日本語)", "ja")
        self._lang_combo.addItem("German (Deutsch)", "de")
        self._lang_combo.addItem("French (Français)", "fr")
        self._lang_combo.addItem("Spanish (Español)", "es")
        self._lang_combo.addItem("Italian (Italiano)", "it")
        self._lang_combo.addItem("Chinese (中文)", "zh")
        saved_lang = str(prefs.get("stt_language") or "auto")
        lang_idx = self._lang_combo.findData(saved_lang)
        if lang_idx >= 0:
            self._lang_combo.setCurrentIndex(lang_idx)
        else:
            self._lang_combo.setCurrentIndex(0)

        align_form.addRow(
            _make_field_label(
                "Audiobook Language:",
                "Spoken language of the audiobook.\n"
                "'Auto-Detect' automatically applies optimal linguistic rules for the detected language.\n\n"
                "Recommended: Auto-Detect",
            ),
            self._lang_combo,
        )

        # Silence snapping
        self._snap_check = QCheckBox("Snap to nearest preceding silence pause")
        self._snap_check.setChecked(
            bool(prefs.get("stt_silence_snap") if prefs.get("stt_silence_snap") is not None else True)
        )
        align_form.addRow(
            "",
            _make_checkbox_with_info(
                self._snap_check,
                "Snaps chapter boundary to silence before speech starts to avoid cutting off the narrator's first words.\n\n"
                "Recommended: Enabled",
            ),
        )

        standard_layout.addWidget(align_group)

        # 3. Pre-Chapter Lead-in (Chapter 0) Group
        lead_group = QGroupBox("Opening Lead-in (Chapter 0)")
        lead_form = QFormLayout(lead_group)
        lead_form.setVerticalSpacing(8)

        self._auto_pre_check = QCheckBox("Auto-create preceding chapter if Chapter 1 starts after audio begins")
        self._auto_pre_check.setChecked(
            bool(prefs.get("stt_auto_pre_chapter") if prefs.get("stt_auto_pre_chapter") is not None else True)
        )
        lead_form.addRow(
            "",
            _make_checkbox_with_info(
                self._auto_pre_check,
                "If Chapter 1 begins after the specified threshold, automatically creates a lead-in chapter starting at 0:00:00.\n\n"
                "Recommended: Enabled",
            ),
        )

        self._pre_title_edit = QLineEdit(str(prefs.get("stt_pre_chapter_title") or "Chapter 0"))
        lead_form.addRow("Lead-in Chapter Title:", self._pre_title_edit)

        self._pre_thresh_spin = QDoubleSpinBox()
        self._pre_thresh_spin.setRange(0.5, 60.0)
        self._pre_thresh_spin.setSingleStep(0.5)
        self._pre_thresh_spin.setSuffix(" s")
        self._pre_thresh_spin.setValue(float(prefs.get("stt_pre_chapter_threshold_s") or 3.0))
        lead_form.addRow(
            _make_field_label(
                "Lead-in Min Threshold:",
                "Minimum offset required before creating a preceding chapter.\n"
                "If Chapter 1 starts earlier than this threshold, it snaps to 0:00:00 without creating Chapter 0.\n\n"
                "Recommended: 3.0 s (Title: 'Chapter 0')",
            ),
            self._pre_thresh_spin,
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
        storage_layout.setSpacing(8)

        dir_row = QHBoxLayout()
        dir_row.addWidget(QLabel("Folder:"))
        dir_row.addWidget(
            InfoIcon(
                "Place offline CTranslate2 model folders inside this directory, "
                "then click 'Scan Folder Now' to automatically detect and list them under 'base.en' in the Active Model dropdown."
            )
        )
        self._dir_edit = QLineEdit(str(get_models_dir()))
        self._dir_edit.setReadOnly(True)
        dir_row.addWidget(self._dir_edit, 1)
        browse_btn = QPushButton("Browse…")
        browse_btn.clicked.connect(self._on_browse_models_dir)
        dir_row.addWidget(browse_btn)
        storage_layout.addLayout(dir_row)

        scan_row = QHBoxLayout()
        self._scan_btn = QPushButton("Scan Folder Now")
        self._scan_btn.setToolTip("Scan models directory for local CTranslate2 models and add them to Active Model")
        self._scan_btn.clicked.connect(self._on_scan_folder_clicked)
        scan_row.addWidget(self._scan_btn)
        scan_row.addStretch()
        storage_layout.addLayout(scan_row)

        adv_layout.addWidget(storage_group)

        # 2. Custom Hugging Face Model Download
        hf_group = QGroupBox("Download Custom Hugging Face Model")
        hf_layout = QVBoxLayout(hf_group)
        hf_layout.setSpacing(8)

        hf_row = QHBoxLayout()
        hf_row.addWidget(QLabel("Repo ID:"))
        hf_row.addWidget(
            InfoIcon(
                "Download custom models directly from Hugging Face.\n"
                "Models must be in CTranslate2 format (containing 'model.bin' and 'config.json').\n"
                "The repository is pre-validated before downloading."
            )
        )
        self._hf_repo_edit = QLineEdit()
        self._hf_repo_edit.setPlaceholderText("e.g. Systran/faster-whisper-small or deepdml/faster-whisper-large-v3-turbo")

        # Trailing paste button inside the Repo ID field
        paste_pix = load_svg_icon(PASTE_SVG, "#888888", 16).pixmap(16, 16)
        self._paste_icon = QIcon()
        self._paste_icon.addPixmap(paste_pix, QIcon.Mode.Normal)

        check_pix = load_svg_icon(CHECK_SVG, "#4caf50", 16).pixmap(16, 16)
        self._check_icon = QIcon()
        self._check_icon.addPixmap(check_pix, QIcon.Mode.Normal)
        self._check_icon.addPixmap(check_pix, QIcon.Mode.Disabled)

        self._paste_action = self._hf_repo_edit.addAction(
            self._paste_icon, QLineEdit.ActionPosition.TrailingPosition
        )
        self._paste_action.setToolTip("Paste from clipboard")
        self._paste_action.triggered.connect(self._on_paste_repo_id)
        self._hf_repo_edit.textEdited.connect(lambda _t: self._set_repo_paste_state(pasted=False))

        hf_row.addWidget(self._hf_repo_edit, 1)
        self._hf_dl_btn = QPushButton("Download Model")
        self._hf_dl_btn.clicked.connect(self._on_download_custom_hf_clicked)
        hf_row.addWidget(self._hf_dl_btn)
        hf_layout.addLayout(hf_row)

        self._adv_status_lbl = QLabel("")
        self._adv_status_lbl.setStyleSheet("font-weight: bold;")
        self._adv_status_lbl.setVisible(False)
        hf_layout.addWidget(self._adv_status_lbl)

        self._adv_progress_bar = QProgressBar()
        self._adv_progress_bar.setObjectName("sttDownloadProgress")
        self._adv_progress_bar.setMinimumHeight(20)
        self._adv_progress_bar.setMaximumHeight(20)
        self._adv_progress_bar.setTextVisible(False)
        self._adv_progress_bar.setVisible(False)
        hf_layout.addWidget(self._adv_progress_bar)

        adv_layout.addWidget(hf_group)

        # 3. Custom Heading Keywords & Speech Biasing
        cue_group = QGroupBox("Chapter Heading Keywords & Cue Recognition")
        cue_form = QFormLayout(cue_group)
        cue_form.setVerticalSpacing(8)

        self._keywords_edit = QLineEdit(str(prefs.get("stt_heading_keywords") or "chronicle, book, act, section, canto"))
        cue_form.addRow(
            _make_field_label(
                "Heading Keywords:",
                "Comma-separated structural words. Ensures chapter start is anchored before the heading word "
                "(e.g. before 'Chronicle' in 'Chronicle One').\n\n"
                "Recommended: chronicle, book, act, section, canto",
            ),
            self._keywords_edit,
        )

        self._biasing_check = QCheckBox("Enable speech prompt biasing (Whisper initial_prompt)")
        self._biasing_check.setChecked(
            bool(prefs.get("stt_prompt_biasing") if prefs.get("stt_prompt_biasing") is not None else True)
        )
        cue_form.addRow(
            "",
            _make_checkbox_with_info(
                self._biasing_check,
                "Primes Whisper with expected chapter titles to greatly improve recognition in noisy intros or over background music.\n\n"
                "Recommended: Enabled",
            ),
        )

        adv_layout.addWidget(cue_group)

        # 4. Advanced Silence Detection Tuning
        silence_group = QGroupBox("Silence Detection Tuning")
        silence_form = QFormLayout(silence_group)
        silence_form.setVerticalSpacing(8)

        self._noise_spin = QSpinBox()
        self._noise_spin.setRange(-60, -15)
        self._noise_spin.setSuffix(" dB")
        self._noise_spin.setValue(int(prefs.get("stt_noise_db") or -35))
        silence_form.addRow(
            _make_field_label(
                "Silence Noise Floor:",
                "Audio quieter than this dB threshold is recognized as potential silence.\n\n"
                "Recommended: -35 dB",
            ),
            self._noise_spin,
        )

        self._min_silence_spin = QSpinBox()
        self._min_silence_spin.setRange(100, 2000)
        self._min_silence_spin.setSingleStep(50)
        self._min_silence_spin.setSuffix(" ms")
        self._min_silence_spin.setValue(int(float(prefs.get("stt_min_silence_s") or 0.3) * 1000))
        silence_form.addRow(
            _make_field_label(
                "Min Silence Length:",
                "Minimum silence pause duration required before splitting or snapping.\n\n"
                "Recommended: 300 ms",
            ),
            self._min_silence_spin,
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
        """Populate the Active Model dropdown with presets, followed by any models discovered in /models."""
        self._model_combo.blockSignals(True)
        self._model_combo.clear()

        # 1. Preset models: tiny.en and base.en always at the top
        for key, info in SUPPORTED_MODELS.items():
            self._model_combo.addItem(f"{key} - {info['description']}", key)

        # 2. Local models present on disk in /models listed under base.en
        try:
            scanned = scan_local_models()
        except Exception as exc:
            _log.warning("Could not scan local models: %s", exc)
            scanned = {}

        for name in sorted(scanned.keys()):
            if name in SUPPORTED_MODELS:
                continue
            desc = scanned[name].get("description", "Local Model")
            clean_name = name.replace("faster-whisper-", "")
            self._model_combo.addItem(f"{clean_name} - {desc}", name)

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
            self._dl_btn.setText("Redownload" if model in SUPPORTED_MODELS else "Ready")
            self._dl_btn.setEnabled(model in SUPPORTED_MODELS)
            self._del_btn.setEnabled(True)
        else:
            approx = SUPPORTED_MODELS.get(model, {}).get("approx_size_mb", 75)
            self._status_lbl.setText(f"Not downloaded (~{approx} MB)")
            self._status_lbl.setStyleSheet("color: #777777;")
            self._dl_btn.setText("Download")
            self._dl_btn.setEnabled(True)
            self._del_btn.setEnabled(False)

    def _on_model_selection_changed(self) -> None:
        self._update_model_status()

    # ── Clipboard Paste in Repo ID ───────────────────────────────────────────

    def _set_repo_paste_state(self, pasted: bool) -> None:
        """Switch trailing button between clickable paste icon and non-clickable checkmark."""
        if pasted:
            self._paste_action.setIcon(self._check_icon)
            self._paste_action.setToolTip("Pasted from clipboard")
            self._paste_action.setEnabled(False)
        else:
            self._paste_action.setIcon(self._paste_icon)
            self._paste_action.setToolTip("Paste from clipboard")
            self._paste_action.setEnabled(True)

    def _on_paste_repo_id(self) -> None:
        """Paste clipboard content into the Repo ID field and turn button into a checkmark."""
        text = QApplication.clipboard().text().strip()
        if text:
            self._hf_repo_edit.setText(text)
            self._set_repo_paste_state(pasted=True)

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
        self._refresh_model_combo()
        custom_found = [k for k in scanned if k not in SUPPORTED_MODELS]
        if not custom_found:
            QMessageBox.information(
                self,
                "Scan Models Directory",
                f"No additional CTranslate2 models found in:\n{get_models_dir()}\n\n"
                "To use an offline model, place its folder (containing 'model.bin' and 'config.json') "
                f"inside '{get_models_dir()}'.",
            )
            return

        names_str = "\n".join(
            f"• {name} ({scanned[name]['approx_size_mb']} MB)"
            for name in sorted(custom_found)
        )
        QMessageBox.information(
            self,
            "Scan Complete",
            f"Found {len(custom_found)} custom model(s) in models directory:\n\n{names_str}\n\n"
            "They are now listed under 'base.en' in the Active Model dropdown menu.",
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

        # Show and reset both progress bars in Standard and Advanced tabs
        for pbar in (self._progress_bar, self._adv_progress_bar):
            pbar.setVisible(True)
            pbar.setRange(0, 100)
            pbar.setValue(0)

        status_msg = f"Connecting to Hugging Face for {model_name}…"
        self._status_lbl.setText(status_msg)
        self._status_lbl.setStyleSheet("color: #2196f3; font-weight: bold;")

        self._adv_status_lbl.setVisible(True)
        self._adv_status_lbl.setText(status_msg)
        self._adv_status_lbl.setStyleSheet("color: #2196f3; font-weight: bold;")

        self._download_worker = ModelDownloadWorker(model_name)
        self._download_worker.progress.connect(self._on_download_progress)
        self._download_worker.finished_success.connect(self._on_download_success)
        self._download_worker.error.connect(self._on_download_error)
        self._download_worker.start()

    def _on_download_progress(self, frac: float, msg: str) -> None:
        percent = int(frac * 100)
        self._progress_bar.setValue(percent)
        self._status_lbl.setText(msg)
        self._adv_progress_bar.setValue(percent)
        self._adv_status_lbl.setText(msg)

    def _on_download_success(self, model: str) -> None:
        self._progress_bar.setVisible(False)
        self._adv_progress_bar.setVisible(False)
        self._adv_status_lbl.setVisible(False)
        self._dl_btn.setEnabled(True)
        self._hf_dl_btn.setEnabled(True)
        self._hf_repo_edit.clear()
        self._set_repo_paste_state(pasted=False)

        clean = model.split("/")[-1].replace("faster-whisper-", "")
        # Refresh Active Model combo and select the downloaded model
        target = clean if is_model_downloaded(clean) else model
        self._refresh_model_combo(select_key=target)
        self._update_model_status()

        from m4bmaker.gui.worker import ModelWarmupWorker

        self._warmup_worker = ModelWarmupWorker(self._selected_model())
        self._warmup_worker.start()
        QMessageBox.information(
            self,
            "Model Ready",
            f"Successfully downloaded model '{model}'!\n\nIt is now listed and selected in the Active Model menu.",
        )

    def _on_download_error(self, err_msg: str) -> None:
        self._progress_bar.setVisible(False)
        self._adv_progress_bar.setVisible(False)
        self._adv_status_lbl.setVisible(False)
        self._dl_btn.setEnabled(True)
        self._hf_dl_btn.setEnabled(True)
        self._update_model_status()
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Critical)
        box.setWindowTitle("Download Failed")
        box.setText("Could not download model.")
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
            next_target = DEFAULT_MODEL if model not in SUPPORTED_MODELS else model
            self._refresh_model_combo(select_key=next_target)
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
        self._lang_combo.setCurrentIndex(0)
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
        prefs.set("stt_language", self._lang_combo.currentData() or "auto")
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
