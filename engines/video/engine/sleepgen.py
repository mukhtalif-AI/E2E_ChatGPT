"""
sleepgen.py - seamless, slow, hypnotic loops for sleep videos.

Every motion is periodic over the loop length, so the last frame flows
into the first with no visible jump. Frames are computed with numpy and
piped straight into ffmpeg (no temp images).

Usage:
  python sleepgen.py --style spiral --palette ember --seconds 60 --out loops/spiral_ember.mp4
  python sleepgen.py --list
"""
import argparse, os, shutil, subprocess, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np

TAU = 2 * np.pi

PALETTES = {
    # (shadow, mid, highlight) in 0-1 RGB. Kept dark and low-contrast on purpose.
    "midnight": ((0.01, 0.01, 0.04), (0.10, 0.07, 0.28), (0.38, 0.30, 0.70)),
    "ember":    ((0.02, 0.005, 0.0), (0.30, 0.09, 0.02), (0.85, 0.42, 0.12)),  # warm = least blue light
    "aurora":   ((0.00, 0.02, 0.03), (0.03, 0.20, 0.22), (0.25, 0.65, 0.55)),
    "rose":     ((0.03, 0.00, 0.02), (0.25, 0.05, 0.18), (0.75, 0.35, 0.55)),
    "mono":     ((0.00, 0.00, 0.00), (0.12, 0.12, 0.14), (0.55, 0.55, 0.60)),
}


def colorize(v, palette, brightness):
    """v in [0,1] -> RGB uint8 through a 3-stop gradient."""
    lo, mid, hi = (np.array(c, dtype=np.float32) for c in PALETTES[palette])
    v = np.clip(v, 0, 1)[..., None]
    a = np.clip(v * 2, 0, 1)
    b = np.clip(v * 2 - 1, 0, 1)
    rgb = lo + (mid - lo) * a + (hi - mid) * b
    return (np.clip(rgb * brightness, 0, 1) * 255).astype(np.uint8)


class Field:
    def __init__(self, w, h):
        self.w, self.h = w, h
        y, x = np.mgrid[0:h, 0:w].astype(np.float32)
        s = min(w, h)
        self.x = (x - w / 2) / s
        self.y = (y - h / 2) / s
        self.r = np.sqrt(self.x ** 2 + self.y ** 2) + 1e-4
        self.th = np.arctan2(self.y, self.x)
        self.vignette = np.clip(1.15 - self.r * 1.1, 0, 1) ** 1.5


# ---- styles: f(field, phase) -> value field in [0,1]; phase goes 0 -> 1 over the loop
def style_spiral(F, p, turns=1):
    arms = 3
    v = np.sin(arms * F.th + 9.0 * np.log(F.r) - TAU * p * 2)       # slow inward drift
    v2 = np.sin(-2 * F.th + 6.0 * np.log(F.r) + TAU * p)            # counter layer, softer
    v = 0.5 + 0.35 * v + 0.15 * v2
    return v * F.vignette


def style_tunnel(F, p, turns=1):
    rings = np.sin(14.0 * np.log(F.r) + TAU * p * 3)
    twist = np.sin(4 * F.th + 3.0 * np.log(F.r) - TAU * p)
    v = 0.5 + 0.4 * rings * (0.6 + 0.4 * twist)
    return v * F.vignette


def style_ripple(F, p, turns=1):
    v = np.sin(22 * F.r - TAU * p * 4) * np.exp(-F.r * 1.6)
    v += 0.5 * np.sin(14 * np.hypot(F.x - 0.25 * np.cos(TAU * p), F.y - 0.18 * np.sin(TAU * p)) - TAU * p * 3)
    return (0.45 + 0.3 * v) * F.vignette


def style_nebula(F, p, turns=1):
    x, y = F.x * 3, F.y * 3
    c, s = np.cos(TAU * p), np.sin(TAU * p)
    # domain warp with integer time frequencies -> seamless
    wx = x + 0.8 * np.sin(1.7 * y + 1.3 * c) + 0.4 * np.sin(2.9 * x - 1.1 * s)
    wy = y + 0.8 * np.cos(1.5 * x + 1.2 * s) + 0.4 * np.cos(3.1 * y + 0.9 * c)
    v = np.sin(wx + 0.7 * np.sin(wy * 1.3 + TAU * p)) * np.cos(wy * 0.9 - 0.6 * np.cos(wx + TAU * p * 2))
    v = 0.5 + 0.45 * v
    return v * (0.35 + 0.65 * F.vignette)


def style_orbs(F, p, turns=1):
    v = np.zeros_like(F.r)
    for i, (ax, ay, fx, fy, rad) in enumerate([(0.45, 0.25, 1, 2, 0.20), (0.35, 0.30, 2, 1, 0.16),
                                               (0.25, 0.20, 1, 3, 0.24), (0.50, 0.15, 3, 2, 0.13)]):
        cx = ax * np.sin(TAU * p * fx + i)
        cy = ay * np.cos(TAU * p * fy + 2 * i)
        d2 = (F.x - cx) ** 2 + (F.y - cy) ** 2
        v += np.exp(-d2 / (rad ** 2))
    return np.clip(v * 0.55, 0, 1)


