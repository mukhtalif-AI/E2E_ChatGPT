"""dataset.py - build the training set: your Sukoon lines as short clips + their exact text.

Source of line timings: the configured episode tree's _work folders:
transcript_script.json = lines matched to your script (best), else transcript_hi.json = speech recogniser
lines, kept only when it was sure). The audio is taken at FULL quality from your .aup3 in episodes_dir when it
is found and lines up; otherwise from that folder's 16 kHz voice.wav.
Everything is copied into  <media>\\dataset\\  (clips\\, refs\\, metadata.jsonl) - training never reads the
source folders again. Safe to re-run: existing clips are kept, new episodes are added.
"""
import glob, hashlib, json, os, re, subprocess, wave
import numpy as np
from . import cfg
from .aup3 import voice_track

SR_OUT = 24000


def say(*a):
    print(*a, flush=True)


def norm_name(s):
    return re.sub(r"[^0-9a-z]", "", os.path.splitext(os.path.basename(s))[0].lower())


def read_wav16(path):
    with wave.open(path) as w:
        n, rate, ch, sw = w.getnframes(), w.getframerate(), w.getnchannels(), w.getsampwidth()
        raw = w.readframes(n)
    x = np.frombuffer(raw, "<i2").astype(np.float32) / 32768.0
    if ch > 1:
        x = x.reshape(-1, ch).mean(1)
    return x, rate


