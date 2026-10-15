"""smokesim.py - make the motion-element library: seamless smoke / steam loops on black, by a small fluid simulation.

Each element = <name>.mp4 (grey on black: brightness = how thick the smoke is) + <name>.json
(kind, loop seconds, anchor = where it attaches, as a fraction of the clip: steam starts at its bottom middle).
Loops are seamless: the simulation runs twice the loop length and the second half is cross-faded onto the first.

  python smokesim.py --out "D:\\MukhtalifHub\\elements"              (all presets, a few minutes each, CPU only)
  python smokesim.py --out ... --only steam_wisp --seconds 3        (quick test)
Needs only numpy (+ ffmpeg.exe or PyAV to write the video).
"""
import argparse, json, os, shutil, subprocess, sys, time
import numpy as np

PRESETS = {
    # name: kind, grid (w, h), source (x frac, width px), buoyancy, vorticity, dissipation, wobble, rate
    "steam_wisp":  dict(kind="steam", grid=(128, 288), src_w=5.0, buoy=1.1, vort=2.2, diss=0.988, wob=0.9, rate=0.55, jets=1),
    "steam_cup":   dict(kind="steam", grid=(144, 288), src_w=7.0, buoy=1.0, vort=2.8, diss=0.986, wob=1.2, rate=0.55, jets=2),
    "steam_curl":  dict(kind="steam", grid=(144, 288), src_w=6.0, buoy=0.8, vort=3.6, diss=0.987, wob=1.6, rate=0.5, jets=2),
    "incense":     dict(kind="smoke", grid=(128, 320), src_w=2.5, buoy=1.3, vort=1.6, diss=0.993, wob=0.6, rate=0.6, jets=1),
    "kettle":      dict(kind="steam", grid=(160, 288), src_w=9.0, buoy=1.6, vort=2.5, diss=0.982, wob=1.0, rate=0.8, jets=1),
}


# ------------------------------------------------------------------ fluid tools
def sample(f, X, Y):
    h, w = f.shape
    X = np.clip(X, 0, w - 1.001); Y = np.clip(Y, 0, h - 1.001)
    x0 = X.astype(np.int32); y0 = Y.astype(np.int32)
    fx = X - x0; fy = Y - y0
    return ((f[y0, x0] * (1 - fx) + f[y0, x0 + 1] * fx) * (1 - fy)
            + (f[y0 + 1, x0] * (1 - fx) + f[y0 + 1, x0 + 1] * fx) * fy)


