from PySide6.QtWidgets import QApplication
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
from PySide6.QtCore import QUrl, QTimer
import sys

app = QApplication(sys.argv)
player = QMediaPlayer()
audio = QAudioOutput()
player.setAudioOutput(audio)
audio.setVolume(1.0)

def on_state(state):
    print("State:", state)

def on_error(err, errstr):
    print("Error:", err, errstr)

def on_status(status):
    print("Status:", status)

player.playbackStateChanged.connect(on_state)
player.errorOccurred.connect(on_error)
player.mediaStatusChanged.connect(on_status)

# load file
path = r'D:\BOOKS\Kate Lister\A Curious History of Sex.mp3'
player.setSource(QUrl.fromLocalFile(path))
player.play()

QTimer.singleShot(2000, app.quit)
app.exec()