def signature(path):
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        f.seek(size // 2)
        return f"{size}-{hashlib.md5(f.read(65536)).hexdigest()}"


def write_clip(path, x, rate):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    r = float(np.sqrt(np.mean(x ** 2))) if len(x) else 0.0
    if r > 1e-5:                                              # same loudness for every clip (about -20 dBFS)
        x = x * min(0.1 / r, 0.95 / max(1e-6, float(np.abs(x).max())))
    try:                                                      # fast path: in this process (no ffmpeg start-up)
        import soundfile as sf, soxr
        y = soxr.resample(np.clip(x, -1, 1).astype(np.float32), rate, SR_OUT, quality="HQ") if rate != SR_OUT else x
        sf.write(path + ".part.wav", np.clip(y, -1, 1), SR_OUT, subtype="PCM_16")
        os.replace(path + ".part.wav", path)
        return
    except ImportError:
        pass
    p = subprocess.run([cfg.ffmpeg(), "-v", "error", "-y", "-f", "f32le", "-ar", str(rate), "-ac", "1", "-i", "-",
                        "-ar", str(SR_OUT), "-ac", "1", "-c:a", "pcm_s16le", path + ".part.wav"],
                       input=np.clip(x, -1, 1).astype("<f4").tobytes())
    if p.returncode:
        raise SystemExit(f"ffmpeg could not write {path}")
    os.replace(path + ".part.wav", path)


def find_aup3s():
    out = {}
    roots = (cfg.C.get("episodes_dir") or "", cfg.C.get("legacy_episodes_dir") or r"D:\Mukhtalif\Episodes")
    for root in roots:
        if os.path.isdir(root):
            for dp, dn, fn in os.walk(root):
                for f in fn:
                    if f.lower().endswith(".aup3"):
                        out[norm_name(f)] = os.path.join(dp, f)
                        out.setdefault(norm_name(os.path.basename(dp)), os.path.join(dp, f))
    return out


def sources():
    roots = (cfg.C.get("import_transcripts_from") or "", cfg.C.get("legacy_import_transcripts_from") or "")
    found = []
    for root in roots:
        if not os.path.isdir(root):
            continue
        voice_files = glob.glob(os.path.join(root, "**", "voice.wav"), recursive=True)
        for v in voice_files:
            d = os.path.dirname(v)
            name = os.path.basename(os.path.dirname(d)) if os.path.basename(d).lower() == "_work" else os.path.basename(d)
            for tf, kind in (("transcript_script.json", "script"), ("transcript_hi.json", "asr")):
                if os.path.exists(os.path.join(d, tf)):
                    found.append({"name": name, "dir": d, "voice": v, "transcript": os.path.join(d, tf), "kind": kind,
                                  "sig": signature(v)})
                    break
    best = {}                                                 # same recording in two folders: keep the script one
    for s in found:
        b = best.get(s["sig"])
        if b is None or (b["kind"] != "script" and s["kind"] == "script"):
            best[s["sig"]] = s
    return list(best.values())


def good_lines(src):
    D = cfg.C["dataset"]
    j = json.load(open(src["transcript"], encoding="utf-8"))
    segs = j["segments"] if isinstance(j, dict) else j
    segs = [s for s in segs if (s.get("text") or "").strip() and s.get("end") is not None]
    keep = []
    for i, s in enumerate(segs):
        dur = s["end"] - s["start"]
        if not D["min_sec"] <= dur <= D["max_sec"]:
            continue
        if not re.search("[ऀ-ॿ]", s["text"]):
            continue
        source = "script" if src["kind"] == "script" and s.get("source") in (None, "script") else "asr"
        if source == "asr":
            ps = [w.get("p") for w in s.get("words") or [] if w.get("p") is not None]
            if not ps or np.mean(ps) < D["asr_min_avg_p"] or min(ps) < D["asr_min_word_p"]:
                continue
        prev_end = segs[i - 1]["end"] if i else 0.0
        next_start = segs[i + 1]["start"] if i + 1 < len(segs) else s["end"] + 1.0
        a = max(s["start"] - 0.12, (prev_end + s["start"]) / 2)
        b = min(s["end"] + 0.25, (s["end"] + next_start) / 2)
        keep.append({"start": round(a, 3), "end": round(b, 3), "text": " ".join(s["text"].split()), "source": source,
                     "speech": (s["start"], s["end"])})
    return keep


def pick_holdout(meta, n):
    cand = [m for m in meta if m["source"] == "script" and 6 <= len(m["text"].split()) <= 22]
    if len(cand) < n:
        cand = [m for m in meta if 6 <= len(m["text"].split()) <= 22]
    if not cand:
        return
    for k in np.linspace(0, len(cand) - 1, min(n, len(cand))).astype(int):
        cand[k]["holdout"] = True


def pick_reference(x, rate, lines, ep, used_ids):
    """8-10.5 s of steady, continuous speech (consecutive lines, short gaps) = the voice prompt."""
    best, best_score = None, -1
    for i in range(len(lines)):
        if lines[i]["id"] in used_ids:
            continue
        s0 = lines[i]["speech"][0]
        speech, j = 0.0, i
        while j < len(lines) and lines[j]["id"] not in used_ids:
            if j > i and lines[j]["speech"][0] - lines[j - 1]["speech"][1] > 0.9:
                break
            speech += lines[j]["speech"][1] - lines[j]["speech"][0]
            span = lines[j]["speech"][1] - s0
            if 8.0 <= span <= 10.5:
                score = speech / span + (0.2 if lines[i]["source"] == "script" else 0)
                if score > best_score:
                    best, best_score = (s0, lines[j]["speech"][1], " ".join(l["text"] for l in lines[i:j + 1])), score
            if span > 10.5:
                break
            j += 1
    if not best:
        return None
    a, b, text = best
    path = os.path.join(cfg.REFS, f"{ep}.wav")
    write_clip(path, x[int((a - 0.08) * rate):int((b + 0.2) * rate)], rate)
    open(path[:-4] + ".txt", "w", encoding="utf-8").write(text + "\n")
    return {"episode": ep, "wav": path, "text": text, "score": round(best_score, 3), "dur": round(b - a, 2)}


def my_voice(m, meta):
    """your voice vector = average voice-encoder embedding of your script-matched lines (or refs\\reference.wav)."""
    import librosa
    pick = [x for x in meta if x["source"] == "script"] or meta
    pick = [pick[k] for k in np.linspace(0, len(pick) - 1, min(80, len(pick))).astype(int)] if pick else []
    wavs = [librosa.load(x["wav"], sr=16000)[0] for x in pick]
    if not wavs:
        r = os.path.join(cfg.REFS, "reference.wav")
        sample = cfg.C.get("my_voice_sample") or r
        if not os.path.exists(sample):
            raise SystemExit("need a sample of your voice: set \"my_voice_sample\" in config.json to a clean .wav of you")
        wavs = [librosa.load(sample, sr=16000)[0]]
    e = np.asarray(m.ve.embeds_from_wavs(wavs, sample_rate=16000), np.float32)
    e = e / (np.linalg.norm(e, axis=1, keepdims=True) + 1e-8)
    v = e.mean(0)
    return v / np.linalg.norm(v)


def main(rebuild=False, skip_recordings=False):
    srcs = sources()
    if not srcs:
        say("no episodes with line timings found in import_transcripts_from = "
            f"{cfg.C.get('import_transcripts_from')!r}")
    use_rec = bool((cfg.C.get("recordings") or {}).get("folders")) and not skip_recordings
    if use_rec:                         # episodes WITHOUT a script: the recordings step does them better (whole
        from . import recordings        # project, its own checks) - only script-matched episodes are imported here
        rec = {norm_name(f) for f in recordings.files()}
        srcs = [s_ for s_ in srcs if s_["kind"] == "script" or norm_name(s_["name"]) not in rec]
    aups = find_aup3s()
    old = {m["id"]: m for m in cfg.read_meta()}
    meta, refs = [], []
    say(f"{len(srcs)} episodes with line timings")
    for src in srcs:
        ep = norm_name(src["name"])
        lines = good_lines(src)
        say(f"\n== {src['name']}  ({'matched to your script' if src['kind'] == 'script' else 'speech recogniser'}): "
            f"{len(lines)} usable lines, {sum(l['end'] - l['start'] for l in lines) / 60:.1f} min")
        if not lines:
            continue
        for k, l in enumerate(lines):
            l["id"] = f"{ep}_{k:04d}"
        todo = rebuild or any(not os.path.exists(os.path.join(cfg.CLIPS, l["id"] + ".wav")) for l in lines) or \
            not os.path.exists(os.path.join(cfg.REFS, ep + ".wav"))
        x = rate = None
        if todo:
            v16, r16 = read_wav16(src["voice"])
            x, rate, how = v16, r16, "16 kHz voice.wav"
            ap = aups.get(ep)
            if ap:
                try:
                    say(f"   reading full-quality voice from {os.path.basename(ap)} ...")
                    nx, nr, tname = voice_track(ap)
                    if abs(len(nx) / nr - len(v16) / r16) < 1.0:
                        x, rate, how = nx, nr, f"{os.path.basename(ap)} track '{tname}' {nr} Hz"
                    else:
                        say(f"   (the .aup3 is {len(nx) / nr:.0f} s long, the timings are for {len(v16) / r16:.0f} s"
                            " - using voice.wav)")
                except Exception as e:
                    say(f"   (could not read the .aup3: {e} - using voice.wav)")
            say(f"   audio: {how}")
        for k, l in enumerate(lines):
            p = os.path.join(cfg.CLIPS, l["id"] + ".wav")
            if rebuild or not os.path.exists(p):
                write_clip(p, x[int(l["start"] * rate):int(l["end"] * rate)], rate)
            m = {"id": l["id"], "wav": p, "text": l["text"], "dur": round(l["end"] - l["start"], 3),
                 "episode": src["name"], "series": "sukoon" if "sukoon" in src["name"].lower() else "story",
                 "source": l["source"], "holdout": bool(old.get(l["id"], {}).get("holdout"))}
            meta.append(m)
            if (k + 1) % 50 == 0:
                say(f"   clips {k + 1}/{len(lines)}")
        if x is not None:
            r = pick_reference(x, rate, lines, ep, set())
            if r:
                refs.append(r)
                say(f"   voice prompt: {r['dur']} s  \"{r['text'][:70]}...\"")
    if use_rec:
        from .model import load_base
        m = load_base()
        me = my_voice(m, meta)
        rmeta, rrefs = recordings.main(m, {norm_name(s_["name"]) for s_ in srcs}, me)
        for x_ in rmeta:
            x_["holdout"] = bool(old.get(x_["id"], {}).get("holdout"))
        meta += rmeta
        refs += rrefs
    if not meta:
        raise SystemExit("no usable lines found - check import_transcripts_from / recordings in config.json")
    want = int(cfg.C["dataset"]["holdout"])
    have = sum(1 for m in meta if m["holdout"])
    if have < want:                                   # top up (earlier kept-out lines stay kept out)
        rest = [m for m in meta if not m["holdout"]]
        pick_holdout(rest, want - have)
    os.makedirs(cfg.DATASET, exist_ok=True)
    with open(cfg.META + ".part", "w", encoding="utf-8") as f:
        for m in meta:
            f.write(json.dumps(m, ensure_ascii=False) + "\n")
    os.replace(cfg.META + ".part", cfg.META)
    rj = os.path.join(cfg.REFS, "refs.json")
    allrefs = {r["episode"]: r for r in (json.load(open(rj, encoding="utf-8")) if os.path.exists(rj) else [])}
    allrefs.update({r["episode"]: r for r in refs})
    if allrefs:
        os.makedirs(cfg.REFS, exist_ok=True)
        json.dump(list(allrefs.values()), open(rj, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        top = max(allrefs.values(), key=lambda r: r["score"])
        main_ref = os.path.join(cfg.REFS, "reference.wav")
        if not os.path.exists(main_ref) or rebuild:
            import shutil
            shutil.copyfile(top["wav"], main_ref)
            shutil.copyfile(top["wav"][:-4] + ".txt", main_ref[:-4] + ".txt")
    tr = [m for m in meta if not m["holdout"]]
    for ser in sorted({m.get("series", "?") for m in meta}):
        say(f"  {ser:7s}: {sum(m['dur'] for m in meta if m.get('series') == ser) / 60:.1f} min")
    say(f"\nDATASET: {len(meta)} lines = {sum(m['dur'] for m in meta) / 60:.1f} min of your voice "
        f"({sum(m['dur'] for m in meta if m['source'] == 'script') / 60:.1f} min script-matched)")
    say(f"  training: {len(tr)} lines, {sum(m['dur'] for m in tr) / 60:.1f} min   |   kept out for the A/B test: "
        f"{len(meta) - len(tr)} lines")
    say(f"  folder: {cfg.DATASET}")
    say("  voice prompt for speaking: dataset\\refs\\reference.wav  (any refs\\<episode>.wav can be set in config)")
