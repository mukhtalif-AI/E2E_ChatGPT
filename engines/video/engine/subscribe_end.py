"""subscribe_end.py - the short "Subscribe + bell" ending drawn over the last seconds of a video.

A red SUBSCRIBE button pops in with a bell beside it, a pointer glides in and clicks it (it turns grey,
SUBSCRIBED), then clicks the bell, which swings and rings; everything fades out. All the art is drawn here
with PIL (no downloaded animation, so no licence to track); the click and bell sounds are made in
app/tools.py (_subscribe_sounds) at the same times.

    plan["subscribe"] = {"start": seconds, "dur": 6.5, "sub_click": 2.2, "bell_click": 3.8}
"""
import math
import os

import numpy as np

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SS = 4                                                    # draw 4x larger, then shrink: smooth edges
RED, GREY, GOLD = (229, 32, 32), (72, 72, 72), (255, 204, 64)
# backdrop per series mood: (colour of the bright parts, colour of the dark parts, glow behind the buttons)
BACKDROP = {
    "horror":   ((95, 14, 16), (8, 2, 3), (120, 10, 12)),         # Bhay: blood red into black
    "thriller": ((22, 52, 70), (3, 6, 9), (30, 80, 105)),         # Rahasya: cold midnight teal
    "warm":     ((105, 62, 24), (9, 5, 2), (150, 90, 30)),        # Kisse: warm amber, like a lamp in a dark room
    "romance":  ((100, 22, 46), (8, 2, 5), (150, 40, 80)),        # Romanch: deep wine and rose
    "soft":     ((58, 42, 88), (5, 4, 9), (90, 70, 140)),         # Kareeb: dusky violet
    "clean":    ((20, 50, 100), (2, 5, 11), (40, 100, 170)),      # Gyaan: deep blue
}


def _font(size):
    from PIL import ImageFont
    for p in (os.path.join(HERE, "assets", "fonts", "Biryani-Black.ttf"), r"C:\Windows\Fonts\arialbd.ttf",
              r"C:\Windows\Fonts\segoeuib.ttf"):
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


def _rrect(dr, box, r, fill):
    try:
        dr.rounded_rectangle(box, r, fill=fill)
    except AttributeError:                                # old Pillow
        x0, y0, x1, y1 = box
        dr.rectangle((x0 + r, y0, x1 - r, y1), fill=fill)
        dr.rectangle((x0, y0 + r, x1, y1 - r), fill=fill)
        for cx, cy in ((x0 + r, y0 + r), (x1 - r, y0 + r), (x0 + r, y1 - r), (x1 - r, y1 - r)):
            dr.ellipse((cx - r, cy - r, cx + r, cy + r), fill=fill)


