import sys
sys.path.insert(0, r'E:\Gemini\m4b-converter\m4bmaker')
from PySide6.QtWidgets import QApplication
from m4bmaker.gui.icons import load_svg_icon
from m4bmaker.gui.svg_icons import PAUSE_SVG

app = QApplication([])
icon = load_svg_icon(PAUSE_SVG, "#f0f0f0", 28)
sizes = icon.availableSizes()
for s in sizes:
    print(s.width(), "x", s.height())
