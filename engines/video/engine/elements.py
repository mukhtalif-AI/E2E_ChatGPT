"""elements.py - find the things in an artwork that should come alive, and animate them in the art loop.

  lamp / candle / lantern / diya  -> soft warm glow that breathes (6 / min) with a gentle flicker,
                                     the light it throws on the room (table, curtain, wall) breathes with it
  cup / mug / kettle (hot drink)  -> steam that rises and curls up from the rim, softly, forever
  indoor scene                    -> no fireflies / stars / mist / moon halo (those are for night skies)

Finding: the selected local vision model once per picture -> cached next to the picture's loop
(<loop>.elements.json, editable by hand). If the local AI server is unavailable, use a simple light-finder.
Everything is periodic over the loop, so the loop still repeats invisibly.

Test alone:  python elements.py "picture.png"            (prints what it finds)
"""
import base64, io, json, os, re
import numpy as np
import sv_av

TAU = 2 * np.pi
PROMPT = ("This picture is {w} x {h} pixels. Find the things in it that could gently move in a calm animation. "
          "Reply ONLY with JSON: {{\"indoor\": true or false, \"items\": [{{\"kind\": \"...\", \"box\": [x1, y1, x2, y2]}}]}} "
          "with pixel coordinates of this picture. kind is one of: "
          "\"lamp\" (a lamp, bulb, lantern or glowing light fitting), \"candle\" (a candle, diya or oil lamp flame), "
          "\"cup\" (a cup, mug, glass, kettle or teapot with a hot drink - box the cup itself), "
          "\"steam\" (steam or smoke already drawn rising from a cup or incense - box the whole wisp), "
          "\"moon\", \"fire\" (fireplace, campfire). Leave out anything that is not clearly there.")
KINDS = {"lamp": "lamp", "light": "lamp", "lantern": "lamp", "bulb": "lamp", "candle": "candle", "diya": "candle",
         "flame": "candle", "cup": "cup", "mug": "cup", "tea": "cup", "teapot": "cup", "kettle": "cup",
         "glass": "cup", "steam": "steam", "smoke": "steam", "vapour": "steam", "vapor": "steam", "moon": "moon", "fire": "fire", "fireplace": "fire", "campfire": "fire"}


# ------------------------------------------------------------------ finding
def _models():
    try:
        return sv_av.local_tags()
    except Exception:
        return []


def _jpeg(src, w):
    from PIL import Image
    im = Image.fromarray((np.clip(src, 0, 1) * 255).astype(np.uint8))
    h = int(round(src.shape[0] * w / src.shape[1]))
    buf = io.BytesIO()
    im.resize((w, h), Image.LANCZOS).save(buf, "JPEG", quality=90)
    return base64.b64encode(buf.getvalue()).decode(), w, h


def ask_vision(src, log=print):
    """src: HxWx3 float 0..1 (the picture as it is). Returns dict(indoor, items[kind, box in picture px]) or None."""
    try:
        img, w, h = _jpeg(src, 1000)
    except Exception as e:
        log(f"  elements: could not prepare the picture ({e})")
        return None
    H0, W0 = src.shape[:2]
    for model in _models():
        try:
            txt = sv_av.local_chat(model, PROMPT.format(w=w, h=h), json_mode=True, timeout=300, image=img)
            data = json.loads(txt)
        except Exception as e:
            log(f"  elements: {model} gave no answer ({str(e)[:80]})")
            continue
        items = []
        for it in data.get("items", []) or []:
            if not isinstance(it, dict):
                continue
            kind = KINDS.get(re.sub(r"[^a-z]", "", str(it.get("kind", "")).lower()))
            b = it.get("box") or it.get("bbox_2d") or it.get("bbox")
            if not kind or not isinstance(b, (list, tuple)) or len(b) < 4:
                continue
            try:
                x1, y1, x2, y2 = [float(v) for v in b[:4]]
            except (TypeError, ValueError):
                continue
            if max(x1, x2, y1, y2) <= 1.0:                       # 0..1
                x1, x2, y1, y2 = x1 * w, x2 * w, y1 * h, y2 * h
            elif max(x1, x2) <= 1000 and max(y1, y2) <= 1000 and max(y1, y2) > h * 1.05:   # 0..1000 grid
                x1, x2, y1, y2 = x1 * w / 1000, x2 * w / 1000, y1 * h / 1000, y2 * h / 1000
            x1, x2 = sorted((x1, x2)); y1, y2 = sorted((y1, y2))
            if x2 - x1 < 4 or y2 - y1 < 4 or (x2 - x1) * (y2 - y1) > 0.5 * w * h:
                continue
            items.append({"kind": kind, "box": [round(x1 * W0 / w), round(y1 * H0 / h),
                                                round(x2 * W0 / w), round(y2 * H0 / h)]})
        log(f"  elements ({model}): indoor={bool(data.get('indoor'))}, "
            + (", ".join(i["kind"] for i in items) or "nothing to animate"))
        return {"indoor": bool(data.get("indoor")), "items": items, "by": model}
    return None


