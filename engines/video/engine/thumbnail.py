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


def over(img, mask, x, y, color, alpha=1.0):
    """paint color through mask (0..1) onto img (HxWx3 float) with its top-left at (x, y)"""
    H, W = img.shape[:2]
    h, w = mask.shape
    x, y = int(round(x)), int(round(y))
    x0, y0, x1, y1 = max(0, x), max(0, y), min(W, x + w), min(H, y + h)
    if x1 <= x0 or y1 <= y0:
        return
    a = mask[y0 - y:y1 - y, x0 - x:x1 - x, None] * float(alpha)
    img[y0:y1, x0:x1] = img[y0:y1, x0:x1] * (1 - a) + np.array(color, np.float32) * a


class Line:
    """one row of title letters with its outline, glow and shadow, ready to paint"""

    def __init__(self, text, fontfile, size, color, J):
        ink, self.top = text_mask(text, fontfile, size)
        ow = float(J.get("outline_w", 0.08)) * size
        edge, p = dilate(ink, ow)
        self.pad = int(p + size * 0.5)               # room for the glow and shadow to fade out
        self.edge = np.pad(edge, self.pad - p)
        self.fill = np.pad(ink, self.pad)
        self.color = color
        self.size = size
        self.pitch = size * 1.12                     # this row's share of the block height
        self.ink_w, self.ink_h = ink.shape[1], ink.shape[0]

    def paint(self, img, x, y, J):
        """(x, y) = left of the ink, top of the row"""
        x, y = x - self.pad, y + self.top - self.pad
        glow = float(J.get("glow", 0) or 0)
        if glow > 0:
            g = gblur(self.edge, self.size * 0.2)
            over(img, np.clip(g * 1.5, 0, 1), x, y, rgb(J.get("accent"), (0.8, 0.1, 0.1)), glow)
        sh = gblur(self.edge, self.size * 0.06)
        over(img, sh, x + self.size * 0.05, y + self.size * 0.06, (0, 0, 0), 0.75)
        if J.get("outline_w", 0.08):
            over(img, self.edge, x, y, rgb(J.get("outline"), (0, 0, 0)))
        over(img, self.fill, x, y, self.color)


def block_height(lines):
    """from the first row's ink top to the last row's ink bottom"""
    if not lines:
        return 0
    return sum(L.pitch for L in lines[:-1]) + lines[-1].top + lines[-1].ink_h - lines[0].top


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


# ------------------------------------------------------------------ the layout
def title_block(J, W, H):
    """the title rows as Line objects, sized to fit the layout's box; returns (lines, box, align)"""
    lay = J.get("layout") or "bottom"
    fontfile = font_path(J.get("font")) or font_path("Nirmala.ttc")
    title = " ".join(str(J.get("title") or "").split())
    sub = " ".join(str(J.get("sub") or "").split())
    if lay == "bottom":
        box, align, rows = (0.05 * W, 0.40 * H, 0.95 * W, 0.93 * H), "center", 2
    elif lay == "left":
        box, align, rows = (0.05 * W, 0.14 * H, 0.58 * W, 0.90 * H), "left", 3
    elif lay == "right":
        box, align, rows = (0.42 * W, 0.14 * H, 0.95 * W, 0.90 * H), "right", 3
    else:
        box, align, rows = (0.07 * W, 0.18 * H, 0.93 * W, 0.86 * H), "center", 2
    bw, bh = box[2] - box[0], box[3] - box[1]
    color, accent = rgb(J.get("color")), rgb(J.get("accent"), (0.85, 0.15, 0.12))
    size = H * 0.19 * float(J.get("size") or 1.0)
    while True:
        main = wrap(title, fontfile, size, bw, rows) if title else []
        subrows = wrap(sub, fontfile, size * 0.56, bw, 2) if sub else []
        if main is not None and subrows is not None:
            main = balance(main, fontfile, size, bw)
            subrows = balance(subrows, fontfile, size * 0.56, bw)
            lines = [Line(t, fontfile, size, color, J) for t in main] + \
                    [Line(t, fontfile, size * 0.56, accent, J) for t in subrows]
            if block_height(lines) <= bh or size < H * 0.05:
                return lines, box, align
        if size < H * 0.05:
            return [], box, align
        size *= 0.93


def pill(img, text, fontfile, size, x, y, bg, fg, anchor="left"):
    """text on a filled box (badge, small line above the title); returns the box height"""
    m = text_mask(text, fontfile, size)[0]
    px, py = size * 0.45, size * 0.28
    w, h = m.shape[1] + 2 * px, m.shape[0] + 2 * py
    if anchor == "right":
        x -= w
    elif anchor == "center":
        x -= w / 2
    H, W = img.shape[:2]
    x0, y0, x1, y1 = int(max(0, x)), int(max(0, y)), int(min(W, x + w)), int(min(H, y + h))
    sh = np.zeros((H, W), np.float32)
    sh[y0:y1, x0:x1] = 1
    over(img, gblur(sh, size * 0.15), size * 0.06, size * 0.08, (0, 0, 0), 0.55)
    img[y0:y1, x0:x1] = np.array(bg, np.float32)
    over(img, m, x + px, y + py, fg)
    return h


