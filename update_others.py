import re
import sys

path = 'e:/Gemini/m4b-converter/m4bmaker/m4bmaker/gui/window.py'
with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

# Change button heights
content = content.replace('self._convert_btn.setFixedHeight(44)', 'self._convert_btn.setFixedHeight(36)')
content = content.replace('self._add_to_queue_btn.setFixedHeight(44)', 'self._add_to_queue_btn.setFixedHeight(36)')
content = content.replace('self._split_btn.setFixedHeight(44)', 'self._split_btn.setFixedHeight(36)')

# Update _toggle_dark_mode
target = r'''        if hasattr\(self, "_dark_btn"\):
            self\._dark_btn\.setIcon\(dark_mode_icon\(self\._dark_mode\)\)
        if self\._queue_window is not None:
            self\._queue_window\.apply_stylesheet\(self\._dark_mode\)'''

replacement = '''        if hasattr(self, "_dark_btn"):
            self._dark_btn.setIcon(dark_mode_icon(self._dark_mode))
        if self._queue_window is not None:
            self._queue_window.apply_stylesheet(self._dark_mode)
        if hasattr(self, "_player"):
            self._player.set_dark_mode(self._dark_mode)
            from m4bmaker.gui.icons import load_svg_icon
            from m4bmaker.gui.svg_icons import PREVIOUS_CHAPTER_SVG, NEXT_CHAPTER_SVG
            from PySide6.QtCore import QSize
            color = "#f0f0f0" if self._dark_mode else "#1a1a1a"
            if hasattr(self, "_ch_prev_btn"):
                self._ch_prev_btn.setIcon(load_svg_icon(PREVIOUS_CHAPTER_SVG, color, 128))
                self._ch_prev_btn.setIconSize(QSize(32, 32))
            if hasattr(self, "_ch_next_btn"):
                self._ch_next_btn.setIcon(load_svg_icon(NEXT_CHAPTER_SVG, color, 128))
                self._ch_next_btn.setIconSize(QSize(32, 32))'''

target = target.replace('\n', '\r?\n')
new_content = re.sub(target, replacement, content)

if new_content == content:
    print("NO MATCH FOUND FOR _toggle_dark_mode!")
    sys.exit(1)

with open(path, 'w', encoding='utf-8', newline='') as f:
    f.write(new_content)
