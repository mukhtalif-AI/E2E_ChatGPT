"""elements_prep.py - turn downloaded element clips (smoke / steam on black) into ready, seamless, grey loops.

  D:\\Audio asset\\Elements\\smoke\\<name>.mp4  ->  D:\\Audio asset\\Elements\\ready\\<name>.mp4 + .json
  ready clip = brightness only (thickness of the smoke), cropped to the smoke, source point found (where it starts),
  seamless (author loops are kept; others are cross-faded into a loop), <= 720 px high, 30 fps.
The .json keeps the link to the register entry (ELEMENTS_INDEX.json) so every use can be traced to its source.

  python elements_prep.py                      (all clips not prepared yet)
  python elements_prep.py --lib "D:\\Audio asset\\Elements" --force
Needs numpy + ffmpeg.
"""
import argparse, json, os, re, shutil, subprocess, sys
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import paths

LIB = paths.HUB.get("elements_dir", "")
# how a clip may be used (by its Pixabay id); anything not listed is only offered as "smoke"
USE = {"338582": ["steam", "smoke"], "338584": ["steam", "smoke"], "338585": ["steam", "smoke"],
       "335197": ["incense", "smoke"], "372422": ["incense", "smoke"], "376850": ["incense", "smoke"],
       "66481": ["overlay"]}
KIND_USE = {"steam": ["steam", "smoke"], "smoke": ["smoke"], "incense": ["incense", "smoke"], "candle": ["candle"],
            "rain": ["rain"], "fireflies": ["fireflies"], "snow": ["snow"], "fog": ["fog"], "fire": ["fire"]}


def find_ffmpeg():
    for c in (os.environ.get("FFMPEG"), paths.ffmpeg(), shutil.which("ffmpeg")):
        if c and os.path.exists(c):
            return c
    sys.exit("ffmpeg not found")


def probe(ff, path):
    r = subprocess.run([ff, "-hide_banner", "-i", path], capture_output=True, text=True, errors="replace").stderr
    m = re.search(r"Video:.*?(\d{2,5})x(\d{2,5}).*?([\d.]+) fps", r)
    return int(m.group(1)), int(m.group(2)), float(m.group(3))


