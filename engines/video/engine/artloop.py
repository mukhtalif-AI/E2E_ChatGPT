"""
artloop.py - turn a still Sukoon artwork into a seamless, living 1080p loop.

Effects (all periodic over the loop, so it repeats invisibly):
  slow breathing camera (zoom + drift), twinkling stars, moon glow that pulses with a
  6-breaths/min rhythm, flickering warm lights (lanterns/candles), drifting fireflies,
  low mist, vignette, and fine film grain (also prevents colour banding in dark skies).
  + elements.py: lamps / candles glow and breathe, hot cups steam; indoor rooms get no stars/fireflies/mist.

Usage:  python artloop.py "D:\\Audio asset\\Images\\sukoon 6.png" --seconds 60 --out loops\\sukoon6.mp4
"""
import sys as _sys
for _s in (_sys.stdout, _sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")   # Hindi text in logs/consoles on Windows
    except Exception:
        pass
import argparse, os, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np

TAU = 2 * np.pi
W, H = 1920, 1080
PAD = 1.08                      # canvas margin for camera moves


# ------------------------------------------------------------------ small numpy image tools
def blur(a, r):
    """Fast separable box blur (x3 ~ gaussian). a: HxW or HxWxC float32."""
    r = int(max(1, r))
    out = a.astype(np.float32)
    for _ in range(3):
        for ax in (0, 1):
            c = np.cumsum(np.pad(out, [(r + 1, r) if i == ax else (0, 0) for i in range(out.ndim)], mode="edge"),
                          axis=ax, dtype=np.float64)
            hi = np.take(c, np.arange(2 * r + 1, c.shape[ax]), axis=ax)
            lo = np.take(c, np.arange(0, c.shape[ax] - 2 * r - 1), axis=ax)
            out = ((hi - lo) / (2 * r + 1)).astype(np.float32)
    return out


def resize(img, w, h):
    """Area/bilinear resize for HxWx3 float32 (quality is fine for <=1.3x scaling)."""
    sh, sw = img.shape[:2]
    ys = (np.arange(h) + 0.5) * sh / h - 0.5
    xs = (np.arange(w) + 0.5) * sw / w - 0.5
    return bilinear(img, *np.meshgrid(xs, ys))


def bilinear(img, X, Y):
    h, w = img.shape[:2]
    X = np.clip(X, 0, w - 1.001); Y = np.clip(Y, 0, h - 1.001)
    x0 = X.astype(np.int32); y0 = Y.astype(np.int32)
    fx = (X - x0)[..., None].astype(np.float32); fy = (Y - y0)[..., None].astype(np.float32)
    a = img[y0, x0]; b = img[y0, x0 + 1]; c = img[y0 + 1, x0]; d = img[y0 + 1, x0 + 1]
    return (a * (1 - fx) + b * fx) * (1 - fy) + (c * (1 - fx) + d * fx) * fy


def load_image(path):
    try:
        import av
        with av.open(path) as c:
            return next(c.decode(video=0)).to_ndarray(format="rgb24")
    except ImportError:
        from PIL import Image
        return np.asarray(Image.open(path).convert("RGB"))


# ------------------------------------------------------------------ scene analysis
class Scene:
    def __init__(self, path, seed=7, seconds=40, elements_cache=None, log=print):
        rng = np.random.default_rng(seed)
        src = load_image(path).astype(np.float32) / 255.0
        cw, ch = int(W * PAD), int(H * PAD)
        sh, sw = src.shape[:2]                                  # cover-fit into canvas
        s = max(cw / sw, ch / sh)
        big = resize(src, int(sw * s + 0.5), int(sh * s + 0.5))
        oy, ox = (big.shape[0] - ch) // 2, (big.shape[1] - cw) // 2
        self.base = np.ascontiguousarray(big[oy:oy + ch, ox:ox + cw])
        self.ch, self.cw = ch, cw
        def to_canvas(b):
            return [int(b[0] * s - ox), int(b[1] * s - oy), int(b[2] * s - ox), int(b[3] * s - oy)]
        self.el = None
        try:
            import elements
            info = elements.find(src, elements_cache, log)
            self.el = elements.Elements(info, self.base, to_canvas, seconds, seed)
        except Exception as e:                                   # never break a video over this
            log(f"  elements: skipped ({str(e)[:120]})")
        indoor = bool(self.el and self.el.indoor)
        lamps = bool(self.el and any(k in ("lamp", "candle", "fire") for k in self.el.kinds))
        lum = self.base @ np.array([0.299, 0.587, 0.114], np.float32)
        yy, xx = np.mgrid[0:ch, 0:cw]

        # stars: small bright points in the upper sky
        local = lum - blur(lum, 4)
        star = np.clip((local - 0.06) / 0.18, 0, 1) * (lum > 0.35) * (yy < ch * 0.55)
        self.star = blur(star, 1)[..., None] * np.array([0.85, 0.85, 1.0], np.float32)
        cell = (yy // 5) * 7919 + (xx // 5) * 104729            # same phase across one star
        self.star_phase = ((cell * 2654435761) % 1000 / 1000.0).astype(np.float32)[..., None]
        self.star_freq = (2 + (cell % 3)).astype(np.float32)[..., None]

        # moon: brightest big blob in the upper 60%
        sm = blur(lum, 25); sm[int(ch * 0.6):] = 0
        my, mx = np.unravel_index(np.argmax(sm), sm.shape)
        if self.el and self.el.moon:
            mx, my = int(self.el.moon[0]), int(self.el.moon[1])
        area = (blur(lum, 3) > 0.75)[max(0, my - 150):my + 150, max(0, mx - 150):mx + 150].sum()
        mr = max(25.0, np.sqrt(area / np.pi))
        d = np.hypot(xx - mx, yy - my)
        self.moon_halo = (np.exp(-(d / (mr * 3.2)) ** 2) * 0.9 + np.exp(-(d / (mr * 9)) ** 2) * 0.35)[..., None]
        self.moon_col = np.array([0.80, 0.72, 1.0], np.float32)
        self.moon = (mx, my, mr)
        if indoor or (lamps and not (self.el and self.el.moon)):
            self.moon_halo = self.moon_halo * 0                   # the bright blob is a lamp, not a moon
        if indoor:
            self.star = self.star * 0

        r, g, b = self.base[..., 0], self.base[..., 1], self.base[..., 2]
        # moon reflection on water: bright band below the horizon under the moon -> shimmer
        band = np.exp(-((xx - mx) / (mr * 1.8)) ** 2) * (yy > ch * 0.52)
        l1 = blur(lum, 1)
        gy = np.abs(np.diff(l1, axis=0, append=l1[-1:]))           # water reflections are horizontal
        gx = np.abs(np.diff(l1, axis=1, append=l1[:, -1:]))        # streaks: strong d/dy, weak d/dx
        streak = np.clip((blur(gy, 6) - 1.6 * blur(gx, 6)) * 60, 0, 1)
        sat = self.base.max(-1) - self.base.min(-1)
        lav = np.clip(1 - np.abs((b - r) - 0.12) * 4, 0, 1) * (b > g)   # moonlight on water is lavender/blue
        texture = blur(np.abs(lum - blur(lum, 3)), 8)
        calm = np.clip(1 - texture * 25, 0, 1)                        # faces/fabric have fine texture
        bright = np.clip((lum - blur(lum, 40)) * 4 + (lum - 0.25) * 1.5, 0, 1)
        # only shimmer when the image clearly has a moon-pillar on water (horizontal streaks under the moon)
        inband = band > 0.5
        conf = float(np.percentile(streak[inband], 90)) if inband.any() else 0.0
        self.has_water = conf > 0.2 and not indoor
        refl = bright * band * np.clip(streak * 3, 0, 1) * lav if self.has_water else np.zeros_like(lum)
        self.refl = blur(refl, 3)[..., None].astype(np.float32)
        self.refl_y = yy.astype(np.float32)

        # warm lights (lanterns, candles, windows): bright + orange
        r, g, b = self.base[..., 0], self.base[..., 1], self.base[..., 2]
        warm = np.clip((r - b - 0.15) * 3, 0, 1) * np.clip((lum - 0.35) * 3, 0, 1)
        self.warm = blur(warm, 18)[..., None] * np.array([1.0, 0.62, 0.25], np.float32)
        if lamps:
            self.warm = self.warm * 0                             # the lamps glow on their own (elements)

        # fireflies: lower 70% of frame, avoid moon
        self.moon_out = (mx - (cw - W) / 2, my - (ch - H) / 2)
        self.flies = []
        for i in range(0 if indoor else 26):
            self.flies.append(dict(
                x=rng.uniform(0.05, 0.95) * cw, y=rng.uniform(0.35, 0.95) * ch,
                ax=rng.uniform(25, 90), ay=rng.uniform(15, 50),
                fx=int(rng.integers(1, 3)), fy=int(rng.integers(1, 4)),
                px=rng.uniform(0, TAU), py=rng.uniform(0, TAU),
                fb=int(rng.integers(2, 6)), pb=rng.uniform(0, TAU),
                size=rng.uniform(1.6, 3.2),
                col=np.array([1.0, 0.80, 0.45] if rng.random() < 0.65 else [0.80, 0.72, 1.0], np.float32)))
        self.sprites = {}

        # mist: lavender haze hugging the bottom
        self.mist_mask = (np.clip((yy / ch - 0.55) / 0.45, 0, 1) ** 1.6)[..., None].astype(np.float32)
        if indoor:
            self.mist_mask = self.mist_mask * 0
        self.mist_x = (xx / cw).astype(np.float32)
        self.mist_y = (yy / ch).astype(np.float32)

        # output-space helpers
        oyy, oxx = np.mgrid[0:H, 0:W].astype(np.float32)
        self.ox, self.oy = oxx, oyy
        v = np.hypot((oxx - W / 2) / (W / 2), (oyy - H / 2) / (H / 2))
        self.vignette = (1 - 0.38 * np.clip(v - 0.35, 0, 1) ** 1.4)[..., None].astype(np.float32)
        self.grain_rng = np.random.default_rng(1)
        yv = np.linspace(0, 1, H, dtype=np.float32)
        self.shade = (1 - 0.30 * np.clip((yv - 0.62) / 0.38, 0, 1) ** 1.3)[:, None, None]

    def sprite(self, size):
        k = round(size * 4) / 4
        if k not in self.sprites:
            r = int(size * 9)
            y, x = np.mgrid[-r:r + 1, -r:r + 1].astype(np.float32)
            d = np.hypot(x, y)
            self.sprites[k] = (np.exp(-(d / size) ** 2) + 0.35 * np.exp(-(d / (size * 4.5)) ** 2))[..., None]
        return self.sprites[k]

    # ---------------------------------------------------------- fast path (precomputed sparse/cropped layers)
    def _prep(self):
        if getattr(self, "_ready", False):
            return
        def bbox(mask, thr=0.003):
            m = mask.max(-1) if mask.ndim == 3 else mask
            ys, xs = np.where(m > thr)
            if not len(ys):
                return None
            return ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
        st = self.star.max(-1) > 0.01
        self.st_idx = np.nonzero(st)
        self.st_val = self.star[self.st_idx]
        self.st_ph = self.star_phase[..., 0][self.st_idx][:, None]
        self.st_fr = self.star_freq[..., 0][self.st_idx][:, None]
        halo = self.moon_halo * self.moon_col
        self.halo_bb = bbox(halo)
        if self.halo_bb:
            y0, y1, x0, x1 = self.halo_bb
            self.halo_c = halo[y0:y1, x0:x1]
        self.refl_bb = bbox(self.refl)
        if self.refl_bb:
            y0, y1, x0, x1 = self.refl_bb
            self.refl_c = self.refl[y0:y1, x0:x1] * self.moon_col
            self.refl_rows = np.arange(y0, y1, dtype=np.float32)[:, None, None]
        self.warm_bb = bbox(self.warm)
        if self.warm_bb:
            y0, y1, x0, x1 = self.warm_bb
            self.warm_c = self.warm[y0:y1, x0:x1]
        rows = np.where(self.mist_mask[:, 0, 0] > 0)[0]
        self.mist_r0 = rows.min() if len(rows) else self.ch - 1
        mm = self.mist_mask[self.mist_r0:]
        self.mist_k = (mm * 0.10).astype(np.float32)                 # (R,1,1)
        x = self.mist_x[0]                                             # (cw,)
        yv = self.mist_y[self.mist_r0:, 0]                             # (R,)
        self.mist_xa, self.mist_xb = TAU * x * 1.5, TAU * x * 0.7
        self.mist_c6, self.mist_s6 = np.cos(yv * 6)[:, None], np.sin(yv * 6)[:, None]
        self.mist_col = np.array([0.42, 0.36, 0.62], np.float32)
        self.vs = (self.vignette * self.shade).astype(np.float32)
        g = np.random.default_rng(1)
        self.grains = [g.normal(0, 0.006, (H, W, 1)).astype(np.float32) for _ in range(6)]
        self._gi = 0
        self._ready = True

    def frame(self, p, breaths):
        self._prep()
        br = 0.5 - 0.5 * np.cos(TAU * p * breaths)              # 0..1 breathing
        img = self.base.copy()
        tw = 0.5 + 0.5 * np.sin(TAU * (self.st_fr * p + self.st_ph))
        img[self.st_idx] += self.st_val * (tw * 0.55 - 0.15)
        if self.halo_bb:
            y0, y1, x0, x1 = self.halo_bb
            img[y0:y1, x0:x1] += self.halo_c * (0.06 + 0.07 * br)
        if self.refl_bb:
            y0, y1, x0, x1 = self.refl_bb
            stripes = 0.5 + 0.5 * np.sin(self.refl_rows * 0.45 - TAU * 9 * p)
            img[y0:y1, x0:x1] += self.refl_c * (stripes * 0.16 - 0.05)
        if self.warm_bb:
            y0, y1, x0, x1 = self.warm_bb
            flick = 0.75 + 0.15 * np.sin(TAU * 7 * p) + 0.10 * np.sin(TAU * 13 * p + 1.3)
            img[y0:y1, x0:x1] += self.warm_c * (0.22 * flick)
        if self.el:
            self.el.apply(img, self.base, p)
        # mist: sin(a+p) * sin(b-2p+6y) built from 1-D waves (cheap outer products)
        sa = np.sin(self.mist_xa + TAU * p)[None, :]
        bb = self.mist_xb - TAU * 2 * p
        m = (sa * (np.sin(bb)[None, :] * self.mist_c6 + np.cos(bb)[None, :] * self.mist_s6)) * 0.5 + 0.5
        k = self.mist_k * m[..., None]
        band = img[self.mist_r0:]
        band *= (1 - k)
        band += k * self.mist_col
        for f in self.flies:
            x = f["x"] + f["ax"] * np.sin(TAU * f["fx"] * p + f["px"])
            y = f["y"] + f["ay"] * np.sin(TAU * f["fy"] * p + f["py"])
            a = (0.5 + 0.5 * np.sin(TAU * f["fb"] * p + f["pb"])) ** 3
            if a < 0.02:
                continue
            spr = self.sprite(f["size"]); r = spr.shape[0] // 2
            x0, y0 = int(x) - r, int(y) - r
            if x0 < 0 or y0 < 0 or x0 + spr.shape[1] > self.cw or y0 + spr.shape[0] > self.ch:
                continue
            img[y0:y0 + spr.shape[0], x0:x0 + spr.shape[1]] += spr * f["col"] * a * 0.9
        # breathing camera: pure scale+translate -> separable bilinear (rows, then columns)
        s = 1.0 + 0.015 * (1 - np.cos(TAU * p))
        dx = 18 * np.sin(TAU * p); dy = 10 * np.sin(TAU * p * 2)
        cx, cy = self.cw / 2 + dx, self.ch / 2 + dy
        Y = np.clip(cy + (np.arange(H, dtype=np.float32) - H / 2) / s, 0, self.ch - 1.001)
        X = np.clip(cx + (np.arange(W, dtype=np.float32) - W / 2) / s, 0, self.cw - 1.001)
        y0 = Y.astype(np.int32); fy = (Y - y0)[:, None, None]
        rows = img[y0] * (1 - fy) + img[y0 + 1] * fy
        x0 = X.astype(np.int32); fx = (X - x0)[None, :, None]
        out = rows[:, x0] * (1 - fx) + rows[:, x0 + 1] * fx
        out *= self.vs
        return out

    def finish(self, img):
        self._prep()
        self._gi = (self._gi + 1) % len(self.grains)
        img += self.grains[self._gi]
        np.clip(img, 0, 1, out=img)
        return (img * 255 + 0.5).astype(np.uint8)


def render_loop(image, out, seconds=60, fps=24, breaths_per_min=6, writer=None, seed=7, elements_cache=None):
    import sleepgen
    sc = Scene(image, seed, seconds, elements_cache or (out + ".elements.json"))
    n = int(seconds * fps)
    breaths = max(1, round(seconds * breaths_per_min / 60))
    wr = writer or sleepgen.make_writer(out, W, H, fps, crf=17)
    print(f"  moon at {sc.moon[:2]}, r={sc.moon[2]:.0f}, water shimmer={sc.has_water}; "
          f"alive: {', '.join(sc.el.kinds) if sc.el and sc.el.kinds else 'sky only'}"
          f"{' (indoor)' if sc.el and sc.el.indoor else ''}; encoder {getattr(wr, 'codec', '?')}")
    t0 = time.time()
    sc._prep()

    def one(i):                                            # every frame depends only on its time p (no state),
        img = sc.frame(i / n, breaths)                     # so several are drawn at once (numpy frees the GIL)
        img += sc.grains[i % len(sc.grains)]
        np.clip(img, 0, 1, out=img)
        return (img * 255 + 0.5).astype(np.uint8)

    from concurrent.futures import ThreadPoolExecutor
    workers = max(1, min(6, (os.cpu_count() or 2) - 1, int(os.environ.get("ARTLOOP_THREADS") or 6)))
    with ThreadPoolExecutor(workers) as ex:
        pending = {}
        nxt = 0
        for i in range(n):
            while nxt < n and len(pending) < workers * 2:
                pending[nxt] = ex.submit(one, nxt); nxt += 1
            wr.write(pending.pop(i).result())
            if i % max(1, n // 10) == 0:
                print(f"  living artwork: {i}/{n} frames ({time.time()-t0:.0f}s, {workers} at once)", flush=True)
    wr.close()
    print(f"done -> {out} ({time.time()-t0:.0f}s)")
    if sc.el and sc.el.used:                                     # downloaded clips: note where they are used
        import json, elements
        json.dump({"clips": sc.el.used}, open(out + ".clips.json", "w", encoding="utf-8"))
        elements.note_use(sc.el.used, f"living artwork {os.path.basename(out)} (picture {os.path.basename(image)})")
    return sc


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("image"); ap.add_argument("--seconds", type=float, default=60)
    ap.add_argument("--fps", type=int, default=24); ap.add_argument("--out", required=True)
    ap.add_argument("--elements", default=None, help="saved element list (.json); default <out>.elements.json")
    a = ap.parse_args()
    render_loop(a.image, a.out, a.seconds, a.fps, elements_cache=a.elements)
