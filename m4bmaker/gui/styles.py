"""QSS stylesheet — design tokens applied to Qt widgets.

Palette
-------
Ink          #1a1a1a   primary text
Ink Light    #4a4a4a   body text
Ink Muted    #7a7a7a   labels / hints
Ground       #f5f2ed   window background
Ground Warm  #ebe6dd   cards / group boxes
White        #faf8f4   input surfaces
Terracotta   #c45a2d   accent / primary action
Rule         #d0c9be   borders / dividers
"""

from __future__ import annotations

STYLESHEET = """

QPushButton#playerPlayBtn, QPushButton#playerRwBtn, QPushButton#playerFfBtn, QPushButton#playerPrevBtn, QPushButton#playerNextBtn {
    padding: 0px;
}
/* Dialogs */
QPushButton#playerPlayBtn, QPushButton#playerRwBtn, QPushButton#playerFfBtn, QPushButton#playerPrevBtn, QPushButton#playerNextBtn {
    padding: 0px;
}

QDialogButtonBox QPushButton {
    min-width: 72px;
}


QPushButton#playerPlayBtn, QPushButton#playerRwBtn, QPushButton#playerFfBtn, QPushButton#playerPrevBtn, QPushButton#playerNextBtn {
    padding: 0px;
}
/* Dialogs */
QPushButton#playerPlayBtn, QPushButton#playerRwBtn, QPushButton#playerFfBtn, QPushButton#playerPrevBtn, QPushButton#playerNextBtn {
    padding: 0px;
}

QDialogButtonBox QPushButton {
    min-width: 72px;
}

/* ── Cover Widget ────────────────────────────────────────────────────── */
QFrame#coverWidget {
    background-color: #242424;
    border: 1px solid #333333;
    border-radius: 4px;
}

QLabel#coverThumb {
    background-color: #2e2e2e;
    border: 1px solid #333333;
    border-radius: 3px;
    color: #888888;
    font-size: 11px;
}

/* ── Dark mode toggle button ─────────────────────────────────────────── */
QPushButton#darkModeBtn {
    background: transparent;
    border: 1px solid #333333;
    border-radius: 5px;
    font-size: 14px;
    padding: 0;
    color: #c8c2b8;
}

QPushButton#darkModeBtn:hover {
    background-color: #2e2e2e;
}

/* ── Clear button ────────────────────────────────────────────────────── */
QPushButton#clearBtn {
    background: transparent;
    border: 1px solid #333333;
    border-radius: 3px;
    color: #888888;
    font-size: 10px;
    padding: 0;
}

QPushButton#clearBtn:hover {
    background-color: #2e2e2e;
    color: #c8c2b8;
    border-color: #444444;
}

/* ── Mode badge ──────────────────────────────────────────────────────── */
QLabel#modeBadge {
    color: #888888;
    font-size: 10px;
    font-weight: 600;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    padding: 1px 6px;
    border: 1px solid #333333;
    border-radius: 3px;
    background-color: #242424;
}

/* ── Queue window ──────────────────────────────────────────────── */
QTableWidget#queueTable {
    border: 1px solid #333333;
    gridline-color: #2a2a2a;
    background-color: #1e1e1e;
    alternate-background-color: #222222;
}
QTableWidget#queueTable::item:selected {
    background-color: #c45a2d;
    color: #f5f2ed;
}
QProgressBar#jobProgress {
    border: 1px solid #333333;
    border-radius: 3px;
    background-color: #2e2e2e;
    text-align: center;
    font-size: 11px;
    color: #c8c2b8;
    max-height: 14px;
}
QProgressBar#jobProgress::chunk {
    background-color: #c45a2d;
    border-radius: 2px;
}
"""


def get_stylesheet(dark: bool = False) -> str:
    """Return the appropriate stylesheet for the given colour mode."""
    return DARK_STYLESHEET if dark else STYLESHEET
