from PySide6.QtCore import Qt, QSize
from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QSlider,
    QSpinBox,
    QComboBox
)

class IconTweakerDialog(QDialog):
    def __init__(self, main_window, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Icon & Button Tweaker")
        self._main_window = main_window
        
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)
        
        layout = QVBoxLayout(self)
        
        self._target_combo = QComboBox()
        self._target_combo.addItems(["Play/Pause", "Rewind/Fast-Forward", "Previous/Next Chapter"])
        self._target_combo.currentIndexChanged.connect(self._on_target_changed)
        layout.addWidget(QLabel("Target Buttons:"))
        layout.addWidget(self._target_combo)
        
        self._btn_w_slider, self._btn_w_spin = self._make_row(layout, "Button Width:", 20, 100)
        self._btn_h_slider, self._btn_h_spin = self._make_row(layout, "Button Height:", 20, 100)
        self._icon_w_slider, self._icon_w_spin = self._make_row(layout, "Icon Width:", 10, 80)
        self._icon_h_slider, self._icon_h_spin = self._make_row(layout, "Icon Height:", 10, 80)
        self._offset_x_slider, self._offset_x_spin = self._make_row(layout, "Icon Offset X (px):", -20, 20)
        self._offset_y_slider, self._offset_y_spin = self._make_row(layout, "Icon Offset Y (px):", -20, 20)
        
        self._code_lbl = QLabel("")
        self._code_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self._code_lbl)
        
        self._on_target_changed(0)

    def _make_row(self, parent_layout, label_text, min_val, max_val):
        row = QHBoxLayout()
        row.addWidget(QLabel(label_text))
        
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(min_val, max_val)
        
        spin = QSpinBox()
        spin.setRange(min_val, max_val)
        
        slider.valueChanged.connect(spin.setValue)
        spin.valueChanged.connect(slider.setValue)
        
        slider.valueChanged.connect(self._apply_changes)
        
        row.addWidget(slider)
        row.addWidget(spin)
        parent_layout.addLayout(row)
        
        return slider, spin

    def _on_target_changed(self, idx):
        self._loading = True
        if idx == 0:
            btn = self._main_window._player._play_btn
        elif idx == 1:
            btn = self._main_window._player._rw_btn
        else:
            btn = self._main_window._ch_prev_btn
            
        self._btn_w_spin.setValue(btn.width())
        self._btn_h_spin.setValue(btn.height())
        self._icon_w_spin.setValue(btn.iconSize().width())
        self._icon_h_spin.setValue(btn.iconSize().height())
        self._offset_x_spin.setValue(0)
        self._offset_y_spin.setValue(0)
        self._loading = False
        self._apply_changes()

    def _apply_changes(self):
        if getattr(self, "_loading", False):
            return
            
        bw = self._btn_w_spin.value()
        bh = self._btn_h_spin.value()
        iw = self._icon_w_spin.value()
        ih = self._icon_h_spin.value()
        ox = self._offset_x_spin.value()
        oy = self._offset_y_spin.value()
        
        # We can use padding to move the icon. Positive X = move right (padding-left)
        # Positive Y = move down (padding-top). Since total size is fixed, we balance it.
        pt = max(0, oy)
        pb = max(0, -oy)
        pl = max(0, ox)
        pr = max(0, -ox)
        style = f"padding: {pt}px {pr}px {pb}px {pl}px;"
        
        idx = self._target_combo.currentIndex()
        if idx == 0:
            btns = [self._main_window._player._play_btn]
            name = "Play/Pause"
        elif idx == 1:
            btns = [self._main_window._player._rw_btn, self._main_window._player._ff_btn]
            name = "RW/FF"
        else:
            btns = [self._main_window._ch_prev_btn, self._main_window._ch_next_btn]
            name = "Prev/Next"
            
        for btn in btns:
            btn.setFixedSize(bw, bh)
            btn.setIconSize(QSize(iw, ih))
            btn.setStyleSheet(style)
            
        self._code_lbl.setText(f"Code for {name}:\nsetFixedSize({bw}, {bh})\nsetIconSize(QSize({iw}, {ih}))\nsetStyleSheet('{style}')")
