"""
kinetic.py - Mukhtalif word-by-word kinetic typography.

Every word of the narration appears exactly when it is spoken. Styles:
  "diary"   - Aparajita Italic, left-aligned beside a thin lavender rule, words ink in one by one,
              key word in lantern-amber; line drifts up and fades when the thought ends.
  "shayari" - centred couplet in Aparajita; the whole line is faintly visible, each word lights
              up as it is spoken (like a reader following your voice), key word glows.
  "verse"   - editorial, asymmetric: key word large in Aparajita Italic, the rest smaller in Akshar,
              placed in the calmest empty area of the artwork (auto-detected).
Word masks come from Pillow+raqm when available, else ffmpeg drawtext (FFmpeg 7+ shapes Devanagari).
"""
import os, subprocess, sys, tempfile
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import paths  # noqa: E402

W, H = 1920, 1080
_ENGINE = os.path.dirname(os.path.abspath(__file__))
HERE = os.path.dirname(_ENGINE) if os.path.basename(_ENGINE).lower() == "engine" else _ENGINE   # project root
FDIRS = [os.path.join(HERE, "assets", "fonts"), os.path.join(HERE, "assets"),
         paths.USER_FONTS, paths.WIN_FONTS]
INK = np.array([0.96, 0.93, 1.00], np.float32)       # moon-white
LAV = np.array([0.74, 0.64, 1.00], np.float32)       # lavender glow
AMBER = np.array([1.00, 0.80, 0.52], np.float32)     # lantern accent for the key word


_FALLBACK = {"aparajbi.ttf": "aparajb.ttf", "aparajb.ttf": "aparaj.ttf", "aparaji.ttf": "aparaj.ttf",
             "aparaj.ttf": "TiroDevanagariHindi-Regular.ttf",
             "Akshar-VariableFont_wght.ttf": "TiroDevanagariHindi-Regular.ttf"}


def font(name):
    while name:
        for d in FDIRS:
            p = os.path.join(d, name)
            if os.path.exists(p):
                return p
        name = _FALLBACK.get(name)
    raise FileNotFoundError("no Devanagari font found in assets/fonts")


def _box(a, r):
    r = int(max(1, r)); out = a.astype(np.float32)
    for _ in range(3):
        for ax in (0, 1):
            c = np.cumsum(np.pad(out, [(r + 1, r) if i == ax else (0, 0) for i in range(2)], mode="edge"), axis=ax)
            out = (np.take(c, np.arange(2 * r + 1, c.shape[ax]), axis=ax) -
                   np.take(c, np.arange(0, c.shape[ax] - 2 * r - 1), axis=ax)) / (2 * r + 1)
    return out.astype(np.float32)


# ---------------------------------------------------------------------------- word masks
_cache = {}


def _pil_ok():
    try:
        from PIL import features
        return features.check("raqm")
    except Exception:
        return False


def _hb_ok():
    try:
        import hbtext
        return hbtext.ok()
    except Exception:
        return False


def _ascender_px(fontfile, size):
    """Font ascender in pixels (hhea table), so ffmpeg-drawn words sit on the same baseline as Pillow ones."""
    import struct
    try:
        with open(fontfile, "rb") as f:
            d = f.read()
        off = 12 if d[:4] != b"ttcf" else struct.unpack(">I", d[12:16])[0] + 12
        n = struct.unpack(">H", d[off - 8:off - 6])[0]
        tabs = {d[off + 16 * i:off + 16 * i + 4]: struct.unpack(">I", d[off + 16 * i + 8:off + 16 * i + 12])[0]
                for i in range(n)}
        upm = struct.unpack(">H", d[tabs[b"head"] + 18:tabs[b"head"] + 20])[0]
        asc = struct.unpack(">h", d[tabs[b"hhea"] + 4:tabs[b"hhea"] + 6])[0]
        return int(round(asc * size / upm))
    except Exception:
        return int(size * 0.9)


