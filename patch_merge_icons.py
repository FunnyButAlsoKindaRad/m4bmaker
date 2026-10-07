import sys

with open('E:\\Gemini\\m4b-converter\\m4bmaker\\m4bmaker\\gui\\icons_original.py', 'r', encoding='utf-8') as f:
    orig = f.read()

with open('E:\\Gemini\\m4b-converter\\m4bmaker\\m4bmaker\\gui\\icons.py', 'r', encoding='utf-8') as f:
    curr = f.read()

# In the original, the last function is load_svg_icon?
# No, in eb210bf, load_svg_icon might not even exist, or if it does, it's at the end.
# Let's just safely replace or append.
if 'def load_svg_icon' in orig:
    import re
    orig = re.sub(r'def load_svg_icon.*', '', orig, flags=re.DOTALL)

# Remove the duplicate imports from curr that are already in orig?
# orig has QIcon, QPixmap, QPainter, Qt, QRectF, QSvgRenderer
# I'll just append the new load_svg_icon definition directly.
func_def = curr[curr.find('def load_svg_icon'):]

combined = orig.rstrip() + "\n\n" + func_def + "\n"

with open('E:\\Gemini\\m4b-converter\\m4bmaker\\m4bmaker\\gui\\icons.py', 'w', encoding='utf-8') as f:
    f.write(combined)