class Fluid:
    def __init__(self, w, h, dt=1.0):
        self.w, self.h, self.dt = w, h, dt
        self.u = np.zeros((h, w), np.float32); self.v = np.zeros((h, w), np.float32)   # v > 0 = up
        self.d = np.zeros((h, w), np.float32); self.T = np.zeros((h, w), np.float32)
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
        self.X, self.Y = xx, yy
        self.p = np.zeros((h, w), np.float32)

    def advect(self, f, sharp=False):
        Xb = self.X - self.dt * self.u; Yb = self.Y + self.dt * self.v       # y grows downward, v up
        a = sample(f, Xb, Yb)
        if not sharp:
            return a
        # MacCormack: go back and forth, correct the error -> finer wisps
        Xf = self.X + self.dt * sample(self.u, Xb, Yb); Yf = self.Y - self.dt * sample(self.v, Xb, Yb)
        b = sample(a, Xf, Yf)
        r = a + 0.5 * (f - b)
        lo = np.minimum.reduce([f, np.roll(f, 1, 0), np.roll(f, -1, 0), np.roll(f, 1, 1), np.roll(f, -1, 1)])
        hi = np.maximum.reduce([f, np.roll(f, 1, 0), np.roll(f, -1, 0), np.roll(f, 1, 1), np.roll(f, -1, 1)])
        lo = sample(lo, Xb, Yb); hi = sample(hi, Xb, Yb)
        return np.clip(r, lo, hi)

    def project(self, iters=30):
        u, v = self.u, self.v
        div = np.zeros_like(u)
        div[1:-1, 1:-1] = 0.5 * ((u[1:-1, 2:] - u[1:-1, :-2]) + (v[:-2, 1:-1] - v[2:, 1:-1]))
        p = self.p
        for _ in range(iters):
            p[1:-1, 1:-1] = (p[1:-1, 2:] + p[1:-1, :-2] + p[2:, 1:-1] + p[:-2, 1:-1] - div[1:-1, 1:-1]) * 0.25
            p[:, 0] = p[:, 1]; p[:, -1] = p[:, -2]; p[-1] = p[-2]; p[0] = 0          # open top
        u[1:-1, 1:-1] -= 0.5 * (p[1:-1, 2:] - p[1:-1, :-2])
        v[1:-1, 1:-1] -= 0.5 * (p[:-2, 1:-1] - p[2:, 1:-1])
        u[:, 0] = u[:, -1] = 0; v[-1] = 0

    def vorticity(self, eps):
        u, v = self.u, self.v
        w = np.zeros_like(u)
        w[1:-1, 1:-1] = 0.5 * ((v[1:-1, 2:] - v[1:-1, :-2]) - (u[:-2, 1:-1] - u[2:, 1:-1]))
        aw = np.abs(w)
        gx = np.zeros_like(u); gy = np.zeros_like(u)
        gx[1:-1, 1:-1] = 0.5 * (aw[1:-1, 2:] - aw[1:-1, :-2])
        gy[1:-1, 1:-1] = 0.5 * (aw[:-2, 1:-1] - aw[2:, 1:-1])
        n = np.sqrt(gx * gx + gy * gy) + 1e-5
        gx /= n; gy /= n
        self.u += self.dt * eps * (gy * w)
        self.v += self.dt * eps * (-gx * w)


def simulate(P, frames, fps=24, seed=1, warm=None, log=print):
    rng = np.random.default_rng(seed)
    w, h = P["grid"]
    F = Fluid(w, h)
    sub = 2                                                    # simulation steps per video frame
    warm = warm if warm is not None else int(fps * 6)          # let the plume reach the top first
    # slow random wobble of the source (smooth noise)
    total = (frames + warm) * sub
    def smooth(n, k):
        r = rng.standard_normal(n + 4 * k)
        c = np.convolve(r, np.hanning(4 * k) / np.hanning(4 * k).sum(), "same")
        return (c[2 * k:2 * k + n] / (c.std() + 1e-6)).astype(np.float32)
    wob_x = smooth(total, 40) * P["wob"]
    wob_s = smooth(total, 25)
    def field(k):
        n = rng.standard_normal((h, w))
        fy = np.fft.fftfreq(h)[:, None]; fx = np.fft.rfftfreq(w)[None, :]
        f = np.fft.irfft2(np.fft.rfft2(n) * np.exp(-(fx ** 2 + fy ** 2) * k * k), s=(h, w))
        return (f / (f.std() + 1e-6)).astype(np.float32)
    turb = [field(14) for _ in range(4)]                       # slowly turning air in the room
    jet_off = [0.0] if P["jets"] == 1 else [-P["src_w"] * 0.6, P["src_w"] * 0.6]
    sx0, sy0 = w / 2, h - 10
    out = []
    t0 = time.time()
    for i in range(total):
        F.T *= 0.99
        for j, off in enumerate(jet_off):
            sx = sx0 + off + wob_x[i] * 1.5 * (1 if j == 0 else -0.7)
            g = np.exp(-((F.X - sx) / P["src_w"]) ** 2 - ((F.Y - sy0) / 3.0) ** 2)
            pulse = 0.75 + 0.25 * np.tanh(wob_s[i] * (1 if j == 0 else -1))
            F.d += g * P["rate"] * 0.3 * pulse
            F.T = np.maximum(F.T, g * pulse)
            F.u = F.u * (1 - g) + g * wob_x[i] * 0.25
            F.v = F.v * (1 - g) + g * 1.8 * pulse
        ph = i / (fps * sub) * 0.7
        rise = np.clip((h - F.Y) / h, 0, 1)                       # air is calmer near the cup
        F.u += 0.007 * P["wob"] * rise * (turb[0] * np.cos(ph) + turb[1] * np.sin(ph))
        F.v += 0.004 * P["wob"] * rise * (turb[2] * np.cos(ph * 1.3) + turb[3] * np.sin(ph * 1.3))
        F.v += F.dt * (P["buoy"] * 0.05 * F.T - 0.004 * F.d)
        F.vorticity(P["vort"] * 0.035)
        F.u = F.advect(F.u); F.v = F.advect(F.v)
        F.project()
        F.d = F.advect(F.d, sharp=True) * P["diss"]
        F.T = F.advect(F.T)
        np.clip(F.u * 0.995, -2.5, 2.5, out=F.u); np.clip(F.v * 0.995, -2.5, 2.5, out=F.v)
        if i % sub == sub - 1 and i // sub >= warm:
            out.append(F.d.copy())
        if i % (sub * fps * 2) == 0:
            log(f"    {i // sub}/{frames + warm} frames ({time.time() - t0:.0f}s)")
    return out