def word_mask(text, fontfile, size):
    """Tight alpha mask (float32 0..1) of one word + its baseline offset (pixels from mask top)."""
    key = (text, fontfile, size)
    if key in _cache:
        return _cache[key]
    pad = int(size * 0.6)
    if _pil_ok():
        from PIL import Image, ImageDraw, ImageFont
        fo = ImageFont.truetype(fontfile, size, layout_engine=ImageFont.Layout.RAQM)
        wdt = int(fo.getlength(text)) + 2 * pad
        hgt = int(size * 2.2)
        im = Image.new("L", (wdt, hgt), 0)
        ImageDraw.Draw(im).text((pad, pad), text, font=fo, fill=255)
        m = np.asarray(im, np.float32) / 255
    elif _hb_ok():                                            # HarfBuzz + FreeType: correct Hindi conjuncts
        import hbtext
        wdt = int(hbtext.width(text, fontfile, size)) + 2 * pad
        m = np.zeros((int(size * 2.2), wdt), np.float32)
        hbtext.draw(m, text, fontfile, size, pad, pad)
    else:
        from overlay import ffmpeg_exe
        tmp = tempfile.mkdtemp(prefix="w_")
        tf = os.path.join(tmp, "w.txt"); open(tf, "w", encoding="utf-8").write(text)
        wdt, hgt = int(size * (len(text) * 0.95 + 2)) + 2 * pad, int(size * 2.2)
        wdt += wdt % 2; hgt += hgt % 2
        out = os.path.join(tmp, "m.gray")
        ff = lambda p: p.replace("\\", "/").replace(":", "\\:")
        subprocess.run([ffmpeg_exe(), "-y", "-v", "error", "-f", "lavfi", "-i", f"color=black:s={wdt}x{hgt}:d=1",
                        "-vf", f"drawtext=fontfile='{ff(fontfile)}':textfile='{ff(tf)}':fontsize={size}:fontcolor=white:"
                               f"x={pad}:y={pad + _ascender_px(fontfile, size)}-ascent,format=gray", "-frames:v", "1", "-f", "rawvideo", out], check=True)
        m = np.fromfile(out, np.uint8).reshape(hgt, wdt).astype(np.float32)
        lo = np.percentile(m, 2); m = np.clip((m - lo) / max(1, 255 - lo), 0, 1)
    cols = np.where(m.max(0) > 0.02)[0]
    x0, x1 = (max(0, cols.min() - 2), cols.max() + 3) if len(cols) else (0, 1)
    m = m[:, x0:x1]
    res = (np.ascontiguousarray(m), pad)                    # baseline-ish: text top sits at y=pad
    _cache[key] = res
    return res


