"""
overlay.py - audio-reactive "living light line" (upgraded equalizer), elegant captions and brand mark.
Pure numpy + ffmpeg.exe drawtext for text masks (no Pillow needed). Shared by preview and final render.
"""
import os, subprocess, sys, tempfile
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import paths  # noqa: E402

W, H = 1920, 1080
LAV = np.array([0.80, 0.72, 1.00], np.float32)       # lavender glow (from the artwork palette)
TXT = np.array([0.95, 0.92, 1.00], np.float32)       # soft lavender-white text
_ENGINE = os.path.dirname(os.path.abspath(__file__))
HERE = os.path.dirname(_ENGINE) if os.path.basename(_ENGINE).lower() == "engine" else _ENGINE   # project root
FONT_DIRS = [os.path.join(HERE, "assets", "fonts"), os.path.join(HERE, "assets"), paths.USER_FONTS, paths.WIN_FONTS]


def font(name):
    for d in FONT_DIRS:
        p = os.path.join(d, name)
        if os.path.exists(p):
            return p
    return None


SERIF_IT = lambda: font("CormorantGaramond-Italic-VariableFont_wght.ttf") or font("georgiai.ttf")
SERIF = lambda: font("CormorantGaramond-VariableFont_wght.ttf") or font("georgia.ttf")
HINDI = lambda: font("TiroDevanagariHindi-Regular.ttf") or font("Nirmala.ttf")
# the channel wordmark: the same classic serif as "सुकून 8.0" (Tiro has fine Latin caps), so the badge is one family
WORDMARK = lambda: font("TiroDevanagariHindi-Regular.ttf") or SERIF()
BRAND_WORD = "सुकून"          # "Sukoon" in English episodes (set by sleep_pipeline / reels)


def ffmpeg_exe():
    import shutil
    for c in [paths.ffmpeg(), shutil.which("ffmpeg")]:
        if c and os.path.exists(c):
            return c
    raise RuntimeError("ffmpeg.exe not found")


def _box(a, r):
    r = int(max(1, r)); out = a.astype(np.float32)
    for _ in range(3):
        for ax in (0, 1):
            c = np.cumsum(np.pad(out, [(r + 1, r) if i == ax else (0, 0) for i in range(2)], mode="edge"), axis=ax)
            out = (np.take(c, np.arange(2 * r + 1, c.shape[ax]), axis=ax) -
                   np.take(c, np.arange(0, c.shape[ax] - 2 * r - 1), axis=ax)) / (2 * r + 1)
    return out.astype(np.float32)


def _ffp(p):
    return p.replace("\\", "/").replace(":", "\\:")


def _pil_raqm():
    try:
        from PIL import features
        return features.check("raqm")
    except Exception:
        return False


def text_mask(lines, fontfile, size, width, line_h=None, pad=40):
    """White-on-black mask (float 0..1) of centered lines (complex-script shaping)."""
    line_h = line_h or int(size * 1.25)
    h = line_h * len(lines) + 2 * pad
    h += h % 2
    if _pil_raqm():
        from PIL import Image, ImageDraw, ImageFont
        fo = ImageFont.truetype(fontfile, size, layout_engine=ImageFont.Layout.RAQM)
        im = Image.new("L", (width, h), 0); d = ImageDraw.Draw(im)
        for i, ln in enumerate(lines):
            d.text(((width - d.textlength(ln, font=fo)) / 2, pad + i * line_h), ln, font=fo, fill=255)
        return np.asarray(im, np.float32) / 255
    try:                                                      # HarfBuzz + FreeType: correct Hindi conjuncts
        import hbtext
        if hbtext.ok():
            m = np.zeros((h, width), np.float32)
            for i, ln in enumerate(lines):
                hbtext.draw(m, ln, fontfile, size, (width - hbtext.width(ln, fontfile, size)) / 2, pad + i * line_h)
            return m
    except Exception:
        pass
    tmp = tempfile.mkdtemp(prefix="txt_")
    draws = []
    for i, ln in enumerate(lines):
        tf = os.path.join(tmp, f"{i}.txt"); open(tf, "w", encoding="utf-8").write(ln)
        draws.append(f"drawtext=fontfile='{_ffp(fontfile)}':textfile='{_ffp(tf)}':fontsize={size}:fontcolor=white:"
                     f"x=(w-text_w)/2:y={pad + i * line_h}")
    out = os.path.join(tmp, "m.gray")
    subprocess.run([ffmpeg_exe(), "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"color=black:s={width}x{h}:d=1",
                    "-vf", ",".join(draws) + ",format=gray", "-frames:v", "1", "-f", "rawvideo", out], check=True)
    v = np.fromfile(out, np.uint8).reshape(h, width).astype(np.float32)
    lo = np.percentile(v, 2)
    return np.clip((v - lo) / max(1, 255 - lo), 0, 1)


