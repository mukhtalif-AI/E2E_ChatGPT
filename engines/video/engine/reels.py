"""
reels.py - Instagram teaser reels (1080x1920, 9:16) made from a finished episode.

Each reel takes one of the best moments of the episode (~20-30 s, starting and ending on whole sentences):
  * the living artwork, re-framed for a phone with a slow drift across the scene
  * a HOOK line in the first 3 seconds (written by the local AI from what you actually say in that part)
  * every word you speak, word by word (most people watch reels muted), key word in amber
  * the light line breathing with your voice, the "Audio Story | सुकून | Sleep Therapy" strip
  * an ending card: "पूरी वीडियो YouTube पे"
  * sound loudness set for Instagram (-14 LUFS), soft fade in/out
A ready-to-paste caption with hashtags is saved next to each reel.  Everything stays inside Instagram's
safe area (nothing under the like/comment buttons or the caption text).

    python reels.py <work\\episode folder> <final video.mp4> [count]
"""
import json, os, pickle, re, subprocess, sys, threading, queue

import numpy as np

ENGINE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ENGINE)
import overlay as ov            # noqa: E402
import kinetic as kn            # noqa: E402
import clips                    # noqa: E402
import paths                    # noqa: E402

RW, RH = 1080, 1920
REEL_TAIL = 2.4 + 3.4                                   # last words finish dissolving (2.4 s), then the end card
Y_HEADER, Y_HOOK, Y_TEXT, Y_CTA, Y_LINE, Y_TAG = 270, 440, 770, 820, 1390, 1478
# where the channel name / episode title go on a reel:
#   "none"      nothing extra - the end card already says Mukhtalif Awaaz
#   "corner"    small top-left badge, the same as the long video's corner badge
#   "title"     the episode title alone at the top, fading in after the hook has gone (never stacked)
#   "signature" "सुकून · title" + MUKHTALIF AWAAZ under the light line
REEL_BRAND = os.environ.get("REEL_BRAND", "corner")


# ------------------------------------------------------------------ helpers
def _ff():
    return ov.ffmpeg_exe()


def _probe(ff):
    return ff.replace("ffmpeg.exe", "ffprobe.exe").replace("/ffmpeg", "/ffprobe")


def _load(p, default=None):
    try:
        return json.load(open(p, encoding="utf-8"))
    except Exception:
        return default


_NV = None


def _nvenc(ff):
    """True when the NVIDIA encoder really works here (a tiny test encode)."""
    global _NV
    if _NV is None:
        try:
            _NV = subprocess.run([ff, "-v", "error", "-f", "lavfi", "-i", "color=black:s=256x256:d=0.1", "-c:v", "h264_nvenc",
                                  "-f", "null", "-"], capture_output=True, timeout=30).returncode == 0
        except Exception:
            _NV = False
    return _NV


def _blit(img, m, x, y, col, a, glow=None, glow_a=0.0):
    """Paint mask m (float 0..1) at (x, y) onto float image img with colour col and opacity a."""
    if a <= 0.003:
        return
    h, w = m.shape
    y0, x0, y1, x1 = max(0, y), max(0, x), min(img.shape[0], y + h), min(img.shape[1], x + w)
    if y1 <= y0 or x1 <= x0:
        return
    reg = img[y0:y1, x0:x1]
    if glow is not None and glow_a > 0:
        reg += ov.LAV * (glow[y0 - y:y1 - y, x0 - x:x1 - x] * glow_a)[..., None]
    mm = (m[y0 - y:y1 - y, x0 - x:x1 - x] * a)[..., None]
    reg[:] = reg * (1 - mm) + np.asarray(col, np.float32) * mm


def _env(t, t0, t1, fi=0.5, fo=0.6):
    if t < t0 or t > t1:
        return 0.0
    q = min(1.0, (t - t0) / fi, (t1 - t) / fo)
    return q * q * (3 - 2 * q)


def _centered(mask):
    cols = np.where(mask.max(0) > 0.03)[0]
    return (RW - (cols.max() - cols.min() + 1)) // 2 - cols.min() if len(cols) else 0


# ------------------------------------------------------------------ words + hook
def _segments(work):
    return clips.episode_segments(work)


def _lines_for(work, a, b):
    """Every spoken sentence inside [a, b] as kinetic lines (times relative to the reel), key words from moments."""
    moments = _load(os.path.join(work, "moments.json"), [])
    out = []
    for s in _segments(work):
        allw = s.get("words", [])
        ws = [w for w in allw if a + 0.2 <= float(w["start"]) and float(w["end"]) <= b - 0.6]
        if len(ws) < 2 or len(ws) < 0.7 * len(allw):          # half-heard sentence at the edge: leave it out
            continue
        key = None
        for m in moments:
            if m.get("key") is None or not (float(m["start"]) <= float(ws[-1]["end"]) and float(m["end"]) >= float(ws[0]["start"])):
                continue
            kw = m["words"][m["key"]]
            for i, w in enumerate(ws):
                if abs(float(w["start"]) - float(kw["start"])) < 0.25:
                    key = i
        words = [dict(w=w["w"], start=float(w["start"]) - a, end=float(w["end"]) - a) for w in ws]
        for i, w in enumerate(words):                           # spelling from the chosen moments (nukta fixes)
            if key == i:
                for m in moments:
                    if m.get("key_word") and abs(float(m["words"][m["key"]]["start"]) - a - w["start"]) < 0.25:
                        w["w"] = m["key_word"]
        # long sentences: split where the meaning (and your voice) pauses, never mid-thought
        for k0, k1 in kn.split_meaning(words):
            piece = words[k0:k1]
            pk = (key - k0) if key is not None and k0 <= key < k1 else None
            out.append(kn.Line(piece, pk))
    return out


HOOK_PROMPT = """These are lines the narrator of a calm Hindi sleep-story channel ("सुकून") actually SAYS in one
25-second part of the episode "{title}":
{lines}

1. "best": the number of the ONE line that would make a tired person scrolling Instagram at night stop and listen -
   emotionally true, complete on its own, makes sense without the rest. Pick, do not rewrite.
2. "caption": an Instagram caption: 2 short warm Hindi lines about this moment, then the line
   "पूरी वीडियो YouTube पे - Mukhtalif Awaaz (link in bio) 🎧", then 8-10 relevant hashtags
   (mix Hindi-audience and English tags like #sukoon #sleepstory #hindistory #audiostory).
Return JSON: {{"best": 0, "caption": "..."}}"""

