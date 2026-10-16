"""narrator_render.py - a corner narrator (your transparent PNG, half upper body) lip-synced to the narration.

Lightweight: numpy + Pillow + FFmpeg only, no model. The mouth follows the loudness of the clean narration
(pre-emphasised, so consonants count), gated by the Whisper word times in words.json when there are any:
  pair mode    your closed-mouth and open-mouth PNGs, blended by how open the mouth should be
  anchor mode  one PNG + a mouth point: the lower lip and chin are pushed down and the gap is drawn dark
Idle motion: breathing (the shoulders rise a little), blinks (your eyes-closed PNG, or lids drawn at the
eye points), a soft drop shadow. The finished video is re-encoded once with the narrator laid over it
(audio copied unchanged).

Run:  <python> -u -s app/narrator_render.py job.json      (written by narrator.py)
Also imported by narrator.py for the preview and the automatic mouth / eye guess.
"""
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

LEVELS = 6          # mouth openness steps (0 = closed)
BREATH = 6          # breathing steps
FLAGS = 0x08000000 if os.name == "nt" else 0      # CREATE_NO_WINDOW

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


# ------------------------------------------------------------------ pictures
def _rgba(path):
    im = Image.open(path)
    im.load()
    return im.convert("RGBA")


def alpha_box(im):
    a = np.asarray(im.split()[-1])
    ys, xs = np.nonzero(a > 12)
    if not len(xs):
        return (0, 0, im.width, im.height)
    return (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)


# ---- face finder: skin colour (YCbCr) -> highest large skin area = the face, then the eyes as the darkest
# high-contrast pair (white of the eye + iris) and the lips one eye-gap below. Hoods, hair and candle-lit hands
# fooled the older neck-based guess (it put the mouth on the forehead).

def _components(mask):
    """4-connected components of a small bool grid -> label array, sizes"""
    h, w = mask.shape
    lab = np.zeros((h, w), np.int32)
    sizes = [0]
    cur = 0
    for y in range(h):
        for x in range(w):
            if mask[y, x] and not lab[y, x]:
                cur += 1
                n = 0
                stack = [(y, x)]
                lab[y, x] = cur
                while stack:
                    cy, cx = stack.pop()
                    n += 1
                    for ny, nx in ((cy + 1, cx), (cy - 1, cx), (cy, cx + 1), (cy, cx - 1)):
                        if 0 <= ny < h and 0 <= nx < w and mask[ny, nx] and not lab[ny, nx]:
                            lab[ny, nx] = cur
                            stack.append((ny, nx))
                sizes.append(n)
    return lab, sizes

