import sys
with open('E:\\Gemini\\m4b-converter\\m4bmaker\\m4bmaker\\gui\\icons.py', 'r', encoding='utf-8') as f:
    text = f.read()

import re
old_func = re.search(r'def load_svg_icon.*', text, flags=re.DOTALL).group(0)

new_func = '''def load_svg_icon(svg_text: str, color: str, size: int = 128) -> QIcon:
    import re
    from PySide6.QtGui import QIcon, QPixmap, QPainter
    from PySide6.QtCore import Qt, QByteArray
    
    svg_text = re.sub(r'\\b(?:width|height)="[^"]*"', "", svg_text)
    svg_text = svg_text.replace("<svg ", f'<svg fill="{color}" ')
    
    # QSvgRenderer might be imported already, or might not
    from PySide6.QtSvg import QSvgRenderer
    renderer = QSvgRenderer(QByteArray(svg_text.encode("utf-8")))
    
    # High-DPI scaling: internal pixmap is larger
    pixmap = QPixmap(size * 4, size * 4)
    pixmap.fill(Qt.GlobalColor.transparent)
    pixmap.setDevicePixelRatio(4.0)
    
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    
    renderer.render(painter)
    painter.end()
    
    return QIcon(pixmap)
'''

text = text.replace(old_func, new_func)

with open('E:\\Gemini\\m4b-converter\\m4bmaker\\m4bmaker\\gui\\icons.py', 'w', encoding='utf-8') as f:
    f.write(text)
