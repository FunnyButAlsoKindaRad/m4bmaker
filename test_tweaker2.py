import sys
import traceback
sys.path.insert(0, r'E:\Gemini\m4b-converter\m4bmaker')
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QTimer
app = QApplication([])
from m4bmaker.gui.window import MainWindow
w = MainWindow()
w.show()

def run_tweaker():
    try:
        w._on_tweaker()
    except Exception as e:
        traceback.print_exc()
        app.quit()
    QTimer.singleShot(2000, app.quit)

QTimer.singleShot(500, run_tweaker)
app.exec()
