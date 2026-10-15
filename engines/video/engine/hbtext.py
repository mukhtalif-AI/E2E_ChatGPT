"""hbtext.py - Hindi text drawn correctly: HarfBuzz joins the letters (च्च, क्ष, ज़्या ...), FreeType draws them.

Why: Pillow on Windows has no text shaper (raqm needs an extra DLL), and the ffmpeg text drawer joined some
conjuncts wrongly in the bold-italic key-word font (बच्चे came out like "बज्जे"). Needs: pip install uharfbuzz freetype-py
(setup_hub.bat does it). When they are missing, the callers fall back to their old ways.

    ok()                                      -> True when both libraries are there
    draw(img_l, text, fontfile, size, x, y)   -> draws white text into a float32 HxW array, (x, y) = left / ascender
    width(text, fontfile, size)               -> advance width in pixels
"""
import numpy as np

_FACES = {}


def ok():
    try:
        import uharfbuzz  # noqa: F401
        import freetype   # noqa: F401
        return True
    except Exception:
        return False


def _faces(fontfile, size):
    key = (fontfile, size)
    if key not in _FACES:
        import uharfbuzz as hb
        import freetype
        blob = hb.Blob.from_file_path(fontfile)
        hface = hb.Face(blob, 0)
        hfont = hb.Font(hface)
        hfont.scale = (size * 64, size * 64)
        ft = freetype.Face(fontfile, 0)
        ft.set_char_size(size * 64)
        _FACES[key] = (hfont, ft, ft.size.ascender / 64.0)
    return _FACES[key]


def _shape(text, fontfile, size):
    import uharfbuzz as hb
    hfont, ft, asc = _faces(fontfile, size)
    buf = hb.Buffer()
    buf.add_str(text)
    buf.guess_segment_properties()
    hb.shape(hfont, buf, {"kern": True, "liga": True})
    return buf.glyph_infos, buf.glyph_positions, ft, asc


def width(text, fontfile, size):
    infos, pos, _, _ = _shape(text, fontfile, size)
    return sum(p.x_advance for p in pos) / 64.0


def ascender(fontfile, size):
    return _faces(fontfile, size)[2]


def draw(img, text, fontfile, size, x, y):
    """img: float32 HxW (0..1); text drawn with its ascender line at y (like Pillow's default anchor)"""
    import freetype
    infos, pos, ft, asc = _shape(text, fontfile, size)
    base = y + asc
    pen = float(x)
    H, W = img.shape
    for info, p in zip(infos, pos):
        ft.load_glyph(info.codepoint, freetype.FT_LOAD_DEFAULT)
        ft.glyph.render(freetype.FT_RENDER_MODE_NORMAL)
        bm = ft.glyph.bitmap
        if bm.rows and bm.width:
            g = np.array(bm.buffer, np.uint8).reshape(bm.rows, bm.pitch)[:, :bm.width].astype(np.float32) / 255
            gx = int(round(pen + p.x_offset / 64.0 + ft.glyph.bitmap_left))
            gy = int(round(base - p.y_offset / 64.0 - ft.glyph.bitmap_top))
            x0, y0 = max(0, gx), max(0, gy)
            x1, y1 = min(W, gx + g.shape[1]), min(H, gy + g.shape[0])
            if x1 > x0 and y1 > y0:
                np.maximum(img[y0:y1, x0:x1], g[y0 - gy:y1 - gy, x0 - gx:x1 - gx], out=img[y0:y1, x0:x1])
        pen += p.x_advance / 64.0
    return pen - x
