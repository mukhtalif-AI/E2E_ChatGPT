"""recordings.py - your UNSCRIPTED Audacity projects -> training lines (runs on the graphics card).

For every .aup3 in config "recordings.folders" (minus "exclude", minus episodes already imported with a script):
  1 the track that sounds most like YOU is used (voice encoder vs your voice; dry tracks preferred over
    "Effect"/"Mix"/"SFX" ones) - read-only, the project is never changed
  2 cut at your pauses into 1.5-14 s lines (pauses under 1 s stay inside a line, as you speak)
  3 each line transcribed by the Hindi checker (Whisper); kept only when it was sure, the text has a sane
    length for the time and no known hallucination words
  4 each line compared with your voice - music / SFX / another person's voice is dropped
Result per project: <media>\\dataset\\projects\\<name>.jsonl (+ clips). Crash-safe: finished projects are skipped.
"""
import fnmatch, glob, json, os, re, time
import numpy as np
from . import cfg
from .aup3 import open_ro, track_audio, speechiness
from .aup3_doc import decode, tracks
from .dataset import write_clip, norm_name, say, pick_reference

PROJ = cfg.PROJECTS
HALLU = re.compile(r"समाचार|बुलेटिन|आकाशवाणी|प्रादेशिक|सब्सक्राइब|प्रधानमंत्री|मुख्यमंत्री|धन्यवाद देखने")
SR16 = 16000


def to16(x, rate):
    if rate == SR16:
        return x
    n = int(len(x) * SR16 / rate)
    return np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x).astype(np.float32)


