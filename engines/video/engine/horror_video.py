"""horror_video.py - a भय (Bhay) horror video: the story's pictures one after another, each with a slow camera move,
dissolving through darkness, graded cold and dark, slow fog, film grain, a candle-like flicker, lightning where the
scene has thunder, the opening title, a small "भय" badge, and the spoken lines as subtitles (Hindi drawn with HarfBuzz).

    python horror_video.py <plan.json> <out.mp4>

plan.json = {"audio": "...wav", "fps": 24, "title": "...", "label": "भय", "scenes": [{"image", "start", "end",
             "flash": [seconds...]}], "lines": [{"start", "end", "text"}]}
Frames are drawn several at once (numpy frees the GIL); NVENC when the graphics card has it.
"""
import sys as _sys
for _s in (_sys.stdout, _sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
import json, os, re, subprocess, sys, time
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
W, H = 1920, 1080
SAFE_BOTTOM = 962
ZOOM = 1.10                                   # pictures are read 10% larger than the frame -> room to move
XF = 1.6                                      # seconds of dissolve (through darkness) between two scenes
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def font(name):
    for d in (os.path.join(HERE, "assets", "fonts"), r"C:\Windows\Fonts"):
        p = os.path.join(d, name)
        if os.path.exists(p):
            return p
    return None


# per series: colour grade, fog, flicker, title colour
MOODS = {
    "horror":   {"sat": 0.72, "tint": (0.86, 0.95, 1.08), "gamma": 1.12, "fog": 0.10, "flick": 1.0, "label": (0.72, 0.08, 0.06)},
    "thriller": {"sat": 0.80, "tint": (0.92, 0.98, 1.04), "gamma": 1.08, "fog": 0.05, "flick": 0.4, "label": (0.85, 0.62, 0.22)},
    "warm":     {"sat": 0.95, "tint": (1.06, 1.0, 0.90), "gamma": 1.0, "fog": 0.0, "flick": 0.0, "label": (0.95, 0.78, 0.50)},
    "romance":  {"sat": 1.0, "tint": (1.08, 0.97, 0.92), "gamma": 0.98, "fog": 0.03, "flick": 0.15, "label": (0.92, 0.45, 0.50)},
    "soft":     {"sat": 0.90, "tint": (1.02, 0.99, 0.96), "gamma": 1.0, "fog": 0.04, "flick": 0.0, "label": (0.82, 0.78, 0.92)},
    "clean":    {"sat": 1.0, "tint": (1.0, 1.0, 1.0), "gamma": 1.0, "fog": 0.0, "flick": 0.0, "label": (0.40, 0.70, 0.98)},
}
# per series: the letters (all in assets\fonts: OFL Google fonts + the Windows Hindi fonts)
FONTS = {
    "horror":   ("RozhaOne-Regular.ttf", "aparajb.ttf"),          # heavy old serif title, firm subtitles
    "thriller": ("Biryani-Black.ttf", "Biryani-Bold.ttf"),        # sharp, modern, investigative
    "warm":     ("Amita-Bold.ttf", "Laila-Regular.ttf"),          # hand-lettered title, soft readable story text
    "romance":  ("Tillana-SemiBold.ttf", "Gotu-Regular.ttf"),     # flowing script title, gentle text
    "soft":     ("Kalam-Regular.ttf", "Martel-Regular.ttf"),      # handwritten, quiet
    "clean":    ("YatraOne-Regular.ttf", "Akshar-VariableFont_wght.ttf"),   # bold clear title, clean sans text
}
MOOD = MOODS["horror"]


def load(path):
    from PIL import Image
    im = Image.open(path).convert("RGB")
    sw, sh = int(W * ZOOM), int(H * ZOOM)
    r = max(sw / im.width, sh / im.height)
    im = im.resize((int(im.width * r + 0.5), int(im.height * r + 0.5)), Image.LANCZOS)
    x0, y0 = (im.width - sw) // 2, (im.height - sh) // 2
    a = np.asarray(im.crop((x0, y0, x0 + sw, y0 + sh)), np.float32) / 255
    # the Bhay grade: a little colour taken out, cold moonlight in the shadows, deeper blacks
    g = a.mean(-1, keepdims=True)
    a = g + (a - g) * MOOD["sat"]
    a = a * np.array(MOOD["tint"], np.float32)
    a = np.clip((a - 0.03) * 1.08, 0, 1) ** MOOD["gamma"] if MOOD["gamma"] > 1.01 else np.clip(a, 0, 1)
    return Image.fromarray((a * 255 + 0.5).astype(np.uint8))


def text_mask(text, fontfile, size):
    """white text on black (float32), Hindi shaped by HarfBuzz when available"""
    import hbtext
    if hbtext.ok():
        w = int(hbtext.width(text, fontfile, size)) + 40
        m = np.zeros((int(size * 1.9), w), np.float32)
        hbtext.draw(m, text, fontfile, size, 20, int(size * 0.2))
        return m
    from PIL import Image, ImageDraw, ImageFont
    f = ImageFont.truetype(fontfile, size)
    im = Image.new("L", (int(f.getlength(text)) + 40, int(size * 1.9)))
    ImageDraw.Draw(im).text((20, int(size * 0.2)), text, font=f, fill=255)
    return np.asarray(im, np.float32) / 255


def logo_sprite(path, h):
    """the channel logo (PNG with transparency) at h pixels tall, as premultiplied float RGBA (0..1)"""
    from PIL import Image
    im = Image.open(path).convert("RGBA")
    h = max(8, int(h))
    im = im.resize((max(8, int(im.width * h / im.height)), h), Image.LANCZOS)
    a = np.asarray(im, np.float32) / 255
    a[..., :3] *= a[..., 3:4]
    return a


def blur(a, r):
    r = int(max(1, r)); out = a.astype(np.float32)
    for _ in range(2):
        for ax in (0, 1):
            c = np.cumsum(np.pad(out, [(r + 1, r) if i == ax else (0, 0) for i in range(out.ndim)], mode="edge"), axis=ax)
            out = (np.take(c, np.arange(2 * r + 1, c.shape[ax]), axis=ax) -
                   np.take(c, np.arange(0, c.shape[ax] - 2 * r - 1), axis=ax)) / (2 * r + 1)
    return out


def wrap(text, fontfile, size, maxw):
    import hbtext
    words, rows, cur = text.split(), [], ""
    for w in words:
        t = (cur + " " + w).strip()
        wd = hbtext.width(t, fontfile, size) if hbtext.ok() else len(t) * size * 0.5
        if cur and wd > maxw:
            rows.append(cur); cur = w
        else:
            cur = t
    return rows + ([cur] if cur else [])


class Video:
    def __init__(self, plan):
        self.plan = plan
        self.fps = int(plan.get("fps") or 24)
        self.sc = sorted(plan["scenes"], key=lambda s: s["start"])
        self.total = float(plan["duration"])
        win = plan.get("window") or [0.0, self.total]               # a reel = a window of the story
        self.a0, self.len = float(win[0]), float(win[1]) - float(win[0])
        self.tt = 4.5 if plan.get("window") else 9.0              # how long the opening title stays
        self.img = {}
        rng = np.random.default_rng(3)
        # soft film grain: made at quarter size and enlarged (clumps like film, and it keeps the file small)
        from PIL import Image as _I
        self.grain = []
        for _ in range(6):
            g = rng.normal(0, 1, (H // 4, W // 4)).astype(np.float32)
            g = np.asarray(_I.fromarray(((g * 40 + 128).clip(0, 255)).astype(np.uint8)).resize((W, H), _I.BILINEAR), np.float32)
            self.grain.append(((g - 128) / 40 * 1.5).clip(-6, 6).astype(np.int16)[..., None])
        yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
        d = np.hypot((xx - W / 2) / (W / 2), (yy - H / 2) / (H / 2))
        k = 0.62 if MOOD.get("fog", 0) >= 0.08 else 0.40          # horror: deep vignette, others lighter
        self.vig = np.clip(1.15 - k * d ** 1.6, 0.18, 1).astype(np.float32)
        fog = blur(rng.random((int(H * 0.6), int(W * 0.6))).astype(np.float32), 60)
        fog = (fog - fog.min()) / max(1e-6, fog.max() - fog.min())
        from PIL import Image
        band = np.clip((np.mgrid[0:H, 0:W * 2][0] - H * 0.35) / (H * 0.65), 0, 1) ** 1.5
        f2 = np.asarray(Image.fromarray((fog * 255).astype(np.uint8)).resize((W * 2, H)), np.float32) / 255 * band
        self.fog = (f2[..., None] * MOOD["fog"] * 255 * np.array([0.7, 0.78, 0.9], np.float32)).astype(np.int16)   # (H,2W,3)
        self.flash = sorted(float(t) for s in self.sc for t in s.get("flash", []))
        # subtitles
        _tf, _sf = FONTS.get(plan.get("mood") or "horror", FONTS["horror"])
        ff = font(_sf) or font("aparajb.ttf") or font("TiroDevanagariHindi-Regular.ttf") or font("Nirmala.ttc")
        self.caps = []
        room = float((plan.get("sub_room") or {}).get("tall" if H > W else "wide") or 0)   # corner narrator side kept free
        maxw = int(min(W - 300, W - 2 * room * W))
        for ln in plan.get("lines", []):
            t = re.sub(r"\.{2,}|…", "…", ln["text"]).strip(" .।")
            if not t:
                continue
            rows = wrap(t, ff, 50, maxw)
            a, b = float(ln["start"]), float(ln["end"])
            groups = [rows[g:g + 2] for g in range(0, len(rows), 2)]       # long lines: two rows at a time
            nw = [max(1, sum(len(r.split()) for r in g)) for g in groups]
            t0 = a
            for g, k in zip(groups, nw):
                t1 = t0 + (b - a) * k / sum(nw)
                self.caps.append((t0, t1 + (0.3 if g is groups[-1] else 0.0), [text_mask(r, ff, 50) for r in g]))
                t0 = t1
        self.cap_i = 0
        # title + badge
        tf = font(_tf) or font("RozhaOne-Regular.ttf") or font("TiroDevanagariHindi-Regular.ttf") or ff
        ch = plan.get("channel") or {}                            # the series' own channel: logo and/or name
        self.logo_img = self.badge_img = None
        if ch.get("logo") and os.path.exists(ch["logo"]):
            try:
                self.logo_img = logo_sprite(ch["logo"], min(H * 0.34, W * 0.52))
                self.badge_img = logo_sprite(ch["logo"], min(H, W) * 0.11)
            except Exception as e:
                print(f"  channel logo not usable ({e}) - the name is drawn", flush=True)
        label = ch.get("name") or plan.get("label") or "भय"
        self.logo = text_mask(label, tf, 210 if len(label) < 8 else 130)
        self.title = text_mask(plan.get("title") or "", ff, 64) if plan.get("title") else None
        self.badge = text_mask(label, tf, 46)
        self.red = np.array(MOOD["label"], np.float32)
        self._halo = {}
        self.sub = None
        if plan.get("subscribe") and not plan.get("window"):      # the Subscribe + bell ending (not on reels)
            from subscribe_end import SubscribeEnd
            self.sub = SubscribeEnd(plan["subscribe"], W, H)
            if ch.get("logo") or ch.get("name"):                  # the channel's logo above the buttons, its name below
                try:
                    self.sub.brand(ch.get("logo") if self.logo_img is not None else "", ch.get("name") or "")
                except Exception as e:
                    print(f"  channel on the ending skipped ({e})", flush=True)
            self.sub.background(self.sc[-1]["image"], self.vig, plan.get("mood") or "horror")   # blurred, in the series colour

    def preload(self):
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(4) as ex:
            for i, a in enumerate(ex.map(lambda s: load(s["image"]), self.sc)):
                self.img[i] = a

    def image(self, i):
        return self.img[i]

    def shot(self, i, t):
        """scene i at time t: a slow eased camera move over the (larger) picture"""
        s = self.sc[i]
        a, b = s["start"], s["end"] + XF
        p = min(1.0, max(0.0, (t - a) / max(0.1, b - a)))
        p = p * p * (3 - 2 * p)
        mode = i % 4
        z = 1.0 + 0.07 * p if mode == 0 else 1.07 - 0.07 * p if mode == 1 else 1.03
        from PIL import Image
        src = self.image(i)
        sw, sh = src.size
        k = 1 / (ZOOM * z) * ZOOM                                 # source pixels per output pixel
        vw, vh = W * k, H * k
        cx = sw / 2 + ((p - 0.5) * (sw - vw) * 0.8 if mode == 2 else (0.5 - p) * (sw - vw) * 0.8 if mode == 3 else 0)
        cy = sh / 2
        im = src.transform((W, H), Image.AFFINE, (k, 0, cx - vw / 2, 0, k, cy - vh / 2), resample=Image.BILINEAR)
        return np.asarray(im)

    def frame(self, k):
        tl = k / self.fps                                         # time in this video (a reel starts at 0)
        t = self.a0 + tl                                          # time in the story
        i = max(0, next((j for j in range(len(self.sc) - 1, -1, -1) if self.sc[j]["start"] <= t), 0))
        gain = 1.0
        nxt = self.sc[i + 1]["start"] if i + 1 < len(self.sc) else None
        if nxt is not None and t > nxt - XF:                      # dissolve through darkness into the next scene
            q = (t - (nxt - XF)) / XF
            img = self.shot(i, t) if q < 0.5 else self.shot(i + 1, t)
            gain = max(0.0, 1 - 2 * q) if q < 0.5 else (2 * q - 1)
        else:
            img = self.shot(i, t)
        # flicker (slow, uneven) + lightning + fades at start / end
        fl = 1.0 + MOOD["flick"] * (0.035 * np.sin(t * 1.7) * np.sin(t * 5.3 + 1.1) + 0.02 * np.sin(t * 13.1))
        for tf in self.flash:
            if 0 <= t - tf < 0.45:
                dt = t - tf
                fl += 0.9 * np.exp(-dt * 14) + (0.6 * np.exp(-(dt - 0.18) * 18) if dt > 0.18 else 0)
        if tl < 2.5:
            gain *= tl / 2.5
        if tl > self.len - 4:
            gain *= max(0.0, (self.len - tl) / 4)
        dim = 1.0
        T = self.tt
        if tl < T:                                                # the opening title darkens the picture a little
            a = min(1.0, max(0.0, (tl - 0.8) / 1.5)) * min(1.0, max(0.0, (T - tl) / 1.5))
            dim = 1 - 0.55 * a
        mul = (self.vig * np.float32(fl * gain * dim * 256)).astype(np.uint16)
        out = (img.astype(np.uint16) * mul[..., None]) >> 8
        off = int((t * 14) % W)
        out = out.astype(np.int16) + (self.fog[:, off:off + W] * np.int16(max(0.0, gain))).astype(np.int16) \
            + self.grain[k % len(self.grain)]
        np.clip(out, 0, 255, out=out)
        out = out.astype(np.uint8)
        if self.sub is not None:
            out = self.sub.backdrop(out, tl, self.len)
        self.overlay(out, t, tl)
        return out

    def put(self, img, m, x, y, col, a):
        h, w = m.shape
        x, y = int(x), int(y)
        if a <= 0:
            return
        # Clip the destination and source mask together. NumPy interprets a negative
        # destination slice from the far edge, while a mask slice starts at zero;
        # using x1-x directly therefore mismatches wide centered titles on portrait reels.
        x0, y0 = max(0, x), max(0, y)
        x1, y1 = min(W, x + w), min(H, y + h)
        if x1 <= x0 or y1 <= y0:
            return
        mx0, my0 = x0 - x, y0 - y
        mx1, my1 = mx0 + (x1 - x0), my0 + (y1 - y0)
        crop = m[my0:my1, mx0:mx1]
        mm = crop[..., None] * np.float32(a)
        reg = img[y0:y1, x0:x1].astype(np.float32) / 255
        key = (id(m), mx0, my0, x1 - x0, y1 - y0)
        if key not in self._halo:
            self._halo[key] = blur(crop, 6)[..., None]
        reg *= (1 - self._halo[key] * np.float32(a * 0.85))       # soft dark halo -> readable on any picture
        reg += (np.minimum(np.asarray(col, np.float32), 1) - reg) * mm
        img[y0:y1, x0:x1] = (reg * 255 + 0.5).astype(np.uint8)

    def put_rgba(self, img, spr, x, y, a):
        """paste a premultiplied RGBA sprite (the channel logo) with opacity a"""
        h, w = spr.shape[:2]
        x, y = int(x), int(y)
        x0, y0, x1, y1 = max(0, x), max(0, y), min(W, x + w), min(H, y + h)
        if a <= 0.01 or x1 <= x0 or y1 <= y0:
            return
        s = spr[y0 - y:y1 - y, x0 - x:x1 - x] * np.float32(a)
        reg = img[y0:y1, x0:x1].astype(np.float32)
        img[y0:y1, x0:x1] = np.clip(reg * (1 - s[..., 3:4]) + s[..., :3] * 255 + 0.5, 0, 255).astype(np.uint8)

    def overlay(self, img, t, tl):
        T = self.tt
        if tl < T:                                                # opening: the channel logo / series name + the title
            a = min(1.0, max(0.0, (tl - 0.8) / 1.5)) * min(1.0, max(0.0, (T - tl) / 1.5))
            if self.logo_img is not None:
                y0 = H * (0.08 if W > H else 0.12)
                self.put_rgba(img, self.logo_img, (W - self.logo_img.shape[1]) / 2, y0, a)
                ty = y0 + self.logo_img.shape[0] * 1.02
            else:
                self.put(img, self.logo, (W - self.logo.shape[1]) / 2, H * 0.17, self.red * 1.25, a)
                ty = H * 0.17 + self.logo.shape[0] * 0.95
            if self.title is not None:
                self.put(img, self.title, (W - self.title.shape[1]) / 2, ty,
                         (0.86, 0.86, 0.88), a * min(1.0, max(0.0, (tl - 1.5) / 1.5)))
        elif tl < self.len - 5:                                   # small badge, top left
            hide = self.sub.bg_mix(tl) if self.sub is not None else 0.0
            ba = min(1.0, (tl - T) / 2) * (1 - hide)
            if self.badge_img is not None:
                self.put_rgba(img, self.badge_img, 50, int(H * 0.06), 0.75 * ba)
            else:
                self.put(img, self.badge, 60, int(H * 0.09), self.red * 1.2, 0.55 * ba)
        for (a, b, ms) in self.caps:                              # the spoken line, bottom, inside the safe area
            if a - 0.25 <= t <= b:
                al = min(1.0, (t - a + 0.25) / 0.25, (b - t) / 0.3)
                y = SAFE_BOTTOM - 40 - sum(m.shape[0] * 0.78 for m in ms)
                for m in ms:
                    self.put(img, m, (W - m.shape[1]) / 2, y, (0.93, 0.92, 0.88), al)
                    y += m.shape[0] * 0.78
        if self.sub is not None:
            self.sub.draw(img, tl)
        return img


def main():
    global MOOD, W, H, SAFE_BOTTOM
    plan = json.load(open(sys.argv[1], encoding="utf-8"))
    out = sys.argv[2]
    MOOD = MOODS.get(plan.get("mood") or "horror", MOODS["horror"])
    if plan.get("size"):                                          # 1080x1920 = a reel
        W, H = int(plan["size"][0]), int(plan["size"][1])
        SAFE_BOTTOM = int(H * (0.80 if H > W else 0.89))
    import sleepgen
    v = Video(plan)
    v.preload()
    fps, n = v.fps, int(v.len * v.fps)
    silent = out[:-4] + "_silent.mp4"
    wr = sleepgen.make_writer(silent, W, H, fps, crf=22)          # ~5-6 Mbit/s: YouTube-ready, not huge
    print(f"  {len(v.sc)} scenes, {v.total / 60:.1f} min, {len(v.caps)} subtitle lines, encoder {getattr(wr, 'codec', '?')}",
          flush=True)
    from concurrent.futures import ThreadPoolExecutor
    workers = max(1, min(6, (os.cpu_count() or 2) - 1))
    t0 = time.time()
    with ThreadPoolExecutor(workers) as ex:
        pend, nxt = {}, 0
        for k in range(n):
            while nxt < n and len(pend) < workers * 2:
                pend[nxt] = ex.submit(v.frame, nxt); nxt += 1
            wr.write(pend.pop(k).result())
            if k % max(1, n // 20) == 0:
                el = time.time() - t0
                print(f"  drawing: {100 * k // n}%  ({el / 60:.1f} min, ~{el / max(1, k) * (n - k) / 60:.0f} min left)", flush=True)
    wr.close()
    import paths
    ff = paths.ffmpeg() or "ffmpeg"
    cut = ["-ss", f"{v.a0:.3f}", "-t", f"{v.len:.3f}"] if plan.get("window") else []
    af = ["-af", f"afade=t=in:d=0.6,afade=t=out:st={max(0.0, v.len - 1.5):.2f}:d=1.5"] if plan.get("window") else []
    r = subprocess.run([ff, "-v", "error", "-y", "-i", silent, *cut, "-i", plan["audio"], *af, "-map", "0:v:0", "-map", "1:a:0",
                        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart", out])
    if r.returncode == 0:
        os.remove(silent)
    print(f"DONE -> {out}  ({(time.time() - t0) / 60:.1f} min)", flush=True)
    return r.returncode


if __name__ == "__main__":
    sys.exit(main())
