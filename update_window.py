import re
import sys

path = 'e:/Gemini/m4b-converter/m4bmaker/m4bmaker/gui/window.py'
with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

target = r'''        offset_row = QHBoxLayout\(\)
        offset_row\.addWidget\(QLabel\("Bulk Offset \(sec\):"\)\)
        self\._offset_spin = QDoubleSpinBox\(\)
        self\._offset_spin\.setRange\(-36000, 36000\)
        offset_row\.addWidget\(self\._offset_spin\)
        offset_btn = QPushButton\("Shift All"\)
        offset_btn\.clicked\.connect\(self\._on_shift_all_chapters\)
        offset_row\.addWidget\(offset_btn\)
        offset_row\.addStretch\(\)
        layout\.addLayout\(ch_tools_row\)
        layout\.addLayout\(offset_row\)

        hint = QLabel\(
            "Double-click or press a key to edit a title  ·  "
            "Enter = next row  ·  Shift\+Enter = previous row  ·  "
            "Shift-click to select a range for Merge  ·  Right-click for bulk tools"
        \)
        hint\.setStyleSheet\("color: #7a7a7a; font-size: 11px;"\)
        hint\.setAlignment\(Qt\.AlignmentFlag\.AlignLeft\)
        layout\.addWidget\(hint\)

        # Player row — prev/next injected directly into the player's button row
        self\._player = AudioPlayerWidget\(\)
        self\._player\.position_changed\.connect\(self\._on_playback_progress\)
        self\._ch_prev_btn = QPushButton\("⏮"\)
        self\._ch_prev_btn\.setFixedWidth\(36\)
        self\._ch_prev_btn\.setToolTip\("Previous chapter"\)
        self\._ch_prev_btn\.setEnabled\(False\)
        self\._ch_prev_btn\.clicked\.connect\(self\._on_chapter_prev\)
        self\._ch_next_btn = QPushButton\("⏭"\)
        self\._ch_next_btn\.setFixedWidth\(36\)
        self\._ch_next_btn\.setToolTip\("Next chapter"\)
        self\._ch_next_btn\.setEnabled\(False\)
        self\._ch_next_btn\.clicked\.connect\(self\._on_chapter_next\)
        # Insert prev/next before play button in the player's row layout
        player_row = self\._player\.layout\(\)\.itemAt\(0\)\.layout\(\)
        player_row\.insertWidget\(0, self\._ch_next_btn\)
        player_row\.insertWidget\(0, self\._ch_prev_btn\)
        layout\.addWidget\(self\._player\)
        return tab'''

replacement = '''        ch_tools_row.addSpacing(12)
        ch_tools_row.addWidget(QLabel("Bulk Offset (sec):"))
        self._offset_spin = QDoubleSpinBox()
        self._offset_spin.setRange(-36000, 36000)
        ch_tools_row.addWidget(self._offset_spin)
        offset_btn = QPushButton("Shift All")
        offset_btn.clicked.connect(self._on_shift_all_chapters)
        ch_tools_row.addWidget(offset_btn)
        ch_tools_row.addStretch()
        layout.addLayout(ch_tools_row)

        self._player = AudioPlayerWidget()
        self._player.position_changed.connect(self._on_playback_progress)
        self._ch_prev_btn = QPushButton()
        self._ch_prev_btn.setFixedSize(36, 36)
        self._ch_prev_btn.setToolTip("Previous chapter")
        self._ch_prev_btn.setEnabled(False)
        self._ch_prev_btn.clicked.connect(self._on_chapter_prev)
        self._ch_next_btn = QPushButton()
        self._ch_next_btn.setFixedSize(36, 36)
        self._ch_next_btn.setToolTip("Next chapter")
        self._ch_next_btn.setEnabled(False)
        self._ch_next_btn.clicked.connect(self._on_chapter_next)
        
        self._player.transport_layout.insertWidget(1, self._ch_prev_btn)
        self._player.transport_layout.insertWidget(4, self._ch_next_btn)
        layout.addWidget(self._player)
        return tab'''

target = target.replace('\n', '\r?\n')

new_content = re.sub(target, replacement, content)

if new_content == content:
    print("NO MATCH FOUND!")
    sys.exit(1)

with open(path, 'w', encoding='utf-8', newline='') as f:
    f.write(new_content)
