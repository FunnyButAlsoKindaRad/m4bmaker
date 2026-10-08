import sys
import traceback
sys.path.insert(0, r'E:\Gemini\m4b-converter\m4bmaker')
from PySide6.QtWidgets import QApplication
app = QApplication([])
from m4bmaker.gui.window import MainWindow
w = MainWindow()
try:
    w._on_tweaker()
except Exception as e:
    traceback.print_exc()