def light_finder(src):
    """No vision model: the brightest big blob; warm -> a lamp (indoor-ish), cool -> the moon (old behaviour)."""
    lum = src @ np.array([0.299, 0.587, 0.114], np.float32)
    from artloop import blur
    sm = blur(lum, 20)
    y, x = np.unravel_index(np.argmax(sm), sm.shape)
    if sm[y, x] < 0.45:
        return {"indoor": False, "items": [], "by": "light-finder"}
    m = blur(lum, 2) > max(0.55, sm[y, x] * 0.9)
    r = int(max(src.shape[:2]) * 0.08)
    sub = m[max(0, y - r):y + r, max(0, x - r):x + r]
    ys, xs = np.nonzero(sub)
    if not len(ys):
        return {"indoor": False, "items": [], "by": "light-finder"}
    x1, x2 = xs.min() + max(0, x - r), xs.max() + max(0, x - r)
    y1, y2 = ys.min() + max(0, y - r), ys.max() + max(0, y - r)
    col = src[y1:y2 + 1, x1:x2 + 1][sub[ys.min():ys.max() + 1, xs.min():xs.max() + 1]].mean(0)
    warm = col[0] - col[2] > 0.08
    return {"indoor": bool(warm), "items": [{"kind": "lamp" if warm else "moon", "box": [int(x1), int(y1), int(x2), int(y2)]}],
            "by": "light-finder"}


def find(src, cache=None, log=print):
    if cache and os.path.exists(cache):
        try:
            d = json.load(open(cache, encoding="utf-8"))
            log(f"  elements: {', '.join(i['kind'] for i in d.get('items', [])) or 'none'} (saved list {os.path.basename(cache)})")
            return d
        except Exception:
            pass
    d = ask_vision(src, log) or light_finder(src)
    if d.get("by") == "light-finder":
        log(f"  elements: vision AI not available - light finder: {', '.join(i['kind'] for i in d['items']) or 'none'}")
    d["picture_size"] = [int(src.shape[1]), int(src.shape[0])]
    if cache and d.get("by") != "light-finder":                # ask the vision AI again next time
        try:
            json.dump(d, open(cache, "w", encoding="utf-8"), indent=1)
        except OSError:
            pass
    return d


# ------------------------------------------------------------------ animating
def _smooth_noise(h, w, scale, rng):
    """Periodic (wraps in both directions) smooth noise in 0..1, via FFT low-pass."""
    n = rng.standard_normal((h, w)).astype(np.float32)
    fy = np.fft.fftfreq(h)[:, None]; fx = np.fft.rfftfreq(w)[None, :]
    k = np.exp(-((fy * scale) ** 2 + (fx * scale) ** 2) * 6)
    out = np.fft.irfft2(np.fft.rfft2(n) * k, s=(h, w)).astype(np.float32)
    out -= out.min(); out /= max(1e-6, out.max())
    return out


