import sys
import re

with open('E:\\Gemini\\m4b-converter\\m4bmaker\\m4bmaker\\gui\\tweaker.py', 'r', encoding='utf-8') as f:
    text = f.read()

# Find the end of __init__ where self._on_target_changed(0) is called
init_end = text.find('self._on_target_changed(0)')

injection = '''
        self._copy_btn = QPushButton("Copy Settings to Clipboard")
        self._copy_btn.clicked.connect(self._copy_settings)
        layout.addWidget(self._copy_btn)
        
        self._on_target_changed(0)
'''

text = text[:init_end] + injection.strip() + '\n' + text[init_end + len('self._on_target_changed(0)'):]

# Add _copy_settings method
method = '''
    def _copy_settings(self):
        from PySide6.QtGui import QGuiApplication
        cb = QGuiApplication.clipboard()
        
        target = self._target_combo.currentText()
        bw = self._btn_w_spin.value()
        bh = self._btn_h_spin.value()
        iw = self._icon_w_spin.value()
        ih = self._icon_h_spin.value()
        ox = self._offset_x_spin.value()
        oy = self._offset_y_spin.value()
        
        text = f"Target: {target}\\nButton: {bw}x{bh}\\nIcon: {iw}x{ih}\\nOffset: X={ox}, Y={oy}"
        cb.setText(text)
        self._copy_btn.setText("Copied!")
        
        from PySide6.QtCore import QTimer
        QTimer.singleShot(2000, lambda: self._copy_btn.setText("Copy Settings to Clipboard"))
'''

text += method

with open('E:\\Gemini\\m4b-converter\\m4bmaker\\m4bmaker\\gui\\tweaker.py', 'w', encoding='utf-8') as f:
    f.write(text)