def face_guess(im):
    """face box from skin colour (YCbCr), or None"""
    a = np.asarray(im.convert("RGBA"), np.float32)
    k = max(1, int(round(max(im.size) / 220)))
    sm = a[::k, ::k]
    r, g, b, al = sm[..., 0], sm[..., 1], sm[..., 2], sm[..., 3]
    y = 0.299 * r + 0.587 * g + 0.114 * b
    cb = 128 - 0.168736 * r - 0.331264 * g + 0.5 * b
    cr = 128 + 0.5 * r - 0.418688 * g - 0.081312 * b
    skin = (al > 160) & (cb > 50) & (cb < 135) & (cr > 132) & (cr < 205) & (y > 30)
    ys, xs = np.nonzero(al > 40)
    if not len(ys):
        return None
    top, bot = ys.min(), ys.max()
    skin[int(top + (bot - top) * 0.75):] = False          # the face is in the upper part of a half body
    lab, sizes = _components(skin)
    if len(sizes) < 2:
        return None
    big = max(sizes[1:])
    if big < 40:
        return None
    cands = [i for i in range(1, len(sizes)) if sizes[i] >= 0.25 * big]
    tops = {i: np.nonzero(lab == i)[0].min() for i in cands}
    best = min(cands, key=lambda i: tops[i])                # the face is the highest large skin area (hands, candle glow lower)
    fy, fx = np.nonzero(lab == best)
    rows = {}
    for yy, xx in zip(fy, fx):
        rows.setdefault(yy, []).append(xx)
    ry = sorted(rows)
    y0 = ry[0]
    widths = [(max(rows[q]) - min(rows[q]) + 1) for q in ry]
    upper = widths[:max(3, len(widths) * 2 // 5)]
    fw = float(np.median(upper))
    cx = float(np.median([np.mean(rows[q]) for q in ry[:max(3, len(ry) // 2)]]))
    return {"x": cx * k, "top": y0 * k, "w": fw * k}


def _blur(arr, r):
    im = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))
    return np.asarray(im.filter(ImageFilter.GaussianBlur(max(0.5, r))), np.float32)

def face_points(im, f):
    a = np.asarray(im.convert("RGBA"), np.float32)
    al = a[..., 3] > 40
    lum = np.where(al, a[..., 0] * 0.3 + a[..., 1] * 0.59 + a[..., 2] * 0.11, 128)
    w, cx, top = f["w"], f["x"], f["top"]
    r = max(1.0, w * 0.035)
    m = _blur(lum, r)
    sd = np.sqrt(np.maximum(0, _blur(lum * lum / 255.0, r) * 255.0 - m * m))      # local contrast: white of the eye + iris
    score = m - 1.4 * sd
    H, W = lum.shape
    best = None
    for y in range(int(top + 0.05 * w), int(min(H - 1, top + 0.95 * w)), max(1, int(w / 80))):
        L = score[y, int(max(0, cx - 0.5 * w)):int(cx - 0.12 * w)]
        R = score[y, int(cx + 0.12 * w):int(min(W, cx + 0.5 * w))]
        if not len(L) or not len(R):
            continue
        v = float(L.min() + R.min())
        if best is None or v < best[0]:
            best = (v, y, int(max(0, cx - 0.5 * w)) + int(L.argmin()), int(cx + 0.12 * w) + int(R.argmin()))
    _, ey, lx, rx = best
    eyes = [[lx, ey], [rx, ey]]
    gap = max(8, rx - lx)
    ecx = (lx + rx) / 2
    my0 = ey + 0.92 * gap
    bestm, by = None, my0
    for y in range(int(my0 - 0.3 * gap), int(my0 + 0.35 * gap)):
        if 0 <= y < H:
            v = float(m[y, int(ecx - 0.2 * gap):int(ecx + 0.2 * gap)].mean()) + abs(y - my0) * 0.3
            if bestm is None or v < bestm:
                bestm, by = v, y
    return {"mouth": [int(round(ecx)), int(by)], "mouth_w": int(round(gap * 0.62)), "eyes": eyes, "eye_w": int(round(gap * 0.42))}


def guess_points(path):
    """mouth and eye points of a half-body PNG, in the PNG's own pixels. A guess: the page lets you correct it."""
    im = _rgba(path)
    try:
        f = face_guess(im)
        if f and f["w"] >= 12:
            p = face_points(im, f)
            p.update({"size": [im.width, im.height], "guessed": True, "guess": "face"})
            return p
    except Exception:
        pass
    p = _guess_points_old(im)
    p["guess"] = "outline"
    return p


def _guess_points_old(im):
    """older guess: head = the part above the narrowest row (the neck)"""
    a = np.asarray(im, np.float32)
    al = a[..., 3] > 40
    x0, y0, x1, y1 = alpha_box(im)
    h = y1 - y0
    widths = al.sum(1)
    lo, hi = y0 + int(h * 0.22), y0 + int(h * 0.62)
    band = widths[lo:hi].astype(np.float32)
    if len(band) > 5:
        band = np.convolve(band, np.ones(5) / 5, "same")
        cands = [i for i in range(len(band)) if band[i] > 0]
        neck = lo + (min(cands, key=lambda i: band[i]) if cands else len(band) // 2)
    else:
        neck = y0 + int(h * 0.45)
    head_rows = [y for y in range(y0, neck) if widths[y] > 0]
    top = head_rows[0] if head_rows else y0
    hh = max(10, neck - top)
    # face width and centre from the rows around the eyes / mouth
    mid = [y for y in range(top + int(hh * 0.45), neck) if widths[y] > 0]
    cols = [np.nonzero(al[y])[0] for y in mid] or [np.nonzero(al[top + hh // 2])[0]]
    lefts = np.array([c.min() for c in cols if len(c)]) if cols else np.array([x0])
    rights = np.array([c.max() for c in cols if len(c)]) if cols else np.array([x1])
    fl, fr = float(np.median(lefts)), float(np.median(rights))
    fw = max(8.0, fr - fl)
    cx = (fl + fr) / 2
    lum = a[..., 0] * 0.3 + a[..., 1] * 0.59 + a[..., 2] * 0.11
    lum = np.where(al, lum, 255)
    # mouth: the darkest short horizontal line in the lower face
    my0 = top + hh * 0.70
    mw = fw * 0.30
    best, by = None, my0
    for y in range(int(top + hh * 0.60), int(min(neck, top + hh * 0.90))):
        xs = slice(int(cx - mw / 2), int(cx + mw / 2))
        v = float(lum[y, xs].mean()) + abs(y - my0) * 0.6
        if best is None or v < best:
            best, by = v, y
    # eyes: darkest spots left / right of the centre in the upper face
    ew = fw * 0.20
    eyes = []
    for side in (-1, 1):
        ex0 = cx + side * fw * 0.20
        bestv, bp = None, (ex0, top + hh * 0.48)
        for y in range(int(top + hh * 0.35), int(top + hh * 0.62), 2):
            for x in range(int(ex0 - ew * 0.6), int(ex0 + ew * 0.6), 2):
                win = lum[max(0, y - 2):y + 3, max(0, x - 3):x + 4]
                v = float(win.mean()) + abs(x - ex0) * 0.4 + abs(y - (top + hh * 0.48)) * 0.4
                if bestv is None or v < bestv:
                    bestv, bp = v, (x, y)
        eyes.append([int(bp[0]), int(bp[1])])
    return {"mouth": [int(round(cx)), int(by)], "mouth_w": int(round(mw)), "eyes": eyes, "eye_w": int(round(ew)),
            "size": [im.width, im.height], "guessed": True}


class Sprite:
    """the narrator drawn at one height, with every mouth / blink / breath state made on demand and kept"""

    def __init__(self, n, height_px, shadow=True):
        base = _rgba(n["mouth_closed"] if n.get("mouth_closed") and os.path.isfile(n["mouth_closed"]) else n["base"])
        box = alpha_box(base)
        pad = 2
        box = (max(0, box[0] - pad), max(0, box[1] - pad), min(base.width, box[2] + pad), min(base.height, box[3] + pad))
        s = float(height_px) / max(1, box[3] - box[1])
        self.s = s
        size = (max(2, int(round((box[2] - box[0]) * s))), max(2, int(round((box[3] - box[1]) * s))))

        def fit(p):
            if not p or not os.path.isfile(p):
                return None
            im = _rgba(p)
            if im.size != base.size:                      # a different size: scale to the base picture
                im = im.resize(base.size, Image.LANCZOS)
            return np.asarray(im.crop(box).resize(size, Image.LANCZOS), np.float32)

        self.base = np.asarray(base.crop(box).resize(size, Image.LANCZOS), np.float32)
        self.open = fit(n.get("mouth_open"))
        self.eyes_closed = fit(n.get("eyes_closed"))
        sx = lambda p: [(p[0] - box[0]) * s, (p[1] - box[1]) * s]
        m = n.get("mouth") or []
        self.mouth = sx(m) if len(m) == 2 else None
        self.mw = float(n.get("mouth_w") or 0) * s or (self.base.shape[1] * 0.12)
        self.eyes = [sx(e) for e in (n.get("eyes") or []) if len(e) == 2]
        self.ew = float(n.get("eye_w") or 0) * s or self.mw * 0.7
        self.open_h = min(0.8, max(0.1, float(n.get("open_h") or 0.34)))   # how far the mouth opens, x mouth width
        self.mode = "pair" if self.open is not None else ("anchor" if self.mouth else "none")
        self.h, self.w = self.base.shape[:2]
        self.pad = max(4, int(self.h * 0.04))
        self.cw, self.ch = self.w + 2 * self.pad, self.h + self.pad    # canvas: room for the shadow and breathing
        self.shadow = None
        if shadow:
            a = Image.fromarray(self.base[..., 3].astype(np.uint8)).filter(ImageFilter.GaussianBlur(max(2, self.h * 0.012)))
            sh = np.zeros((self.ch, self.cw), np.float32)
            off = min(self.pad, max(2, int(self.h * 0.010)))
            sa = np.asarray(a, np.float32) * 0.38
            sh[self.pad + off:, self.pad + off:self.pad + off + self.w] = sa[:self.h - off, :]
            self.shadow = sh
        self.lid = None
        self.cache = {}

    # ---- states
    def _mouth(self, img, o):
        if o <= 0.02:
            return img
        if self.mode == "pair":
            k = min(1.0, o * 1.15)
            k = k * k * (3 - 2 * k)                     # smooth step: little ghosting between the two pictures
            return img * (1 - k) + self.open * k
        if self.mode != "anchor":
            return img
        mx, my = self.mouth
        mw = self.mw
        d = o * self.open_h * mw                         # how far the lower lip and chin drop
        x0, x1 = int(max(0, mx - 0.95 * mw)), int(min(self.w, mx + 0.95 * mw + 1))
        y0, y1 = int(max(0, my)), int(min(self.h, my + 1.35 * mw + 1))
        if x1 - x0 < 3 or y1 - y0 < 3:
            return img
        out = img.copy()
        xs = np.arange(x0, x1, dtype=np.float32)
        ys = np.arange(y0, y1, dtype=np.float32)
        wx = np.clip(1 - ((xs - mx) / (0.95 * mw)) ** 2, 0, 1) ** 0.8        # lens shape: open in the middle
        dx = d * wx                                                        # drop per column
        yy = ys[:, None] - my                                              # 0 at the lip line
        full = 0.62 * mw                                                   # chin moves fully down to here ...
        tail = max(1.0, (y1 - my) - full)                                  # ... then the neck takes it up
        fade = np.clip(1 - (yy - full) / tail, 0, 1)
        fade = fade * fade * (3 - 2 * fade)
        disp = dx[None, :] * np.where(yy < full, 1.0, fade)
        src = np.clip(ys[:, None] - disp, 0, self.h - 1)
        i0 = np.floor(src).astype(np.int32)
        f = (src - i0)[..., None]
        i1 = np.minimum(i0 + 1, self.h - 1)
        cols = np.arange(x0, x1)[None, :].repeat(len(ys), 0)
        warped = img[i0, cols] * (1 - f) + img[i1, cols] * f
        # the gap between the lips: dark mouth, a little lighter at the edges
        gap = yy < dx[None, :]
        depth = np.clip(yy / np.maximum(dx[None, :], 1e-3), 0, 1)
        mid = np.sin(np.pi * depth) * wx[None, :]
        inner = np.stack([38 + 25 * (1 - mid), 12 + 10 * (1 - mid), 16 + 10 * (1 - mid)], -1)
        warped[..., :3] = np.where(gap[..., None], inner, warped[..., :3])
        warped[..., 3] = np.where(gap, np.maximum(img[y0:y1, x0:x1, 3], 230), warped[..., 3])
        # soften the gap's outline (1 px blend with the original edge)
        out[y0:y1, x0:x1] = warped
        return out

    def _lid_colour(self, img, ex, ey):
        r = self.ew
        y0, y1 = int(max(0, ey - r * 0.9)), int(min(self.h, ey + r * 0.9))
        x0, x1 = int(max(0, ex - r * 1.1)), int(min(self.w, ex + r * 1.1))
        reg = img[y0:y1, x0:x1].reshape(-1, 4)
        reg = reg[reg[:, 3] > 200]
        if not len(reg):
            return np.array([180, 140, 120], np.float32)
        lum = reg[:, 0] * 0.3 + reg[:, 1] * 0.59 + reg[:, 2] * 0.11
        k = np.argsort(lum)[int(len(lum) * 0.72)]          # skin is the lighter part around an eye
        return reg[k, :3]

    def _blink(self, img, b):
        if b <= 0:
            return img
        if self.eyes_closed is not None:
            return img * (1 - b) + self.eyes_closed * b
        if not self.eyes:
            return img
        out = img.copy()
        im = Image.fromarray(np.clip(out, 0, 255).astype(np.uint8), "RGBA")
        for ex, ey in self.eyes:
            col = tuple(int(c) for c in self._lid_colour(img, ex, ey))
            rw, rh = self.ew * 0.62, self.ew * 0.36
            layer = Image.new("RGBA", im.size, (0, 0, 0, 0))
            dr = ImageDraw.Draw(layer)
            top = ey - rh * 1.15
            bottom = ey - rh * 1.15 + (rh * 2.3) * b              # the lid comes down from above the eye
            dr.ellipse([ex - rw, top, ex + rw, max(top + 1, bottom)], fill=col + (255,))
            lw = max(1, int(round(self.ew * 0.07)))
            if b >= 0.99:
                dr.arc([ex - rw, ey - rh * 0.9, ex + rw, ey + rh * 0.55], 15, 165, fill=(30, 22, 22, 255), width=lw)
            else:
                dr.line([ex - rw * 0.92, bottom - lw, ex + rw * 0.92, bottom - lw], fill=(30, 22, 22, 230), width=lw)
            layer = layer.filter(ImageFilter.GaussianBlur(max(0.5, self.ew * 0.03)))
            im = Image.alpha_composite(im, layer)
        arr = np.asarray(im, np.float32).copy()
        arr[..., 3] = img[..., 3]                                 # never paint outside the figure
        return arr

    def state(self, level, blink, breath):
        key = (level, blink, breath)
        got = self.cache.get(key)
        if got is not None:
            return got
        img = self._mouth(self.base, level / (LEVELS - 1))
        img = self._blink(img, (0.0, 0.55, 1.0)[blink])
        k = 1.0 + 0.011 * breath / max(1, BREATH - 1)           # breathing: up to 1.1 % taller, from the bottom
        im = Image.fromarray(np.clip(img, 0, 255).astype(np.uint8), "RGBA")
        hh = int(round(self.h * k))
        if hh != self.h:
            im = im.resize((self.w, hh), Image.BICUBIC)
        canvas = np.zeros((self.ch, self.cw, 4), np.float32)
        if self.shadow is not None:
            canvas[..., 3] = self.shadow
        fg = np.asarray(im, np.float32)
        y = self.ch - hh
        reg = canvas[max(0, y):, self.pad:self.pad + self.w]
        fg = fg[max(0, -y):]
        fa = fg[..., 3:4] / 255
        ba = reg[..., 3:4] / 255
        oa = fa + ba * (1 - fa)
        rgb = (fg[..., :3] * fa + reg[..., :3] * ba * (1 - fa)) / np.maximum(oa, 1e-4)
        canvas[max(0, y):, self.pad:self.pad + self.w] = np.concatenate([rgb, oa * 255], -1)
        out = np.clip(canvas, 0, 255).astype(np.uint8)
        if len(self.cache) < 400:
            self.cache[key] = out
        return out


# ------------------------------------------------------------------ placement
def place(W, H, sp, corner="bottom-right", margin=0.03, dock=True):
    """top-left pixel of the narrator canvas inside a WxH frame"""
    m = int(round(margin * H))
    right = corner.endswith("right")
    x = W - sp.cw - m + sp.pad if right else m - sp.pad
    if corner.startswith("bottom"):
        y = H - sp.ch + (0 if dock else -m)
    else:
        y = m - (sp.ch - sp.h)
    return int(x), int(y)


def place_box(W, H, height_frac, aspect, corner="bottom-right", margin=0.03, dock=True):
    """where the visible figure lands (x, y, w, h) without building a sprite - for the subtitle room and the page"""
    h = height_frac * H
    w = h * aspect
    m = margin * H
    x = W - w - m if corner.endswith("right") else m
    y = (H - h - (0 if dock else m)) if corner.startswith("bottom") else m
    return [int(x), int(y), int(w), int(h)]


# ------------------------------------------------------------------ sound -> mouth
def decode(ffmpeg, path, rate=16000):
    p = subprocess.run([ffmpeg, "-v", "error", "-i", path, "-vn", "-ac", "1", "-ar", str(rate), "-f", "s16le", "-"],
                       capture_output=True, creationflags=FLAGS)
    if p.returncode != 0:
        raise RuntimeError("could not read the sound of " + path + ": " + p.stderr.decode("utf-8", "replace")[-300:])
    return np.frombuffer(p.stdout, np.int16).astype(np.float32) / 32768


def energy100(x, rate=16000):
    """loudness at 100 per second, in dB, of the pre-emphasised sound (consonants count, rumble does not)"""
    y = np.empty_like(x)
    y[0] = x[0] if len(x) else 0
    y[1:] = x[1:] - 0.95 * x[:-1]
    hop = rate // 100
    n = len(y) // hop
    if n == 0:
        return np.zeros(1, np.float32)
    e = (y[:n * hop].reshape(n, hop) ** 2).mean(1)
    return (10 * np.log10(e + 1e-9)).astype(np.float32)


def find_shift(narr_db, vid_db):
    """narration time = video time + shift, from the loudness of both (cross-correlation); (shift, confidence)"""
    a = np.maximum(narr_db, np.percentile(narr_db, 20)) - np.mean(narr_db)
    b = np.maximum(vid_db, np.percentile(vid_db, 20)) - np.mean(vid_db)
    a = (a - a.mean()) / (a.std() + 1e-6)
    b = (b - b.mean()) / (b.std() + 1e-6)
    n = 1
    while n < len(a) + len(b):
        n *= 2
    c = np.fft.irfft(np.fft.rfft(a, n) * np.conj(np.fft.rfft(b, n)), n)
    k = int(np.argmax(c))
    conf = float((c[k] - c.mean()) / (c.std() + 1e-6))
    if k > len(a):
        k -= n
    return k / 100.0, conf


def mouth_track(narr_db, words, fps, n_frames, shift, start=0.0):
    """openness level 0..LEVELS-1 for every video frame"""
    voiced = np.zeros(len(narr_db), bool)
    if words:
        for w in words:
            a, b = int(max(0, (float(w["start"]) - 0.04) * 100)), int(min(len(voiced), (float(w["end"]) + 0.05) * 100))
            voiced[a:b] = True
    ref = narr_db[voiced] if voiced.sum() > 50 else narr_db[narr_db > np.percentile(narr_db, 40)]
    if not len(ref):
        return np.zeros(n_frames, np.int8)
    lo, hi = float(np.percentile(ref, 12)), float(np.percentile(ref, 97))
    o = np.clip((narr_db - lo) / max(3.0, hi - lo), 0, 1) ** 0.85
    if voiced.sum() > 50:
        o = np.where(voiced, o, 0)
    else:
        o = np.where(o > 0.14, o, 0)
    out = np.zeros(n_frames, np.int8)
    cur = 0.0
    for k in range(n_frames):
        t = k / fps + shift
        i = int(t * 100)
        if k / fps < start or i < 2 or i >= len(o) - 2:
            v = 0.0
        else:
            v = float(o[i - 2:i + 3].max())
        cur += (v - cur) * (0.75 if v > cur else 0.5)               # opens fast, closes a little slower
        out[k] = int(round(min(1.0, cur) * (LEVELS - 1))) if cur > 0.08 else 0
    return out


def blink_track(n_frames, fps, seed=7):
    rng = np.random.default_rng(seed)
    out = np.zeros(n_frames, np.int8)
    t = rng.uniform(1.0, 3.0)
    pat = [1, 2, 2, 1] if fps < 40 else [1, 1, 2, 2, 2, 1, 1]
    while True:
        k = int(t * fps)
        if k >= n_frames:
            break
        for j, v in enumerate(pat):
            if k + j < n_frames:
                out[k + j] = v
        if rng.random() < 0.12:                                       # now and then a double blink
            k2 = k + len(pat) + int(0.12 * fps)
            for j, v in enumerate(pat):
                if k2 + j < n_frames:
                    out[k2 + j] = v
        t += rng.uniform(2.6, 6.0)
    return out


def breath_index(k, fps, period=4.4):
    p = 0.5 - 0.5 * math.cos(2 * math.pi * (k / fps) / period)
    return int(round(p * (BREATH - 1)))


# ------------------------------------------------------------------ video
def probe(ffmpeg, path):
    p = subprocess.run([ffmpeg, "-hide_banner", "-i", path], capture_output=True, creationflags=FLAGS)
    s = p.stderr.decode("utf-8", "replace")
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", s)
    dur = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3)) if m else 0.0
    v = re.search(r"Stream #\S+.*Video:.*?(\d{2,5})x(\d{2,5})", s)
    f = re.search(r"Stream #\S+.*Video:.*?([\d.]+) fps", s) or re.search(r"Stream #\S+.*Video:.*?([\d.]+) tbr", s)
    if not v:
        raise RuntimeError("not a video: " + path)
    return int(v.group(1)), int(v.group(2)), float(f.group(1)) if f else 24.0, dur


def encoders(ffmpeg):
    try:
        s = subprocess.run([ffmpeg, "-hide_banner", "-encoders"], capture_output=True, creationflags=FLAGS).stdout.decode("utf-8", "replace")
    except OSError:
        return []
    out = []
    if " h264_nvenc " in s:
        out.append("nvenc")
    out.append("x264")
    return out


def _enc_args(kind):
    if kind == "nvenc":
        return ["-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", "20", "-b:v", "0", "-pix_fmt", "yuv420p"]
    return ["-c:v", "libx264", "-preset", "medium", "-crf", "19", "-pix_fmt", "yuv420p"]


def render_one(ffmpeg, item, n, log=print):
    src, out = item["video"], item["out"]
    W, H, fps, dur = probe(ffmpeg, src)
    pl = item.get("place") or {}
    sp = Sprite(n, max(40, int(round(float(pl.get("size", 0.38)) * H))), bool(pl.get("shadow", True)))
    x, y = place(W, H, sp, pl.get("corner", "bottom-right"), float(pl.get("margin", 0.03)), bool(pl.get("dock", True)))
    nf = int(math.ceil(dur * fps)) + 2
    narr = item.get("narration") or ""
    if narr and os.path.isfile(narr):
        ndb = energy100(decode(ffmpeg, narr))
        how = "clean narration"
    else:
        ndb = energy100(decode(ffmpeg, src))
        narr, how = src, "the video's own sound (no clean narration found)"
    shift = item.get("shift")
    if shift is None:
        if narr == src:
            shift, conf = 0.0, 99.0
        else:
            shift, conf = find_shift(ndb, energy100(decode(ffmpeg, src)))
            log(f"   narrator: voice found at {shift:+.2f} s in the video (match {conf:.1f})")
            if conf < 4:
                log("   narrator: the match is weak; the lips may be off - check the result")
    words = []
    wj = item.get("words") or ""
    if wj and os.path.isfile(wj) and narr != src:
        try:
            words = json.load(open(wj, encoding="utf-8")).get("words") or []
        except (OSError, ValueError):
            words = []
    start = float(item.get("start", 0.0))
    mouth = mouth_track(ndb, words, fps, nf, float(shift), start)
    blink = blink_track(nf, fps) if pl.get("blink", True) else np.zeros(nf, np.int8)
    fade_in, fade_out = start + 0.0, max(start + 1, dur - 1.0)
    log(f"   narrator: {os.path.basename(src)}  {W}x{H} {fps:g} fps {dur / 60:.1f} min, {sp.mode} mouth, "
        f"{len(words)} timed words, sound from {how}, corner {pl.get('corner', 'bottom-right')} at {x},{y}")
    tmp = out[:-4] + "_narrator.part.mp4"
    last_err = ""
    for enc in encoders(ffmpeg):
        cmd = [ffmpeg, "-v", "error", "-y", "-i", src, "-f", "rawvideo", "-pix_fmt", "rgba",
               "-s", f"{sp.cw}x{sp.ch}", "-framerate", f"{fps:g}", "-i", "-",
               "-filter_complex", f"[0:v][1:v]overlay=x={x}:y={y}:eof_action=pass:shortest=0:format=auto[v]",
               "-map", "[v]", "-map", "0:a?", *_enc_args(enc), "-c:a", "copy", "-movflags", "+faststart", tmp]
        t0 = time.time()
        p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=FLAGS)
        try:
            for k in range(nf):
                fr = sp.state(int(mouth[k]), int(blink[k]), breath_index(k, fps))
                t = k / fps
                fa = min(1.0, max(0.0, (t - fade_in) / 0.6)) * min(1.0, max(0.0, (dur - t) / 1.0)) if t < fade_in + 0.6 or t > fade_out else 1.0
                if fa < 0.999:
                    fr = fr.copy()
                    fr[..., 3] = (fr[..., 3].astype(np.float32) * fa).astype(np.uint8)
                p.stdin.write(fr.tobytes())
                if k % max(1, nf // 10) == 0 and k:
                    el = time.time() - t0
                    log(f"   narrator: {100 * k // nf}%  (~{el / k * (nf - k) / 60:.0f} min left, {enc})")
        except (BrokenPipeError, OSError):
            pass
        try:
            p.stdin.close()
        except OSError:
            pass
        err = p.stderr.read().decode("utf-8", "replace")
        rc = p.wait()
        if rc == 0 and os.path.isfile(tmp) and os.path.getsize(tmp) > 1000:
            os.replace(tmp, out)
            log(f"   narrator: done -> {out}  ({(time.time() - t0) / 60:.1f} min, {enc}, {len(sp.cache)} drawn states)")
            return {"ok": True, "shift": float(shift), "encoder": enc, "box": [x + sp.pad, y + sp.ch - sp.h, sp.w, sp.h]}
        last_err = err[-600:]
        log(f"   narrator: encoder {enc} failed ({last_err.strip().splitlines()[-1] if last_err.strip() else rc}); trying the next")
    if os.path.exists(tmp):
        os.remove(tmp)
    raise RuntimeError("narrator overlay failed: " + last_err)


# ------------------------------------------------------------------ preview
def preview(n, frame_path, W, H, pl, openness=0.0, blink=0, guides=True, subtitle_room=None):
    """sample frame + narrator (+ subtitle area and mouth / eye points) -> PIL image"""
    if frame_path and os.path.isfile(frame_path):
        bg = Image.open(frame_path).convert("RGB").resize((W, H), Image.LANCZOS)
    else:
        g = np.linspace(40, 90, H)[:, None].repeat(W, 1)
        bg = Image.fromarray(np.stack([g * 0.8, g * 0.85, g], -1).astype(np.uint8))
    sp = Sprite(n, max(40, int(round(float(pl.get("size", 0.38)) * H))), bool(pl.get("shadow", True)))
    x, y = place(W, H, sp, pl.get("corner", "bottom-right"), float(pl.get("margin", 0.03)), bool(pl.get("dock", True)))
    lvl = int(round(max(0.0, min(1.0, openness)) * (LEVELS - 1)))
    fr = Image.fromarray(sp.state(lvl, int(blink), 0), "RGBA")
    im = bg.convert("RGBA")
    layer = Image.new("RGBA", im.size, (0, 0, 0, 0))
    layer.paste(fr, (x, y))
    im = Image.alpha_composite(im, layer)
    if guides:
        d = ImageDraw.Draw(im, "RGBA")
        if subtitle_room:
            sx0, sy0, sx1, sy1 = subtitle_room
            d.rectangle([sx0, sy0, sx1, sy1], outline=(255, 230, 120, 200), width=max(1, W // 640))
            d.text((sx0 + 8, sy0 + 4), "subtitles", fill=(255, 230, 120, 220))
    return im.convert("RGB"), {"x": x, "y": y, "sprite": [sp.cw, sp.ch], "scale": sp.s, "pad": sp.pad,
                               "figure": [x + sp.pad, y + sp.ch - sp.h, sp.w, sp.h], "mode": sp.mode}


def main():
    job = json.load(open(sys.argv[1], encoding="utf-8"))
    ff = job.get("ffmpeg") or shutil.which("ffmpeg") or "ffmpeg"
    res = []
    ok = True
    for it in job["items"]:
        try:
            res.append(dict(render_one(ff, it, job["narrator"], _log), video=it["out"]))
        except Exception as e:
            ok = False
            print(f"   narrator: {os.path.basename(it.get('video', ''))} failed: {e}", flush=True)
            res.append({"ok": False, "video": it.get("out"), "error": str(e)})
    if job.get("result"):
        with open(job["result"], "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=1)
    return 0 if ok else 1


def _log(s):
    print(s, flush=True)


if __name__ == "__main__":
    sys.exit(main())
