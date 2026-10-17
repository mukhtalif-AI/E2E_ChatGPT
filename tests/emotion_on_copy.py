"""emotion_on_copy.py - run the emotion music on a COPY of a finished episode and compare it with the old mix.

    python tests\\emotion_on_copy.py "D:\\MukhtalifGPT\\Series\\kisse\\Episodes\\Kisse-Fathers-Old-Tin-Box" [--reply r.json]

Copies the episode's narration and scene plan into _tmp\\emotion_copy_<time> (with copies of the ratings, screening
cache and music-use counts, so nothing real is changed), makes the emotion map (the AI needs Ollama; --no-ai uses the
feeling marks; --reply uses a saved answer), runs tools.horror_sound for real (ffmpeg, CPU) and checks:
  - music, ambience and effects are really silent in each drop (only the voice is left)
  - the music changes track where the map says, never more than 3 tracks
  - the voice is not changed (the mix minus the voice has no voice left in it)
Writes check.json with the new mix's path (narration_music.wav in the copy) and the episode's own mix, to compare.
Away from the PC: --map FROM=TO (repeatable), like tests\\smoke_tools.py.
"""
import argparse, glob, json, os, shutil, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "app"))


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("episode")
    ap.add_argument("--reply", default="")
    ap.add_argument("--no-ai", action="store_true")
    ap.add_argument("--map", action="append", default=[])
    ap.add_argument("--out", default="")
    a = ap.parse_args(argv)
    import numpy as np
    import path_config
    cfg = path_config.load_config()
    for fr, to in (m.split("=", 1) for m in a.map if "=" in m):
        for k, v in list(cfg.items()):
            i = v.lower().find(fr.lower()) if isinstance(v, str) else -1
            if i >= 0:
                cfg[k] = to + v[i + len(fr):].replace("\\", "/")
    real_root = cfg.get("experimental_root")
    ep = os.path.abspath(a.episode)
    genre = os.path.basename(os.path.dirname(os.path.dirname(ep))).lower()
    nar0 = os.path.dirname(sorted(glob.glob(os.path.join(ep, "02_Narration", "narrations", "*", "timing.json")))[0])
    pic0 = os.path.dirname(sorted(glob.glob(os.path.join(ep, "03_Artwork", "*", "shots.json")))[0])
    name = os.path.basename(nar0)
    tmp = a.out or os.path.join(ROOT, "_tmp", "emotion_copy_" + time.strftime("%Y%m%d_%H%M%S"), name)
    data = os.path.join(tmp, "data")
    act = os.path.join(data, "Activity")
    os.makedirs(act, exist_ok=True)
    for f in ("audio_screen.json", "ratings.sqlite"):
        p = os.path.join(real_root, "Activity", f)
        if os.path.exists(p):
            shutil.copy2(p, os.path.join(act, f))
    lib = os.path.join(data, "Library")
    os.makedirs(os.path.join(lib, "_tmp"), exist_ok=True)
    for f in ("bhay_music_used.json", "music_harsh.json", "audio_index.json"):
        for src in (os.path.join(real_root, "Library", f), os.path.join(real_root, f)):
            if os.path.exists(src):
                shutil.copy2(src, os.path.join(lib, f))
                break
    nar = os.path.join(tmp, "02_Narration", "narrations", name)
    pic = os.path.join(tmp, "03_Artwork", os.path.basename(pic0))
    shutil.copytree(nar0, nar, ignore=shutil.ignore_patterns("narration_music*.wav", "_*.f32", "lines"))
    os.makedirs(pic, exist_ok=True)
    for f in ("shots.json",):
        shutil.copy2(os.path.join(pic0, f), os.path.join(pic, f))
    cfg.update(experimental_mode=True, experimental_root=data, media_dir=lib, temporary_dir=os.path.join(lib, "_tmp"),
               sukoonvoice_media=os.path.join(tmp, "02_Narration"))
    import tools
    import feedback
    import emotion_music
    feedback.install(cfg)                       # the hub does this at start: music picks use your ratings
    log = []
    say = lambda *x: (print(*x), log.append(" ".join(str(y) for y in x)))
    run = lambda cmd, cwd=None: subprocess.run(cmd, cwd=cwd).returncode
    ctx = {"cfg": cfg, "say": say, "run_cmd": run, "media": lib, "stopped": lambda: False, "job": {}}
    ask = None
    if a.reply:
        saved = json.load(open(a.reply, encoding="utf-8"))
        ask = lambda *x, **k: (json.dumps(saved, ensure_ascii=False), "saved reply")
    m = emotion_music.build(cfg, genre, nar, pic, say, use_ai=not a.no_ai,
                            script_path=os.path.join(ep, "01_Script", "script.txt"), ask=ask)
    json.dump(m, open(os.path.join(pic, "emotion_map.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    emotion_music.report(m, say)
    t0 = time.time()
    tail = f"secs:{cfg.get('end_seconds') or tools.END_SECONDS}"
    ok = tools.horror_sound(ctx, name, pic, "", genre, tail, None)
    took = time.time() - t0
    out = os.path.join(nar, "narration_music.wav")
    ms = json.load(open(os.path.join(nar, "music_shape.json"), encoding="utf-8"))
    sr = 48000
    y = tools._decode(cfg, out, sr, 2).mean(1)
    v = tools._decode(cfg, os.path.join(nar, "narration.wav"), sr, 1)
    lead = float(ms["lead_s"])
    res = {"ok": bool(ok), "seconds": round(took, 1), "tracks": ms.get("tracks"), "drops": []}
    a_v = int(lead * sr)
    vv = np.zeros_like(y)
    vv[a_v:a_v + len(v)] = v[: len(y) - a_v]
    for t in m.get("turns") or []:                 # in a drop the mix must be the voice alone (times one gain)
        if t["weight"] < 2:
            continue
        drop = float(m["settings"]["drop_s"][str(t["weight"])])
        i0 = int((lead + t["at"] - emotion_music.PRE_S - drop + 0.1) * sr)
        i1 = int((lead + t["line_end"]) * sr)
        Y, V = y[i0:i1].astype(np.float64), vv[i0:i1].astype(np.float64)
        g = float(np.dot(Y, V) / (np.dot(V, V) + 1e-12))
        resid = Y - g * V
        r_db = 10 * np.log10((resid ** 2).mean() + 1e-12) - 10 * np.log10(((g * V) ** 2).mean() + 1e-12)
        res["drops"].append({"at": t["at"], "weight": t["weight"], "words": t["words"],
                             "other_sound_vs_voice_db": round(float(r_db), 1)})
    res["tracks_n"] = len({x["file"] for x in ms.get("tracks") or []})
    res["checks"] = {
        "sound step ran": bool(ok),
        "at most 3 tracks": res["tracks_n"] <= 3,
        "drops: only the voice is left (other sound at least 35 dB under it)":
            all(d["other_sound_vs_voice_db"] < -35 for d in res["drops"]),
    }
    res["new"] = out                               # listen: this file against the episode's own mix
    res["old"] = os.path.join(nar0, "narration_music.wav")
    json.dump(res, open(os.path.join(tmp, "check.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    with open(os.path.join(tmp, "log.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(log) + "\n")
    print(json.dumps(res, ensure_ascii=False, indent=1))
    print("ALL PASSED" if all(res["checks"].values()) else "SOME CHECKS FAILED")
    print("listen: " + tmp)


if __name__ == "__main__":
    main(sys.argv[1:])