def to_loop(frames, L):
    """frames: 2L simulated -> L seamless: out[i] = A[i] * i/L + A[i+L] * (1 - i/L)."""
    A = frames
    return [A[i] * (i / L) + A[i + L] * (1 - i / L) for i in range(L)]


def render(name, P, out_dir, seconds=10, fps=24, scale=3, ffmpeg=None, log=print):
    L = int(seconds * fps)
    log(f"  {name}: simulating {2 * L} frames on a {P['grid'][0]}x{P['grid'][1]} grid ...")
    sim = simulate(P, 2 * L, fps, seed=sum(map(ord, name)), log=log)
    loop = to_loop(sim, L)
    w, h = P["grid"]
    fade = np.clip((h - 6 - np.arange(h)) / 22.0, 0, 1)[:, None].astype(np.float32)   # steam shows a bit above the rim
    loop = [f * fade for f in loop]
    hi = np.percentile(np.stack(loop[::12]), 99.5) + 1e-6       # thickest smoke -> near white
    w, h = P["grid"]
    W, H = w * scale, h * scale
    W -= W % 2; H -= H % 2
    from PIL import Image, ImageFilter
    path = os.path.join(out_dir, name + ".mp4")
    ff = ffmpeg or shutil.which("ffmpeg") or "ffmpeg"
    cmd = [ff, "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "gray", "-s", f"{W}x{H}", "-r", str(fps), "-i", "-",
           "-c:v", "libx264", "-crf", "14", "-preset", "slow", "-pix_fmt", "yuv420p", path]
    pr = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    for f in loop:
        a = np.clip(f / hi, 0, 1) ** 0.85
        im = Image.fromarray((a * 255).astype(np.uint8)).resize((W, H), Image.BICUBIC).filter(ImageFilter.GaussianBlur(0.8))
        pr.stdin.write(im.tobytes())
    pr.stdin.close(); pr.wait()
    meta = {"kind": P["kind"], "seconds": seconds, "fps": fps, "size": [W, H],
            "anchor": [0.5, (h - 8) / h],                     # the source point, as a fraction of the clip
            "source_width": P["src_w"] * scale * (1 if P["jets"] == 1 else 2.2) / W,
            "blend": "screen-grey", "made_by": "smokesim.py"}
    json.dump(meta, open(path[:-4] + ".json", "w"), indent=1)
    log(f"  {name}: done -> {path}")
    return path


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--only", default=None, help="comma list of presets")
    ap.add_argument("--seconds", type=float, default=10)
    ap.add_argument("--ffmpeg", default=None)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    names = a.only.split(",") if a.only else list(PRESETS)
    for n in names:
        if os.path.exists(os.path.join(a.out, n + ".mp4")) and not a.only:
            print(f"  {n}: exists, kept"); continue
        render(n, PRESETS[n], a.out, a.seconds, ffmpeg=a.ffmpeg)
