"""Compact audio playback widget using PySide6 QMediaPlayer.

Provides a play/pause button, stop button, seek slider, and a time
readout.  Used in the Chapters tab to preview source audio so the user
can identify and edit chapter titles.

Call :meth:`AudioPlayerWidget.load` to open a file and start playback.
Call :meth:`AudioPlayerWidget.seek_chapter` to jump to a timestamp
(milliseconds) without reloading the file.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QUrl, Signal
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

_ICON_PLAY = "\u25b6"
_ICON_PAUSE = "\u23f8"
_ICON_STOP = "\u23f9"


def _fmt_ms(ms: int) -> str:
    """Format milliseconds as M:SS.mmm or H:MM:SS.mmm."""
    total_s = max(0, ms) // 1000
    millis = max(0, ms) % 1000
    h, rem = divmod(total_s, 3600)
    m, sec = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{sec:02d}.{millis:03d}"
    return f"{m}:{sec:02d}.{millis:03d}"


class AudioPlayerWidget(QWidget):
    position_changed = Signal(int)
    """Play/Pause + seek slider + time readout for audio preview.

    Selecting a row in the :class:`ChapterTable` should call
    :meth:`load` (new file) or :meth:`seek_chapter` (same file, e.g.
    when editing an existing .m4b).
    """

    # delay (ms) before seeking after a new source is set, to allow
    # the media backend to buffer enough to accept a seek command.
    _SEEK_DELAY_MS = 250

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self._player = QMediaPlayer()
        self._audio_out = QAudioOutput()
        self._player.setAudioOutput(self._audio_out)
        self._audio_out.setVolume(1.0)

        self._seeking = False  # guard re-entrant slider/position updates

        # M7: a single reusable deferred-seek timer, cancelled and restarted
        # on every load/seek so a quick second click cannot fire a stale
        # target position from an earlier request (or the previous file).
        self._seek_timer = QTimer(self)
        self._seek_timer.setSingleShot(True)
        self._seek_timer.timeout.connect(self._apply_pending_seek)
        self._pending_seek_ms: int | None = None

        # ── buttons ──────────────────────────────────────────────────────────
        self._rw_btn = QPushButton()
        self._rw_btn.setObjectName("playerRwBtn")
        self._rw_btn.setStyleSheet("padding: 0px;")
        self._rw_btn.setFixedSize(40, 40)
        self._rw_btn.setToolTip("Rewind 15s")
        self._rw_btn.clicked.connect(lambda: self.seek_relative(-15000))

        self._play_btn = QPushButton(_ICON_PLAY)
        self._play_btn.setObjectName("playerPlayBtn")
        self._play_btn.setStyleSheet("padding: 0px;")
        self._play_btn.setFixedSize(46, 46)
        self._play_btn.setToolTip("Play / Pause")
        self._play_btn.clicked.connect(self._toggle_play)

        self._ff_btn = QPushButton()
        self._ff_btn.setObjectName("playerFfBtn")
        self._ff_btn.setStyleSheet("padding: 0px;")
        self._ff_btn.setFixedSize(40, 40)
        self._ff_btn.setToolTip("Fast Forward 15s")
        self._ff_btn.clicked.connect(lambda: self.seek_relative(15000))

        # ── timeline slider ───────────────────────────────────────────────────
        self._slider = QSlider(Qt.Orientation.Horizontal)
        self._slider.setMinimum(0)
        self._slider.setMaximum(0)
        self._slider.sliderPressed.connect(self._on_slider_pressed)
        self._slider.sliderReleased.connect(self._on_slider_released)
        self._slider.sliderMoved.connect(self._player.setPosition)

        # ── time label ────────────────────────────────────────────────────────
        self._time_lbl = QLabel("—:—— / —:——")
        self._time_lbl.setStyleSheet(
            "font-size: 11px; color: #7a7a7a; background: transparent;"
        )
        self._time_lbl.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )

        # ── layout ────────────────────────────────────────────────────────────
        # Top Row (Time Readout right-aligned)
        top_row = QHBoxLayout()
        top_row.setContentsMargins(0, 0, 0, 0)
        top_row.addStretch()
        top_row.addWidget(self._time_lbl)

        # Middle Row (Progress bar)
        mid_row = QHBoxLayout()
        mid_row.setContentsMargins(0, 0, 0, 0)
        mid_row.addWidget(self._slider, stretch=1)

        # Bottom Row (Transport buttons)
        self._transport_layout = QHBoxLayout()
        self._transport_layout.setContentsMargins(0, 0, 0, 0)
        self._transport_layout.setSpacing(12)
        self._transport_layout.addStretch()
        self._transport_layout.addWidget(self._rw_btn)
        self._transport_layout.addWidget(self._play_btn)
        self._transport_layout.addWidget(self._ff_btn)
        self._transport_layout.addStretch()

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 4, 0, 0)
        outer.setSpacing(4)
        outer.addLayout(top_row)
        outer.addLayout(mid_row)
        outer.addLayout(self._transport_layout)

        # ── player signals ────────────────────────────────────────────────────
        self._player.positionChanged.connect(self._on_position_changed)
        self._player.durationChanged.connect(self._on_duration_changed)
        self._player.playbackStateChanged.connect(self._on_state_changed)
        self._player.mediaStatusChanged.connect(lambda status: self._update_buttons())
        self._player.errorOccurred.connect(self._on_error)
        self._player.mediaStatusChanged.connect(self._on_status)

    def _on_error(self, error, error_string):
        self._time_lbl.setText(f"Err: {error_string}")
        import logging
        logging.getLogger(__name__).error(f"Player Error {error}: {error_string}")

    def _on_status(self, status):
        import logging
        logging.getLogger(__name__).info(f"Player Status: {status}")

        self._update_buttons(QMediaPlayer.PlaybackState.StoppedState)

    # ── public interface ──────────────────────────────────────────────────────

    def load(self, path: Path, start_ms: int = 0) -> None:
        """Load *path* and start playback, optionally seeking to *start_ms*.

        If *path* is already the current source, calls :meth:`seek_chapter`
        instead (avoids unnecessary reloading when navigating chapters inside
        a single .m4b file).
        """
        new_url = QUrl.fromLocalFile(str(path.resolve()))
        if self._player.source() == new_url:
            self.seek_chapter(start_ms)
            return

        self._player.setSource(new_url)
        self._player.play()
        self._defer_seek(start_ms)

    def load_paused(self, path: Path, start_ms: int = 0) -> None:
        """Load *path* and seek to *start_ms* without starting playback.

        Use this when selecting a chapter row should preview position
        but not auto-start audio.
        """
        new_url = QUrl.fromLocalFile(str(path.resolve()))
        if self._player.source() == new_url:
            self._cancel_pending_seek()
            self._player.setPosition(start_ms)
            return

        self._player.setSource(new_url)
        self._defer_seek(start_ms)

    def seek_chapter(self, start_ms: int) -> None:
        """Seek to *start_ms* in the currently loaded file and resume play."""
        if self._player.source().isEmpty():
            return
        self._cancel_pending_seek()
        self._player.setPosition(start_ms)
        if self._player.playbackState() != QMediaPlayer.PlaybackState.PlayingState:
            self._player.play()

    def stop(self) -> None:
        """Stop playback and reset the slider."""
        self._cancel_pending_seek()
        self._player.stop()

    def release(self) -> None:
        """Stop playback and clear the loaded source.

        Call before an external process rewrites the currently-open file
        (M6) — QMediaPlayer can hold a file handle/lock on the source even
        while stopped, which fails an in-place save on Windows.
        """
        self._cancel_pending_seek()
        self._player.stop()
        self._player.setSource(QUrl())

    # ── deferred seek (M7) ────────────────────────────────────────────────────

    def _defer_seek(self, start_ms: int) -> None:
        """Schedule a seek to *start_ms* after the backend has buffered.

        Cancels any seek already pending so a rapid second load/seek cannot
        later apply a stale target — e.g. a quick second click landing on
        the *previous* chapter's position, or on the wrong file entirely.
        """
        self._cancel_pending_seek()
        if start_ms <= 0:
            return
        self._pending_seek_ms = start_ms
        self._seek_timer.start(self._SEEK_DELAY_MS)

    def _cancel_pending_seek(self) -> None:
        self._seek_timer.stop()
        self._pending_seek_ms = None

    def _apply_pending_seek(self) -> None:
        """Bound-method timer callback — reads the target from state, not a closure."""
        if self._pending_seek_ms is not None:
            self._player.setPosition(self._pending_seek_ms)
            self._pending_seek_ms = None

    @property
    def is_playing(self) -> bool:
        return self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState

    @property
    def current_position_ms(self) -> int:
        """Current playback position in milliseconds."""
        return self._player.position()

    @property
    def has_source(self) -> bool:
        """True if a file is loaded."""
        return not self._player.source().isEmpty()

    @property
    def transport_layout(self) -> QHBoxLayout:
        return self._transport_layout

    def seek_relative(self, offset_ms: int) -> None:
        """Seek relative to current position."""
        if self._player.source().isEmpty():
            return
        new_pos = max(0, min(self._player.position() + offset_ms, self._player.duration()))
        self._player.setPosition(new_pos)

    def set_dark_mode(self, dark_mode: bool) -> None:
        """Update SVG icons for buttons."""
        from PySide6.QtCore import QSize
        from m4bmaker.gui.icons import load_svg_icon
        from m4bmaker.gui.svg_icons import PLAY_SVG, PAUSE_SVG, REWIND_SVG, FAST_FORWARD_SVG
        
        color = "#f0f0f0" if dark_mode else "#1a1a1a"
        
        # We store SVG strings in instance so _update_buttons can toggle Play/Pause
        self._color = color
        
        # Default states
        self._rw_btn.setIcon(load_svg_icon(REWIND_SVG, color, 24))
        self._rw_btn.setIconSize(QSize(24, 24))
        
        self._ff_btn.setIcon(load_svg_icon(FAST_FORWARD_SVG, color, 24))
        self._ff_btn.setIconSize(QSize(24, 24))
        
        self._update_buttons(self._player.playbackState())

    # ── internal slots ────────────────────────────────────────────────────────

    def _toggle_play(self) -> None:
        if self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self._player.pause()
        else:
            self._player.play()

    def _on_slider_pressed(self) -> None:
        self._seeking = True

    def _on_slider_released(self) -> None:
        self._seeking = False
        self._player.setPosition(self._slider.value())

    def _on_position_changed(self, position_ms: int) -> None:
        if not self._seeking:
            self._slider.setValue(position_ms)
        duration = self._player.duration()
        self._time_lbl.setText(f"{_fmt_ms(position_ms)} / {_fmt_ms(duration)}")
        self.position_changed.emit(position_ms)

    def _on_duration_changed(self, duration_ms: int) -> None:
        self._slider.setMaximum(duration_ms)

    def _on_state_changed(self, state: QMediaPlayer.PlaybackState) -> None:
        self._update_buttons(state)

    def _update_buttons(self, state: QMediaPlayer.PlaybackState) -> None:
        playing = self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        if hasattr(self, '_color'):
            from PySide6.QtCore import QSize
            from m4bmaker.gui.icons import load_svg_icon
            from m4bmaker.gui.svg_icons import PLAY_SVG, PAUSE_SVG
            svg = PAUSE_SVG if playing else PLAY_SVG
            self._play_btn.setIcon(load_svg_icon(svg, self._color, 28))
            self._play_btn.setIconSize(QSize(28, 28))
            self._play_btn.setText("") # Remove text if SVG is used
            self._play_btn.update()
            self._play_btn.setToolTip("Pause" if playing else "Play")
        else:
            self._play_btn.setText(_ICON_PAUSE if playing else _ICON_PLAY)
