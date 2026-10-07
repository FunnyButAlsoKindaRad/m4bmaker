from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QSlider, QTableWidget
import sys

app = QApplication([])
w = QWidget()
layout = QVBoxLayout(w)

player = QWidget()
player_lay = QHBoxLayout(player)
btn = QPushButton("Play")
btn.setFixedSize(46, 46)
player_lay.addWidget(btn)

table = QTableWidget(5, 5)

layout.addWidget(player)
layout.addWidget(table, stretch=1)

slider = QSlider()
slider.setRange(20, 100)
slider.setValue(46)
def on_val(v):
    btn.setFixedSize(btn.width(), v)
slider.valueChanged.connect(on_val)

layout.addWidget(slider)

w.show()
sys.exit(app.exec())
