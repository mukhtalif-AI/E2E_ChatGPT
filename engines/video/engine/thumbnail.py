"""thumbnail.py - a YouTube thumbnail (1920x1080) for a series episode: one of its pictures, the series grade, a shade
behind the words, the title in big outlined letters (Hindi shaped by HarfBuzz), an optional small line above it, the
series badge (भय ...) and the channel logo / name. Settings come from the hub's Thumbnails page (app/thumbnail.py).

    python thumbnail.py <spec.json>          spec = one job or a list of jobs; each writes job["out"]

job = {"out", "w": 1920, "h": 1080, "image", "focus": 0.5, "grade": "horror" | "pop" | "",
       "layout": "bottom" | "left" | "right" | "center", "title", "sub", "kicker",
       "font", "size": 1.0, "color", "accent", "outline", "outline_w": 0.08, "glow": 0.6, "shade": 0.7, "vignette": 0.4,
       "badge": {"text", "font"} | null, "logo": {"path", "corner", "size"} | null, "name": {"text", "font"} | null}
Prints one JSON line per job: {"out", "ok", "error"?}.
"""
import sys as _sys
for _s in (_sys.stdout, _sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
import json, os, sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CORNERS = ("top-left", "top-right", "bottom-left", "bottom-right")


def font_path(name):
    if not name:
        return ""
    if os.path.isabs(name) and os.path.exists(name):
        return name
    for d in (os.path.join(HERE, "assets", "fonts"), r"C:\Windows\Fonts"):
        p = os.path.join(d, os.path.basename(name))
        if os.path.exists(p):
            return p
    return ""


def rgb(c, default=(1.0, 1.0, 1.0)):
    c = str(c or "").strip().lstrip("#")
    if len(c) == 3:
        c = "".join(x * 2 for x in c)
    try:
        return tuple(int(c[i:i + 2], 16) / 255 for i in (0, 2, 4))
    except ValueError:
        return default


# ------------------------------------------------------------------ text
def text_mask(text, fontfile, size):
    """white text on black (float32), cropped to the ink, and how far the ink top sits under the line's top
    (so rows keep one even pitch whatever their letters); Hindi shaped by HarfBuzz when available"""
    size = max(8, int(size))
    top = int(size * 0.4)
    try:
        import hbtext
        use_hb = hbtext.ok()
    except Exception:
        use_hb = False
    if use_hb:
        w = int(hbtext.width(text, fontfile, size)) + size
        m = np.zeros((int(size * 2.2), w), np.float32)
        hbtext.draw(m, text, fontfile, size, size // 2, top)
    else:
        from PIL import Image, ImageDraw, ImageFont
        f = ImageFont.truetype(fontfile, size)
        im = Image.new("L", (int(f.getlength(text)) + size, int(size * 2.2)))
        ImageDraw.Draw(im).text((size // 2, top), text, font=f, fill=255)
        m = np.asarray(im, np.float32) / 255
    ys, xs = np.nonzero(m > 0.02)
    if not len(ys):
        return np.zeros((1, 1), np.float32), 0
    return m[ys.min():ys.max() + 1, xs.min():xs.max() + 1], ys.min() - top


def text_width(text, fontfile, size):
    try:
        import hbtext
        if hbtext.ok():
            return hbtext.width(text, fontfile, int(size))
    except Exception:
        pass
    from PIL import ImageFont
    return ImageFont.truetype(fontfile, int(size)).getlength(text)


def wrap(text, fontfile, size, maxw, max_rows):
    """words into rows no wider than maxw; None when it needs more than max_rows"""
    rows, cur = [], ""
    for w in text.split():
        t = (cur + " " + w).strip()
        if cur and text_width(t, fontfile, size) > maxw:
            rows.append(cur)
            cur = w
        else:
            cur = t
    rows += [cur] if cur else []
    if len(rows) > max_rows or any(text_width(r, fontfile, size) > maxw for r in rows):
        return None
    return rows


def balance(rows, fontfile, size, maxw):
    """rows of very different length -> move words so they are about even (looks designed, no lone last word)"""
    if len(rows) < 2:
        return rows
    words = " ".join(rows).split()
    n = len(rows)
    best, score = rows, max(text_width(r, fontfile, size) for r in rows)
    import itertools
    for cuts in itertools.combinations(range(1, len(words)), n - 1):
        parts = [" ".join(words[a:b]) for a, b in zip((0,) + cuts, cuts + (len(words),))]
        wmax = max(text_width(t, fontfile, size) for t in parts)
        if wmax < score:
            best, score = parts, wmax
    return best


def dilate(m, r):
    """grow a mask by r pixels (octagon steps; numpy only)"""
    r = int(max(0, r))
    out = np.pad(m, r + 2)
    for i in range(r):
        a = out.copy()
        np.maximum(a[1:], out[:-1], out=a[1:]); np.maximum(a[:-1], out[1:], out=a[:-1])
        np.maximum(a[:, 1:], out[:, :-1], out=a[:, 1:]); np.maximum(a[:, :-1], out[:, 1:], out=a[:, :-1])
        if i % 2:
            b = a.copy()
            np.maximum(b[1:, 1:], a[:-1, :-1], out=b[1:, 1:]); np.maximum(b[:-1, :-1], a[1:, 1:], out=b[:-1, :-1])
            np.maximum(b[1:, :-1], a[:-1, 1:], out=b[1:, :-1]); np.maximum(b[:-1, 1:], a[1:, :-1], out=b[:-1, 1:])
            a = b
        out = a
    return out, r + 2


def gblur(m, r):
    from PIL import Image, ImageFilter
    if r < 1:
        return m
    im = Image.fromarray((np.clip(m, 0, 1) * 255).astype(np.uint8), "L").filter(ImageFilter.GaussianBlur(r))
    return np.asarray(im, np.float32) / 255


class Layer:
    """a transparent picture (premultiplied colour + alpha) that one element is drawn into, placed later"""

    def __init__(self, w, h):
        self.rgb = np.zeros((int(h), int(w), 3), np.float32)
        self.a = np.zeros((int(h), int(w)), np.float32)
        self.shape = self.a.shape


def over(img, mask, x, y, color, alpha=1.0):
    """paint color (RGB, or an HxWx3 array the size of mask) through mask (0..1) onto img (HxWx3 float, or a
    Layer) with its top-left at (x, y)"""
    H, W = img.shape[:2]
    h, w = mask.shape
    x, y = int(round(x)), int(round(y))
    x0, y0, x1, y1 = max(0, x), max(0, y), min(W, x + w), min(H, y + h)
    if x1 <= x0 or y1 <= y0:
        return
    a = mask[y0 - y:y1 - y, x0 - x:x1 - x, None] * float(alpha)
    col = color[y0 - y:y1 - y, x0 - x:x1 - x] if isinstance(color, np.ndarray) else np.array(color, np.float32)
    if isinstance(img, Layer):
        img.rgb[y0:y1, x0:x1] = img.rgb[y0:y1, x0:x1] * (1 - a) + col * a
        img.a[y0:y1, x0:x1] = img.a[y0:y1, x0:x1] * (1 - a[..., 0]) + a[..., 0]
    else:
        img[y0:y1, x0:x1] = img[y0:y1, x0:x1] * (1 - a) + col * a


def put(img, L, x, y):
    """a Layer onto the picture with its top-left at (x, y)"""
    H, W = img.shape[:2]
    h, w = L.shape
    x, y = int(round(x)), int(round(y))
    x0, y0, x1, y1 = max(0, x), max(0, y), min(W, x + w), min(H, y + h)
    if x1 <= x0 or y1 <= y0:
        return
    a = L.a[y0 - y:y1 - y, x0 - x:x1 - x, None]
    img[y0:y1, x0:x1] = img[y0:y1, x0:x1] * (1 - a) + L.rgb[y0 - y:y1 - y, x0 - x:x1 - x]


def box_mask(w, h, r=0):
    """a filled (rounded when r) rectangle mask"""
    m = np.ones((max(1, int(h)), max(1, int(w))), np.float32)
    r = int(min(r, m.shape[0] // 2, m.shape[1] // 2))
    if r > 1:
        yy, xx = np.mgrid[0:r, 0:r].astype(np.float32)
        c = np.clip(r - np.sqrt((xx - r + 0.5) ** 2 + (yy - r + 0.5) ** 2) + 0.5, 0, 1)
        m[:r, :r] = c; m[:r, -r:] = c[:, ::-1]; m[-r:, :r] = c[::-1]; m[-r:, -r:] = c[::-1, ::-1]
    return m


def words_of(text):
    """'हवेली का *रहस्य*' -> [('हवेली', False), ('का', False), ('रहस्य', True)]; *...* = accent colour"""
    import re
    out = []
    for m in re.finditer(r"\*([^*]+)\*|(\S+)", str(text or "")):
        hl = m.group(1) is not None
        out += [(w, hl) for w in (m.group(1) if hl else m.group(2)).replace("*", "").split()]
    return out


def plain(text):
    return " ".join(w for w, _ in words_of(text))


class Line:
    """one row of letters (each word in its own colour) with outline, glow and shadow, ready to paint"""

    def __init__(self, words, fontfile, size, color, hl_color, J, plain_style=False):
        if isinstance(words, str):
            words = words_of(words)
        space = max(size * 0.25, text_width(" ", fontfile, size))
        parts, x = [], 0.0
        for w, hl in words:
            m, top = text_mask(w, fontfile, size)
            parts.append((m, top, x, hl_color if hl else color))
            x += m.shape[1] + space
        t0 = min((p[1] for p in parts), default=0)
        h = max((p[1] + p[0].shape[0] for p in parts), default=1) - t0
        w = int(max(1, x - space)) + 1
        self.plain_style = plain_style               # text on a box: thin outline, no glow / ring
        ow = float(J.get("outline_w", 0.08)) * size * (0.35 if plain_style else 1)
        ring = 0 if plain_style else float(J.get("stroke2", 0) or 0) * size
        self.pad = int(ow + ring + 2 + size * 0.5)   # room for the glow and shadow to fade out
        canvas = lambda: np.zeros((h + 2 * self.pad, w + 2 * self.pad), np.float32)
        self.fills = []
        ink = np.zeros((h, w), np.float32)
        shade = float(J.get("gradient", 0) or 0) if not plain_style else 0
        ramp = np.linspace(1.0, 1.0 - shade, h + 2 * self.pad, dtype=np.float32)[:, None, None]
        for m, top, x0, col in parts:
            y0, xi = top - t0, int(round(x0))
            f = canvas()
            m = m[:, :w - xi]                        # (rounding can make the last word 1 px too wide)
            f[self.pad + y0:self.pad + y0 + m.shape[0], self.pad + xi:self.pad + xi + m.shape[1]] = m
            if shade > 0:                            # letters a little darker towards their foot: looks printed
                col = np.broadcast_to(np.array(col, np.float32)[None, None, :] * ramp, f.shape + (3,))
            self.fills.append((f, col))
            sl = ink[y0:y0 + m.shape[0], xi:xi + m.shape[1]]
            np.maximum(sl, m, out=sl)
        edge, p = dilate(ink, ow)
        self.edge = np.pad(edge, self.pad - p)
        self.ring = None
        if ring >= 1:
            rr, p2 = dilate(ink, ow + ring)
            self.ring = np.pad(rr, self.pad - p2)
        self.top = t0
        self.size = size
        self.boxed = False
        self.pitch = size * 1.12                     # this row's share of the block height
        self.ink_w, self.ink_h = w, h

    def paint(self, img, x, y, J):
        """(x, y) = left of the ink, top of the row"""
        x, y = x - self.pad, y + self.top - self.pad
        accent = rgb(J.get("accent"), (0.8, 0.1, 0.1))
        outer = self.ring if self.ring is not None else self.edge
        glow = 0 if self.plain_style else float(J.get("glow", 0) or 0)
        if glow > 0:
            g = gblur(outer, self.size * 0.22)
            over(img, np.clip(g * 1.6, 0, 1), x, y, accent, glow)
        sh = gblur(outer, self.size * 0.07)
        over(img, sh, x + self.size * 0.05, y + self.size * 0.07, (0, 0, 0), 0.4 if self.plain_style else 0.8)
        if self.ring is not None:
            over(img, self.ring, x, y, rgb(J.get("stroke2_color") or J.get("accent"), accent))
        if J.get("outline_w", 0.08):
            over(img, self.edge, x, y, rgb(J.get("outline"), (0, 0, 0)))
        for f, col in self.fills:
            over(img, f, x, y, col)


def row_width(text, fontfile, size):
    ws = [w for w, _ in words_of(text)]
    return sum(text_width(w, fontfile, size) for w in ws) + max(size * 0.25, text_width(" ", fontfile, size)) * max(0, len(ws) - 1)


def row_tops(lines):
    """where each row starts (its line top, from the first row's ink top): an even pitch, but never closer than a
    small gap between one row's ink (or box) and the next"""
    out, y, prev = [], 0.0, None
    for L in lines:
        if prev is None:
            y = -L.top
        else:
            gap = L.size * 0.12 + (prev.size * 0.15 if prev.boxed else 0) + (L.size * 0.3 if L.boxed else 0)
            y = max(y + prev.pitch, y + prev.top + prev.ink_h + gap - L.top)
        out.append(y)
        prev = L
    return out


def block_height(lines):
    """from the first row's ink top to the last row's ink bottom"""
    if not lines:
        return 0
    return row_tops(lines)[-1] + lines[-1].top + lines[-1].ink_h


def ink_color(bg):
    """white letters on a dark colour, near-black on a light one"""
    return (1, 1, 1) if 0.299 * bg[0] + 0.587 * bg[1] + 0.114 * bg[2] < 0.6 else (0.06, 0.06, 0.06)


# ------------------------------------------------------------------ picture
def load_picture(path, W, H, focus):
    from PIL import Image
    im = Image.open(path).convert("RGB")
    r = max(W / im.width, H / im.height)
    im = im.resize((max(W, int(im.width * r + 0.5)), max(H, int(im.height * r + 0.5))), Image.LANCZOS)
    x0 = int((im.width - W) * min(1, max(0, float(focus))))
    y0 = (im.height - H) // 2
    return np.asarray(im.crop((x0, y0, x0 + W, y0 + H)), np.float32) / 255


def grade(a, how):
    if how == "pop":                               # a bit more colour and contrast: reads better as a small tile
        g = a.mean(-1, keepdims=True)
        a = g + (a - g) * 1.18
        return np.clip((a - 0.5) * 1.10 + 0.5, 0, 1)
    try:
        from horror_video import MOODS
    except Exception:
        MOODS = {}
    M = MOODS.get(how)
    if not M:
        return a
    g = a.mean(-1, keepdims=True)
    a = g + (a - g) * M["sat"]
    a = a * np.array(M["tint"], np.float32)
    a = np.clip((a - 0.03) * 1.08, 0, 1) ** M["gamma"] if M["gamma"] > 1.01 else np.clip(a, 0, 1)
    return np.clip((a - 0.5) * 1.06 + 0.5, 0, 1)   # thumbnails want a touch more contrast than the video


def shade(a, layout, amount):
    """darkness behind the words so they read on any picture"""
    H, W = a.shape[:2]
    amount = min(1.0, max(0.0, float(amount)))
    if amount <= 0:
        return a
    yy = np.linspace(0, 1, H, dtype=np.float32)[:, None]
    xx = np.linspace(0, 1, W, dtype=np.float32)[None, :]
    if layout == "bottom":
        d = np.clip((yy - 0.38) / 0.62, 0, 1) ** 1.4 + 0 * xx
    elif layout == "left":
        d = np.clip((0.68 - xx) / 0.68, 0, 1) ** 1.2 + 0 * yy
    elif layout == "right":
        d = np.clip((xx - 0.32) / 0.68, 0, 1) ** 1.2 + 0 * yy
    else:
        d = 0.55 + 0 * xx * yy
    return a * (1 - amount * d[..., None])


def vignette(a, amount):
    if amount <= 0:
        return a
    H, W = a.shape[:2]
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    r = np.sqrt(((xx - W / 2) / (W / 2)) ** 2 + ((yy - H / 2) / (H / 2)) ** 2) / 1.414
    return a * (1 - float(amount) * np.clip((r - 0.35) / 0.65, 0, 1) ** 1.6)[..., None]


def punch(a, amount):
    """crisper and a bit more colourful: thumbnails are seen small, soft pictures look dull there"""
    amount = min(1.0, max(0.0, float(amount or 0)))
    if amount <= 0:
        return a
    from PIL import Image, ImageFilter
    H, W = a.shape[:2]
    im = Image.fromarray((np.clip(a, 0, 1) * 255 + 0.5).astype(np.uint8))
    im = im.filter(ImageFilter.UnsharpMask(radius=max(1.0, 2.2 * W / 1920), percent=int(90 * amount), threshold=2))
    a = np.asarray(im, np.float32) / 255
    g = a.mean(-1, keepdims=True)
    return np.clip(g + (a - g) * (1 + 0.22 * amount), 0, 1)


def frame(img, color, width):
    """a coloured border round the whole picture with a thin dark line inside it"""
    H, W = img.shape[:2]
    f = int(round(float(width or 0) * H))
    if f < 1:
        return
    col = np.array(color, np.float32)
    img[:f] = col; img[-f:] = col; img[:, :f] = col; img[:, -f:] = col
    d = max(1, f // 4)
    for sl in ((slice(f, f + d), slice(f, W - f)), (slice(H - f - d, H - f), slice(f, W - f)),
               (slice(f, H - f), slice(f, f + d)), (slice(f, H - f), slice(W - f - d, W - f))):
        img[sl] = img[sl] * 0.25


# ------------------------------------------------------------------ the layout
def title_box(J, W, H):
    lay = J.get("layout") or "bottom"
    if lay == "bottom":
        return (0.05 * W, 0.40 * H, 0.95 * W, 0.93 * H), "center", 2
    if lay == "left":
        return (0.05 * W, 0.14 * H, 0.58 * W, 0.90 * H), "left", 3
    if lay == "right":
        return (0.42 * W, 0.14 * H, 0.95 * W, 0.90 * H), "right", 3
    return (0.07 * W, 0.18 * H, 0.93 * W, 0.86 * H), "center", 2


def title_rows(J, W, H):
    """the title as rows [(words, small)] and the letter size that fits the layout's box"""
    fontfile = font_path(J.get("font")) or font_path("Nirmala.ttc")
    title = " ".join(str(J.get("title") or "").split())
    sub = " ".join(str(J.get("sub") or "").split())
    box, align, rows = title_box(J, W, H)
    bw, bh = box[2] - box[0], box[3] - box[1]
    size = H * 0.19 * float(J.get("size") or 1.0)
    rows_in = [str(r) for r in (J.get("rows") or []) if str(r).strip()]
    while size >= H * 0.05:
        if rows_in:                                  # your own rows, word for word: '-' first = a smaller row
            spec = [(words_of(r.strip()[1:]), True) if r.strip().startswith("-") else (words_of(r), False) for r in rows_in]
        else:
            main = wrap(plain(title), fontfile, size, bw, rows) if title else []
            subrows = wrap(plain(sub), fontfile, size * 0.56, bw, 2) if sub else []
            if main is None or subrows is None:
                size *= 0.93
                continue
            spec = [(w, False) for w in rehighlight(balance(main, fontfile, size, bw), title)] + \
                   [(w, True) for w in rehighlight(balance(subrows, fontfile, size * 0.56, bw), sub)]
        widths = [row_width(" ".join(w for w, _ in ws), fontfile, size * (0.56 if sm else 1)) for ws, sm in spec]
        if all(x <= bw for x in widths):
            lines = make_lines(J, spec, fontfile, size)
            if block_height(lines) <= bh:
                return spec, size
        size *= 0.93
    return [], H * 0.05


def rehighlight(rows, text):
    """wrapped plain rows -> word lists that keep the *highlight* marks of the original text"""
    marks = words_of(text)
    out, i = [], 0
    for r in rows:
        n = len(r.split())
        out.append(marks[i:i + n])
        i += n
    return out


def make_lines(J, spec, fontfile, size):
    color, accent = rgb(J.get("color")), rgb(J.get("accent"), (0.85, 0.15, 0.12))
    boxed = bool(J.get("sub_box"))
    on_box = ink_color(accent)
    out = []
    for ws, sm in spec:
        if sm and boxed:                             # a small row on an accent box: letters in a contrast colour
            L = Line(ws, fontfile, size * 0.56, on_box, color if on_box != color else (1, 0.9, 0.3), J, True)
            L.boxed = True
        else:
            L = Line(ws, fontfile, size * (0.56 if sm else 1), accent if sm else color, color if sm else accent, J)
        out.append(L)
    return out


def title_layer(J, W, H, scale=1.0):
    """the title rows drawn into one Layer; returns (layer, pad = where the letters' block starts in it)"""
    spec, size = title_rows(J, W, H)
    if not spec:
        return None, 0
    fontfile = font_path(J.get("font")) or font_path("Nirmala.ttc")
    lines = make_lines(J, spec, fontfile, size * scale)
    _, align, _ = title_box(J, W, H)
    maxw = max(L.ink_w for L in lines)
    bx, by = size * scale * 0.2, size * scale * 0.09
    pad = int(max(L.pad for L in lines) + bx)
    blk = block_height(lines)
    lay = Layer(maxw + 2 * pad, blk + 2 * pad)
    accent = rgb(J.get("accent"), (0.85, 0.15, 0.12))
    for L, t in zip(lines, row_tops(lines)):
        y = pad + t
        x = pad + {"left": 0, "right": maxw - L.ink_w, "center": (maxw - L.ink_w) / 2}[align]
        if L.boxed:
            m = box_mask(L.ink_w + 2 * bx, L.ink_h + 2 * by, L.size * 0.12)
            over(lay, gblur(np.pad(m, int(L.size * 0.3)), L.size * 0.12), x - bx - int(L.size * 0.3) + L.size * 0.06,
                 y + L.top - by - int(L.size * 0.3) + L.size * 0.08, (0, 0, 0), 0.6)
            over(lay, m, x - bx, y + L.top - by, accent)
        L.paint(lay, x, y, J)
    lay.ink = (pad, pad, maxw, blk)
    return lay, pad


def pill_layer(text, fontfile, size, bg, fg):
    """text on a rounded filled box with a soft shadow"""
    m = text_mask(text, fontfile, size)[0]
    px, py = size * 0.45, size * 0.28
    w, h = m.shape[1] + 2 * px, m.shape[0] + 2 * py
    sp = int(size * 0.4)
    L = Layer(w + 2 * sp, h + 2 * sp)
    bm = box_mask(w, h, size * 0.18)
    over(L, gblur(np.pad(bm, sp), size * 0.15), size * 0.06, size * 0.08, (0, 0, 0), 0.55)
    over(L, bm, sp, sp, bg)
    over(L, m, sp + px, sp + py, fg)
    L.ink = (sp, sp, w, h)
    return L


def logo_layer(path, h):
    from PIL import Image
    lg = Image.open(path).convert("RGBA")
    h = max(16, int(h))
    lg = lg.resize((max(1, int(lg.width * h / lg.height)), h), Image.LANCZOS)
    la = np.asarray(lg, np.float32) / 255
    sp = int(h * 0.12)
    L = Layer(lg.width + 2 * sp, h + 2 * sp)
    over(L, gblur(np.pad(la[..., 3], sp), h * 0.04), h * 0.02, h * 0.03, (0, 0, 0), 0.5)
    over(L, la[..., 3], sp, sp, la[..., :3])
    L.ink = (sp, sp, lg.width, h)
    return L


def name_layer(text, fontfile, size):
    nm = text_mask(text, fontfile, size)[0]
    ed, pd = dilate(nm, size * 0.12)
    sp = pd + int(size * 0.3)
    L = Layer(nm.shape[1] + 2 * sp, nm.shape[0] + 2 * sp)
    over(L, gblur(np.pad(ed, sp - pd), size * 0.1), 0, size * 0.05, (0, 0, 0), 0.6)
    over(L, ed, sp - pd, sp - pd, (0, 0, 0), 0.85)
    over(L, nm, sp, sp, (1, 1, 1))
    L.ink = (sp, sp, nm.shape[1], nm.shape[0])
    return L


def elements(J, W, H):
    """every movable piece as (name, Layer, x, y) = its top-left; J["pos"][name] = {cx, cy, s} moves / sizes it
    (cx, cy = the piece's centre as a share of the width / height)"""
    pos = J.get("pos") or {}
    S = lambda n: min(3.0, max(0.3, float((pos.get(n) or {}).get("s") or 1)))
    def place(n, L, x, y):
        p = pos.get(n) or {}
        if p.get("cx") is not None and p.get("cy") is not None:
            x, y = float(p["cx"]) * W - L.shape[1] / 2, float(p["cy"]) * H - L.shape[0] / 2
        return (n, L, x, y)
    fontfile = font_path(J.get("font")) or font_path("Nirmala.ttc")
    accent = rgb(J.get("accent"), (0.85, 0.15, 0.12))
    on_acc = ink_color(accent)
    lay = J.get("layout") or "bottom"
    box, align, _ = title_box(J, W, H)
    out = []
    T, pad = title_layer(J, W, H, S("title"))
    tink = None
    if T is not None:
        _, _, iw, ih = T.ink
        ix = {"left": box[0], "right": box[2] - iw, "center": (W - iw) / 2}[align]
        iy = box[3] - ih if lay == "bottom" else box[1] + (box[3] - box[1] - ih) / 2
        e = place("title", T, ix - pad, iy - pad)
        out.append(e)
        tink = (e[2] + pad, e[3] + pad, iw, ih)
    kicker = " ".join(str(J.get("kicker") or "").split())
    if kicker:                                       # small line above the title, on an accent box
        K = pill_layer(kicker, fontfile, H * 0.045 * S("kicker"), accent, on_acc)
        kx0, ky0, kw, kh = K.ink
        tx, ty, tw, th = tink or (box[0], box[1] + H * 0.1, box[2] - box[0], 0)
        kx = {"left": tx, "right": tx + tw - kw, "center": tx + (tw - kw) / 2}[align]
        out.append(place("kicker", K, kx - kx0, max(H * 0.03, ty - kh - H * 0.03) - ky0))
    m = int(H * 0.035)
    taken = set()
    logo, name = J.get("logo") or {}, J.get("name") or {}
    lc = logo.get("corner") if logo.get("corner") in CORNERS else "top-right"
    lbox = None
    if logo.get("path") and os.path.exists(logo["path"]):
        L = logo_layer(logo["path"], H * float(logo.get("size") or 0.2) * S("logo"))
        x0, y0, lw, lh = L.ink
        e = place("logo", L, (m if "left" in lc else W - lw - m) - x0, (m if "top" in lc else H - lh - m) - y0)
        out.append(e)
        lbox = (e[2] + x0, e[3] + y0, lw, lh)
        taken.add(lc)
    if name.get("text"):
        nf = font_path(name.get("font")) or fontfile
        if lbox:                                     # the channel name just under / over the logo
            N = name_layer(name["text"], nf, H * 0.036 * S("name"))
            x0, y0, nw, nh = N.ink
            nx = min(W - m - nw, max(m, lbox[0] + lbox[2] / 2 - nw / 2))
            ny = lbox[1] + lbox[3] + H * 0.012 if lbox[1] < H / 2 else lbox[1] - nh - H * 0.012
            out.append(place("name", N, nx - x0, ny - y0))
        else:                                        # no logo: the channel name as a small label in that corner
            N = pill_layer(name["text"], nf, H * 0.045 * S("name"), (0.06, 0.06, 0.07), (1, 1, 1))
            x0, y0, nw, nh = N.ink
            out.append(place("name", N, (m if "left" in lc else W - nw - m) - x0, (m if "top" in lc else H - nh - m) - y0))
            taken.add(lc)
    badge = J.get("badge") or {}
    if badge.get("text"):                            # the series word (भय ...) on its colour, in a free top corner
        bc = "top-left" if "top-left" not in taken else ("top-right" if "top-right" not in taken else "bottom-left")
        B = pill_layer(badge["text"], font_path(badge.get("font")) or fontfile, H * 0.06 * S("badge"), accent, on_acc)
        x0, y0, bw, bh = B.ink
        out.append(place("badge", B, (m if "left" in bc else W - bw - m) - x0, (m if "top" in bc else H - bh - m) - y0))
    return out


def background(J, W, H):
    a = load_picture(J["image"], W, H, J.get("focus", 0.5))
    a = punch(grade(a, J.get("grade") or ""), J.get("punch", 0))
    a = vignette(shade(a, J.get("layout") or "bottom", J.get("shade", 0.7)), float(J.get("vignette", 0.35) or 0))
    return np.ascontiguousarray(a, np.float32)


def save_rgb(img, out, quality=92):
    from PIL import Image
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    im = Image.fromarray((np.clip(img, 0, 1) * 255 + 0.5).astype(np.uint8))
    tmp = out + ".part" + os.path.splitext(out)[1]
    if out.lower().endswith(".png"):
        im.save(tmp, optimize=True)
    else:
        im.save(tmp, "JPEG", quality=int(quality), optimize=True)
    os.replace(tmp, out)


def render(J):
    W, H = int(J.get("w") or 1920), int(J.get("h") or 1080)
    img = background(J, W, H)
    frame(img, rgb(J.get("frame_color") or J.get("accent"), (0.85, 0.15, 0.12)), J.get("frame", 0))
    for n, L, x, y in elements(J, W, H):             # (same order as the page's preview: pieces over the border)
        put(img, L, x, y)
    save_rgb(img, J["out"], J.get("quality") or 92)
    return {}


def render_layers(J):
    """for the page's drag and drop: the background (with the frame) and every piece as its own PNG"""
    from PIL import Image
    W, H = int(J.get("w") or 960), int(J.get("h") or 540)
    d = J["layers"]
    os.makedirs(d, exist_ok=True)
    img = background(J, W, H)
    frame(img, rgb(J.get("frame_color") or J.get("accent"), (0.85, 0.15, 0.12)), J.get("frame", 0))
    save_rgb(img, os.path.join(d, "bg.jpg"), 85)
    els = []
    for n, L, x, y in elements(J, W, H):
        a = np.clip(L.a, 0, 1)
        c = np.where(a[..., None] > 1e-4, L.rgb / np.maximum(a[..., None], 1e-4), 0)
        arr = np.dstack([np.clip(c, 0, 1), a])
        Image.fromarray((arr * 255 + 0.5).astype(np.uint8), "RGBA").save(os.path.join(d, n + ".png"), optimize=False)
        h, w = L.shape
        els.append({"name": n, "x": x / W, "y": y / H, "w": w / W, "h": h / H,
                    "cx": (x + w / 2) / W, "cy": (y + h / 2) / H})
    return {"elements": els}


def main():
    spec = json.load(open(sys.argv[1], encoding="utf-8"))
    for J in spec if isinstance(spec, list) else [spec]:
        try:
            extra = render_layers(J) if J.get("layers") else render(J)
            print(json.dumps(dict({"out": J.get("out") or J.get("layers"), "ok": True}, **extra), ensure_ascii=False), flush=True)
        except Exception as e:
            print(json.dumps({"out": J.get("out"), "ok": False, "error": f"{type(e).__name__}: {e}"},
                             ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