def _finish(im, shadow=True):
    """4x RGBA drawing -> 1x premultiplied float array (h, w, 4) with a soft shadow under it"""
    from PIL import Image, ImageFilter
    w, h = im.size
    pad = 12 * SS
    big = Image.new("RGBA", (w + 2 * pad, h + 2 * pad), (0, 0, 0, 0))
    if shadow:
        a = im.split()[3].filter(ImageFilter.GaussianBlur(5 * SS)).point(lambda v: int(v * 0.6))
        big.paste(Image.new("RGBA", (w, h), (0, 0, 0, 255)), (pad, pad + 4 * SS), a)
    big.alpha_composite(im, (pad, pad))
    small = big.resize(((w + 2 * pad) // SS, (h + 2 * pad) // SS), Image.LANCZOS)
    a = np.asarray(small, np.float32) / 255
    a[..., :3] *= a[..., 3:4]
    return a


def _button(text, col, txt_col, k):
    from PIL import Image, ImageDraw
    f = _font(int(38 * k * SS))
    tw = int(f.getlength(text)) if hasattr(f, "getlength") else f.getsize(text)[0]
    w, h = max(int(330 * k * SS), tw + int(76 * k * SS)), int(92 * k * SS)
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    dr = ImageDraw.Draw(im)
    _rrect(dr, (0, 0, w - 1, h - 1), int(16 * k * SS), col + (255,))
    bb = dr.textbbox((0, 0), text, font=f) if hasattr(dr, "textbbox") else (0, 0, tw, f.getsize(text)[1])
    dr.text(((w - (bb[2] - bb[0])) / 2 - bb[0], (h - (bb[3] - bb[1])) / 2 - bb[1]), text, font=f, fill=txt_col)
    return _finish(im)


def _bell(col, rung, k):
    """a round dark badge with a bell in it; rung = gold bell with sound waves"""
    from PIL import Image, ImageDraw
    s = int(100 * k * SS)
    im = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    dr = ImageDraw.Draw(im)
    dr.ellipse((0, 0, s - 1, s - 1), fill=(32, 32, 32, 235))
    u = s / 100.0
    c = col + (255,)
    dr.ellipse((46 * u, 18 * u, 54 * u, 26 * u), fill=c)                              # knob
    dr.pieslice((29 * u, 22 * u, 71 * u, 64 * u), 180, 360, fill=c)                     # dome
    dr.polygon([(29 * u, 43 * u), (71 * u, 43 * u), (77 * u, 66 * u), (23 * u, 66 * u)], fill=c)   # skirt
    _rrect(dr, (19 * u, 64 * u, 81 * u, 71 * u), int(3 * u), c)                       # rim
    dr.ellipse((43 * u, 71 * u, 57 * u, 81 * u), fill=c)                              # clapper
    if rung:
        for r in (0, 1):
            o = 9 * r * u
            dr.arc((10 * u - o, 26 * u - o, 34 * u - o, 62 * u + o), 120, 240, fill=c, width=int(4 * u))
            dr.arc((66 * u + o, 26 * u - o, 90 * u + o, 62 * u + o), 300, 60, fill=c, width=int(4 * u))
    return _finish(im)


def _pointer(k):
    from PIL import Image, ImageDraw
    s = int(72 * k * SS)
    im = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    dr = ImageDraw.Draw(im)
    u = s / 72.0 * 1.2
    pts = [(4, 2), (4, 46), (15, 36), (23, 54), (31, 50), (23, 33), (38, 33)]
    dr.polygon([(x * u, y * u) for x, y in pts], fill=(20, 20, 20, 255))
    inner = [(8, 11), (8, 37), (16, 30), (24, 47), (27, 45), (19, 29), (29, 29)]
    dr.polygon([(x * u, y * u) for x, y in inner], fill=(255, 255, 255, 255))
    return _finish(im)


def _ease(p):
    """smootherstep: starts and stops gently"""
    p = min(1.0, max(0.0, p))
    return p * p * p * (p * (6 * p - 15) + 10)


def _pop(p):
    """0 -> a small overshoot -> 1 (gentle ease-out-back)"""
    p = min(1.0, max(0.0, p))
    c = 0.9
    return 1 + (c + 1) * (p - 1) ** 3 + c * (p - 1) ** 2


def _bump(u, at, d=0.3):
    """a soft press: 0 -> 1 -> 0 over d seconds"""
    return 0.5 - 0.5 * math.cos(2 * math.pi * (u - at) / d) if 0 <= u - at < d else 0.0


def _mix(u, at, d=0.3):
    """0 before at, 1 after at + d, smooth in between (cross-fade of two looks)"""
    return _ease((u - at) / d)


class SubscribeEnd:
    BG_IN = 1.8                       # the picture fades into the blurred dark-red background this long before the buttons

    def __init__(self, spec, W, H):
        self.t0 = float(spec["start"])
        self.dur = float(spec.get("dur") or 6.5)
        self.c1 = float(spec.get("sub_click") or 2.2)
        self.c2 = float(spec.get("bell_click") or 3.8)
        self.W, self.H = W, H
        k = min(W, H) / 1080.0 * 1.25                     # size relative to a 1080 p frame
        self.k = k
        self.sub = _button("SUBSCRIBE", RED, (255, 255, 255), k)
        self.subd = _button("SUBSCRIBED", GREY, (225, 225, 225), k)
        self.bell = _bell((255, 255, 255), False, k)
        self.bell_on = _bell(GOLD, True, k)
        self.ptr = _pointer(k)
        gap = 26 * k
        bw, ww = self.sub.shape[1] - 24, self.bell.shape[1] - 24       # without the shadow padding
        x0 = (W - (bw + gap + ww)) / 2
        cy = H * (0.50 if W > H else 0.58)
        self.pos_sub = (x0 + bw / 2, cy)
        self.pos_bell = (x0 + bw + gap + ww / 2, cy)
        self._rot = {}
        self.bg = None

    def background(self, image_path, vig=None, mood="horror"):
        """the last picture, blurred smooth and graded dark in the series' colour (Bhay: almost black-red), with a
        soft glow behind the buttons: the still backdrop for the ending"""
        from PIL import Image, ImageFilter
        W, H = self.W, self.H
        im = Image.open(image_path).convert("RGB")
        w, h = im.size
        sc = max(W / w, H / h)                                     # cover the frame, centred
        cw, ch = W / sc, H / sc
        im = im.crop((int((w - cw) / 2), int((h - ch) / 2), int((w + cw) / 2), int((h + ch) / 2)))
        small = im.resize((max(8, W // 5), max(8, H // 5)), Image.BILINEAR).filter(ImageFilter.GaussianBlur(3.5))
        big = small.resize((W, H), Image.BICUBIC).filter(ImageFilter.GaussianBlur(5))
        a = np.asarray(big, np.float32) / 255
        lum = a @ np.array([0.3, 0.59, 0.11], np.float32)
        lum = np.clip(lum / max(1e-3, float(np.percentile(lum, 99))), 0, 1) ** 0.8
        tint, base, glow = (np.array(c, np.float32) for c in BACKDROP.get(mood, BACKDROP["horror"]))
        rgb = lum[..., None] * tint + base
        rgb = rgb * 0.75 + a * 255 * 0.12                             # a little of the picture's own colour stays
        yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
        cx, cy = (self.pos_sub[0] + self.pos_bell[0]) / 2, self.pos_sub[1]
        r = np.exp(-(((xx - cx) / (W * 0.30)) ** 2 + ((yy - cy) / (H * 0.30)) ** 2))
        rgb += r[..., None] * glow * 0.45                             # draws the eye to the buttons
        if vig is not None:
            rgb *= np.asarray(vig, np.float32)[..., None]
        rgb += np.random.default_rng(1).uniform(-0.5, 0.5, rgb.shape).astype(np.float32)   # fixed dither: no bands
        self.bg = np.clip(rgb + 0.5, 0, 255).astype(np.uint8)

    def bg_mix(self, t):
        """0 = the story picture, 1 = the blurred backdrop (eased over BG_IN seconds before the buttons)"""
        return _ease((t - (self.t0 - self.BG_IN)) / self.BG_IN) if self.bg is not None else 0.0

    def active(self, t):
        return self.t0 <= t <= self.t0 + self.dur

    def backdrop(self, out, t, length):
        """fade the frame into the backdrop; the backdrop itself fades to black in the last 0.8 s"""
        q = self.bg_mix(t)
        if q <= 0:
            return out
        e = min(1.0, max(0.0, (length - t) / 0.8))
        return (out.astype(np.float32) * (1 - q) + self.bg.astype(np.float32) * (q * e) + 0.5).astype(np.uint8)

    def _paste(self, img, spr, cx, cy, alpha, scale=1.0, angle=0.0, key=None):
        if alpha <= 0.01 or scale <= 0.02:
            return
        if abs(scale - 1) > 0.01 or abs(angle) > 0.5:
            from PIL import Image
            ck = (key, round(scale, 3), round(angle * 2) / 2)
            a = self._rot.get(ck)
            if a is None:
                h, w = spr.shape[:2]
                chans = [Image.fromarray(spr[..., i]) for i in range(4)]       # float 'F' images
                nw, nh = max(1, int(w * scale)), max(1, int(h * scale))
                chans = [c.resize((nw, nh), Image.BILINEAR) for c in chans]
                if abs(angle) > 0.5:
                    chans = [c.rotate(angle, Image.BILINEAR, expand=True) for c in chans]
                a = np.dstack([np.asarray(c, np.float32) for c in chans])
                if len(self._rot) < 600:
                    self._rot[ck] = a
            spr = a
        h, w = spr.shape[:2]
        x, y = int(round(cx - w / 2)), int(round(cy - h / 2))
        x0, y0, x1, y1 = max(0, x), max(0, y), min(self.W, x + w), min(self.H, y + h)
        if x1 <= x0 or y1 <= y0:
            return
        s = spr[y0 - y:y1 - y, x0 - x:x1 - x] * np.float32(alpha)
        reg = img[y0:y1, x0:x1].astype(np.float32)
        reg = reg * (1 - s[..., 3:4]) + s[..., :3] * 255
        img[y0:y1, x0:x1] = np.clip(reg + 0.5, 0, 255).astype(np.uint8)

    def draw(self, img, t):
        if not self.active(t):
            return img
        u = t - self.t0
        fade = _ease((self.dur - u) / 0.9)
        c1, c2 = self.c1, self.c2
        # button: grows in softly, a gentle press on the click, then cross-fades to grey SUBSCRIBED
        sa = _ease(u / 0.5) * fade
        ss = (0.85 + 0.15 * _pop(u / 0.7)) * (1 - 0.05 * _bump(u, c1))
        m = _mix(u, c1 + 0.05)
        if m > 0:                                                         # new look solid underneath, old one fades off it
            self._paste(img, self.subd, *self.pos_sub, sa, ss, key="subd")
        self._paste(img, self.sub, *self.pos_sub, sa * (1 - m), ss, key="sub")
        # bell: a moment later; on its click it cross-fades to gold and swings slowly to rest
        ba = _ease((u - 0.25) / 0.5) * fade
        bs = (0.85 + 0.15 * _pop((u - 0.25) / 0.7)) * (1 - 0.06 * _bump(u, c2))
        x = u - c2 - 0.05
        ang = 14 * math.exp(-x * 1.6) * math.sin(2 * math.pi * 2.2 * x) if 0 < x < 3 else 0.0
        m = _mix(u, c2 + 0.05)
        if m > 0:
            self._paste(img, self.bell_on, *self.pos_bell, ba, bs, ang, key="bell_on")
        self._paste(img, self.bell, *self.pos_bell, ba * (1 - m), bs, ang, key="bell")
        # pointer: glides to the button, clicks, glides to the bell, clicks, drifts away
        k = self.k
        start = (self.W * 0.5 + 520 * k, self.pos_sub[1] + 330 * k)
        tip = lambda p: (p[0] + 14 * k, p[1] + 18 * k)                   # sprite centre sits below-right of the tip
        on_sub = (self.pos_sub[0] + 70 * k, self.pos_sub[1] + 6 * k)
        on_bell = (self.pos_bell[0] + 4 * k, self.pos_bell[1] + 6 * k)
        away = (on_bell[0] + 200 * k, on_bell[1] + 150 * k)
        lerp = lambda a, b, p: (a[0] + (b[0] - a[0]) * p, a[1] + (b[1] - a[1]) * p)
        if u < c1 - 0.15:
            pos = lerp(start, on_sub, _ease((u - 0.9) / (c1 - 1.05)))
        elif u < c1 + 0.45:
            pos = on_sub
        elif u < c2 - 0.15:
            pos = lerp(on_sub, on_bell, _ease((u - c1 - 0.45) / max(0.1, c2 - c1 - 0.6)))
        elif u < c2 + 0.5:
            pos = on_bell
        else:
            pos = lerp(on_bell, away, _ease((u - c2 - 0.5) / 1.1))
        pa = min(_ease((u - 0.9) / 0.4), _ease((c2 + 1.6 - u) / 0.6)) * fade
        psc = 1 - 0.12 * (_bump(u, c1 - 0.1) + _bump(u, c2 - 0.1))
        self._paste(img, self.ptr, *tip(pos), pa, psc, key="ptr")
        return img