FALLBACK_HOOKS = ["क्या आपको भी नींद नहीं आती?", "बस आँखें बंद कीजिए और सुनिए…", "ये बात दिल को छू जाएगी…"]

HOOK_PROMPT_EN = """These are lines the narrator of a calm English sleep-story channel ("Sukoon") actually SAYS in one
25-second part of the episode "{title}":
{lines}

1. "best": the number of the ONE line that would make a tired person scrolling Instagram at night stop and listen -
   emotionally true, complete on its own, makes sense without the rest. Pick, do not rewrite.
2. "caption": an Instagram caption: 2 short warm English lines about this moment, then the line
   "Full episode on YouTube - Mukhtalif Awaaz (link in bio) 🎧", then 8-10 relevant hashtags
   (like #sukoon #sleepstory #audiostory #sleeptherapy).
Return JSON: {{"best": 0, "caption": "..."}}"""
FALLBACK_HOOKS_EN = ["Can't sleep tonight?", "Just close your eyes and listen…", "This one is for your heart…"]


def is_en(work):
    try:
        return open(os.path.join(work, "lang.txt"), encoding="utf-8").read().strip() == "en"
    except OSError:
        return False


def hook_and_caption(work, title, a, b, k, used=()):
    """The hook is the narrator's OWN strongest line in this part (chosen by the local AI, never rewritten) -
    always natural Hindi, always true to the episode."""
    cache_p = os.path.join(work, "reels.json")
    cache = _load(cache_p, {})
    key = f"v3 {a:.1f}"
    if key in cache and cache[key].get("hook"):
        return cache[key]["hook"], cache[key]["caption"]
    sents = []                                            # whole spoken sentences (pieces joined back together)
    for s in sorted(_segments(work), key=lambda s: float(s["start"])):
        ws = s.get("words", [])
        if not ws:
            continue
        if sents and (float(s["start"]) - sents[-1][1] <= 0.3 or
                      (s.get("sent") is not None and s.get("sent") == sents[-1][2])):
            sents[-1][0].extend(ws); sents[-1][1] = float(s["end"]); sents[-1][3].append(list(ws))
        else:
            sents.append([list(ws), float(s["end"]), s.get("sent"), [list(ws)]])
    cands = []
    def add(ws):
        if a - 0.5 <= float(ws[0]["start"]) and float(ws[-1]["end"]) <= b + 0.5 and 5 <= len(ws) <= 16:
            t = " ".join(w["w"] for w in ws)
            if t not in used and t not in cands:
                cands.append(t)
    for ws, _, _, pieces in sents:
        if len(ws) <= 16:
            add(ws)
        else:                                             # a long sentence (BRAKEUP reel 1: 25 words): its
            for pc in pieces:                             # spoken pieces can still be a strong line on their own
                add(pc)
    hook, cap, ai = None, None, False
    if cands:
        try:
            import sleep_pipeline as sp
            import sv_av
            model = sp.pick_llm()
            lines = "\n".join(f"{i}. {c}" for i, c in enumerate(cands))
            en = is_en(work)
            r = json.loads(sv_av.local_chat(model, (HOOK_PROMPT_EN if en else HOOK_PROMPT).format(
                title=title or ("Sukoon" if en else "सुकून"), lines=lines), timeout=300))
            i = int(r.get("best", 0))
            hook = cands[i] if 0 <= i < len(cands) else cands[0]
            cap = re.sub(r"#\s+", "#", str(r.get("caption", ""))).strip()
            if cap and "#mukhtalifawaaz" not in cap.lower():
                cap += " #mukhtalifawaaz"
            ai = True
        except Exception as e:
            print(f"      (AI choice skipped: {e})", flush=True)
            hook = max(cands, key=len)
    if hook and ai:                                       # only AI results are remembered (edit reels.json freely)
        cache[key] = dict(hook=hook, caption=cap or "")
        json.dump(cache, open(cache_p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    if is_en(work):
        hook = hook or FALLBACK_HOOKS_EN[(k - 1) % len(FALLBACK_HOOKS_EN)]
        cap = cap or (f"{title or 'Sukoon'}\n\nClose your eyes, and just listen.\n\nFull episode on YouTube - Mukhtalif Awaaz "
                      "(link in bio) 🎧\n\n#sukoon #sleepstory #audiostory #mukhtalifawaaz #sleeptherapy #bedtimestory")
    hook = hook or FALLBACK_HOOKS[(k - 1) % len(FALLBACK_HOOKS)]
    cap = cap or (f"{title or 'सुकून'}\n\nआँखें बंद कीजिए, और बस सुनिए।\n\nपूरी वीडियो YouTube पे - Mukhtalif Awaaz (link in bio) 🎧\n\n"
                  "#sukoon #sleepstory #hindistory #audiostory #mukhtalifawaaz #sleeptherapy #nostalgia #hindi")
    return hook, cap


# ------------------------------------------------------------------ background: artwork re-framed for a phone
# The artwork is read a bit larger than the phone frame; a "camera" then glides over it with smooth
# sub-pixel zooms and drifts (a new framing at each new sentence) - alive, but never jittery.
SRC_W, SRC_H = 1350, 2400        # source window (9:16); the camera shows 1080..1350 px of it
H2 = 2592                         # artwork scaled to this height (8% extra trimmed mostly from the dark bottom)


def _pan(loop):
    """x of the source window in the scaled artwork - centred on the people if known."""
    sw = int(round(H2 * 16 / 9 / 2) * 2)
    cx = 0.5
    boxes = _load(loop + ".subject.json", {}).get("boxes", []) if os.path.exists(loop + ".subject.json") else []
    xs = []
    for bx in boxes:
        try:
            x0, _, x1, _ = (bx if isinstance(bx, (list, tuple)) else [bx[k] for k in ("x0", "y0", "x1", "y1")])[:4]
            xs.append((float(x0) + float(x1)) / 2 / 1920)
        except Exception:
            pass
    if xs:
        cx = float(np.clip(np.median(xs), 0.3, 0.7))
    return sw, int(np.clip(round(cx * sw - SRC_W / 2), 0, sw - SRC_W))


def _bg_reader(ff, loop, loop_len, fps, a, dur, q):
    sw, x = _pan(loop)
    off = (a * fps % max(1, loop_len)) / fps if loop_len else 0
    n = int(round(dur * fps))
    vf = (f"scale={sw}:{H2}:flags=lanczos,crop={SRC_W}:{SRC_H}:{x}:{int((H2 - SRC_H) * 0.25)},"
          f"eq=saturation=1.06,format=rgb24")
    p = subprocess.Popen([ff, "-v", "error", "-stream_loop", "-1", "-ss", f"{off:.3f}", "-i", loop, "-frames:v", str(n),
                          "-vf", vf, "-r", str(fps), "-f", "rawvideo", "-"], stdout=subprocess.PIPE, bufsize=SRC_W * SRC_H * 3 * 2)
    fb = SRC_W * SRC_H * 3
    for _ in range(n):
        buf = p.stdout.read(fb)
        if len(buf) < fb:
            break
        q.put(np.frombuffer(buf, np.uint8).reshape(SRC_H, SRC_W, 3))
    q.put(None)
    p.wait()


SHOTS = [(1.00, 0.50, 0.50), (1.10, 0.46, 0.54), (1.05, 0.55, 0.47), (1.13, 0.52, 0.57), (1.07, 0.48, 0.45)]   # gentle reframes


def _ease(q):
    q = min(1.0, max(0.0, q)); return q * q * (3 - 2 * q)


# camera motions for reels: name -> (label, f(t, dur) -> (zoom, cx, cy)); "sentence" = the original framing changes
MOTIONS = {
    "sentence": ("New framing at each sentence", None),
    "push_in": ("Slow push-in", lambda t, d: (1.0 + 0.24 * _ease(t / d), 0.5, 0.52)),
    "pull_back": ("Pull-back reveal", lambda t, d: (1.26 - 0.26 * _ease(t / d), 0.5, 0.5)),
    "pan_lr": ("Pan left to right", lambda t, d: (1.2, 0.36 + 0.28 * _ease(t / d), 0.5)),
    "pan_rl": ("Pan right to left", lambda t, d: (1.2, 0.64 - 0.28 * _ease(t / d), 0.5)),
    "tilt_up": ("Tilt up to the sky", lambda t, d: (1.2, 0.5, 0.62 - 0.24 * _ease(t / d))),
    "breathing": ("Breathing (still, gentle pulse)", lambda t, d: (1.06 + 0.035 * (0.5 - 0.5 * np.cos(2 * np.pi * t / 10)), 0.5, 0.5)),
    "drift": ("Floating drift", lambda t, d: (1.14, 0.5 + 0.07 * np.sin(2 * np.pi * t / max(8.0, d)), 0.5 + 0.04 * np.sin(4 * np.pi * t / max(8.0, d)))),
}
MIX = ["push_in", "pan_lr", "tilt_up", "pull_back", "drift", "pan_rl", "breathing"]


class Camera:
    """Smooth framing changes at each new sentence + a slow continuous push-in - or one of MOTIONS."""

    def __init__(self, cuts, dur, glide=2.6, motion="sentence"):
        self.motion = MOTIONS.get(motion, MOTIONS["sentence"])[1]
        self.keys = [(0.0, SHOTS[0])]
        for i, t in enumerate(c for c in cuts if 2.0 < c < dur - 4.0):
            if t - self.keys[-1][0] >= 3.0:
                self.keys.append((t - 0.3, SHOTS[1 + (len(self.keys) - 1) % (len(SHOTS) - 1)]))
        self.dur, self.glide = dur, glide

    def at(self, t):
        if self.motion:
            return self.motion(t, self.dur)
        j = max(i for i, (t0, _) in enumerate(self.keys) if t >= t0 or i == 0)
        shot = self.keys[j][1]
        prev = self.keys[j - 1][1] if j > 0 else shot
        q = min(1.0, max(0.0, (t - self.keys[j][0]) / self.glide))
        q = q * q * (3 - 2 * q)
        z, cx, cy = (p + (c - p) * q for p, c in zip(prev, shot))
        z = min(1.25, z * (1 + 0.05 * t / max(1.0, self.dur)))     # gentle push-in all the time
        return z, cx, cy


try:                                                # ComfyUI's Python has PyTorch + CUDA: the camera runs on the GPU
    import torch
    import torch.nn.functional as _F
    _DEV = "cuda" if torch.cuda.is_available() else None
except Exception:
    torch, _DEV = None, None


def _view(z, cx, cy):
    vw = SRC_W / z
    vh = vw * RH / RW
    x0 = float(np.clip(cx * SRC_W - vw / 2, 0, SRC_W - vw))
    y0 = float(np.clip(cy * SRC_H - vh / 2, 0, SRC_H - vh))
    return x0, y0, vw, vh


def _sample_gpu(src, z, cx, cy):
    x0, y0, vw, vh = _view(z, cx, cy)
    if not src.flags.writeable:                         # frames read from ffmpeg are read-only: no torch warning
        src = src.copy()
    s = torch.from_numpy(src).to(_DEV).permute(2, 0, 1)[None].float()
    xs = x0 + (torch.arange(RW, device=_DEV, dtype=torch.float32) + 0.5) * (vw / RW)
    ys = y0 + (torch.arange(RH, device=_DEV, dtype=torch.float32) + 0.5) * (vh / RH)
    gx = xs / SRC_W * 2 - 1
    gy = ys / SRC_H * 2 - 1
    grid = torch.stack(torch.meshgrid(gy, gx, indexing="ij")[::-1], -1)[None]
    out = _F.grid_sample(s, grid, mode="bilinear", padding_mode="border", align_corners=False)
    return out[0].permute(1, 2, 0).cpu().numpy()


def _sample(src, z, cx, cy):
    if _DEV:
        return _sample_gpu(src, z, cx, cy)
    return _sample_cpu(src, z, cx, cy)


def _sample_cpu(src, z, cx, cy):
    """Smooth (bilinear, sub-pixel) RW x RH view of the source at zoom z centred at (cx, cy) in 0..1."""
    vw = SRC_W / z
    vh = vw * RH / RW
    x0 = np.clip(cx * SRC_W - vw / 2, 0, SRC_W - vw)
    y0 = np.clip(cy * SRC_H - vh / 2, 0, SRC_H - vh)
    ys = y0 + (np.arange(RH, dtype=np.float32) + 0.5) * (vh / RH) - 0.5
    xs = x0 + (np.arange(RW, dtype=np.float32) + 0.5) * (vw / RW) - 0.5
    yi = np.clip(ys.astype(np.int32), 0, SRC_H - 2); fy = np.clip(ys - yi, 0, 1)[:, None, None]
    xi = np.clip(xs.astype(np.int32), 0, SRC_W - 2); fx = np.clip(xs - xi, 0, 1)[None, :, None]
    a = src[yi].astype(np.float32)
    r = a + (src[yi + 1].astype(np.float32) - a) * fy
    c = r[:, xi]
    return c + (r[:, xi + 1] - c) * fx


class Motes:
    """A few soft specks of light drifting upward (dust in moonlight) - subtle life in every frame."""

    def __init__(self, seed, n=38):
        rng = np.random.default_rng(seed)
        self.x = rng.uniform(0, RW, n); self.y = rng.uniform(250, RH - 300, n)
        self.vy = rng.uniform(8, 24, n); self.sw = rng.uniform(5, 16, n); self.fq = rng.uniform(0.08, 0.25, n)
        self.ph = rng.uniform(0, 6.28, n); self.a = rng.uniform(0.18, 0.5, n); self.s = rng.integers(0, 3, n)
        self.patch = []
        for sg in (1.3, 2.0, 3.0):
            r = int(sg * 3)
            yy, xx = np.mgrid[-r:r + 1, -r:r + 1]
            self.patch.append(np.exp(-(xx ** 2 + yy ** 2) / (2 * sg * sg)).astype(np.float32))
        self.col = np.array([1.0, 0.94, 0.86], np.float32)

    def draw(self, frame, t):
        span = RH - 550
        for i in range(len(self.x)):
            y = 250 + (self.y[i] - 250 - self.vy[i] * t) % span
            x = self.x[i] + self.sw[i] * np.sin(self.fq[i] * 6.283 * t + self.ph[i])
            tw = 0.6 + 0.4 * np.sin(t * 1.3 + self.ph[i] * 3)
            p = self.patch[self.s[i]]
            r = p.shape[0] // 2
            xi, yi = int(x), int(y)
            if xi - r < 0 or xi + r + 1 > RW:
                continue
            reg = frame[yi - r:yi + r + 1, xi - r:xi + r + 1].astype(np.float32)
            reg += (p * (255 * self.a[i] * tw))[..., None] * self.col
            frame[yi - r:yi + r + 1, xi - r:xi + r + 1] = np.clip(reg, 0, 255).astype(np.uint8)


# ------------------------------------------------------------------ one reel
class Band:
    def __init__(self, frame, r0, r1):
        self.frame, self.r0, self.r1 = frame, max(0, r0), min(RH, r1)
        self.a = frame[self.r0:self.r1].astype(np.float32) * (1 / 255.0)

    def commit(self):
        np.clip(self.a, 0, 1, out=self.a)
        self.frame[self.r0:self.r1] = (self.a * 255 + 0.5).astype(np.uint8)


def reel_signature(label, title):
    """A quiet signature under the light line (replaces the stacked top header and the
    "Audio Story | सुकून | Sleep Therapy" strip): "सुकून · <episode title>" in the classic serif, then a silver
    hairline and MUKHTALIF AWAAZ - the same family as the corner badge of the long video. Soft dark shadows
    keep it readable on any artwork.  -> (layers [(mask, y, colour)], height); masks are full reel width."""
    P = 20
    layers, y = [], 0
    def add(m, col, sh, r):
        nonlocal y
        m = ov._crop(m, pad=P)
        full = np.zeros((m.shape[0], RW), np.float32)
        x = (RW - m.shape[1]) // 2
        full[:, x:x + m.shape[1]] = m
        layers.insert(0, (np.clip(ov._box(full, r) * 2.4, 0, 1) * sh, y, (0.0, 0.0, 0.0)))   # shadow first
        layers.append((full, y, col))
        y += m.shape[0] - 2 * P
        return full
    line = f"{label}  ·  {title}" if title else label
    add(ov._embolden(ov.text_mask([line], ov.HINDI(), 44, RW, pad=10), 0.4), ov.PEARL, 0.55, 7)
    y += 22
    sub = add(ov._embolden(ov.text_mask(["M U K H T A L I F   A W A A Z"], ov.WORDMARK(), 23, RW, pad=10), 0.3),
              ov.SILVER, 0.55, 5)
    cols = np.where(sub.max(0) > 0.05)[0]
    if len(cols):                                       # hairline between the two lines, fading at both ends
        xs = np.arange(RW, dtype=np.float32)
        c, half = (cols.min() + cols.max()) / 2, (cols.max() - cols.min()) * 0.40
        hl = np.zeros((3, RW), np.float32)
        hl[1] = np.clip(1 - np.abs(xs - c) / half, 0, 1) ** 1.5 * 0.8
        hl[0] = hl[2] = hl[1] * 0.3
        layers.append((hl, layers[-1][1] + P - 13, ov.SILVER))
    return layers, y + 2 * P


def render_reel(st, work, a, b, k, out_mp4, hook, label, title):
    ff = _ff()
    kn.H = RH                                          # kinetic text may sit anywhere in the tall frame
    fps = st["fps"]
    dur = b - a
    n = int(round(dur * fps))
    import compose
    bands, level = ov.audio_bands(compose.load_wav16k(st["audio"], a, dur), 16000, fps)   # this reel's sound
    # --- static pieces
    rows = np.ones(RH, np.float32)                                     # legibility: soft top + bottom shade
    yy = np.arange(RH, dtype=np.float32)
    rows *= 1 - 0.30 * np.clip(1 - yy / 520, 0, 1) ** 1.5
    rows *= 1 - 0.25 * np.clip((yy - 1450) / 470, 0, 1) ** 1.3
    rows *= 1 - 0.18 * np.exp(-((yy - 920) / 330) ** 2)               # gentle dim behind the words
    rows_u = rows[:, None, None]
    sig, sig_h = reel_signature(label, title) if REEL_BRAND == "signature" else ([], 0)
    sig_y0 = Y_LINE + 52                               # just under the light line
    corner = None
    if REEL_BRAND == "corner":                         # the long video's badge, top-left below Instagram's bar
        corner = ov.Brand(title=label, t_in=0.0)
    tmask = None
    if REEL_BRAND == "title" and title:
        tmask = ov._crop(ov._embolden(ov.text_mask([title], ov.HINDI(), 66, RW, pad=10), 0.4), pad=24)
        tshadow = np.clip(ov._box(tmask, 9) * 2.4, 0, 1) * 0.55
    hl = ov.wrap_balanced(hook, 18) if len(hook) <= 36 else ov.wrap(hook, 27)
    hs = 80 if len(hl) <= 2 and len(hook) <= 36 else (70 if len(hl) <= 2 else 62)
    m_hook = ov.text_mask(hl, ov.BOLD_HI(), hs, RW, line_h=int(hs * 1.45), pad=30)
    hook_y = Y_HOOK - 45 * max(0, len(hl) - 2)
    g_hook = ov._box(m_hook, 14)
    m_cta1 = ov.text_mask(["पूरी वीडियो YouTube पे"], ov.BOLD_HI(), 62, RW, pad=24)
    _t2 = ov._crop(ov.text_mask(["Mukhtalif Awaaz  ·  headphones के साथ"], ov.LATIN(), 32, RW, pad=14), pad=6)
    _ic = ov.headphones_icon(40)
    _h = max(_t2.shape[0], _ic.shape[0]) + 12
    _row = np.zeros((_h, _ic.shape[1] + 16 + _t2.shape[1]), np.float32)
    _row[(_h - _ic.shape[0]) // 2:(_h - _ic.shape[0]) // 2 + _ic.shape[0], :_ic.shape[1]] = _ic
    _row[(_h - _t2.shape[0]) // 2:(_h - _t2.shape[0]) // 2 + _t2.shape[0], _ic.shape[1] + 16:] = _t2
    m_cta2 = np.zeros((_h, RW), np.float32)                          # icon + name as one centred row
    _x = (RW - _row.shape[1]) // 2
    m_cta2[:, _x:_x + _row.shape[1]] = _row
    g_cta1 = ov._box(m_cta1, 12)
    ico = ov.headphones_icon(40)
    ll = ov.LightLine(width=880, y=Y_LINE, height=120)
    ll.x0 = (RW - 880) // 2
    lines = _lines_for(work, a, b)
    track = kn.KineticTrack(lines, "mukhtalif", [[150, Y_TEXT]])
    mo = os.environ.get("REELS_MOTION") or "mix"            # smooth continuous motions by default
    if mo == "mix":                                         # a different motion for every reel
        mo = MIX[(k - 1) % len(MIX)]
    print(f"      reel {k}: camera = {MOTIONS.get(mo, MOTIONS['sentence'])[0]}", flush=True)
    cam = Camera([ln.start for ln in lines], dur, motion=mo)
    motes = Motes(int(a * 10) + k)
    cta_t0 = max(dur - 3.4, dur * 0.6)
    i0 = 0

    # --- encoder
    venc = ["-c:v", "h264_nvenc", "-preset", "p6", "-rc", "vbr", "-cq", "19", "-b:v", "9M", "-maxrate", "14M",
            "-bufsize", "28M", "-profile:v", "high"] if _nvenc(ff) else ["-c:v", "libx264", "-preset", "medium", "-crf", "18"]
    part = out_mp4 + ".part.mp4"
    enc = subprocess.Popen([ff, "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{RW}x{RH}", "-r", str(fps),
                            "-i", "-", "-ss", f"{a:.3f}", "-t", f"{dur:.3f}", "-i", st["audio"],
                            "-map", "0:v", "-map", "1:a", *venc, "-pix_fmt", "yuv420p",
                            "-af", f"afade=t=in:st=0:d=0.25,afade=t=out:st={dur - 1.8:.2f}:d=1.8,"
                                   f"loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000",
                            "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart", part],
                           stdin=subprocess.PIPE)
    q = queue.Queue(maxsize=12)
    threading.Thread(target=_bg_reader, args=(ff, st["loop"], st.get("loop_len") or 0, fps, a, dur, q), daemon=True).start()

    for i in range(n):
        fr = q.get()
        if fr is None:
            break
        t = i / fps
        fi = min(i0 + i, len(level) - 1)
        lv = float(level[fi])
        lvs = float(level[max(0, fi - 36):fi + 1].mean())
        z, cx, cy = cam.at(t)
        frame = (_sample(fr, z, cx, cy) * rows_u).astype(np.uint8)
        motes.draw(frame, t)
        if corner is not None:
            bc = Band(frame, 150, 150 + 200)
            ak0 = 0.92 * _env(t, 0.3, dur, 1.0, 1.0)
            for m, y, col, g, gcol, gamt, shines in corner.layers():
                _blit(bc.a, m, 24 + corner.x0, y, col, ak0)
            bc.commit()
        if tmask is not None:
            at0 = 0.95 * _env(t, 4.2, dur, 1.2, 1.0)            # after the hook has gone
            if at0 > 0:
                ty0 = Y_HOOK + 20
                bt = Band(frame, ty0, ty0 + tmask.shape[0])
                tx = (RW - tmask.shape[1]) // 2
                _blit(bt.a, tshadow, tx, 0, (0.0, 0.0, 0.0), at0)
                _blit(bt.a, tmask, tx, 0, ov.PEARL, at0)
                bt.commit()
        # hook (first ~3.6 s)
        ak = _env(t, 0.0, 3.8, 0.35, 0.7)
        if ak > 0:
            rise = int((1 - min(1.0, t / 0.35)) * 14)
            b2 = Band(frame, hook_y - 30, hook_y + m_hook.shape[0] + 30)
            _blit(b2.a, m_hook, 0, 30 + rise, ov.TXT, 0.97 * ak, g_hook, 0.55 * ak)
            b2.commit()
        # the narrator's words
        ac = _env(t, cta_t0, dur, 0.7, 0.6)                          # ending card takes over the middle
        ys = [track.bbox(li) for li in track.active(t)] if ac < 0.999 else []
        if ys:
            y0, y1 = min(p for p, _ in ys), max(p for _, p in ys)
            b3 = Band(frame, y0, min(y1, Y_LINE - 70))
            before = b3.a.copy() if ac > 0 else None
            track.draw(b3.a, t, y_off=b3.r0)
            if before is not None:
                b3.a = b3.a * (1 - ac) + before * ac
            b3.commit()
        if ac > 0:
            b5 = Band(frame, Y_CTA, Y_CTA + m_cta1.shape[0] + m_cta2.shape[0] + 40)
            _blit(b5.a, m_cta1, 0, 0, kn.AMBER, 0.95 * ac, g_cta1, 0.5 * ac)
            _blit(b5.a, m_cta2, 0, m_cta1.shape[0] - 4, ov.TXT, 0.8 * ac)
            b5.commit()
        b4 = Band(frame, Y_LINE - 80, sig_y0 + sig_h + 10)
        # light line + the signature (breathing very gently with the music)
        sub = b4.a[Y_LINE - ll.h // 2 - (Y_LINE - 80):]
        class _V:                                                   # LightLine indexes absolute rows
            def __getitem__(s, key):
                ys_, xs_ = key
                return sub[ys_.start - (Y_LINE - ll.h // 2):ys_.stop - (Y_LINE - ll.h // 2), xs_]
        ll.draw(_V(), bands[min(fi, len(bands) - 1)], t + a, lv)
        asg = 0.92 * _env(t, 0.6, dur, 1.2, 1.0) * (0.85 + 0.15 * min(1.0, lvs * 1.3))
        for m, y, col in sig:
            _blit(b4.a, m, 0, sig_y0 - (Y_LINE - 80) + y, col, asg)
        b4.commit()
        # gentle fade from black at the very start, to black at the end
        g = min(1.0, (t + 0.15) / 0.35, (dur - t) / 0.9)
        if g < 1:
            frame = (frame.astype(np.float32) * max(g, 0)).astype(np.uint8)
        enc.stdin.write(frame.tobytes())
    enc.stdin.close()
    if enc.wait() == 0:
        os.replace(part, out_mp4)
        return True
    return False


# ------------------------------------------------------------------ all reels of an episode
def _windows_at(work, times, length):
    """your own moments: start at the sentence beginning nearest each time, end on a whole sentence"""
    sents = clips.sentence_bounds(clips.episode_segments(work))
    out = []
    for t in times:
        if not sents:
            out.append((max(0.0, t), t + length, {})); continue
        a = min((x for x, _ in sents), key=lambda x: abs(x - t))
        ends = [e for _, e in sents if e >= a + length * 0.6]
        e = min(ends, key=lambda e: abs(e - (a + length))) if ends else a + length
        out.append((max(0.0, a - 0.3), e, {}))
    return out


# ------------------------------------------------------------------ quality gate (same rules as the hub's Repair page)
def analyse_motion(d, fps, loop_len_s=0.0):
    """d = frame-to-frame change. -> problems: jump (one frame leaps), abrupt camera move (the view starts moving
    hard from standstill = 'stutter movement'), loop pop, frozen."""
    out = []
    if len(d) < 10:
        return out
    k = 48
    pad = np.pad(d, (k, k), mode="edge")
    loc = np.array([np.median(pad[i:i + 2 * k + 1]) for i in range(len(d))]) if len(d) < 60000 else \
        np.convolve(d, np.ones(2 * k + 1) / (2 * k + 1), "same")
    nb = np.maximum(np.r_[d[1:2], d[:-1]], np.r_[d[1:], d[-2:-1]])          # bigger of the two neighbours
    last = -1e9
    for i in np.where((d > np.maximum(5 * loc, loc + 1.0)) & (d > 0.8))[0]:
        t = (i + 1) / fps
        if t - last < 2:
            continue
        isolated = d[i] > 2.5 * nb[i]
        sustained = (d[i:i + 6] > 3 * loc[i]).all()
        before = d[max(0, i - 3):i]
        abrupt = sustained and len(before) and d[i] > 3 * max(float(before.max()), 0.05)
        if not (isolated or abrupt):
            continue                                          # a smooth, eased camera move: fine
        last = t
        seam = loop_len_s > 0 and min(t % loop_len_s, loop_len_s - t % loop_len_s) < 0.15
        kind = "loop pop" if seam and isolated else "jump" if isolated else "abrupt camera move"
        why = {"loop pop": "the living artwork jumps where its loop starts again",
               "jump": f"one frame leaps (change {d[i]:.1f} vs {loc[i]:.1f} around it)",
               "abrupt camera move": "the view starts moving hard from standstill (stutter movement)"}[kind]
        out.append({"at": round(float(t), 2), "kind": kind, "why": why})
    still, run = d < 0.02, 0
    for i, st in enumerate(np.append(still, False)):
        if st:
            run += 1; continue
        if run >= int(fps):
            a = (i - run + 1) / fps
            around = np.concatenate([d[max(0, i - run - 48):i - run], d[i:i + 48]])
            if len(around) and np.median(around) > 0.05:
                out.append({"at": round(float(a), 2), "kind": "frozen", "why": f"the picture stands still for {run / fps:.1f} s"})
        run = 0
    return out[:60]



def motion_problems(path, w=64, h=36):
    try:
        ff = _ff()
        r = subprocess.run([ff, "-v", "error", "-i", path, "-an", "-vf", f"scale={w}:{h}:flags=area,format=gray",
                            "-f", "rawvideo", "-"], capture_output=True)
        a_ = np.frombuffer(r.stdout, np.uint8)
        n = len(a_) // (w * h)
        f = a_[:n * w * h].reshape(n, h, w).astype(np.int16)
        d = np.abs(np.diff(f, axis=0)).mean(axis=(1, 2)) if n > 1 else np.zeros(0)
        return [p_ for p_ in analyse_motion(d, 24.0) if p_["at"] > 0.6 and p_["at"] < n / 24.0 - 0.6]
    except Exception as e:
        print(f"      (quality check skipped: {e})", flush=True)
        return []


def make(work, video, count=3, length=24.0, info=None):
    """info: dict(audio, loop, fps, label, title); default = the .reel_info.json saved with the video."""
    import paths
    gpy = paths.GPU_PYTHON
    if not _DEV and not info and os.path.exists(gpy) and not os.environ.get("REELS_CHILD"):
        # this Python has no GPU library: run the reels with ComfyUI's Python (PyTorch + CUDA) - much faster camera
        env = dict(os.environ, REELS_CHILD="1", PYTHONIOENCODING="utf-8")
        import time as _t
        t_start = _t.time() - 2
        r = subprocess.run([gpy, "-s", os.path.abspath(__file__), work, video, str(count)], env=env)
        od = os.path.join(os.path.dirname(os.path.abspath(video)), paths.EPISODE_REELS_NAME)
        if r.returncode == 0 and os.path.isdir(od):
            return [os.path.join(od, f) for f in os.listdir(od) if f.endswith(".mp4") and not f.endswith(".part.mp4")
                    and os.path.getmtime(os.path.join(od, f)) >= t_start]
        print("      (GPU reels failed - making them on the CPU, slower)", flush=True)
    info = info or _load(os.path.splitext(video)[0] + ".reel_info.json")
    if not info:
        raise RuntimeError("no .reel_info.json next to the video - re-render the episode once, or pass audio/loop")
    import compose
    st = dict(audio=info["audio"], loop=info["loop"], fps=int(info.get("fps") or 24),
              loop_len=compose._loop_len(info["loop"]))
    if is_en(work):
        import overlay as _ov
        _ov.BRAND_WORD = "Sukoon"
    label = info.get("label") or ("Sukoon" if is_en(work) else "सुकून")
    # your title.txt wins (it can change after the video was rendered - BRAKEUP's reels showed an old title)
    tf = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(video))), "title.txt")
    title_txt = open(tf, encoding="utf-8-sig").read().strip().splitlines()[0].strip() \
        if os.path.exists(tf) and open(tf, encoding="utf-8-sig").read().strip() else ""
    title = title_txt or info.get("title") or (_load(os.path.join(work, "title.json"), {}) or {}).get("title") or ""
    # options from the hub (env, so they also reach the GPU child process):
    #   REELS_LENGTH  seconds per reel     REELS_NEW=1  only moments no earlier reel used
    #   REELS_AT      "123,316" = start near these seconds (your own choice of moments)
    length = float(os.environ.get("REELS_LENGTH") or length)
    od = os.path.join(os.path.dirname(os.path.abspath(video)), paths.EPISODE_REELS_NAME)
    os.makedirs(od, exist_ok=True)
    log_p = os.path.join(od, "reels_made.json")
    used = _load(log_p, []) or []
    at = [float(x) for x in re.split(r"[,\s]+", os.environ.get("REELS_AT", "").strip()) if x]
    if at:
        wins = _windows_at(work, at, length)
    else:
        wins = clips.windows(work, count + (len(used) + 6 if os.environ.get("REELS_NEW") else 0), length)
        if os.environ.get("REELS_NEW"):
            wins = [w for w in wins if all(w[1] + 3 <= u["a"] or w[0] >= u["b"] - 3 for u in used)][:count]
            if not wins:
                print("      every good moment already has a reel - choose times yourself, or a shorter length", flush=True)
    first = 1
    if at or os.environ.get("REELS_NEW"):                     # number after the reels already there
        nums = [int(m_.group(1)) for f in os.listdir(od) for m_ in [re.match(r"reel(\d+)_", f)] if m_]
        first = max(nums, default=0) + 1
    made, used_hooks = [], [u.get("hook", "") for u in used]
    for k, (a, e, m) in enumerate(wins, first):
        b = e + REEL_TAIL                                     # full sentence, its words fade, then the end card
        hook, cap = hook_and_caption(work, title, a, b, k, used=[h for h in used_hooks])
        used_hooks.append(hook)
        kw = re.sub(r"[^\w\u0900-\u097F]+", "", m.get("key_word", "")) or "reel"
        out = os.path.join(od, f"reel{k}_{kw}.mp4")
        if os.path.exists(out):                               # never overwrite a reel: keep the old one aside
            os.makedirs(os.path.join(od, "_old"), exist_ok=True)
            import time as _t
            os.replace(out, os.path.join(od, "_old", _t.strftime("%H%M%S_") + os.path.basename(out)))
        print(f"      reel {k}: {int(a // 60)}:{int(a % 60):02d} ({b - a:.0f}s)  hook: {hook}", flush=True)
        ok = render_reel(st, work, a, b, k, out, hook, label, title)
        if ok:                                              # quality gate: never hand over a reel that jumps or freezes
            probs = motion_problems(out)
            if probs:
                print(f"      reel {k}: {', '.join(p_['kind'] + ' at ' + str(p_['at']) + ' s' for p_ in probs[:4])} - making it again, smooth drift", flush=True)
                os.makedirs(os.path.join(od, "_old"), exist_ok=True)
                os.replace(out, os.path.join(od, "_old", "broken_" + os.path.basename(out)))
                keep = os.environ.get("REELS_MOTION")
                os.environ["REELS_MOTION"] = "drift"
                try:
                    ok = render_reel(st, work, a, b, k, out, hook, label, title)
                finally:
                    if keep is None:
                        os.environ.pop("REELS_MOTION", None)
                    else:
                        os.environ["REELS_MOTION"] = keep
                if ok:
                    left = motion_problems(out)
                    print(f"      reel {k}: " + ("smooth now" if not left else f"still {len(left)} spot(s) - see Repair"), flush=True)
            else:
                print(f"      reel {k}: checked - smooth", flush=True)
        if ok:
            open(os.path.splitext(out)[0] + "_caption.txt", "w", encoding="utf-8").write(cap + "\n")
            made.append(out)
            import time as _t
            used.append({"a": round(a, 2), "b": round(b, 2), "file": os.path.basename(out), "hook": hook,
                         "date": _t.strftime("%Y-%m-%d %H:%M")})
            json.dump(used, open(log_p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        else:
            print(f"      reel {k} failed", flush=True)
    print(f"      reels folder: {od}", flush=True)
    return made


def _generic_scene(w, h, seed=3):
    """a calm night scene drawn from nothing (sky, moon, stars, hills, a lit window) - shows every motion clearly"""
    rng = np.random.default_rng(seed)
    y = np.linspace(0, 1, h, dtype=np.float32)[:, None]
    x = np.linspace(0, 1, w, dtype=np.float32)[None, :]
    top, bot = np.array([18, 22, 52], np.float32), np.array([74, 58, 110], np.float32)
    img = (top + (bot - top) * (y ** 1.3)[..., None]) * np.ones((h, w, 1), np.float32)
    for _ in range(int(w * h / 900)):                      # stars
        sx, sy = rng.integers(0, w), rng.integers(0, int(h * 0.55))
        img[sy, sx] = 200 + rng.random() * 55
    d = np.hypot(x - 0.68, (y - 0.22) * h / w)            # moon + glow
    img += (np.exp(-(d / 0.05) ** 8) * 210 + np.exp(-(d / 0.16) ** 2) * 60)[..., None] * np.array([1, 0.97, 0.9])
    for k, (base, amp, col) in enumerate([(0.66, 0.05, 30), (0.74, 0.04, 20), (0.83, 0.03, 12)]):   # hills
        ridge = base + amp * np.sin(x * (5 + 3 * k) + k) + 0.015 * np.sin(x * 23 + k * 2)
        img[np.broadcast_to(y > ridge, (h, w))] = col
    wx, wy = int(w * 0.3), int(h * 0.76)                   # a small lit window on the near hill
    img[wy:wy + 10, wx:wx + 8] = np.array([255, 200, 120])
    return np.clip(img, 0, 255).astype(np.float32)


def motion_previews(loop, out_dir, seconds=5.0, fps=15):
    """a short small preview of every camera motion on this episode's artwork (CPU, ~1 min) -> out_dir/<motion>.mp4"""
    ff = _ff()
    os.makedirs(out_dir, exist_ok=True)
    if loop == "generic":                                   # one neutral picture: the same previews for every episode
        src = _generic_scene(SRC_W // 3, SRC_H // 3)
    else:
        sw, x = _pan(loop)
        vf = f"scale={sw}:{H2}:flags=lanczos,crop={SRC_W}:{SRC_H}:{x}:{int((H2 - SRC_H) * 0.25)},scale={SRC_W // 3}:{SRC_H // 3},format=rgb24"
        raw = subprocess.run([ff, "-v", "error", "-i", loop, "-frames:v", "1", "-vf", vf, "-f", "rawvideo", "-"],
                             capture_output=True).stdout
        src = np.frombuffer(raw, np.uint8).reshape(SRC_H // 3, SRC_W // 3, 3).astype(np.float32)
    sh, sw3 = src.shape[:2]
    pw, ph = 270, 480
    n = int(seconds * fps)
    cuts = [seconds * 0.35, seconds * 0.7]
    for name, (label, _) in MOTIONS.items():
        out = os.path.join(out_dir, name + ".mp4")
        cam = Camera(cuts, seconds, glide=0.8, motion=name)
        p = subprocess.Popen([ff, "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{pw}x{ph}",
                              "-r", str(fps), "-i", "-", "-c:v", "libx264", "-preset", "veryfast", "-crf", "26",
                              "-pix_fmt", "yuv420p", "-movflags", "+faststart", out], stdin=subprocess.PIPE)
        for i in range(n):
            z, cx, cy = cam.at(i / fps)
            vw = sw3 / z; vh = vw * ph / pw
            x0 = float(np.clip(cx * sw3 - vw / 2, 0, sw3 - vw)); y0 = float(np.clip(cy * sh - vh / 2, 0, sh - vh))
            X = np.clip(x0 + (np.arange(pw) + 0.5) * vw / pw, 0, sw3 - 1.001)
            Y = np.clip(y0 + (np.arange(ph) + 0.5) * vh / ph, 0, sh - 1.001)
            xi, yi = X.astype(int), Y.astype(int)
            fx, fy = (X - xi)[None, :, None], (Y - yi)[:, None, None]
            r0 = src[yi][:, xi] * (1 - fx) + src[yi][:, xi + 1] * fx
            r1 = src[yi + 1][:, xi] * (1 - fx) + src[yi + 1][:, xi + 1] * fx
            p.stdin.write((r0 * (1 - fy) + r1 * fy).astype(np.uint8).tobytes())
        p.stdin.close(); p.wait()
    json.dump({k: v[0] for k, v in MOTIONS.items()}, open(os.path.join(out_dir, "motions.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print(f"      {len(MOTIONS)} motion previews -> {out_dir}", flush=True)


if __name__ == "__main__" and len(sys.argv) > 1 and sys.argv[1] == "--previews":
    motion_previews(sys.argv[2], sys.argv[3])
    sys.exit(0)

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("work"); ap.add_argument("video"); ap.add_argument("count", nargs="?", type=int, default=3)
    ap.add_argument("--audio"); ap.add_argument("--loop"); ap.add_argument("--label"); ap.add_argument("--fps", type=int, default=24)
    a = ap.parse_args()
    info = None
    if a.audio and a.loop:
        sys.path.insert(0, ENGINE)
        import sleep_pipeline as spl
        info = dict(audio=a.audio, loop=a.loop, fps=a.fps, label=spl.episode_label(a.label or ""), title=None)
    make(a.work, a.video, a.count, info=info)