def wrap_balanced(text, max_chars):
    """One line if it fits, else two lines split at the word boundary nearest the middle."""
    if len(text) <= max_chars:
        return [text]
    words = text.split()
    best, cut = 1e9, 1
    for k in range(1, len(words)):
        a, b = " ".join(words[:k]), " ".join(words[k:])
        d = abs(len(a) - len(b))
        if d < best:
            best, cut = d, k
    return [" ".join(words[:cut]), " ".join(words[cut:])]


def wrap(text, max_chars):
    lines, cur = [], ""
    for wd in text.split():
        t = (cur + " " + wd).strip()
        if len(t) > max_chars and cur:
            lines.append(cur); cur = wd
        else:
            cur = t
    return lines + [cur]


# ------------------------------------------------------------------ captions
class Caption:
    def __init__(self, text, start, end, size=74, y_center=728):
        f = SERIF_IT()
        lines = wrap(text, 46)
        m = text_mask(lines, f, size, W)
        m = np.maximum(m, np.maximum(np.roll(m, 1, 1), np.roll(m, 1, 0)) * 0.9)   # slightly bolder strokes
        self.mask = m
        self.soft = _box(self.mask, 3)                     # out-of-focus version for the focus-in
        self.glow = _box(self.mask, 14)
        self.h = self.mask.shape[0]
        self.y0 = int(y_center - self.h / 2)
        self.start, self.end = start, end

    def draw(self, img, t, fade=2.4, rise=14):
        if t < self.start or t > self.end:
            return
        s = min(1.0, (t - self.start) / fade, (self.end - t) / fade)
        e = s * s * (3 - 2 * s)                             # smoothstep
        dy = int(round((1 - e) * rise)) if t - self.start < fade else 0
        y0 = self.y0 + dy
        m = self.mask * e + self.soft * (1 - e)             # blur -> sharp as it appears
        reg = img[y0:y0 + self.h]
        a_glow = (self.glow * 0.55 * e)[..., None]
        reg[:] = reg + LAV * a_glow * 0.6                   # soft lavender halo
        a = (m * 0.92 * e)[..., None]
        reg[:] = reg * (1 - a) + TXT * a


# ------------------------------------------------------------------ brand mark
def _embolden(m, k=0.6):
    """A touch bolder: thicken strokes by a fraction of a pixel (k = how much)."""
    return np.maximum(m, np.maximum(np.roll(m, 1, 1), np.roll(m, 1, 0)) * k)


SILVER = np.array([0.86, 0.88, 0.93], np.float32)     # brushed-silver for the channel wordmark
PEARL = np.array([0.93, 0.91, 1.00], np.float32)      # pearl-lavender for the episode badge


# YouTube "zoom to fill" on long phones (20:9) cuts ~10% off the top and the bottom of a 16:9 video:
# everything that must be seen stays between SAFE_TOP and SAFE_BOTTOM.
SAFE_TOP, SAFE_BOTTOM = 118, 962