def speech_runs(x, hop=0.02):
    """(copied from the Studio's asr_hindi) speech = louder than the room / breaths."""
    n = int(SR16 * hop)
    f = x[: len(x) // n * n].reshape(-1, n)
    db = 10 * np.log10((f.astype(np.float64) ** 2).mean(1) + 1e-10)
    db = np.convolve(db, np.ones(5) / 5, mode="same")
    floor, top = np.percentile(db, 15), np.percentile(db, 97)
    on = db > max(floor + 0.40 * (top - floor), top - 28.0)
    runs, i = [], 0
    while i < len(on):
        if on[i]:
            j = i
            while j < len(on) and on[j]:
                j += 1
            runs.append([i * hop, j * hop]); i = j
        else:
            i += 1
    merged = []
    for r in runs:
        if merged and r[0] - merged[-1][1] < 0.18:
            merged[-1][1] = r[1]
        else:
            merged.append(r)
    return [r for r in merged if r[1] - r[0] > 0.12]


def lines_from_runs(runs, join_gap=1.0, max_len=14.0, min_len=1.5):
    out = []
    for s, e in runs:
        if out and s - out[-1][1] < join_gap and e - out[-1][0] <= max_len:
            out[-1][1] = e
        else:
            out.append([s, e])
    return [o for o in out if min_len <= o[1] - o[0] <= max_len]


def files():
    R = cfg.C.get("recordings") or {}
    ex = [e.lower() for e in R.get("exclude", [])]
    out = []
    for folder in R.get("folders", []):
        for f in sorted(glob.glob(os.path.join(folder, "*.aup3"))):
            n = os.path.basename(f).lower()
            if any(fnmatch.fnmatch(n, e) or fnmatch.fnmatch(os.path.splitext(n)[0], e) for e in ex):
                continue
            out.append(f)
    return out


class Ear:
    """voice encoder (who is speaking) + Hindi Whisper with confidence (what was said)."""

    def __init__(self, m):
        from . import asr
        self.m = m
        self.ve = m.ve
        if "m" not in asr._M:
            asr._M["m"] = asr._load()
        self.wm, self.proc, self.dev, self.dt = asr._M["m"]
        self.t_ve = self.t_wh = 0.0
        import torch
        if torch.cuda.is_available():
            free, total = torch.cuda.mem_get_info()
            say(f"      Whisper on {next(self.wm.parameters()).device} ({self.dt}); graphics card memory: "
                f"{(total - free) / 2**30:.1f} of {total / 2**30:.1f} GB in use (all programs)")
        else:
            say("      !! Whisper runs on the PROCESSOR (no CUDA) - very slow")

    def emb(self, wavs16):
        e = np.asarray(self.ve.embeds_from_wavs(wavs16, sample_rate=SR16), np.float32)
        return e / (np.linalg.norm(e, axis=1, keepdims=True) + 1e-8)

    def transcribe(self, wavs16):
        import torch
        feats = self.proc.feature_extractor(wavs16, sampling_rate=SR16, return_tensors="pt").input_features
        feats = feats.to(self.dev, self.dt)
        tok = self.proc.tokenizer
        special = set(tok.all_special_ids)
        if not getattr(self, "no_fast", False):
            from . import asr
            try:
                return asr.fast_decode(self.wm, self.proc, feats, [len(x) / SR16 for x in wavs16])
            except Exception as e:
                say(f"   (fast decoding failed: {repr(e)[:160]} - using the slow one)")
                self.no_fast = True
        if not getattr(self, "no_scores", False):
            try:
                with torch.inference_mode():
                    out = self.wm.generate(feats, language="hi", task="transcribe", num_beams=1, do_sample=False,
                                           max_new_tokens=160, return_dict_in_generate=True, output_scores=True)
                    sc = self.wm.compute_transition_scores(out.sequences, out.scores, normalize_logits=True)
            except Exception as e:
                say(f"   (word confidence not available: {repr(e)[:120]} - using the other checks only)")
                self.no_scores = True
        if getattr(self, "no_scores", False):
            with torch.inference_mode():
                seqs = self.wm.generate(feats, language="hi", task="transcribe", num_beams=2, max_new_tokens=160)
            return [(" ".join(tok.decode([t for t in q.tolist() if t not in special]).split()), 0.0) for q in seqs]
        res = []
        seqs = out.sequences[:, -sc.shape[1]:]
        for b in range(len(wavs16)):
            ids = seqs[b].tolist()
            lp = [float(sc[b, k]) for k, t in enumerate(ids) if t not in special and np.isfinite(float(sc[b, k]))]
            text = tok.decode([t for t in ids if t not in special]).strip()
            res.append((" ".join(text.split()), float(np.mean(lp)) if lp else -9.0))
        return res


def pick_track(path, ear, me):
    con = open_ro(path)
    try:
        d, doc = con.execute("select dict, doc from project").fetchone()
        trs = tracks(decode(bytes(d), bytes(doc)))
        best, best_s, notes = None, -9, []
        for i, t in enumerate(trs):
            if t["mute"] or not t["clips"]:
                continue
            x = track_audio(con, t)
            rate = int(t["rate"])
            if len(x) < rate * 20 or speechiness(x[: rate * 300], rate) < 0.05:
                continue
            x16 = to16(x, rate)
            runs = lines_from_runs(speech_runs(x16))
            if len(runs) < 5:
                continue
            pick = [runs[k] for k in np.linspace(0, len(runs) - 1, min(12, len(runs))).astype(int)]
            e = ear.emb([x16[int(a * SR16):int(b * SR16)] for a, b in pick])
            sim = float(np.median(e @ me))
            pen = 0.04 if re.search(r"effect|mix|sfx|reverb|echo", t["name"] or "", re.I) else 0.0
            notes.append(f"'{t['name']}' {sim:.2f}")
            if sim - pen > best_s:
                best, best_s = (x, rate, t["name"], sim), sim - pen
            del x16
        return best, notes
    finally:
        con.close()


def process(path, ear, me, R):
    name = os.path.splitext(os.path.basename(path))[0]
    ep = norm_name(os.path.basename(path))       # from the FILE name: "Sukoon 2.1" and "Sukoon 2.2" must differ
    t0 = time.time()
    best, notes = pick_track(path, ear, me)
    if not best:
        say(f"   no voice track found ({', '.join(notes) or 'nothing speech-like'})")
        return [], None
    x, rate, tname, sim = best
    say(f"   track '{tname}' (sounds like you: {sim:.2f}; all: {', '.join(notes)})")
    if sim < float(R["speaker_min_similarity"]) - 0.1:
        say("   -> not your voice, skipped")
        return [], None
    x16 = to16(x, rate)
    spans = lines_from_runs(speech_runs(x16))
    total = len(x16) / SR16
    lines, dropped = [], {"voice": 0, "unsure": 0, "text": 0}
    B = 16
    t_pick = time.time() - t0
    t_asr = 0.0
    for k in range(0, len(spans), B):
        part = spans[k:k + B]
        cuts = [[max(0.0, a - 0.12), min(total, b + 0.25)] for a, b in part]
        w16 = [x16[int(a * SR16):int(b * SR16)] for a, b in cuts]
        ta = time.time()
        sims = ear.emb(w16) @ me
        tb = time.time()
        heard = ear.transcribe(w16)
        t_asr += time.time() - ta
        ear.t_ve += tb - ta; ear.t_wh += time.time() - tb
        say(f"   listening {min(k + B, len(spans))}/{len(spans)} lines   ({time.time() - t0:.0f} s, kept so far "
            f"{len(lines)})")
        for (a, b), (sa, sb), s, (text, lp) in zip(cuts, part, sims, heard):
            letters = len(re.sub("[^ऀ-ॿ]", "", text))
            cps = letters / max(0.3, sb - sa)
            words = text.split()
            rep = any(words[i] == words[i + 1] == words[i + 2] for i in range(len(words) - 2))
            if s < float(R["speaker_min_similarity"]):
                dropped["voice"] += 1
            elif lp < float(R["asr_min_logprob"]):
                dropped["unsure"] += 1
            elif not (5 <= cps <= 22) or HALLU.search(text) or rep or len(words) < 2 or \
                    re.search("[a-zA-Z]", text):
                dropped["text"] += 1
            else:
                lines.append({"start": round(a, 3), "end": round(b, 3), "text": text, "source": "asr",
                              "speech": (sa, sb), "sim": round(float(s), 3), "lp": round(lp, 3)})
    tw = time.time()
    for i, l in enumerate(lines):
        l["id"] = f"{ep}_{i:04d}"
        write_clip(os.path.join(cfg.CLIPS, l["id"] + ".wav"), x[int(l["start"] * rate):int(l["end"] * rate)], rate)
    ref = pick_reference(x, rate, lines, ep, set()) if lines else None
    series = "sukoon" if "sukoon" in name.lower() else "story"
    meta = [{"id": l["id"], "wav": os.path.join(cfg.CLIPS, l["id"] + ".wav"), "text": l["text"],
             "dur": round(l["end"] - l["start"], 3), "episode": name, "series": series, "source": "asr",
             "holdout": False, "sim": l["sim"], "lp": l["lp"]} for l in lines]
    say(f"   kept {len(meta)} of {len(spans)} lines = {sum(m['dur'] for m in meta) / 60:.1f} min   (dropped: "
        f"{dropped['voice']} not you, {dropped['unsure']} unclear words, {dropped['text']} odd text)  "
        f"[{time.time() - t0:.0f} s: read+track {t_pick:.0f}, listen {t_asr:.0f} (voice check {ear.t_ve:.0f}, Whisper "
        f"{ear.t_wh:.0f}), save {time.time() - tw:.0f}]")
    ear.t_ve = ear.t_wh = 0.0
    return meta, ref


def main(m, have_names, me):
    """m = loaded Chatterbox (for its voice encoder); have_names = episodes already imported with timings;
    me = your voice vector. Returns (all project lines, new refs)."""
    R = dict({"speaker_min_similarity": 0.72, "asr_min_logprob": -0.45}, **(cfg.C.get("recordings") or {}))
    fs = [f for f in files() if norm_name(f) not in have_names]
    os.makedirs(PROJ, exist_ok=True)
    todo = [f for f in fs if not os.path.exists(os.path.join(PROJ, norm_name(f) + ".jsonl"))]
    say(f"\n== your other recordings: {len(fs)} projects ({len(fs) - len(todo)} already done, {len(todo)} to do)")
    if todo:                              # only the voice encoder is needed here: the rest of Chatterbox
        import torch                      # leaves the graphics card so Whisper has room to run fast
        m.t3.to("cpu"); m.s3gen.to("cpu")
        torch.cuda.empty_cache()
    ear = Ear(m) if todo else None
    refs = []
    t0 = time.time()
    for k, f in enumerate(todo):
        say(f"\n[{k + 1}/{len(todo)}] {os.path.basename(f)}"
            + (f"   (~{(time.time() - t0) / k * (len(todo) - k) / 60:.0f} min left)" if k else ""))
        try:
            meta, ref = process(f, ear, me, R)
        except Exception as e:
            say(f"   !! could not read it: {repr(e)[:200]} - skipped")
            meta, ref = [], None
        if ref:
            refs.append(ref)
        p = os.path.join(PROJ, norm_name(f) + ".jsonl")
        with open(p + ".part", "w", encoding="utf-8") as fh:
            for x in meta:
                fh.write(json.dumps(x, ensure_ascii=False) + "\n")
        os.replace(p + ".part", p)
    allm = []
    for f in fs:
        p = os.path.join(PROJ, norm_name(f) + ".jsonl")
        if os.path.exists(p):
            allm += [json.loads(l) for l in open(p, encoding="utf-8") if l.strip()]
    return allm, refs
