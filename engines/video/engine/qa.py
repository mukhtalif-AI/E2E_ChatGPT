"""
qa.py - automatic quality check of a finished video: duration/bitrate/streams + sample frames
(title card, every caption at full visibility, mid-video, goodnight outro) -> <video>_qa/ folder.
Usage: python -s qa.py output\\video.mp4 [work\\<name>\\captions.json]
"""
import sys as _sys
for _s in (_sys.stdout, _sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")   # Hindi text in logs/consoles on Windows
    except Exception:
        pass
import json, os, sys
import numpy as np


def main():
    run(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)


def run(vid, caps_path=None, quiet=False):
    import av
    caps = json.load(open(caps_path, encoding="utf-8")) if caps_path and os.path.exists(caps_path) else []
    outdir = os.path.splitext(vid)[0] + "_qa"
    os.makedirs(outdir, exist_ok=True)
    c = av.open(vid)
    v = c.streams.video[0]
    a = c.streams.audio[0] if c.streams.audio else None
    dur = float(c.duration / 1e6)
    rep = {"file": vid, "size_MB": round(os.path.getsize(vid) / 1e6, 1), "duration_s": round(dur, 2),
           "video": f"{v.codec_context.name} {v.width}x{v.height} {float(v.average_rate or 0):.2f}fps",
           "audio": f"{a.codec_context.name} {a.rate}Hz {a.channels}ch" if a else None,
           "bitrate_Mbps": round(os.path.getsize(vid) * 8 / dur / 1e6, 2)}
    times = {"title_6s": 6.0, "mid": dur / 2, "end_goodnight": max(0, dur - 9)}
    for i, cp in enumerate(caps):
        times[f"cap{i:02d}"] = float(cp["start"]) + 5.0
    stats = {}
    for name, t in sorted(times.items(), key=lambda kv: kv[1]):
        if t >= dur:
            continue
        c.seek(int(t / v.time_base), stream=v, backward=True)
        for fr in c.decode(v):
            if fr.time is not None and fr.time >= t - 0.05:
                img = fr.to_ndarray(format="rgb24")
                small = fr.reformat(width=960, height=540)
                small.to_image().save(os.path.join(outdir, f"{t:07.1f}_{name}.jpg"), quality=88) \
                    if hasattr(small, "to_image") and _has_pil() else _save_jpg(small, os.path.join(outdir, f"{t:07.1f}_{name}.jpg"))
                stats[name] = {"t": round(t, 1), "mean": round(float(img.mean()), 1)}
                break
    rep["frames"] = stats
    # audio sanity: decode 30 s from the middle and measure level
    if a:
        c.seek(int(dur / 2 * 1e6))
        s, n = 0.0, 0
        for fr in c.decode(a):
            x = fr.to_ndarray().astype(np.float32)
            s += float((x ** 2).sum()); n += x.size
            if n > a.rate * 2 * 30:
                break
        rep["audio_rms_dBFS_mid"] = round(10 * np.log10(s / max(n, 1) + 1e-12), 1)
    json.dump(rep, open(os.path.join(outdir, "report.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    if not quiet:
        print(json.dumps(rep, ensure_ascii=False, indent=1))
    return rep


def _has_pil():
    try:
        import PIL  # noqa
        return True
    except ImportError:
        return False


def _save_jpg(frame, path):
    import av
    out = av.open(path, "w", format="image2")
    s = out.add_stream("mjpeg", rate=1)
    s.width, s.height, s.pix_fmt = frame.width, frame.height, "yuvj420p"
    f = frame.reformat(format="yuvj420p")
    f.pts = 0
    for p in s.encode(f):
        out.mux(p)
    for p in s.encode():
        out.mux(p)
    out.close()


if __name__ == "__main__":
    main()