class Brand:
    """Premium corner badge: "सुकून 8.0" in pearl, a fine silver hairline, MUKHTALIF AWAAZ in silver, and a slow
    light-sweep across it every ~18 s (like light catching a metal plate).
    Readability: the wordmark is a bold sans (the thin serif at 21 px vanished on bright skies) and both lines sit
    on a soft dark shadow, so they read on any artwork without a visible box.
    Fades in once the opening title has gone.  Drawn identically by the CPU and GPU renderers via layers()."""

    W_BADGE = 480
    SHADOW = np.zeros(3, np.float32)

    def __init__(self, title="सुकून", sub="M U K H T A L I F   A W A A Z", t_in=0.0):
        P = 22                                                     # room for the shadow to fade out
        self.m1 = _embolden(text_mask([title], HINDI(), 48, self.W_BADGE, pad=P), 0.55)
        self.m2 = _embolden(text_mask([sub], WORDMARK(), 23, self.W_BADGE, pad=P), 0.3)   # classic serif caps
        self.g1 = _box(self.m1, 8)
        self.g2 = _box(self.m2, 4)
        self.s1 = np.clip(_box(self.m1, 7) * 2.2, 0, 1) * 0.45   # soft dark shadow behind each line
        self.s2 = np.clip(_box(self.m2, 6) * 3.0, 0, 1) * 0.60
        cols = np.where(self.m2.max(0) > 0.05)[0]
        l, r = (int(cols.min()), int(cols.max())) if len(cols) else (100, 320)
        hl = np.zeros((5, self.W_BADGE), np.float32)              # hairline, fading out at both ends
        x = np.arange(self.W_BADGE, dtype=np.float32)
        c, half = (l + r) / 2, (r - l) * 0.40
        hl[2] = np.clip(1 - np.abs(x - c) / max(1, half), 0, 1) ** 1.5 * 0.9
        hl[1] = hl[3] = hl[2] * 0.25
        self.hl = hl
        self.t_in = t_in
        self.x0, self.y0 = 30 - (self.W_BADGE - 420) // 2, SAFE_TOP - P
        self.y_hl = self.y0 + P + int(48 * 1.25) - 4
        self.y2 = self.y_hl + 10 - P

    def k(self, t):
        if t is None or t >= self.t_in + 2.5:
            return 1.0
        q = max(0.0, (t - self.t_in) / 2.5)
        return q * q * (3 - 2 * q)

    def sweep(self, t, period=18.0, dur=3.2):
        """Per-column extra light (0..1) of the slow sheen; None when no sweep is passing."""
        if t is None:
            return None
        ph = (t - self.t_in - 6.0) % period
        if ph > dur or t < self.t_in + 4:
            return None
        cx = -80 + (self.W_BADGE + 160) * (ph / dur)
        x = np.arange(self.W_BADGE, dtype=np.float32)
        return np.exp(-((x - cx) / 46.0) ** 2) * np.sin(np.pi * ph / dur)

    def layers(self):
        """(mask, y, colour, glow mask, glow colour, glow amount, shines) - shared with the GPU renderer."""
        return [(self.s1, self.y0, self.SHADOW, None, None, 0.0, False),
                (self.s2, self.y2, self.SHADOW, None, None, 0.0, False),
                (self.m1, self.y0, PEARL, self.g1, LAV, 0.30, False),
                (self.hl, self.y_hl, SILVER, None, None, 0.0, True),
                (self.m2, self.y2, SILVER, self.g2, SILVER, 0.25, True)]

    def draw(self, img, alpha=0.92, t=None):
        alpha *= self.k(t)
        if alpha <= 0:
            return
        sw = self.sweep(t)
        for m, y, col, g, gcol, gamt, shines in self.layers():
            reg = img[y:y + m.shape[0], self.x0:self.x0 + m.shape[1]]
            if g is not None:
                reg[:] = reg + gcol * (g * gamt * alpha)[..., None]
            a = (m * alpha)[..., None]
            reg[:] = reg * (1 - a) + col * a
            if shines and sw is not None:
                reg[:] = reg + (m * sw[None, :] * 0.55 * alpha)[..., None]       # light catching the silver


