import os, sys
# add path so we can import
sys.path.insert(0, r'E:\Gemini\m4b-converter\m4bmaker')
from PySide6.QtWidgets import QApplication
from m4bmaker.gui.icons import load_svg_icon
from m4bmaker.gui.svg_icons import PAUSE_SVG

app = QApplication([])
try:
    icon = load_svg_icon(PAUSE_SVG, "#f0f0f0")
    print("SUCCESS")
except Exception as e:
    print("FAILED", e)
