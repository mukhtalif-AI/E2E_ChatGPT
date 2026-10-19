"""tests/calib_music_check.py - run music_check on finished episodes (read only) from the Linux device VM.
python3 calib_music_check.py <app dir> <data root (MukhtalifGPT mount)> <out.json> [episode narration dir or mix ...]
Each argument may be  narration_dir  or  narration_dir::mix.wav  (another mix of the same voice, e.g. an older copy)."""
import json, ntpath, os, sys, time, types

app, root, outp = sys.argv[1], sys.argv[2], sys.argv[3]
sys.path.insert(0, app)
sys.modules["tools"] = types.ModuleType("tools")          # never import the hub (it makes folders from Windows paths)
import audio_screen, emotion_music, music_check

cache = json.load(open(os.path.join(root, "Activity", "audio_screen.json"), encoding="utf-8"))
try:
    vocals = json.load(open(os.path.join(root, "Library", "music_vocals.json"), encoding="utf-8"))
except Exception:
    vocals = {}
win = lambda p: str(p).replace("/", "\\").lower()
fos = types.ModuleType("os")
fos.__dict__.update(os.__dict__)
fos.path = ntpath
audio_screen.os = fos
audio_screen.norm = win
audio_screen._load = lambda cfg: {"files": cache.get("files", {}), "overrides": cache.get("overrides", {}),
                                  "treated": cache.get("treated", {})}
audio_screen._vocals = lambda cfg: vocals
emotion_music.os = fos
cfg = {"ffmpeg": "ffmpeg"}

res = {}
for arg in sys.argv[4:]:
    nar, _, mix = arg.partition("::")
    genre = nar.split("/Series/")[1].split("/")[0] if "/Series/" in nar else ""
    t0 = time.time()
    try:
        r = music_check.check(cfg, nar, genre, mix_path=mix or None)
        key = os.path.basename(nar) + ("::" + os.path.basename(mix) if mix else "")
        res[key] = {"genre": genre, "meas": r["meas"], "rows": r["rows"], "secs": round(time.time() - t0, 1),
                    "tracks": [x.get("file") for x in r["tracks"]]}
        bad = [x["rule"] for x in r["rows"] if not x["ok"]]
        print(f"{key[:60]:60} {genre:8} {time.time() - t0:5.1f}s fail: {', '.join(bad) or '-'}", flush=True)
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"{arg}: ERROR {e}", flush=True)
    json.dump(res, open(outp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