# ------------------------------------------------------------------ opening extras (channel style)
BLUE = np.array([0.66, 0.74, 1.00], np.float32)       # the blue-lavender of the old "सुकून" titles
LATIN = lambda: font("Biryani-Bold.ttf") or font("Montserrat-VariableFont_wght.ttf") or SERIF()   # Biryani has clean Latin too
BOLD_HI = lambda: font("aparajb.ttf") or font("RozhaOne-Regular.ttf") or HINDI()   # Biryani/Sahitya mis-shape ज़िंदगी / नहीं


def _crop(m, pad=4):
    cols = np.where(m.max(0) > 0.04)[0]
    rows = np.where(m.max(1) > 0.04)[0]
    if not len(cols):
        return m[:2, :2] * 0
    return m[max(0, rows[0] - pad):rows[-1] + pad + 1, max(0, cols[0] - pad):cols[-1] + pad + 1]


def headphones_icon(size=34):
    """Small line-art headphones, drawn procedurally (no emoji font needed)."""
    s = size * 4                                        # supersample, then shrink
    yy, xx = np.mgrid[0:s, 0:s].astype(np.float32)
    cx, cy, r, th = s / 2, s * 0.55, s * 0.40, s * 0.075
    d = np.hypot(xx - cx, yy - cy)
    m = ((np.abs(d - r) < th / 2) & (yy < cy)).astype(np.float32)          # head band
    for ex in (cx - r, cx + r):                                               # ear cups
        m = np.maximum(m, ((np.abs(xx - ex) < s * 0.09) & (yy > cy - s * 0.04) & (yy < cy + s * 0.32)).astype(np.float32))
    m = m.reshape(size, 4, size, 4).mean((1, 3))
    return m


class Sprite:
    """A pre-rendered mask at a fixed spot with a fade-in / hold / fade-out envelope."""

    def __init__(self, mask, x, y, color, t_in, t_out, fade_in=2.0, fade_out=4.0, amax=0.8, glow=0.0, react=0.0):
        self.mask, self.x, self.y, self.color = mask.astype(np.float32), int(x), int(y), np.asarray(color, np.float32)
        self.t_in, self.t_out, self.fi, self.fo, self.amax = t_in, t_out, fade_in, fade_out, amax
        self.react = react                                 # >0: breathes with the music like the light line
        if glow or react:                                  # room for the glow to fade out (no boxy halo)
            P = 30
            self.mask = np.pad(self.mask, P); self.x -= P; self.y -= P
        if self.y < 0: self.mask = self.mask[-self.y:]; self.y = 0
        if self.x < 0: self.mask = self.mask[:, -self.x:]; self.x = 0
        self.mask = self.mask[:H - self.y, :W - self.x]           # never past the frame edge
        self.glow = _box(self.mask, 8) * glow if glow else (_box(self.mask, 9) * 0.5 if react else None)
        self.h, self.w = self.mask.shape
        self.start, self.end = t_in, t_out + fade_out

    def k(self, t, lv=0.0):
        if t < self.t_in or t > self.end:
            return 0.0
        q = min(1.0, (t - self.t_in) / self.fi, (self.end - t) / self.fo)
        a = q * q * (3 - 2 * q) * self.amax
        if self.react:                                     # dim when quiet, softly brighter with the sound
            a *= (1 - self.react) + self.react * min(1.0, lv * 1.3)
        return a

    def glow_k(self, t, lv=0.0):
        a = self.k(t, lv)
        return a * (0.4 + 1.4 * min(1.0, lv)) if self.react else a

    def draw(self, img, t, lv=0.0):
        a = self.k(t, lv)
        if a <= 0:
            return
        reg = img[self.y:self.y + self.h, self.x:self.x + self.w]
        if self.glow is not None:
            reg[:] = reg + LAV * (self.glow * self.glow_k(t, lv))[..., None]
        m = (self.mask * a)[..., None]
        reg[:] = reg * (1 - m) + self.color * m


