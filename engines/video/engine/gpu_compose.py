"""
gpu_compose.py - the same final render as compose.py, but every frame is drawn on the GPU (PyTorch/CUDA)
and encoded by NVENC through ffmpeg.exe, with the audio muxed in the same pass.

Run by compose.compose() in ComfyUI's Python (torch + CUDA already there):
  python -s gpu_compose.py <state.pkl> <bands.npz> <out.mp4>
Falls back (exit code != 0) -> compose.py renders on the CPU instead.
"""
import sys as _sys
for _s in (_sys.stdout, _sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
import json, os, pickle, queue, subprocess, sys, threading, time
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import warnings
warnings.filterwarnings("ignore")
import torch
import compose
import overlay as ov
import kinetic as kn

W, H = 1920, 1080
dev = torch.device("cuda")
DT = torch.float16


def T(a):
    """numpy -> GPU tensor (float16)."""
    return torch.from_numpy(np.ascontiguousarray(a, dtype=np.float32)).to(dev, DT)


def C(col):
    return torch.tensor(np.asarray(col, np.float32), device=dev, dtype=DT)


# ------------------------------------------------------------------ IO: decode loop / encode + mux
def reader(loop, skip, n, q):
    """Decode the art loop (forever, from frame `skip`) on a background thread as raw yuv420p
    (half the bytes of RGB, and no CPU colour conversion - that happens on the GPU)."""
    ff = compose._ff()
    fps = 24
    p = subprocess.Popen([ff, "-v", "error", "-threads", "0", "-stream_loop", "-1", "-ss", f"{skip / fps:.4f}",
                          "-i", loop, "-frames:v", str(n), "-f", "rawvideo", "-pix_fmt", "yuv420p", "-"],
                         stdout=subprocess.PIPE, bufsize=W * H * 2 * 4)
    sz = W * H * 3 // 2
    for _ in range(n):
        b = p.stdout.read(sz)
        if len(b) < sz:
            break
        q.put(np.frombuffer(b, np.uint8))
    q.put(None)
    p.stdout.close(); p.wait()


def from_yuv420(buf):
    """yuv420p bytes -> float RGB on the GPU (BT.601 limited range, matching ffmpeg's default)."""
    x = torch.from_numpy(buf).to(dev, non_blocking=True).float()
    y = x[:W * H].view(H, W)
    u = x[W * H:W * H * 5 // 4].view(1, 1, H // 2, W // 2)
    v = x[W * H * 5 // 4:].view(1, 1, H // 2, W // 2)
    u = torch.nn.functional.interpolate(u, size=(H, W), mode="bilinear", align_corners=False)[0, 0] - 128
    v = torch.nn.functional.interpolate(v, size=(H, W), mode="bilinear", align_corners=False)[0, 0] - 128
    y = (y - 16) * (255 / 219)
    r = y + 1.596 * v * (255 / 255)
    g = y - 0.392 * u - 0.813 * v
    b = y + 2.017 * u
    return (torch.stack([r, g, b], -1) * (1 / 255.0)).clamp(0, 1).to(DT)


def writer(enc, wq):
    while True:
        d = wq.get()
        if d is None:
            break
        enc.stdin.write(d)


def encoder(out, st, fps, video_only=False):
    ff = compose._ff()
    if video_only:                                   # one chunk of the video; audio is added when joining
        venc = ["-c:v", "h264_nvenc", "-preset", "p6", "-rc", "vbr", "-cq", "21", "-b:v", "0", "-maxrate", "10M",
                "-bufsize", "20M", "-spatial-aq", "1", "-g", str(fps * 5)]
        return subprocess.Popen([ff, "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "yuv420p", "-s", f"{W}x{H}",
                                 "-r", str(fps), "-i", "-", *venc, "-f", "mp4", out],
                                stdin=subprocess.PIPE, bufsize=W * H * 2)
    total, start, audio = st["total"], st["start"], st["audio"]
    af = f"afade=t=in:d=6,afade=t=out:st={max(0, total - 8):.2f}:d=8"
    venc = ["-c:v", "h264_nvenc", "-preset", "p6", "-rc", "vbr", "-cq", "21", "-b:v", "0", "-maxrate", "10M",
            "-bufsize", "20M", "-spatial-aq", "1", "-g", str(fps * 2)]
    cmd = [ff, "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "yuv420p", "-s", f"{W}x{H}", "-r", str(fps),
           "-i", "-", "-ss", str(start), "-t", f"{total:.3f}", "-i", audio, "-map", "0:v", "-map", "1:a",
           *venc, "-af", af, "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart", out]
    return subprocess.Popen(cmd, stdin=subprocess.PIPE, bufsize=W * H * 2)


def to_yuv420(img):
    """float RGB (0..1) on GPU -> yuv420p bytes (BT.601 limited range, like ffmpeg's default)."""
    x = img.clamp(0, 1).float()
    r, g, b = x[..., 0], x[..., 1], x[..., 2]
    y = 16 + 65.481 * r + 128.553 * g + 24.966 * b
    u = 128 - 37.797 * r - 74.203 * g + 112.0 * b
    v = 128 + 112.0 * r - 93.786 * g - 18.214 * b
    u = u.view(H // 2, 2, W // 2, 2).mean((1, 3))
    v = v.view(H // 2, 2, W // 2, 2).mean((1, 3))
    yuv = torch.cat([y.reshape(-1), u.reshape(-1), v.reshape(-1)]).round().clamp(0, 255).to(torch.uint8)
    return yuv.cpu().numpy().tobytes()


# ------------------------------------------------------------------ GPU versions of the overlays
class GKinetic:
    def __init__(self, kt):
        self.kt, self.cache = kt, {}

    def _t(self, it):
        k = id(it)
        if k not in self.cache:
            self.cache[k] = (T(it["m"]), T(it["soft"]), T(it["glow"]))
        return self.cache[k]

    @staticmethod
    def _region(img, m, x, y):
        h, w = m.shape
        y0, x0, y1, x1 = max(0, y), max(0, x), min(H, y + h), min(W, x + w)
        if y1 <= y0 or x1 <= x0:
            return None, None
        return img[y0:y1, x0:x1], m[y0 - y:y1 - y, x0 - x:x1 - x]

    def draw(self, img, t):
        for op in self.kt.frame_ops(t):
            if op[0] == "rule":
                _, rx, y0, y1, out = op
                y0, y1 = max(0, y0), max(0, y1)
                if y1 > y0:
                    img[y0:y1, rx:rx + 2] = img[y0:y1, rx:rx + 2] * 0.4 + C(kn.LAV) * (0.6 * out)
            elif op[0] == "glow":
                _, it, x, y, col, a = op
                reg, m = self._region(img, self._t(it)[2], x, y)
                if reg is not None:
                    reg += C(col) * (m[..., None] * a)
            else:
                _, it, p, focus, x, y, col, a = op
                mt, soft, _ = self._t(it)
                m = mt * p + soft * (1 - p) if focus else mt
                reg, m = self._region(img, m, x, y)
                if reg is not None:
                    mm = m[..., None] * a
                    reg.mul_(1 - mm).add_(C(col) * mm)


class GLightLine:
    def __init__(self, ll):
        self.ll = ll
        self.yy = T(ll.yy)                                           # [h,1]
        self.env = T(ll.env)
        self.lav = C(ov.LAV * 0.9 * 0.9)
        self.txt = C(ov.TXT)

    def draw(self, img, bands, t, level):
        ll = self.ll
        amp = np.interp(np.abs(ll.x) * (len(bands) - 1), np.arange(len(bands)), bands)
        wave = np.sin(ll.x * 5 * np.pi + t * 1.6) * 0.65 + np.sin(ll.x * 11 * np.pi - t * 2.3) * 0.35
        amp = np.convolve(amp, np.ones(61) / 61, mode="same")
        off = wave * (0.12 + amp * 0.9) * ll.env * (ll.h * 0.30) + np.sin(ll.x * 3 * np.pi + t * 0.7) * 3 * ll.env
        d = (self.yy - T(off)[None, :]).abs().float()
        core = torch.exp(-(d / 1.3) ** 2)
        glow = torch.exp(-(d / 7.0) ** 2) * 0.55 + torch.exp(-(d / 24.0) ** 2) * 0.16
        env = self.env.float()[None, :]
        a = ((core * 0.85 + glow) * (env * (0.45 + 0.55 * level))).to(DT)
        c2 = (core * env * 0.25 * level).to(DT)
        y0 = ll.y - ll.h // 2
        reg = img[y0:y0 + ll.h, ll.x0:ll.x0 + ll.w]
        reg += self.lav * a[..., None] + self.txt * c2[..., None]


class GCaptionHi:
    """GPU twin of overlay.CaptionHi (used by title + goodnight cards)."""

    def __init__(self, c):
        self.c = c
        self.mask, self.soft, self.glow = T(c.mask), T(c.soft), T(c.glow)
        self.x = torch.arange(W, device=dev, dtype=torch.float32)
        self.lav, self.txt = C(ov.LAV), C(getattr(c, "col", ov.TXT))

    def draw(self, img, t):
        c = self.c
        if t < c.start or t > c.end:
            return
        lt, rem = t - c.start, c.end - t
        col = None
        dy = 0
        if lt < c.reveal:
            p = lt / c.reveal
            p = p * p * (3 - 2 * p)
            front = c.xl - c.feather + p * (c.xr - c.xl + 2 * c.feather)
            col = ((front - self.x) / c.feather).clamp(0, 1)
            col = (col * col * (3 - 2 * col)).to(DT)[None, :]
            dy = int(round((1 - p) * 8))
        out_k = 1.0
        if rem < c.outro:
            q = rem / c.outro
            out_k = q * q * (3 - 2 * q)
            dy = -int(round((1 - out_k) * 10))
        breathe = 0.85 + 0.15 * np.sin((t - c.start) * 2 * np.pi / 10.0)
        focus = (col if col is not None else 1.0) * out_k if (col is not None or out_k < 1) else None
        m = self.mask * focus + self.soft * (1 - focus) if focus is not None else self.mask
        alpha_col = col if col is not None else 1.0
        a_txt = m * alpha_col * (out_k * 0.94)
        a_glow = self.glow * alpha_col * (0.45 * breathe * out_k + 0.25 * (1 - out_k))
        y0 = c.y0 + dy
        y1 = min(H, y0 + c.h)
        reg = img[max(0, y0):y1]
        a_txt, a_glow = a_txt[max(0, -y0):max(0, -y0) + reg.shape[0]], a_glow[max(0, -y0):max(0, -y0) + reg.shape[0]]
        reg += self.lav * (a_glow * 0.65)[..., None]
        reg.mul_(1 - a_txt[..., None]).add_(self.txt * a_txt[..., None])


class GBrand:
    """GPU twin of overlay.Brand (same layers, glow and light-sweep)."""

    def __init__(self, br, alpha=0.92):
        self.br, self.alpha = br, alpha
        self.parts = []
        for m, y, col, g, gcol, gamt, shines in br.layers():
            self.parts.append((y, T(m), C(col), T(g * gamt) if g is not None else None,
                               C(gcol) if gcol is not None else None, shines))
        self.extras = [GSprite(sp) for sp in getattr(br, "extras", [])]

    def draw(self, img, t=None, lvs=0.0):
        k = (self.br.k(t) if hasattr(self.br, "k") else 1.0) * self.alpha
        if k > 0:
            sw = self.br.sweep(t) if hasattr(self.br, "sweep") else None
            swt = T(sw) if sw is not None else None
            x0 = self.br.x0
            for y, m, col, g, gcol, shines in self.parts:
                reg = img[y:y + m.shape[0], x0:x0 + m.shape[1]]
                if g is not None:
                    reg += gcol * (g * k)[..., None]
                mk = m * k
                reg.mul_(1 - mk[..., None]).add_(col * mk[..., None])
                if shines and swt is not None:
                    reg += (mk * swt[None, :] * 0.55)[..., None]
        for gs in self.extras:
            gs.draw(img, t, lvs)


class GSprite:
    """GPU twin of overlay.Sprite."""

    def __init__(self, sp):
        self.sp = sp
        self.m = T(sp.mask)
        self.g = T(sp.glow) if sp.glow is not None else None
        self.col, self.lav = C(sp.color), C(ov.LAV)

    def draw(self, img, t, lv=0.0):
        sp = self.sp
        a = sp.k(t, lv) if t is not None else 0.0
        if a <= 0:
            return
        reg = img[sp.y:sp.y + sp.h, sp.x:sp.x + sp.w]
        if self.g is not None:
            reg += self.lav * (self.g * sp.glow_k(t, lv))[..., None]
        m = (self.m * a)[..., None]
        reg.mul_(1 - m).add_(self.col * m)


# ------------------------------------------------------------------ main loop
def main():
    st = pickle.load(open(sys.argv[1], "rb"))
    d = np.load(sys.argv[2])
    bands, level = d["bands"], d["level"]
    out = sys.argv[3]
    fps, total = st["fps"], st["total"]
    caps, br, cards, eq, halo = compose._build(st["audio"], st["captions"], fps, st["start"], total, st["viz"],
                                               st["brand"], st["moon_xy"], st["lang"], st["title"],
                                               st["goodnight"], st.get("kinetic"))
    gk = GKinetic(caps[0]) if caps and hasattr(caps[0], "frame_ops") else None
    gcaps = [GCaptionHi(c) for c in caps if isinstance(c, ov.CaptionHi)]
    geq = GLightLine(eq) if isinstance(eq, ov.LightLine) else None
    gcards = [(cd, GCaptionHi(cd.cap), GCaptionHi(cd.sub) if cd.sub else None) for cd in cards]
    gbr = GBrand(br) if br is not None else None
    ghalo = None
    if halo is not None:
        hm, hb = halo
        ghalo = (T(hm), hb)
    n_all = int(total * fps)

    def render_range(i0, i1, out, video_only):
        n = i1 - i0
        skip = (int(st["start"] * fps) + i0) % max(1, st["loop_len"]) if st["loop_len"] else 0
        q = queue.Queue(maxsize=16)
        threading.Thread(target=reader, args=(st["loop"], skip, n, q), daemon=True).start()
        enc = encoder(out, st, fps, video_only)
        wq = queue.Queue(maxsize=8)
        wt = threading.Thread(target=writer, args=(enc, wq), daemon=True)
        wt.start()
        t0 = time.time()
        for i in range(n):
            fr = q.get()
            if fr is None:
                break
            fi = i0 + i                                   # frame number in the whole video
            t = fi / fps
            img = from_yuv420(fr)
            lv = float(level[min(fi, len(level) - 1)])
            if ghalo is not None and lv > 0.02:
                hm, (hy0, hy1, hx0, hx1) = ghalo
                img[hy0:hy1, hx0:hx1] += hm * (0.05 * lv)
            if gk is not None:
                gk.draw(img, t)
            if geq is not None:
                geq.draw(img, bands[min(fi, len(bands) - 1)], t, lv)
            for gc in gcaps:
                gc.draw(img, t)
            for cd, gc, gs in gcards:
                if cd.start <= t <= cd.end:
                    gc.draw(img, t)
                    if gs is not None:
                        gs.draw(img, t)
            if gbr is not None:
                gbr.draw(img, t, float(level[max(0, min(fi, len(level) - 1) - 36):min(fi, len(level) - 1) + 1].mean()))
            g = min(1.0, t / 4.0, (total - t) / 6.0)
            dl, da = st["dim_last"], st["dim_amount"]
            if dl and t > total * (1 - dl):
                g *= 1 - da * min(1.0, (t - total * (1 - dl)) / (total * dl))
            if g < 1:
                img = img * max(g, 0.0)
            wq.put(to_yuv420(img))
            if i and i % (fps * 60) == 0 and not video_only:
                el = time.time() - t0
                print(f"      {100 * i // n:3d}%  ({i / el:.0f} frames/s, ~{(n - i) / (i / el) / 60:.1f} min left)", flush=True)
        wq.put(None); wt.join()
        enc.stdin.close()
        if enc.wait():
            sys.exit("encoder failed")

    if sys.argv[3].lower().endswith(".json"):            # list of pending chunks [[i0, i1, path], ...]
        jobs = json.load(open(sys.argv[3]))
        t_all = time.time()
        for k, (i0, i1, path) in enumerate(jobs):
            part = path + ".part.mp4"
            render_range(i0, i1, part, True)
            os.replace(part, path)                         # a finished chunk is never half-written
            el = time.time() - t_all
            print(f"      {jobs_done_pct(k, jobs, n_all):3d}%  video chunk done  "
                  f"(~{el / (k + 1) * (len(jobs) - k - 1) / 60:.1f} min left)", flush=True)
    else:
        render_range(0, n_all, sys.argv[3], False)


def jobs_done_pct(k, jobs, n_all):
    return int(100 * jobs[k][1] / max(1, n_all))


if __name__ == "__main__":
    main()