STYLES = {"spiral": style_spiral, "tunnel": style_tunnel, "ripple": style_ripple,
          "nebula": style_nebula, "orbs": style_orbs}


def find_ffmpeg(explicit=None):
    for c in [explicit, os.environ.get("FFMPEG"), shutil.which("ffmpeg"),
              __import__("paths").ffmpeg()]:
        if c and (os.path.exists(c) or shutil.which(c)):
            return c
    return None


class _FFmpegPipe:
    def __init__(self, ffmpeg, out, w, h, fps, crf, encoder):
        venc = (["-c:v", "h264_nvenc", "-preset", "p6", "-cq", str(crf), "-b:v", "0"]
                if encoder == "nvenc" else ["-c:v", "libx264", "-preset", "slow", "-crf", str(crf)])
        cmd = [ffmpeg, "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
               "-s", f"{w}x{h}", "-r", str(fps), "-i", "-", *venc,
               "-pix_fmt", "yuv420p", "-g", str(fps * 2), "-movflags", "+faststart", out]
        self.p = subprocess.Popen(cmd, stdin=subprocess.PIPE)
        self.codec = "ffmpeg:" + encoder

    def write(self, rgb):
        self.p.stdin.write(rgb.tobytes())

    def close(self):
        self.p.stdin.close(); self.p.wait()
        if self.p.returncode:
            sys.exit("ffmpeg failed")


def make_writer(out, w, h, fps, crf=20, encoder="auto", ffmpeg=None, backend="auto"):
    """PyAV (bundled FFmpeg libs) if available, else an ffmpeg.exe pipe."""
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    if backend in ("auto", "av"):
        try:
            from sv_av import VideoWriter
            prefer = {"nvenc": ("h264_nvenc",), "x264": ("libx264",)}.get(encoder, ("h264_nvenc", "libx264"))
            from sv_av import pick_video_codec
            return VideoWriter(out, w, h, fps, codec=pick_video_codec(w, h, fps, prefer))
        except ImportError:
            if backend == "av":
                raise
    ff = find_ffmpeg(ffmpeg)
    if not ff:
        sys.exit("Neither PyAV nor ffmpeg.exe is available")
    return _FFmpegPipe(ff, out, w, h, fps, crf, "nvenc" if encoder == "nvenc" else "x264")


def render(style, palette, seconds, fps, w, h, out, ffmpeg=None, crf=20, breathe=6, encoder="auto", backend="auto"):
    F = Field(w, h)
    n = int(round(seconds * fps))
    wr = make_writer(out, w, h, fps, crf, encoder, ffmpeg, backend)
    # breathing: whole-image brightness pulse; cycles per loop must be an integer
    cycles = max(1, round(seconds * breathe / 60)) if breathe > 0 else 0
    t0 = time.time()
    print(f"  encoder: {wr.codec}")
    for i in range(n):
        p = i / n
        v = STYLES[style](F, p)
        br = 1.0 if cycles == 0 else 0.82 + 0.18 * (0.5 - 0.5 * np.cos(TAU * p * cycles))
        wr.write(colorize(v, palette, br))
        if i % max(1, n // 10) == 0:
            print(f"  {style}/{palette}: {i}/{n} frames  ({time.time() - t0:.0f}s)", flush=True)
    wr.close()
    print(f"done -> {out}  ({time.time() - t0:.0f}s)")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--style", default="spiral", choices=STYLES)
    ap.add_argument("--palette", default="midnight", choices=PALETTES)
    ap.add_argument("--seconds", type=float, default=60, help="loop length")
    ap.add_argument("--fps", type=int, default=24)
    ap.add_argument("--size", default="1920x1080")
    ap.add_argument("--crf", type=int, default=20)
    ap.add_argument("--breathe", type=float, default=6, help="breaths/minute pulse, 0 = off")
    ap.add_argument("--encoder", default="auto", choices=["auto", "x264", "nvenc"])
    ap.add_argument("--backend", default="auto", choices=["auto", "av", "ffmpeg"])
    ap.add_argument("--ffmpeg")
    ap.add_argument("--out")
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args()
    if a.list:
        print("styles:", ", ".join(STYLES)); print("palettes:", ", ".join(PALETTES)); return
    w, h = map(int, a.size.lower().split("x"))
    out = a.out or f"loops/{a.style}_{a.palette}_{w}x{h}.mp4"
    render(a.style, a.palette, a.seconds, a.fps, w, h, out, a.ffmpeg, a.crf, a.breathe, a.encoder, a.backend)


if __name__ == "__main__":
    main()