def opening_extras(hold=60.0, total=None):
    """'🎧 Use Headphones' (top right, first minute) and 'Audio Story | सुकून | Sleep Therapy' (bottom, whole
    video, dimly breathing with the music)."""
    sp = []
    txt = _embolden(_crop(text_mask(["Use Headphones"], LATIN(), 24, 400, pad=8)), 0.45)
    ico = headphones_icon(34)
    h = max(txt.shape[0], ico.shape[0])
    row = np.zeros((h, ico.shape[1] + 12 + txt.shape[1]), np.float32)
    row[(h - ico.shape[0]) // 2:(h - ico.shape[0]) // 2 + ico.shape[0], :ico.shape[1]] = ico
    row[(h - txt.shape[0]) // 2:(h - txt.shape[0]) // 2 + txt.shape[0], ico.shape[1] + 12:] = txt
    sp.append(Sprite(row, W - 44 - row.shape[1], SAFE_TOP, SILVER, 2.0, (total - 4.0) if total else hold, fade_in=2.5,
                     fade_out=3.0, amax=0.78, glow=0.45))          # stays for the whole video, silver like the badge
    parts = [(_crop(text_mask(["Audio Story"], LATIN(), 25, 500, pad=8)), TXT),
             (_crop(text_mask(["|"], LATIN(), 25, 60, pad=8)), LAV),
             (_crop(text_mask([BRAND_WORD], BOLD_HI(), 34 if BRAND_WORD == "सुकून" else 30, 300, pad=10)), BLUE),
             (_crop(text_mask(["|"], LATIN(), 25, 60, pad=8)), LAV),
             (_crop(text_mask(["Sleep Therapy"], LATIN(), 25, 500, pad=8)), TXT)]
    gap = 22
    tw = sum(m.shape[1] for m, _ in parts) + gap * (len(parts) - 1)
    hmax = max(m.shape[0] for m, _ in parts)
    x, yc = (W - tw) // 2, SAFE_BOTTOM - 14                       # bottom strip, under the light line (safe area)
    for m, col in parts:
        sp.append(Sprite(m, x, yc - hmax // 2 + (hmax - m.shape[0]) // 2, col, 3.0,
                         (total - 4.0) if total else hold, fade_in=3.0, fade_out=3.0,
                         amax=0.62 if col is BLUE else 0.48, react=0.55))
        x += m.shape[1] + gap
    return sp


# ------------------------------------------------------------------ audio analysis
def audio_bands(wav, sr, fps, n_bands=48, fmin=60, fmax=7000):
    """Per-video-frame log-spaced band energies, normalised and smoothed (attack/release)."""
    hop = sr / fps
    n = int(len(wav) / hop)
    win = 2048
    pad = np.pad(wav, (win // 2, win // 2))
    freqs = np.fft.rfftfreq(win, 1 / sr)
    edges = np.geomspace(fmin, fmax, n_bands + 1)
    idx = [np.where((freqs >= edges[i]) & (freqs < edges[i + 1]))[0] for i in range(n_bands)]
    idx = [ix if len(ix) else np.array([np.argmin(abs(freqs - edges[i]))]) for i, ix in enumerate(idx)]
    hann = np.hanning(win).astype(np.float32)
    E = np.zeros((n, n_bands), np.float32)
    for f in range(n):
        s = int(f * hop)
        spec = np.abs(np.fft.rfft(pad[s:s + win] * hann))
        E[f] = [spec[ix].mean() for ix in idx]
    E = np.log1p(E * 50)
    lo, hi = np.percentile(E, 20, axis=0), np.percentile(E, 98, axis=0)
    E = np.clip((E - lo) / np.maximum(hi - lo, 1e-6), 0, 1)
    out = np.zeros_like(E); cur = np.zeros(n_bands, np.float32)
    for f in range(n):                                      # smooth: quick-ish attack, slow release
        k = np.where(E[f] > cur, 0.45, 0.10)
        cur = cur + (E[f] - cur) * k
        out[f] = cur
    level = np.clip(out.mean(1) * 1.4, 0, 1)
    return out, level


# ------------------------------------------------------------------ the upgraded equalizer
class LightLine:
    """A thin glowing waveform that ripples with the audio; ends fade out; never jittery."""

    def __init__(self, width=1000, y=870, height=130):
        self.w, self.h, self.y = width, height, y
        self.top = y - height // 2
        self.x0 = (W - width) // 2
        x = np.linspace(-1, 1, width, dtype=np.float32)
        self.env = np.clip(np.cos(x * np.pi / 2), 0, 1) ** 1.6            # fade to nothing at the ends
        self.x = x
        self.yy = np.arange(height, dtype=np.float32)[:, None] - height / 2

    def draw(self, img, bands, t, level):
        nb = len(bands)
        sym = np.concatenate([bands[::-1], bands])          # mirror: lows in centre, highs outward
        amp = np.interp(np.abs(self.x) * (nb - 1), np.arange(nb), bands)
        wave = np.sin(self.x * 5 * np.pi + t * 1.6) * 0.65 + np.sin(self.x * 11 * np.pi - t * 2.3) * 0.35
        amp = np.convolve(amp, np.ones(61) / 61, mode='same')     # no spikes
        off = wave * (0.12 + amp * 0.9) * self.env * (self.h * 0.30) + np.sin(self.x * 3 * np.pi + t * 0.7) * 3 * self.env
        d = np.abs(self.yy - off[None, :])
        core = np.exp(-(d / 1.3) ** 2)
        glow = np.exp(-(d / 7.0) ** 2) * 0.55 + np.exp(-(d / 24.0) ** 2) * 0.16
        a = (core * 0.85 + glow) * (self.env[None, :] * (0.45 + 0.55 * level))
        y0 = self.y - self.h // 2
        reg = img[y0:y0 + self.h, self.x0:self.x0 + self.w]
        reg[:] = reg + (LAV * 0.9)[None, None, :] * a[..., None] * 0.9 + TXT * (core * self.env * 0.25 * level)[..., None]


class SoftBars:
    """Upgraded classic equalizer: mirrored rounded bars with glow and reflection."""

    def __init__(self, n=40, gap=10, bar=8, y=870, maxh=110):
        self.n, self.gap, self.bar, self.y, self.maxh = n, gap, bar, y, maxh
        self.top = y - maxh
        self.w = n * 2 * (bar + gap)
        self.x0 = (W - self.w) // 2

    def draw(self, img, bands, t, level):
        nb = len(bands)
        vals = np.interp(np.linspace(0, nb - 1, self.n), np.arange(nb), bands)
        vals = np.concatenate([vals[::-1], vals])
        hgt = int(self.maxh * 1.6)
        layer = np.zeros((hgt, self.w), np.float32)
        base = int(self.maxh)
        xs = np.arange(len(vals)) * (self.bar + self.gap)
        env = np.clip(np.cos(np.linspace(-1, 1, len(vals)) * np.pi / 2), 0, 1) ** 1.2
        for i, v in enumerate(vals):
            h = int(5 + (v ** 0.8) * env[i] * self.maxh * 1.4)
            if env[i] < 0.12:
                continue
            layer[max(0, base - h):base, xs[i]:xs[i] + self.bar] = 1.0
            r = int(h * 0.35)                                # faint reflection
            layer[base + 2:base + 2 + r, xs[i]:xs[i] + self.bar] = np.linspace(0.25, 0, r)[:, None] if r else 0
        layer = np.clip(_box(layer, 1) * 1.3, 0, 1)            # rounded edges
        glow = _box(layer, 6)
        a = np.clip(layer * 0.75 + glow * 0.6, 0, 1) * (0.5 + 0.5 * level)
        y0 = self.y - base
        reg = img[y0:y0 + hgt, self.x0:self.x0 + self.w]
        reg[:] = reg + LAV * a[..., None] * 0.7


def bottom_shade(strength=0.30, start=0.62):
    y = np.linspace(0, 1, H, dtype=np.float32)
    return (1 - strength * np.clip((y - start) / (1 - start), 0, 1) ** 1.3)[:, None, None]


# ------------------------------------------------------------------ Hindi captions: calm "ink-flow" reveal
class CaptionHi:
    """Devanagari line that flows in left->right with a feathered edge, comes into focus,
    breathes softly while held, then dissolves upward into its glow."""

    def __init__(self, text, start, end, size=78, y_center=732, reveal=3.2, outro=2.8, col=None, fontfile=None):
        self.col = TXT if col is None else np.asarray(col, np.float32)
        lines = wrap_balanced(text, 44)
        m = text_mask(lines, fontfile or HINDI(), size, W, line_h=int(size * 1.45))
        self.mask = np.maximum(m, np.roll(m, 1, 1) * 0.85)          # a hair bolder
        self.soft = _box(self.mask, 4)
        self.glow = _box(self.mask, 16)
        self.h = self.mask.shape[0]
        self.y0 = int(y_center - self.h / 2 - (len(lines) - 1) * 58)   # 2 lines grow upward
        cols = np.where(self.mask.max(0) > 0.05)[0]
        self.xl, self.xr = (cols.min(), cols.max()) if len(cols) else (0, W)
        self.x = np.arange(W, dtype=np.float32)
        self.start, self.end, self.reveal, self.outro = start, end, reveal, outro
        self.feather = 260.0

    def draw(self, img, t):
        if t < self.start or t > self.end:
            return
        lt, rem = t - self.start, self.end - t
        if lt < self.reveal:                                         # ink flowing in
            p = lt / self.reveal
            p = p * p * (3 - 2 * p)
            front = self.xl - self.feather + p * (self.xr - self.xl + 2 * self.feather)
            col = np.clip((front - self.x) / self.feather, 0, 1)
            col = col * col * (3 - 2 * col)
            focus = col[None, :]
            alpha_col = col[None, :]
            dy = int(round((1 - p) * 8))
        else:
            focus = 1.0; alpha_col = 1.0; dy = 0
        out_k = 1.0
        if rem < self.outro:                                         # dissolving
            q = rem / self.outro
            out_k = q * q * (3 - 2 * q)
            focus = focus * out_k
            dy = -int(round((1 - out_k) * 10))
        breathe = 0.85 + 0.15 * np.sin((t - self.start) * 2 * np.pi / 10.0)   # one slow breath / 10 s
        m = self.mask * focus + self.soft * (1 - focus) if not np.isscalar(focus) or focus < 1 else self.mask
        a_txt = (m * alpha_col * out_k * 0.94)
        a_glow = self.glow * alpha_col * (0.45 * breathe * out_k + 0.25 * (1 - out_k))
        y0 = self.y0 + dy
        reg = img[y0:y0 + self.h]
        reg[:] = reg + LAV * (a_glow * 0.65)[..., None]
        reg[:] = reg * (1 - a_txt[..., None]) + self.col * a_txt[..., None]


class TitleCard:
    """Centered Hindi title that ink-flows in, holds, then dissolves (used for intro and the goodnight outro)."""

    def __init__(self, text, start, end, size=112, sub=None, y_center=500, sub_size=36, col=None, fontfile=None):
        self.cap = CaptionHi(text, start, end, size=size, y_center=y_center, reveal=3.6, outro=3.2, col=col, fontfile=fontfile)
        self.sub = CaptionHi(sub, start + 1.2, end, size=sub_size, y_center=y_center + int(size * 0.95 + sub_size * 0.3), reveal=2.6,
                             outro=3.0) if sub else None
        self.start, self.end = start, end
        self.y0 = self.cap.y0
        self.top = self.cap.y0 - 30
        self.bottom = (self.sub.y0 + self.sub.h if self.sub else self.cap.y0 + self.cap.h) + 30

    def draw(self, img, t):
        self.cap.draw(img, t)
        if self.sub:
            self.sub.draw(img, t)
