import sys
with open('E:\\Gemini\\m4b-converter\\m4bmaker\\m4bmaker\\gui\\icons.py', 'r', encoding='utf-8') as f:
    text = f.read()

import re
old_func = re.search(r'def load_svg_icon.*', text, flags=re.DOTALL).group(0)

new_func = '''def load_svg_icon(svg_text: str, color: str, size: int = 128, offset_x: float = 0.0, offset_y: float = 0.0) -> QIcon:
    import re
    from PySide6.QtGui import QIcon, QPixmap, QPainter
    from PySide6.QtSvg import QSvgRenderer
    from PySide6.QtCore import Qt, QByteArray, QRectF
    
    svg_text = re.sub(r'\\b(?:width|height)="[^"]*"', "", svg_text)
    svg_text = svg_text.replace("<svg ", f'<svg fill="{color}" ')
    svg_text = svg_text.replace("<path ", f'<path fill="{color}" ')
    
    renderer = QSvgRenderer(QByteArray(svg_text.encode("utf-8")))
    
    s = float(size)
    dx = float(offset_x)
    dy = float(offset_y)
    
    # EXACT 1:1 pixel rendering for flawless Windows QIcon matching
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    
    renderer.render(painter, QRectF(dx, dy, s, s))
    painter.end()
    
    return QIcon(pixmap)
'''

text = text.replace(old_func, new_func)

with open('E:\\Gemini\\m4b-converter\\m4bmaker\\m4bmaker\\gui\\icons.py', 'w', encoding='utf-8') as f:
    f.write(text)
