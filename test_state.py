import sys
from PySide6.QtWidgets import QApplication
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
from PySide6.QtCore import QUrl, QTimer

app = QApplication([])
p = QMediaPlayer()
a = QAudioOutput()
p.setAudioOutput(a)

def on_state(state):
    print("State:", state)
    if state == QMediaPlayer.PlaybackState.PlayingState:
        print("PLAYING STATE REACHED")

p.playbackStateChanged.connect(on_state)

# play a short valid mp3
# since we don't have one, we can't fully test audio playback state.