def render(J):
    from PIL import Image
    W, H = int(J.get("w") or 1920), int(J.get("h") or 1080)
    lay = J.get("layout") or "bottom"
    a = load_picture(J["image"], W, H, J.get("focus", 0.5))
    a = grade(a, J.get("grade") or "")
    a = vignette(shade(a, lay, J.get("shade", 0.7)), float(J.get("vignette", 0.35) or 0))
    img = np.ascontiguousarray(a, np.float32)
    fontfile = font_path(J.get("font")) or font_path("Nirmala.ttc")
    accent = rgb(J.get("accent"), (0.85, 0.15, 0.12))

    lines, box, align = title_block(J, W, H)
    total = block_height(lines)
    y = box[3] - total if lay == "bottom" else box[1] + (box[3] - box[1] - total) / 2
    y -= lines[0].top if lines else 0               # y = top of the first row, its ink starts lower
    kicker = " ".join(str(J.get("kicker") or "").split())
    if kicker:                                     # small line above the title, on an accent box
        ks = H * 0.045
        kx = {"left": box[0], "right": box[2], "center": (box[0] + box[2]) / 2}[align]
        kh = text_mask(kicker, fontfile, ks)[0].shape[0] + ks * 0.56
        ky = max(H * 0.03, y + (lines[0].top if lines else 0) - kh - H * 0.03)
        pill(img, kicker, fontfile, ks, kx, ky, accent, (1, 1, 1) if sum(accent) < 2.2 else (0, 0, 0), align)
    for L in lines:
        x = {"left": box[0], "right": box[2] - L.ink_w, "center": (W - L.ink_w) / 2}[align]
        L.paint(img, x, y, J)
        y += L.pitch

    m = int(H * 0.035)
    taken = set()
    logo = J.get("logo") or {}
    name = J.get("name") or {}
    lc = logo.get("corner") if logo.get("corner") in CORNERS else "top-right"
    if logo.get("path") and os.path.exists(logo["path"]):
        lg = Image.open(logo["path"]).convert("RGBA")
        h = max(16, int(H * float(logo.get("size") or 0.2)))
        lg = lg.resize((max(1, int(lg.width * h / lg.height)), h), Image.LANCZOS)
        la = np.asarray(lg, np.float32) / 255
        lx = m if "left" in lc else W - lg.width - m
        ly = m if "top" in lc else H - h - m
        over(img, gblur(la[..., 3], h * 0.04), lx + h * 0.02, ly + h * 0.03, (0, 0, 0), 0.5)
        y0, x0 = max(0, ly), max(0, lx)
        part = la[y0 - ly:min(H, ly + h) - ly, x0 - lx:min(W, lx + lg.width) - lx]
        al = part[..., 3:4]
        img[y0:y0 + part.shape[0], x0:x0 + part.shape[1]] = img[y0:y0 + part.shape[0], x0:x0 + part.shape[1]] * (1 - al) + part[..., :3] * al
        taken.add(lc)
        if name.get("text"):                         # the channel name just under / over the logo
            nf = font_path(name.get("font")) or fontfile
            ns = H * 0.036
            nm = text_mask(name["text"], nf, ns)[0]
            ed, pd = dilate(nm, ns * 0.12)
            nx = lx + lg.width / 2 - nm.shape[1] / 2
            nx = min(W - m - nm.shape[1], max(m, nx))
            ny = ly + h + ns * 0.3 if "top" in lc else ly - nm.shape[0] - ns * 0.3
            over(img, ed, nx - pd, ny - pd, (0, 0, 0), 0.85)
            over(img, nm, nx, ny, (1, 1, 1))
    elif name.get("text"):                           # no logo: the channel name as a small label in that corner
        nf = font_path(name.get("font")) or fontfile
        ns = H * 0.045
        nx = m if "left" in lc else W - m
        ny = m if "top" in lc else H - m - ns * 1.6
        pill(img, name["text"], nf, ns, nx, ny, (0.06, 0.06, 0.07), (1, 1, 1), "left" if "left" in lc else "right")
        taken.add(lc)
    badge = J.get("badge") or {}
    if badge.get("text"):                            # the series word (भय ...) on its colour, in a free top corner
        bc = "top-left" if "top-left" not in taken else ("top-right" if "top-right" not in taken else "bottom-left")
        bf = font_path(badge.get("font")) or fontfile
        bs = H * 0.06
        bx = m if "left" in bc else W - m
        by = m if "top" in bc else H - m - bs * 1.8
        pill(img, badge["text"], bf, bs, bx, by, accent, (1, 1, 1) if sum(accent) < 2.2 else (0, 0, 0),
             "left" if "left" in bc else "right")

    out = J["out"]
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    im = Image.fromarray((np.clip(img, 0, 1) * 255 + 0.5).astype(np.uint8))
    tmp = out + ".part" + os.path.splitext(out)[1]
    if out.lower().endswith(".png"):
        im.save(tmp, optimize=True)
    else:
        im.save(tmp, "JPEG", quality=int(J.get("quality") or 92), optimize=True)
    os.replace(tmp, out)
    return out


def main():
    spec = json.load(open(sys.argv[1], encoding="utf-8"))
    for J in spec if isinstance(spec, list) else [spec]:
        try:
            render(J)
            print(json.dumps({"out": J.get("out"), "ok": True}, ensure_ascii=False), flush=True)
        except Exception as e:
            print(json.dumps({"out": J.get("out"), "ok": False, "error": f"{type(e).__name__}: {e}"},
                             ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