# ---------------------------------------------------------------------------- calm-area finder
def calm_region(frame, box_w, box_h, avoid_bottom=0.27, avoid_top=0.17, k=1, avoid=()):
    """Top-left corner(s) of the box_w x box_h window(s) with least visual detail (asymmetric placement).
    k>1 returns up to k non-overlapping candidates, calmest first."""
    g = frame.astype(np.float32).mean(-1) / 255
    small = g[::8, ::8]
    gy, gx = np.gradient(small)
    detail = np.abs(gx) + np.abs(gy) + 0.6 * small          # avoid busy and bright areas (moon, lamps)
    for x1, y1, x2, y2 in avoid:                             # people / faces found by the vision model
        m = 48                                               # keep a margin around them
        detail[max(0, (y1 - m) // 8):(y2 + m) // 8 + 1, max(0, (x1 - m) // 8):(x2 + m) // 8 + 1] += 5.0
    bw, bh = box_w // 8, box_h // 8
    ii = detail.cumsum(0).cumsum(1)
    cand = []
    y_lo = int(small.shape[0] * avoid_top)
    hmax = int(small.shape[0] * (1 - avoid_bottom)) - bh
    for y in range(y_lo, max(y_lo + 1, hmax), 3):
        for x in range(18, small.shape[1] - bw - 18, 3):
            s = ii[y + bh, x + bw] - ii[y, x + bw] - ii[y + bh, x] + ii[y, x]
            cand.append((s, x * 8, y * 8))
    cand.sort()
    out, best = [], None
    for s, x, y in cand:
        if best is not None and s > best * 1.6 + 1.0:
            break                                            # other spots are much busier: use fewer places
        if all(abs(x - ox) >= box_w * 0.8 or abs(y - oy) >= box_h * 0.8 for ox, oy in out):
            out.append((x, y))
            best = s if best is None else best
        if len(out) >= k:
            break
    out = out or [(160, 200)]
    return out[0] if k == 1 else out


# ---------------------------------------------------------------------------- lines that keep their meaning
JOIN_BEFORE = {"और", "लेकिन", "पर", "मगर", "क्योंकि", "जैसे", "तो", "जब", "कि", "या", "फिर", "ताकि", "जो", "अगर"}
END_AFTER = {"है", "हैं", "था", "थी", "थे", "हो", "हूँ", "ना", "गया", "गई", "दो", "जाओ"}
NO_CUT_BEFORE = {"से", "की", "का", "के", "को", "में", "ने", "तक", "ही", "भी", "तरह", "वाला", "वाली", "वाले", "सा", "सी",
                 "हैं", "है", "था", "थी", "थे", "रहा", "रही", "रहे", "जाता", "जाती", "सकते", "सकता", "सकती"}
NO_CUT_AFTER = {"न", "किसी", "एक", "हर", "बहुत", "कितना", "कितनी", "सबसे", "मेरे", "मेरी", "मेरा", "तुम्हारे", "तुम्हारी",
                "तुम्हारा", "उनके", "अपने", "अपनी", "अपना", "इस", "उस", "ये", "वो", "यह", "वह", "और", "कि", "जो", "जब"}


def split_meaning(words, max_words=9, min_words=3):
    """A long spoken sentence -> on-screen pieces that each say something whole: cut where the voice pauses,
    after '...' / ',' / '।', before 'और / लेकिन / क्योंकि ...', never leaving a 1-2 word scrap.
    words: [{"w","start","end"}] -> list of (i0, i1) index ranges."""
    n = len(words)
    if n <= max_words:
        return [(0, n)]
    def gain(i):                                            # cut between words[i-1] and words[i]
        prev, nxt = words[i - 1], words[i]
        g = 2.0 * max(0.0, float(nxt["start"]) - float(prev["end"]))          # the pause you actually made
        pw = str(prev["w"])
        if pw.endswith(("...", "…", ",", "।", "?", "!")):
            g += 1.2
        if str(nxt["w"]).strip("।,.?!…") in JOIN_BEFORE:
            g += 0.8
        if pw.strip("।,.?!…") in END_AFTER:
            g += 0.4
        if str(nxt["w"]).strip("।,.?!…") in NO_CUT_BEFORE:
            g -= 1.5                                        # "किसी न किसी | तरह से" - never
        if pw.strip("।,.?!…") in NO_CUT_AFTER and not pw.endswith(("...", "…", ",", "।")):
            g -= 1.0
        return g
    best = {0: (0.0, [])}                                   # dynamic programming over cut positions
    for j in range(min_words, n + 1):
        cands = []
        for i in best:
            k = j - i
            g = gain(j) if j < n else 1.0
            ok_len = min_words <= k <= max_words or (2 <= k < min_words and g >= 1.5)   # a short piece only at a real pause
            if ok_len:
                bal = -0.08 * abs(k - 6)                    # ~6 words reads best
                cands.append((best[i][0] + g - 0.7 + bal, best[i][1] + [(i, j)]))     # -0.7: no weak cuts
        if cands:
            best[j] = max(cands)
    return best[n][1] if n in best else [(i, min(n, i + max_words)) for i in range(0, n, max_words)]


# ---------------------------------------------------------------------------- layout + animation
def _smooth(x):
    x = min(1.0, max(0.0, x)); return x * x * (3 - 2 * x)


class Line:
    """One spoken thought: words with start/end times, optional key-word index."""

    def __init__(self, words, key=None):
        self.words = words                                  # [{"w","start","end"}]
        self.key = key
        self.start = words[0]["start"]
        self.end = words[-1]["end"]


class KineticTrack:
    def __init__(self, lines, style="diary", region=None):
        self.lines, self.style = lines, style
        self.region = region
        self._layout = {}

    # ---- layout per style -------------------------------------------------
    def _layout_line(self, li):
        if li in self._layout:
            return self._layout[li]
        ln = self.lines[li]
        st = self.style
        items = []
        if st == "diary":
            f, size = font("aparaji.ttf"), 76
            x, y0, maxw = 250, 640, 1250                        # above the light line, inside the full-screen safe area
            lh = int(size * 1.45)
            cx, cy = x, y0
            for i, w in enumerate(ln.words):
                kf = font("aparajbi.ttf") if i == ln.key else f
                m, pad = word_mask(w["w"], kf, size)
                if cx + m.shape[1] > x + maxw and cx > x:
                    cx, cy = x, cy + lh
                items.append(dict(m=m, x=cx, y=cy - pad, w=w, key=(i == ln.key)))
                cx += m.shape[1] + int(size * 0.28)
            # lift the block so its last row sits above the light line
            rows = (cy - y0) // lh
            for it in items:
                it["y"] -= rows * lh
            meta = dict(rule=(x - 46, y0 - rows * lh - 6, y0 + int(size * 1.15)))
        elif st == "shayari":
            f, size = font("aparaj.ttf"), 82
            words = ln.words
            # split into 2 balanced rows if long
            widths = [word_mask(w["w"], f, size)[0].shape[1] for w in words]
            sp = int(size * 0.3)
            total = sum(widths) + sp * (len(words) - 1)
            rows = [list(range(len(words)))]
            if total > 1300 and len(words) > 2:
                acc, cut = 0, 1
                for k in range(len(words)):
                    acc += widths[k] + sp
                    if acc >= total / 2:
                        cut = k + 1; break
                rows = [list(range(cut)), list(range(cut, len(words)))]
            lh = int(size * 1.5)
            ytop = 690 - (len(rows) - 1) * lh
            for r, idxs in enumerate(rows):
                rw = sum(widths[k] for k in idxs) + sp * (len(idxs) - 1)
                cx = (W - rw) // 2
                for k in idxs:
                    kf = font("aparajb.ttf") if k == ln.key else f
                    m, pad = word_mask(words[k]["w"], kf, size)
                    items.append(dict(m=m, x=cx, y=ytop + r * lh - pad, w=words[k], key=(k == ln.key)))
                    cx += widths[k] + sp
            meta = {}
        else:  # verse / mukhtalif (handwritten body + calligraphic key word, in the calm part of the art)
            if st == "mukhtalif":
                big_f, small_f, bs, ss = font("Amita-Bold.ttf"), font("Kalam-Light.ttf"), 124, 60
                lh_small, lh_big = 86, 176
            else:
                big_f, small_f, bs, ss = font("aparaji.ttf"), font("Akshar-VariableFont_wght.ttf"), 132, 56
                lh_small, lh_big = 74, 150
            reg = self.region or (160, 200)
            if isinstance(reg[0], (list, tuple)):
                reg = reg[li % len(reg)]
            rx, ry = reg
            rx, ry = min(max(rx, 160), W - 980), max(ry, 110)   # keep a calm margin from the frame edge
            cx, cy = rx, ry
            maxw = 820
            line_h = lh_small
            for i, w in enumerate(ln.words):
                is_key = (i == ln.key)
                m, pad = word_mask(w["w"], big_f if is_key else small_f, bs if is_key else ss)
                if is_key and cx > rx:                       # key word gets its own row
                    cx, cy = rx, cy + line_h; line_h = lh_small
                if cx + m.shape[1] > rx + maxw and cx > rx:
                    cx, cy = rx, cy + line_h; line_h = lh_small
                items.append(dict(m=m, x=cx, y=cy - pad + (0 if is_key else 8), w=w, key=is_key))
                if is_key:
                    cx, cy, line_h = rx + 6, cy + lh_big, lh_small
                else:
                    cx += m.shape[1] + 18
            meta = {}
            if st == "mukhtalif" and items:                  # thin lavender rule beside the block
                top = min(it["y"] + it["m"].shape[0] // 4 for it in items)
                bot = max(it["y"] + it["m"].shape[0] * 3 // 4 for it in items)
                meta = dict(rule=(rx - 40, top, bot))
        for it in items:
            it["glow"] = _box(it["m"], 10)
            it["soft"] = _box(it["m"], 3)
        self._layout[li] = (items, meta)
        return self._layout[li]

    # ---- drawing ------------------------------------------------------------
    def active(self, t, lead=0.35, tail=2.2):
        return [i for i, ln in enumerate(self.lines) if ln.start - lead <= t <= ln.end + tail]

    def bbox(self, li, margin=40):
        items, meta = self._layout_line(li)
        y0 = min(it["y"] for it in items) - margin - 20
        y1 = max(it["y"] + it["m"].shape[0] for it in items) + margin + 12
        if meta.get("rule"):
            y0 = min(y0, meta["rule"][1] - 30); y1 = max(y1, meta["rule"][2] + 10)
        return max(0, y0), min(H, y1)

    def frame_ops(self, t):
        """Everything to draw at time t as simple operations (shared by the CPU and GPU renderers)."""
        ops = []
        for li in self.active(t):
            tail = 2.2
            if li + 1 < len(self.lines):                     # never overlap the next thought
                tail = max(0.7, min(tail, self.lines[li + 1].start - self.lines[li].end - 0.15))
            if t <= self.lines[li].end + tail:
                ops += self.line_ops(li, t, tail)
        return ops

    def line_ops(self, li, t, tail=2.2):
        ln = self.lines[li]
        items, meta = self._layout_line(li)
        hold = min(0.6, tail * 0.3)
        out = 1.0 - _smooth((t - ln.end - hold) / (tail - hold))     # whole line dissolves after the thought
        lift = int((1 - out) * 18)
        ops = []
        if meta.get("rule"):
            rx, ry0, ry1 = meta["rule"]
            grow = _smooth((t - ln.start + 0.3) / 0.9)
            yb = int(ry0 + (ry1 - ry0) * grow)
            if yb > ry0:
                ops.append(("rule", rx, ry0 - lift, yb - lift, out))
        for it in items:
            w = it["w"]
            p = _smooth((t - w["start"] + 0.12) / 0.55)        # word ink-in, slightly ahead of the voice
            speaking = 1.0 if w["start"] - 0.1 <= t <= w["end"] + 0.25 else 0.0
            col = AMBER if it["key"] else INK
            if self.style == "shayari":
                a = (0.16 * out + (0.92 - 0.16) * p) * out     # whole line faintly present
                focus = False
                glow_a = (0.55 * speaking + 0.18 * p) * out
            else:
                a = 0.94 * p * out
                focus = True                                   # soft -> sharp as the word is spoken
                glow_a = (0.40 * speaking + 0.16 * p) * out
            if a <= 0.003:
                continue
            dy = int((1 - p) * 10) - lift
            ops.append(("glow", it, it["x"], it["y"] + dy, LAV if not it["key"] else AMBER * 0.9, glow_a * 0.55))
            ops.append(("ink", it, p, focus, it["x"], it["y"] + dy, col, a))
        return ops

    def draw(self, img, t, y_off=0):
        self.y_off = y_off
        for op in self.frame_ops(t):
            if op[0] == "rule":
                _, rx, y0, y1, out = op
                y0, y1 = max(0, y0 - y_off), max(0, y1 - y_off)
                img[y0:y1, rx:rx + 2] = img[y0:y1, rx:rx + 2] * 0.4 + LAV * 0.6 * out
            elif op[0] == "glow":
                _, it, x, y, col, a = op
                self._add(img, it["glow"], x, y, col, a)
            else:
                _, it, p, focus, x, y, col, a = op
                m = it["m"] * p + it["soft"] * (1 - p) if focus else it["m"]
                self._blit(img, m, x, y, col, a)

    def _blit(self, img, m, x, y, col, a):
        y -= getattr(self, "y_off", 0)
        h, w = m.shape
        y0, x0 = max(0, y), max(0, x)
        y1, x1 = min(img.shape[0], y + h), min(img.shape[1], x + w)
        if y1 <= y0 or x1 <= x0:
            return
        mm = m[y0 - y:y1 - y, x0 - x:x1 - x][..., None] * a
        reg = img[y0:y1, x0:x1]
        reg[:] = reg * (1 - mm) + col * mm

    def _add(self, img, m, x, y, col, a):
        y -= getattr(self, "y_off", 0)
        h, w = m.shape
        y0, x0 = max(0, y), max(0, x)
        y1, x1 = min(img.shape[0], y + h), min(img.shape[1], x + w)
        if y1 <= y0 or x1 <= x0:
            return
        img[y0:y1, x0:x1] += col * (m[y0 - y:y1 - y, x0 - x:x1 - x][..., None] * a)
