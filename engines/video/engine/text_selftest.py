"""text_selftest.py - draws a few hard Hindi words with the hub's text drawer -> E:\\...\\_tmp\\hb_test.png"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import hbtext
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FONTS = os.path.join(HERE, "assets", "fonts")
out = os.path.join(os.path.dirname(os.path.dirname(HERE)), "_tmp", "hb_test.png")
print("HarfBuzz + FreeType available:", hbtext.ok())
img = np.zeros((360, 1300), np.float32)
for i, f in enumerate(["aparajbi.ttf", "aparaj.ttf", "TiroDevanagariHindi-Regular.ttf"]):
    hbtext.draw(img, "बच्चे बच्चा सच्चा क्षमा ज़्यादा श्रद्धा", os.path.join(FONTS, f), 70, 20, 20 + i * 110)
from PIL import Image
os.makedirs(os.path.dirname(out), exist_ok=True)
Image.fromarray((img * 255).astype(np.uint8)).save(out)
print("saved", out)
