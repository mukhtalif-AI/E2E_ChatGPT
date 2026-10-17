"""steps/mix.py - music index and voice + music mix. Moved out of tools.py on 16 Oct 2026 (TOOLS_SPLIT_PLAN.md step 4d).

Do not import this file directly: import tools and use tools.<name>. Names that stay in tools.py, and names other files swap
(feedback, narrator, Tinker), are written as tools.X here on purpose.
"""
import glob, json, os, random, re, shutil, subprocess, time, urllib.error, urllib.parse, urllib.request, wave

import tools

WIN = os.name == "nt"


# ------------------------------------------------------------------ your music: index + the Sukoon music shape
def audio_index_path(cfg):
    return os.path.join(cfg["media_dir"], "audio_index.json")


def load_audio_index(cfg):
    try:
        return json.load(open(audio_index_path(cfg), encoding="utf-8"))
    except Exception:
        return {}


def audio_index(ctx, rebuild=False):
    cfg = ctx["cfg"]
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # app\steps\mix.py -> project root
    cmd = [cfg["gpu_python"], "-s", "-u", os.path.join(root, "app", "audio_index.py"), os.path.join(root, "config.json")]
    if rebuild:
        cmd.append("--rebuild")
    return ctx["run_cmd"](cmd, cwd=os.path.join(root, "app")) == 0


def auto_track(cfg):
    """the music you used most in your Sukoon episodes (if it is in BGM), else the calmest Calm track"""
    ai = load_audio_index(cfg)
    used = (ai.get("shape") or {}).get("used_tracks") or {}
    best = [f for f, _ in sorted(used.items(), key=lambda kv: -kv[1]) if os.path.exists(f)]
    if best:
        return best[0]
    calm = sorted([t for t in ai.get("tracks", []) if t.get("kind") == "music" and t.get("mood", "").lower() == "calm"
                   and os.path.exists(t["file"])], key=lambda t: -t.get("calm", 0))
    return calm[0]["file"] if calm else ""