class Lamp:
    """Soft breathing glow + the light it throws on the room."""
    def __init__(self, base, box, candle, seconds, rng):
        ch, cw = base.shape[:2]
        x1, y1, x2, y2 = box
        lum = base @ np.array([0.299, 0.587, 0.114], np.float32)
        crop = base[y1:y2, x1:x2].reshape(-1, 3); cl = lum[y1:y2, x1:x2].reshape(-1)
        hot = crop[cl >= np.percentile(cl, 80)] if len(cl) else np.ones((1, 3), np.float32)
        col = hot.mean(0); col = col / max(1e-3, col.max())
        self.col = (0.5 * col + 0.5 * np.array([1.0, 0.72, 0.42], np.float32)).astype(np.float32)   # warm it up
        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
        R = 0.5 * np.sqrt(max(4, (x2 - x1) * (y2 - y1)))
        reach = int(min(max(ch, cw), R * 7))
        self.bb = (max(0, int(cy - reach)), min(ch, int(cy + reach)), max(0, int(cx - reach)), min(cw, int(cx + reach)))
        Y0, Y1, X0, X1 = self.bb
        yy, xx = np.mgrid[Y0:Y1, X0:X1].astype(np.float32)
        d = np.hypot((xx - cx) / 1.0, (yy - cy) / 1.0)
        b = base[Y0:Y1, X0:X1]; l = lum[Y0:Y1, X0:X1]
        warmish = np.clip((b[..., 0] - b[..., 2]) * 3 + 0.35, 0, 1)
        # light already on the room near the lamp -> brighten it a little, in its own colours
        self.lit = (np.exp(-(d / (R * 4.5)) ** 2) * np.clip(l * 1.6, 0, 1) * warmish)[..., None].astype(np.float32)
        # the lamp shade itself + a soft halo in the air
        core = np.clip((l - 0.55) * 3, 0, 1) * (d < R * 1.6)
        from artloop import blur
        self.core = blur(core.astype(np.float32), max(2, R * 0.15))[..., None]
        self.halo = (0.55 * np.exp(-(d / (R * 1.5)) ** 2) + 0.45 * np.exp(-(d / (R * 4.0)) ** 2))[..., None].astype(np.float32)
        self.candle = candle
        self.breaths = max(1, round(seconds * 6 / 60))
        self.ph = rng.uniform(0, TAU, 4)
        self.center = (cx, cy, R)

    def gain(self, p):
        br = 0.5 - 0.5 * np.cos(TAU * p * self.breaths)              # 0..1, with the breathing
        f = self.ph
        if self.candle:                                               # a flame dances
            fl = 0.10 * np.sin(TAU * 23 * p + f[0]) + 0.07 * np.sin(TAU * 37 * p + f[1]) + 0.05 * np.sin(TAU * 61 * p + f[2])
        else:                                                         # a lamp only shimmers a touch
            fl = 0.035 * np.sin(TAU * 11 * p + f[0]) + 0.02 * np.sin(TAU * 29 * p + f[1])
        return 0.55 + 0.45 * br + fl

    def apply(self, img, base, p):
        g = self.gain(p)
        Y0, Y1, X0, X1 = self.bb
        r = img[Y0:Y1, X0:X1]
        r += base[Y0:Y1, X0:X1] * self.lit * (0.22 * g - 0.04)      # room light (slightly dimmer at the low point)
        r += self.halo * self.col * (0.075 * g)
        r += self.core * self.col * (0.10 * g)


