"""sukoon_tail_on_copy.py - check that an Experimental Sukoon episode gets its music after the voice (19 Oct 2026).

    python tests\\sukoon_tail_on_copy.py "D:\\MukhtalifGPT\\Series\\sukoon\\Episodes\\<episode>" [--total 31] [--map FROM=TO]

Uses the Experimental job settings (media_dir = <root>\\Library, where no audio_index.json lives) and checks:
  - the music index is found (the planner then adds the 'music the way your Sukoon episodes do it' step)
  - the Sukoon episode plan has shape_mix with tail total:<minutes>
  - shape_mix on a COPY of the episode's narration.wav (in _tmp\\sukoon_tail_<time>) gives a mix of <total> minutes
The finished episode is only read.
"""
import argparse, glob, json, os, shutil, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "app"))


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("episode")
    ap.add_argument("--total", type=float, default=31)
    ap.add_argument("--map", action="append", default=[])
    a = ap.parse_args(argv)
    import path_config
    cfg = path_config.load_config()
    maps = [m.split("=", 1) for m in a.map if "=" in m]

    def mp(v):
        for fr, to in maps:
            i = v.lower().find(fr.lower()) if isinstance(v, str) else -1
            if i >= 0:
                return to + v[i + len(fr):].replace("\\", "/")
        return v
    for k, v in list(cfg.items()):
        cfg[k] = mp(v)
    for k in ("ffmpeg", "ffprobe"):                                        # away from the PC: the one on PATH
        if not os.path.exists(str(cfg.get(k) or "")) and shutil.which(k):
            cfg[k] = shutil.which(k)
    root = cfg["experimental_root"]
    ep = os.path.abspath(a.episode)
    nar0 = os.path.dirname(sorted(glob.glob(os.path.join(ep, "02_Narration", "narrations", "*", "narration.wav")))[0])
    name = os.path.basename(nar0)
    tmp = os.path.join(ROOT, "_tmp", "sukoon_tail_" + time.strftime("%Y%m%d_%H%M%S"))
    nar = os.path.join(tmp, name)
    os.makedirs(nar)
    shutil.copy2(os.path.join(nar0, "narration.wav"), nar)
    cfg.update(experimental_mode=True, media_dir=os.path.join(root, "Library"),
               temporary_dir=os.path.join(tmp, "_tmp"))
    os.makedirs(cfg["temporary_dir"])
    import tools
    from steps import planner
    ok = True
    ai = tools.load_audio_index(cfg)
    print("index found:", bool(ai.get("shape")), "| Library copy:", os.path.exists(tools.audio_index_path(cfg)))
    ok &= bool(ai.get("shape"))
    b = {"music_shape": True, "music_track": "", "total_min": a.total}
    print("planner use_shape:", planner.use_shape(b, cfg))
    ok &= planner.use_shape(b, cfg)
    tools.narration_dir = lambda c, n, existing=False: nar                 # the copy, never the episode
    track = next((mp(f) for f, _ in sorted(((ai.get("shape") or {}).get("used_tracks") or {}).items(),
                                           key=lambda kv: -kv[1]) if os.path.exists(mp(f))), "")
    print("track:", track)
    ctx = {"cfg": cfg, "say": print, "run_cmd": lambda c, cwd=None: subprocess.run(c, cwd=cwd).returncode}
    t0 = time.time()
    tools.shape_mix(ctx, name, track, f"total:{a.total}")
    dur = lambda p: float(subprocess.run([cfg.get("ffprobe") or "ffprobe", "-v", "error", "-show_entries",
                                          "format=duration", "-of", "csv=p=0", p], capture_output=True,
                                         text=True).stdout or 0)
    v, m = dur(os.path.join(nar, "narration.wav")), dur(os.path.join(nar, "narration_music.wav"))
    print(f"voice {v / 60:.1f} min, mix {m / 60:.1f} min (wanted {a.total:.1f}), {time.time() - t0:.0f} s")
    ok &= abs(m / 60 - a.total) < 0.1
    print("PASS" if ok else "FAIL", "| copy:", tmp)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