def read_grey(path, max_h=720, fps_out=30):
    """Frames as brightness 0..1 (brightest channel, so blue-ish smoke counts fully)."""
    ff = find_ffmpeg()
    w, h, fps = probe(ff, path)
    sc = min(1.0, max_h / h)
    W, H = int(w * sc) // 2 * 2, int(h * sc) // 2 * 2
    fo = min(fps, fps_out)
    raw = subprocess.run([ff, "-v", "error", "-i", path, "-vf", f"fps={fo},scale={W}:{H}", "-f", "rawvideo",
                          "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout
    a = np.frombuffer(raw, np.uint8).reshape(-1, H, W, 3)
    return a.max(-1).astype(np.float32) / 255, fo


def prepare(src, out_dir, item_key=None, log=print, kind=None):
    """-> ready path, or None when the clip is not an element on black (it is then left out of ready\\)"""
    name = os.path.splitext(os.path.basename(src))[0]
    F, fps = read_grey(src)
    N, H, W = F.shape
    dark = float((np.median(F[::max(1, N // 15)], axis=0) < 0.1).mean())
    if dark < 0.45:
        log(f"  not on a black background ({dark:.0%} dark) - not used: {name}")
        return None
    black = np.percentile(F[::max(1, N // 20)], 5)
    F = np.clip((F - black) / max(1e-3, 1 - black), 0, 1)
    # seamless? jump from last to first frame vs a normal frame-to-frame step
    step = np.mean([np.abs(F[i + 1] - F[i]).mean() for i in range(0, N - 1, max(1, N // 30))])
    jump = np.abs(F[0] - F[-1]).mean()
    if jump <= step * 2.5:
        loop = F; how = "author's loop"
    else:
        L = N // 2
        w = (np.arange(L, dtype=np.float32) / L)[:, None, None]
        loop = F[:L] * w + F[L:2 * L] * (1 - w); how = "cross-faded into a loop"
    mean = loop.mean(0); mx = loop.max(0)
    rows = mean.mean(1)
    on = np.where(rows > rows.max() * 0.12)[0]
    y_src = int(on.max()) if len(on) else H - 1                 # lowest row with smoke = where it starts
    band = mean[max(0, y_src - H // 12):y_src + 1].mean(0)
    xs = np.arange(W)
    x_src = float((band * xs).sum() / max(1e-6, band.sum()))
    base_w = float(np.sqrt(((xs - x_src) ** 2 * band).sum() / max(1e-6, band.sum())) * 2.5)
    cols = np.where(mx.max(0) > 0.06)[0]
    x0, x1 = (int(cols.min()), int(cols.max()) + 1) if len(cols) else (0, W)
    y1 = min(H, y_src + 8)
    rws = np.where(mx.max(1) > 0.06)[0]
    y0 = int(rws.min()) if len(rws) else 0
    x0 -= x0 % 2; y0 -= y0 % 2
    if (x1 - x0) % 2: x1 = min(W, x1 + 1) if x1 < W else x1 - 1
    if (y1 - y0) % 2: y1 = min(H, y1 + 1) if y1 < H else y1 - 1
    crop = loop[:, y0:y1, x0:x1]
    hi = np.percentile(crop[::max(1, len(crop) // 20)], 99.7)
    crop = np.clip(crop / max(1e-3, hi), 0, 1)
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, name + ".mp4")
    hh, ww = crop.shape[1], crop.shape[2]
    pr = subprocess.Popen([find_ffmpeg(), "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "gray", "-s", f"{ww}x{hh}",
                           "-r", str(fps), "-i", "-", "-c:v", "libx264", "-crf", "15", "-preset", "slow",
                           "-pix_fmt", "yuv420p", out], stdin=subprocess.PIPE)
    for f in crop:
        pr.stdin.write((f * 255 + 0.5).astype(np.uint8).tobytes())
    pr.stdin.close(); pr.wait()
    pid = name.rsplit("-", 1)[-1]
    meta = {"source_file": os.path.relpath(src, os.path.dirname(out_dir)).replace("\\", "/"), "register_key": item_key,
            "use": USE.get(pid) or KIND_USE.get(kind or "", [kind or "smoke"]), "fps": round(fps, 3), "frames": int(len(crop)),
            "seconds": round(len(crop) / fps, 3), "loop": how, "size": [int(crop.shape[2]), int(crop.shape[1])],
            "anchor": [round((x_src - x0) / crop.shape[2], 4), round((y_src - y0) / crop.shape[1], 4)],
            "base_width": round(base_w / crop.shape[2], 4)}
    json.dump(meta, open(out[:-4] + ".json", "w", encoding="utf-8"), indent=1)
    log(f"  ready: {name}  {meta['seconds']}s {how}, {meta['size'][0]}x{meta['size'][1]}, use {'/'.join(meta['use'])}")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lib", default=LIB)
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    ready = os.path.join(a.lib, "ready")
    ix = {}
    p = os.path.join(a.lib, "ELEMENTS_INDEX.json")
    if os.path.exists(p):
        ix = json.load(open(p, encoding="utf-8")).get("items", {})
    changed = False
    for key, it in sorted(ix.items()):
        if it.get("ready") in ("no - not on black",) and not a.force:
            continue
        src = os.path.join(a.lib, *key.split("/"))
        out = os.path.join(ready, os.path.splitext(os.path.basename(key))[0] + ".mp4")
        if not os.path.exists(src) or (os.path.exists(out) and not a.force):
            continue
        r = prepare(src, ready, key, kind=it.get("kind"))
        it["ready"] = "yes" if r else "no - not on black"
        changed = True
    if changed and os.path.exists(p):                             # note the result in the register
        full = json.load(open(p, encoding="utf-8"))
        for key, it in ix.items():
            if key in full.get("items", {}) and "ready" in it:
                full["items"][key]["ready"] = it["ready"]
        json.dump(full, open(p + ".tmp", "w", encoding="utf-8"), indent=1, ensure_ascii=False)
        os.replace(p + ".tmp", p)


if __name__ == "__main__":
    main()