class Steam:
    """Thin wisps that rise from the cup's rim, sway and curl, and fade as they go up.
    Steam already painted in the picture is taken out first (it would stand frozen next to the moving one)."""
    def __init__(self, base, box, seconds, rng, scale=1.0, painted=None):
        ch, cw = base.shape[:2]
        x1, y1, x2, y2 = box
        bw, bh = x2 - x1, y2 - y1
        sx = self._opening_x(base, box)
        if painted:                                                       # painted steam starts at the opening
            px = (painted[0] + painted[2]) / 2
            sx = float(np.clip(px, x1 + 0.25 * bw, x2 - 0.25 * bw))
        sy = y1 + 0.06 * bh
        self.sx, self.sy = sx, sy
        hs = max(bh * 2.6, 140 * scale)
        if painted:
            hs = max(hs, sy - painted[1] + 0.3 * bh)
        hs = int(min(sy - 4, hs))
        ws = int(max(bw * 1.3, 90 * scale))
        self.hs, self.ws = hs, ws
        self.removed = False
        if hs < 30:
            self.bb = None; return
        X0 = int(sx - ws / 2); Y0 = int(sy - hs)
        X0 = max(0, min(cw - ws, X0))
        self.bb = (Y0, Y0 + hs, X0, X0 + ws)
        u = (1 - (np.arange(hs, dtype=np.float32) + 0.5) / hs)[:, None]   # 0 at the rim, 1 at the top
        x = (np.arange(ws, dtype=np.float32) + X0 - sx)[None, :]          # px from the opening's centre
        self.u, self.x = u, x
        self.env = (np.clip(u / 0.10, 0, 1) ** 1.5 * np.clip(1 - u, 0, 1) ** 1.4).astype(np.float32)
        self.width = (bw * 0.025 + bw * 0.09 * u).astype(np.float32)       # thin wisps that widen as they rise
        if painted:
            px1, py1, px2, py2 = painted
            mx, my = 0.25 * (px2 - px1) + 6, 0.10 * (py2 - py1) + 6
            self.removed = self._remove_painted_steam(base, (max(0, int(py1 - my)), int(min(sy + 2, py2 + my)),
                                                             max(0, int(px1 - mx)), min(cw, int(px2 + mx))), bw)
        speed = 55.0 * scale                                               # px / s upward at 1080p
        P = hs
        self.rise = max(1, round(speed * seconds / P))                     # whole cycles per loop -> seamless
        self.noise = _smooth_noise(P, ws, scale=max(6.0, P / 18), rng=rng)
        self.noise2 = _smooth_noise(P, ws, scale=max(4.0, P / 40), rng=rng)
        self.P = P
        self.ribbons = []
        for j in range(4):
            f = rng.uniform(0.9, 1.6)                                      # waves along the height
            n = max(1, round(speed * f * seconds / hs * rng.uniform(0.8, 1.15)))
            self.ribbons.append(dict(f=f, n=n, ph=rng.uniform(0, TAU), amp=bw * rng.uniform(0.12, 0.26),
                                     drift=bw * rng.uniform(-0.25, 0.25), off=bw * rng.uniform(-0.15, 0.15),
                                     w=rng.uniform(0.6, 1.3), k=rng.uniform(0.6, 1.0)))
        # the colour of steam lit by the room (a little warm), slightly brighter than what is behind it
        self.col = np.array([0.93, 0.89, 0.85], np.float32)
        self.sway_n = max(1, round(seconds / 9))
        self.strength = 0.30 if self.removed else 0.55                     # painted steam -> only faint extra wisps
        self.flow_n = max(1, round(seconds / 3.2))

    @staticmethod
    def _opening_x(base, box):
        """Middle of the cup's body (not its handle): the widest run of columns that differ from the sides."""
        x1, y1, x2, y2 = box
        bw, bh = x2 - x1, y2 - y1
        band = base[y1 + int(bh * 0.4):y1 + int(bh * 0.85), x1:x2]
        if band.shape[0] < 3 or band.shape[1] < 8:
            return (x1 + x2) / 2
        side = np.concatenate([band[:, :max(1, bw // 20)], band[:, -max(1, bw // 20):]], 1).reshape(-1, 3)
        ref = np.median(side, 0)
        fill = (np.abs(band - ref).sum(-1) > 0.12).mean(0)
        best, run, start, bs = 0, 0, 0, 0
        for i, f in enumerate(fill > 0.6):
            run = run + 1 if f else 0
            if run == 1:
                start = i
            if run > best:
                best, bs = run, start
        return x1 + bs + best / 2 if best > bw * 0.35 else (x1 + x2) / 2

    def _remove_painted_steam(self, base, bb, bw):
        """Lift the painted wisps off the picture: S = wisps (bright, greyish, thinner than the opening), base -= S.
        apply() puts S back every frame, swaying with waves that travel up the wisp."""
        from PIL import Image, ImageFilter
        from artloop import blur
        Y0, Y1, X0, X1 = bb
        if Y1 - Y0 < 8 or X1 - X0 < 8:
            return False
        reg = base[Y0:Y1, X0:X1]
        k = int(max(9, bw * 0.3)) | 1
        opened = []
        for c in range(3):
            im = Image.fromarray((np.clip(reg[..., c], 0, 1) * 255).astype(np.uint8))
            im = im.filter(ImageFilter.MinFilter(k)).filter(ImageFilter.MaxFilter(k))
            opened.append(np.asarray(im, np.float32) / 255)
        bg = blur(np.stack(opened, -1), k // 3)
        lum = reg @ np.array([0.299, 0.587, 0.114], np.float32)
        lb = bg @ np.array([0.299, 0.587, 0.114], np.float32)
        sat = reg.max(-1) - reg.min(-1)
        m = np.clip((lum - lb - 0.02) * 14, 0, 1) * np.clip(1.2 - sat * 2.5, 0, 1)   # bright, greyish, thin
        h, w = m.shape                                                  # soft edges of the region
        ey = np.clip(np.minimum(np.arange(h), h - 1 - np.arange(h)) / 6, 0, 1)[:, None]
        ex = np.clip(np.minimum(np.arange(w), w - 1 - np.arange(w)) / 6, 0, 1)[None, :]
        m = np.clip(blur(m, 2) * 1.8, 0, 1) * ey * ex
        if m.mean() < 0.01:
            return False
        S = np.clip(reg - bg, 0, None) * m[..., None]
        reg -= S
        self.pbb, self.S = bb, S.astype(np.float32)
        hh, ww = m.shape
        self.pu = (1 - (np.arange(hh, dtype=np.float32) + 0.5) / hh)        # 0 bottom .. 1 top of the wisp
        self.pxs = np.arange(ww, dtype=np.float32)[None, :]
        self.pA = bw * 0.07
        return True

    def _painted(self, img, p):
        Y0, Y1, X0, X1 = self.pbb
        u = self.pu
        n1, n2 = self.flow_n, self.flow_n + 1
        dx = self.pA * u ** 1.2 * (np.sin(TAU * (1.4 * u - n1 * p)) + 0.4 * np.sin(TAU * (2.7 * u - n2 * p) + 1.7))
        X = np.clip(self.pxs - dx[:, None], 0, self.S.shape[1] - 1.001)
        x0 = X.astype(np.int32); fx = (X - x0)[..., None]
        rows = np.arange(len(u))[:, None]
        Sw = self.S[rows, x0] * (1 - fx) + self.S[rows, x0 + 1] * fx
        mod = 0.78 + 0.22 * np.sin(TAU * (2.2 * u - n1 * p) + 0.6)          # puffs travelling up
        img[Y0:Y1, X0:X1] += Sw * mod[:, None, None]

    def apply(self, img, base, p):
        if not self.bb:
            return
        if self.removed:
            self._painted(img, p)
        Y0, Y1, X0, X1 = self.bb
        u, x = self.u, self.x
        sway = np.sin(TAU * self.sway_n * p) * 0.35 * u                    # the whole column leans a little
        off = int(p * self.rise * self.P) % self.P
        nz = np.roll(self.noise, -off, axis=0)                             # texture rises with the steam
        nz2 = np.roll(self.noise2, -(int(p * self.rise * 2 * self.P) % self.P), axis=0)
        D = np.zeros_like(nz)
        for r in self.ribbons:
            c = (r["off"] + r["drift"] * u + sway * r["amp"]
                 + r["amp"] * u ** 0.8 * np.sin(TAU * (r["f"] * u - r["n"] * p) + r["ph"])
                 + (nz2 - 0.5) * r["amp"] * 1.4 * u)
            wv = self.width * r["w"]
            puff = (0.5 + 0.5 * np.sin(TAU * (r["f"] * 1.7 * u - r["n"] * p) + r["ph"] * 2)) ** 1.3
            D += np.exp(-((x - c) / wv) ** 2) * (0.25 + 0.75 * puff) * r["k"]
        a = np.clip(D * self.env * (0.2 + 0.8 * nz) * self.strength, 0, 0.6)[..., None]
        reg = img[Y0:Y1, X0:X1]
        reg += (self.col - reg) * a + a * 0.06


# ------------------------------------------------------------------ the downloaded library (real smoke / steam clips)
LIB_DIRS = [os.environ.get("MUKHTALIF_ELEMENTS", ""), sv_av.paths.CFG.get("elements_dir", "")]


def library():
    for d in LIB_DIRS:
        if d and os.path.isdir(os.path.join(d, "ready")):
            return d
    return None


def pick_clip(use, seed):
    """A ready clip for this use ('steam', 'incense' ...), the same one every time for the same picture."""
    lib = library()
    if not lib:
        return None
    ready = os.path.join(lib, "ready")
    cands = []
    for f in sorted(os.listdir(ready)):
        if f.endswith(".json"):
            m = json.load(open(os.path.join(ready, f), encoding="utf-8"))
            if use in m.get("use", []) and os.path.exists(os.path.join(ready, f[:-5] + ".mp4")):
                m["path"] = os.path.join(ready, f[:-5] + ".mp4"); m["lib"] = lib
                cands.append(m)
    return cands[seed % len(cands)] if cands else None


def read_clip(path, w, h):
    import subprocess
    from elements_prep import find_ffmpeg
    raw = subprocess.run([find_ffmpeg(), "-v", "error", "-i", path, "-vf", f"scale={w}:{h}:flags=bicubic",
                          "-f", "rawvideo", "-pix_fmt", "gray", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(-1, h, w)


def note_use(keys, what, lib=None, log=print):
    """Register: add '<what> (date)' to used_in of every clip used (ELEMENTS_INDEX.json + .csv)."""
    import csv, time
    lib = lib or library()
    if not lib or not keys:
        return
    p = os.path.join(lib, "ELEMENTS_INDEX.json")
    try:
        ix = json.load(open(p, encoding="utf-8"))
    except Exception:
        return
    changed = False
    for k in keys:
        it = ix.get("items", {}).get(k)
        if it is None:
            continue
        if not any(u.get("video") == what for u in it.setdefault("used_in", [])):
            it["used_in"].append({"video": what, "date": time.strftime("%Y-%m-%d %H:%M")}); changed = True
    if not changed:
        return
    json.dump(ix, open(p + ".tmp", "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    os.replace(p + ".tmp", p)
    try:
        with open(p[:-5] + ".csv", "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["file", "kind", "site", "page", "title", "author", "author page", "original file link", "licence",
                        "licence link", "commercial use", "credit needed", "downloaded on", "sha256", "used in"])
            for name, it in sorted(ix["items"].items()):
                L = it["license"]
                w.writerow([name, it["kind"], it["site"], it["page"], it["title"], it["author"], it["author_url"],
                            it["original_file"], L["name"], L["summary_url"], "yes" if L["commercial_use"] else "NO",
                            "yes" if L["attribution_required"] else "no", it["downloaded_on"], it["sha256"],
                            " | ".join(f'{u["video"]} ({u["date"]})' for u in it.get("used_in", []))])
    except OSError:
        pass                                                      # the csv may be open in Excel - json is kept
    log(f"  register: use noted for {len(keys)} clip(s) -> {what}")


class ClipSteam(Steam):
    """Real steam from the library, standing on the cup's opening, coloured by the room's light.
    Painted steam is lifted off the picture first (the real one replaces it)."""
    def __init__(self, base, box, seconds, rng, clip, scale=1.0, painted=None):
        super().__init__(base, box, seconds, rng, scale, painted)
        self.clip = None
        if not self.bb:
            return
        ch, cw = base.shape[:2]
        x1, y1, x2, y2 = box
        bw, bh = x2 - x1, y2 - y1
        W0, H0 = clip["size"]; ax, ay = clip["anchor"]
        base_px = max(4.0, clip["base_width"] * W0)
        s = min(0.5 * bw / base_px, (self.sy - 6) / (ay * H0), 3.0 * bh / (ay * H0))
        w, h = max(8, int(W0 * s) // 2 * 2), max(8, int(H0 * s) // 2 * 2)
        X0 = int(round(self.sx - ax * w)); Y0 = int(round(self.sy - ay * h))
        self.frames = read_clip(clip["path"], w, h)
        # keep the part that is on the canvas
        cx0, cy0 = max(0, X0), max(0, Y0); cx1, cy1 = min(cw, X0 + w), min(ch, Y0 + h)
        self.cbb = (cy0, cy1, cx0, cx1)
        self.csl = (slice(cy0 - Y0, cy1 - Y0), slice(cx0 - X0, cx1 - X0))
        uu = (Y0 + ay * h - np.arange(cy0, cy1, dtype=np.float32)) / max(1.0, ay * h)   # 0 at the rim .. 1 top
        self.cfade = (np.clip(uu / 0.08, 0, 1) * np.clip((1 - uu) / 0.25, 0, 1))[:, None]
        # light it like the room around it: the picture's colour there, lifted towards white
        Yc0, Yc1, Xc0, Xc1 = self.cbb
        room = base[Yc0:Yc1, Xc0:Xc1].reshape(-1, 3).mean(0)
        self.ccol = np.clip(0.55 * np.ones(3) + 0.45 * room / max(1e-3, room.max()), 0, 1).astype(np.float32)
        self.reps = max(1, round(seconds / clip["seconds"]))       # whole repeats per loop -> seamless
        self.clip = clip
        self.cstrength = 0.62
        self.key = clip.get("register_key")

    def apply(self, img, base, p):
        if self.clip is None:
            return super().apply(img, base, p)
        n = len(self.frames)
        f = self.frames[int((p * self.reps) % 1.0 * n) % n][self.csl].astype(np.float32) / 255
        a = (f * self.cfade * self.cstrength)[..., None]
        Y0, Y1, X0, X1 = self.cbb
        reg = img[Y0:Y1, X0:X1]
        reg += (self.ccol - reg) * a


class Elements:
    def __init__(self, info, base, to_canvas, seconds, seed=7, scale=1.0):
        rng = np.random.default_rng(seed + 101)
        self.indoor = bool(info.get("indoor"))
        self.layers, self.kinds = [], []
        self.moon = None
        self.used = []                                            # register keys of library clips in this picture
        steams = [to_canvas(i["box"]) for i in info.get("items", []) if i.get("kind") == "steam"]
        for it in info.get("items", []):
            x1, y1, x2, y2 = to_canvas(it["box"])
            ch, cw = base.shape[:2]
            x1, x2 = max(0, x1), min(cw, x2); y1, y2 = max(0, y1), min(ch, y2)
            if x2 - x1 < 4 or y2 - y1 < 4:
                continue
            k = it["kind"]
            if k in ("lamp", "candle", "fire"):
                self.layers.append(Lamp(base, (x1, y1, x2, y2), k != "lamp", seconds, rng))
            elif k == "cup":
                near = [b for b in steams if b[0] < x2 and b[2] > x1 and b[3] <= y1 + (y2 - y1) * 0.5]
                clip = pick_clip("steam", seed)
                if clip:
                    st = ClipSteam(base, (x1, y1, x2, y2), seconds, rng, clip, scale, near[0] if near else None)
                    if st.clip is not None and st.key:
                        self.used.append(st.key)
                else:
                    st = Steam(base, (x1, y1, x2, y2), seconds, rng, scale, near[0] if near else None)
                self.layers.append(st)
            elif k == "moon":
                self.moon = ((x1 + x2) / 2, (y1 + y2) / 2, max(x2 - x1, y2 - y1) / 2)
            else:
                continue
            self.kinds.append(k)

    def apply(self, img, base, p):
        for l in self.layers:
            l.apply(img, base, p)


if __name__ == "__main__":
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from artloop import load_image
    src = load_image(sys.argv[1]).astype(np.float32) / 255
    print(json.dumps(ask_vision(src) or light_finder(src), indent=1))
