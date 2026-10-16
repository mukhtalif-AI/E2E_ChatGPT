"""subscribe_end.py - the short "Subscribe + bell" ending drawn over the last seconds of a video.

A red SUBSCRIBE button pops in with a bell beside it, a pointer glides in and clicks it (it turns grey,
SUBSCRIBED), then clicks the bell, which swings and rings; everything fades out. All the art is drawn here
with PIL (no downloaded animation, so no licence to track); the click and bell sounds are made in
app/tools.py (_subscribe_sounds) at the same times.

    plan["subscribe"] = {"start": seconds, "dur": 5.5, "sub_click": 1.3, "bell_click": 2.6}
"""
import math
import os

import numpy as np

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SS = 4                                                    # draw 4x larger, then shrink: smooth edges
RED, GREY, GOLD = (229, 32, 32), (72, 72, 72), (255, 204, 64)


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
    p = min(1.0, max(0.0, p))
    return p * p * (3 - 2 * p)


def _pop(p):
    """0 -> overshoot -> 1 (ease-out-back)"""
    p = min(1.0, max(0.0, p))
    c = 1.9
    return 1 + (c + 1) * (p - 1) ** 3 + c * (p - 1) ** 2


def _bump(u, at, d=0.18):
    return math.sin(math.pi * (u - at) / d) if 0 <= u - at < d else 0.0


class SubscribeEnd:
    def __init__(self, spec, W, H):
        self.t0 = float(spec["start"])
        self.dur = float(spec.get("dur") or 5.5)
        self.c1 = float(spec.get("sub_click") or 1.3)
        self.c2 = float(spec.get("bell_click") or 2.6)
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

    def active(self, t):
        return self.t0 <= t <= self.t0 + self.dur

    def _paste(self, img, spr, cx, cy, alpha, scale=1.0, angle=0.0, key=None):
        if alpha <= 0.01 or scale <= 0.02:
            return
        if abs(scale - 1) > 0.01 or abs(angle) > 0.5:
            from PIL import Image
            ck = (key, round(scale, 2), round(angle))
            a = self._rot.get(ck)
            if a is None:
                h, w = spr.shape[:2]
                chans = [Image.fromarray(spr[..., i]) for i in range(4)]       # float 'F' images
                nw, nh = max(1, int(w * scale)), max(1, int(h * scale))
                chans = [c.resize((nw, nh), Image.BILINEAR) for c in chans]
                if abs(angle) > 0.5:
                    chans = [c.rotate(angle, Image.BILINEAR, expand=True) for c in chans]
                a = np.dstack([np.asarray(c, np.float32) for c in chans])
                if len(self._rot) < 400:
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
        fade = min(1.0, (self.dur - u) / 0.6)
        c1, c2 = self.c1, self.c2
        # button: pops in, squashes on the click, turns grey
        sa = min(1.0, u / 0.25) * fade
        ss = _pop(u / 0.45) * (1 - 0.07 * _bump(u, c1))
        self._paste(img, self.subd if u >= c1 + 0.06 else self.sub, *self.pos_sub, sa, ss,
                    key="subd" if u >= c1 + 0.06 else "sub")
        # bell: pops in a moment later, swings and turns gold when clicked
        ba = min(1.0, max(0.0, (u - 0.15) / 0.25)) * fade
        bs = _pop((u - 0.15) / 0.45) * (1 - 0.08 * _bump(u, c2))
        rung = u >= c2 + 0.05
        ang = 24 * math.exp(-(u - c2) * 2.4) * math.sin(2 * math.pi * 4.2 * (u - c2)) if rung else 0.0
        self._paste(img, self.bell_on if rung else self.bell, *self.pos_bell, ba, bs, ang,
                    key="bell_on" if rung else "bell")
        # pointer: glides to the button, clicks, glides to the bell, clicks, leaves
        k = self.k
        start = (self.W * 0.5 + 520 * k, self.pos_sub[1] + 330 * k)
        tip = lambda p: (p[0] + 14 * k, p[1] + 18 * k)                   # sprite centre sits below-right of the tip
        on_sub = (self.pos_sub[0] + 70 * k, self.pos_sub[1] + 6 * k)
        on_bell = (self.pos_bell[0] + 4 * k, self.pos_bell[1] + 6 * k)
        if u < c1 - 0.1:
            p = _ease((u - 0.45) / (c1 - 0.55))
            pos = (start[0] + (on_sub[0] - start[0]) * p, start[1] + (on_sub[1] - start[1]) * p)
        elif u < c1 + 0.35:
            pos = on_sub
        elif u < c2 - 0.1:
            p = _ease((u - c1 - 0.35) / max(0.1, c2 - c1 - 0.45))
            pos = (on_sub[0] + (on_bell[0] - on_sub[0]) * p, on_sub[1] + (on_bell[1] - on_sub[1]) * p)
        elif u < c2 + 0.35:
            pos = on_bell
        else:                                                             # moves off so the ringing bell shows
            p = _ease((u - c2 - 0.35) / 0.8)
            pos = (on_bell[0] + 160 * k * p, on_bell[1] + 120 * k * p)
        pa = min(1.0, max(0.0, (u - 0.45) / 0.2), max(0.0, (c2 + 1.2 - u) / 0.4)) * fade
        psc = 1 - 0.15 * (_bump(u, c1 - 0.04, 0.2) + _bump(u, c2 - 0.04, 0.2))
        self._paste(img, self.ptr, *tip(pos), pa, psc, key="ptr")
        return img
