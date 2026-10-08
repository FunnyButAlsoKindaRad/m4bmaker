import sys
import re

with open('E:\\Gemini\\m4b-converter\\m4bmaker\\m4bmaker\\gui\\tweaker.py', 'r', encoding='utf-8') as f:
    text = f.read()

# Make sure main_window has _tweaker_state
init_patch = '''        self._main_window = main_window
        if not hasattr(self._main_window, "_tweaker_state"):
            self._main_window._tweaker_state = {}'''

text = text.replace('        self._main_window = main_window', init_patch)

# Replace _on_target_changed
old_on_target = re.search(r'    def _on_target_changed\(self, idx\):.*?self\._apply_changes\(\)', text, flags=re.DOTALL).group(0)

new_on_target = '''    def _on_target_changed(self, idx):
        self._loading = True
        if idx == 0:
            btn = self._main_window._player._play_btn
            key = "play"
        elif idx == 1:
            btn = self._main_window._player._rw_btn
            key = "rw"
        else:
            btn = self._main_window._ch_prev_btn
            key = "ch"
            
        state = self._main_window._tweaker_state.get(key, {})
            
        self._btn_w_spin.setValue(state.get("bw", btn.width()))
        self._btn_h_spin.setValue(state.get("bh", btn.height()))
        self._icon_w_spin.setValue(state.get("iw", btn.iconSize().width()))
        self._icon_h_spin.setValue(state.get("ih", btn.iconSize().height()))
        self._offset_x_spin.setValue(state.get("ox", 0))
        self._offset_y_spin.setValue(state.get("oy", 0))
        self._loading = False
        self._apply_changes()'''

text = text.replace(old_on_target, new_on_target)

# Replace _apply_changes
old_apply = re.search(r'    def _apply_changes\(self\):.*?name = "Prev/Next"', text, flags=re.DOTALL).group(0)

new_apply = '''    def _apply_changes(self):
        if getattr(self, "_loading", False):
            return
            
        bw = self._btn_w_spin.value()
        bh = self._btn_h_spin.value()
        iw = self._icon_w_spin.value()
        ih = self._icon_h_spin.value()
        ox = self._offset_x_spin.value()
        oy = self._offset_y_spin.value()
        
        idx = self._target_combo.currentIndex()
        if idx == 0:
            btns = [self._main_window._player._play_btn]
            name = "Play/Pause"
            key = "play"
        elif idx == 1:
            btns = [self._main_window._player._rw_btn, self._main_window._player._ff_btn]
            name = "RW/FF"
            key = "rw"
        else:
            btns = [self._main_window._ch_prev_btn, self._main_window._ch_next_btn]
            name = "Prev/Next"
            key = "ch"
            
        self._main_window._tweaker_state[key] = {
            "bw": bw, "bh": bh, "iw": iw, "ih": ih, "ox": ox, "oy": oy
        }
        
        # We can use padding to move the icon. Positive X = move right (padding-left)
        # Positive Y = move down (padding-top). Since total size is fixed, we balance it.
        pt = max(0, oy)
        pb = max(0, -oy)
        pl = max(0, ox)
        pr = max(0, -ox)
        style = f"padding: {pt}px {pr}px {pb}px {pl}px; min-width: {bw}px; min-height: {bh}px; max-width: {bw}px; max-height: {bh}px;"
'''

text = text.replace(old_apply, new_apply)

with open('E:\\Gemini\\m4b-converter\\m4bmaker\\m4bmaker\\gui\\tweaker.py', 'w', encoding='utf-8') as f:
    f.write(text)