def harshness(cfg, track):
    """how piercing a track is: loudest short peaks in the 2-6 kHz band (sharp piano / bell notes) above their usual
    level, in dB. Measured once per track (music_harsh.json in the hub media folder)."""
    import numpy as np
    cp = os.path.join(cfg["media_dir"], "music_harsh.json")
    try:
        cache = json.load(open(cp, encoding="utf-8"))
    except Exception:
        cache = {}
    k = f"{track}|{int(os.path.getmtime(track)) if os.path.exists(track) else 0}"
    if k in cache:
        return cache[k]
    p = subprocess.run([cfg["ffmpeg"], "-v", "error", "-i", track, "-t", "600", "-ac", "1", "-ar", "16000", "-af",
                        "highpass=f=2000,lowpass=f=6000", "-f", "f32le", "-"], capture_output=True,
                       creationflags=0x08000000 if WIN else 0)
    a = np.frombuffer(p.stdout, np.float32)
    if len(a) < 16000:
        return 0.0
    fr = a[: len(a) // 800 * 800].reshape(-1, 800)                       # 50 ms frames
    e = 10 * np.log10(np.mean(fr ** 2, axis=1) + 1e-12)
    loud = e[e > np.percentile(e, 20)]
    v = round(float(np.percentile(loud, 99.5) - np.median(loud)), 1) if len(loud) > 20 else 0.0
    cache[k] = v
    json.dump(cache, open(cp, "w", encoding="utf-8"), indent=1)
    return v


def tamed(ctx, track):
    """a piercing track gets its sharp notes softened (dip at 2.5-5 kHz + a fast compressor on the peaks), the way
    you lower those envelopes by hand; the original is never changed (copy in the hub's _tmp\\tamed)"""
    cfg, say = ctx["cfg"], ctx["say"]
    h = harshness(cfg, track)
    if h < float(cfg.get("music_harsh_tame", 14)):
        return track
    d = os.path.join(cfg["temporary_dir"], "tamed")
    os.makedirs(d, exist_ok=True)
    out = os.path.join(d, re.sub(r"[^\w\-]+", "_", os.path.splitext(os.path.basename(track))[0])[:60] + ".wav")
    if not os.path.exists(out) or os.path.getmtime(out) < os.path.getmtime(track):
        subprocess.run([cfg["ffmpeg"], "-v", "error", "-y", "-i", track, "-af",
                        "equalizer=f=3200:t=q:w=1.1:g=-5,equalizer=f=5500:t=q:w=1:g=-3,"
                        "acompressor=threshold=0.08:ratio=5:attack=1:release=90:makeup=1.6", "-ar", "48000", out],
                       capture_output=True, creationflags=0x08000000 if WIN else 0)
    say(f"   music has sharp notes (harshness {h:.0f} dB) - softened them for this video")
    return out if os.path.exists(out) else track


def _decode(cfg, path, sr=48000, ch=1):
    p = subprocess.run([cfg["ffmpeg"], "-v", "error", "-i", path, "-ac", str(ch), "-ar", str(sr), "-f", "f32le", "-"],
                       capture_output=True, creationflags=0x08000000 if WIN else 0)
    import numpy as np
    a = np.frombuffer(p.stdout, np.float32)
    return a.reshape(-1, ch) if ch > 1 else a


def shape_mix(ctx, name, track="", tail="1min", music_db=None):
    """your voice + music the way your Sukoon episodes do it: the music starts alone, sits under the story at your
    usual level, comes back up after you stop and plays on; smooth ramps. -> narration_music.wav (+ music_shape.json)"""
    import numpy as np
    cfg, say = ctx["cfg"], ctx["say"]
    d = tools.narration_dir(cfg, name, existing=True)
    vp = os.path.join(d, "narration.wav")
    if not os.path.exists(vp):
        raise RuntimeError("no narration.wav to put music under")
    track = track if track and track not in ("auto", "none") else auto_track(cfg)
    if not track or not os.path.exists(track):
        raise RuntimeError("no music track found - build the audio index or choose a track")
    sh = dict(load_audio_index(cfg).get("shape") or {})
    lead = float(np.clip(sh.get("lead_s", 4.0), 2, 15))
    fade_in = float(np.clip(sh.get("fade_in_s", 2.0), 0.5, lead))
    story = float(sh.get("story_db", -18.0)) if music_db in (None, "") else -abs(float(music_db))
    intro = float(np.clip(sh.get("intro_db", story + 4), story - 4, story + 10))
    outro = float(np.clip(sh.get("outro_db", story + 3), story - 4, story + 10))
    tails = {"1min": 60.0, "5min": 300.0, "usual": float(np.clip(sh.get("tail_s", 60), 30, 3600))}
    tail_s = tails.get(tail, 60.0)
    if str(tail).startswith("secs:"):                      # Experimental genre ending: the voice + a few seconds
        tail_s = float(np.clip(float(tail[5:] or tools.END_SECONDS), 1, 3600))
    fade_out = float(np.clip(sh.get("fade_out_s", 8.0), 4, tail_s * 0.6))
    sr = 48000
    v = _decode(cfg, vp, sr, 1)
    if str(tail).startswith("total:"):                     # fill up to a total length (e.g. a 30 min episode)
        tail_s = max(60.0, float(tail[6:]) * 60 - lead - len(v) / sr)
        fade_out = float(np.clip(sh.get("fade_out_s", 8.0), 4, tail_s * 0.6))
    say(f"   music: {os.path.basename(track)}  | starts {lead:.1f} s before you, {intro:.0f} dB -> {story:.0f} dB under "
        f"your voice, back to {outro:.0f} dB after you stop, "
        f"{f'{tail_s:.0f} s' if tail_s < 90 else f'{tail_s / 60:.0f} min'} more")
    n = int((lead + len(v) / sr + tail_s) * sr)
    m = _decode(cfg, tamed(ctx, track), sr, 2)
    if len(m) < sr * 4:
        raise RuntimeError("the music track could not be read")
    k = min(int(3 * sr), len(m) // 3)                                      # loop with 3 s crossfades
    P = len(m) - k
    w_in = np.linspace(0, 1, k, dtype=np.float32)[:, None]

    def music(a0, a1):                                                      # samples a0..a1 of the endless loop
        pos = np.arange(a0, a1)
        q, o = pos // P, pos % P
        out = m[o].copy()
        blend = (q > 0) & (o < k)
        if blend.any():
            ob = o[blend]
            out[blend] = m[ob] * w_in[ob] + m[P + ob] * (1 - w_in[ob])
        return out

    hop = sr // 2                                                          # levels in dB (0.5 s frames)
    fr = v[: len(v) // hop * hop].reshape(-1, hop)
    vdb = 10 * np.log10((fr.astype(np.float64) ** 2).mean(1) + 1e-12)
    vdb_act = float(np.median(vdb[vdb > np.percentile(vdb, 95) - 18]))
    mf = m[: len(m) // hop * hop].mean(1).reshape(-1, hop)
    mdb = 10 * np.log10((mf.astype(np.float64) ** 2).mean(1) + 1e-12)
    mdb_act = float(np.median(mdb[mdb > np.percentile(mdb, 95) - 25]))
    g = lambda rel: 10 ** ((vdb_act + rel - mdb_act) / 20)                  # gain that puts music at rel dB
    speech = vdb > np.percentile(vdb, 95) - 18
    vs = lead + float(np.argmax(speech)) * 0.5
    ve = lead + (len(speech) - float(np.argmax(speech[::-1]))) * 0.5
    end = n / sr
    rise = min(4.0, 0.3 * max(0.1, end - ve))                              # back up after you stop (short endings: faster)
    pts = [(0, 0), (fade_in, g(intro)), (vs, g(intro)), (vs + 3, g(story)), (ve, g(story)), (ve + rise, g(outro)),
           (max(ve + rise, end - fade_out), g(outro)), (end, 0)]
    px, py = [p[0] for p in pts], [p[1] for p in pts]
    a_v = int(lead * sr)
    raw = os.path.join(d, "_shape_mix.f32")
    peak = 0.0
    with open(raw, "wb") as fo:                                             # one minute at a time (low memory)
        for c0 in range(0, n, 60 * sr):
            c1 = min(n, c0 + 60 * sr)
            ch = music(c0, c1) * np.interp(np.arange(c0, c1) / sr, px, py).astype(np.float32)[:, None]
            v0, v1 = max(c0, a_v), min(c1, a_v + len(v))
            if v1 > v0:
                ch[v0 - c0:v1 - c0] += v[v0 - a_v:v1 - a_v, None]
            peak = max(peak, float(np.abs(ch).max()))
            fo.write(ch.astype("<f4").tobytes())
    outp = os.path.join(d, "narration_music.wav")
    if os.path.exists(outp):
        os.replace(outp, os.path.join(d, time.strftime("narration_music_old_%H%M%S.wav")))
    vol = f"volume={0.98 / peak:.4f}," if peak > 0.98 else ""
    rc = ctx["run_cmd"]([cfg["ffmpeg"], "-v", "error", "-y", "-f", "f32le", "-ar", str(sr), "-ac", "2", "-i", raw,
                         "-af", vol + "loudnorm=I=-16:TP=-1.5:LRA=11", "-ar", str(sr), "-c:a", "pcm_s16le", outp])
    try:
        os.remove(raw)
    except OSError:
        pass
    json.dump({"lead_s": lead, "track": track, "intro_db": intro, "story_db": story, "outro_db": outro,
               "tail_s": tail_s}, open(os.path.join(d, "music_shape.json"), "w", encoding="utf-8"), indent=1)
    if rc == 0:
        say(f"   -> {outp}  ({end / 60:.1f} min)")
    return rc == 0
