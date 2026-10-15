"""
sleep_pipeline.py - narration audio -> calm looping visuals + softly fading English key lines.
Runs on ComfyUI's embedded Python (torch+CUDA, transformers, PyAV, Pillow) - nothing extra installed.

  1. transcribe  Whisper large-v3-turbo via transformers on the GPU      -> work/<name>/transcript.json
  2. pick lines  Ollama (configured model; Gemma by default) picks calming lines      -> work/<name>/captions.json
                 (edit captions.json freely; it is reused on the next run)
  3. loop        seamless background loop, rendered once per style        -> loops/
  4. video       loop + captions (Pillow) + audio (AAC), encoded by PyAV   -> output/

Usage:
  python -s sleep_pipeline.py "D:\\Audacity\\Sukoon_Sleep Therapy.mp3"
  ... --style nebula --palette midnight --max-lines 15 --llm Qwen3.6-35B-A3B-NVFP4 --redo-captions --preview 120
"""
import sys as _sys
for _s in (_sys.stdout, _sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")   # Hindi text in logs/consoles on Windows
    except Exception:
        pass
import argparse, hashlib, json, os, re, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import sv_av
from sv_av import HERE, ENGINE, HF_CACHE, WHISPER_ID

# ------------------------------------------------------------------ console output
T0 = time.time()
NOISE = re.compile(r"it/s\]|Loading weights|Fetching \d+ files|Materializing param|FutureWarning|UserWarning|"
                   r"warnings\.warn|attention_mask|max_new_tokens|logits processor|generation_config|Using `|"
                   r"\[notice\]|^\s*$")


PLAN = {}                    # estimated minutes still needed per step (filled in by main, refined as we go)
EN = False                   # an English episode (--language english): English prompts, words, title, branding


def _title(text):
    try:
        import ctypes
        ctypes.windll.kernel32.SetConsoleTitleW(text)
    except Exception:
        pass


def eta_line(n):
    left = sum(v for k, v in PLAN.items() if k >= n)
    fin = time.localtime(time.time() + left * 60)
    return left, time.strftime("%H:%M", fin)


def episode_label(name):
    """'Sukoon 8.0 - Trip To Bachpan' -> 'सुकून 8.0' (just 'सुकून' when the folder has no number; 'Sukoon' in English)."""
    word = "Sukoon" if EN else "सुकून"
    m = re.search(r"(?i)(?:sukoon|sukun|सुकून)[\s_\-]*(\d+(?:[._]\d+)?)", name or "")
    return word + " " + m.group(1).replace("_", ".") if m else word


def save_json(obj, path):
    """Write-then-rename: a crash can never leave a half-written file behind."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def source_fingerprint(audio, script):
    """Cache identity for this episode's actual audio and optional trusted script."""
    def digest(path):
        if not path or not os.path.isfile(path):
            return ""
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for block in iter(lambda: f.read(1024 * 1024), b""):
                h.update(block)
        return h.hexdigest()
    return {"audio": os.path.abspath(audio), "audio_sha256": digest(audio),
            "script": os.path.abspath(script) if script else "", "script_sha256": digest(script)}


def prepare_work_cache(work, identity):
    """Reuse transcript artifacts only when they belong to this exact audio/script pair."""
    cache_files = ("transcript.json", "transcript_turbo.json", "transcript_hi.json",
                   "transcript_script.json", "captions.json", "moments.json", "title.json")
    has_cache = any(os.path.exists(os.path.join(work, f)) for f in cache_files) or \
                os.path.isdir(os.path.join(work, "gapfill"))
    marker = os.path.join(work, "source_fingerprint.json")
    try:
        previous = json.load(open(marker, encoding="utf-8"))
    except (OSError, ValueError):
        previous = None
    if os.path.isdir(work) and has_cache and previous != identity:
        base = work + "_stale_" + time.strftime("%Y%m%d_%H%M%S")
        archive, n = base, 1
        while os.path.exists(archive):
            archive, n = base + f"_{n}", n + 1
        os.replace(work, archive)
        info(f"saved transcript cache had different or unknown inputs; preserved it in {os.path.basename(archive)} and starting clean")
    os.makedirs(work, exist_ok=True)
    save_json(identity, marker)


def step(n, text):
    el = time.time() - T0
    left, fin = eta_line(n)
    print(f"\n[{n}/6] {text}   (elapsed {int(el // 60)}:{int(el % 60):02d}"
          f"  |  expected finish ~{fin}, about {left:.0f} min left)", flush=True)
    _title(f"Sukoon video - step {n}/6 - ready ~{fin}")


def info(text):
    print(f"      {text}", flush=True)


def run_live(cmd, env=None):
    """Run a helper process and show its useful lines (library progress bars/warnings are hidden)."""
    import subprocess
    e = dict(os.environ, PYTHONIOENCODING="utf-8", HF_HUB_DISABLE_PROGRESS_BARS="1",
             TRANSFORMERS_VERBOSITY="error", PYTHONWARNINGS="ignore", **(env or {}))
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=e,
                         text=True, encoding="utf-8", errors="replace", bufsize=1)
    for line in p.stdout:
        for part in line.replace("\r", "\n").split("\n"):
            if part.strip() and not NOISE.search(part):
                print("      " + part.strip()[:150], flush=True)
    return p.wait()


JUNK = {"", "संगीत", "music", "[music]", "(music)", "♪", "thank you", "thanks for watching",
        "धन्यवाद", "subtitles by", "please subscribe"}


def ts(t):
    h, rem = divmod(t, 3600); m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


FW_ID = "mobiuslabsgmbh/faster-whisper-large-v3-turbo"   # faster-whisper (CTranslate2) build of the same model


def _complete(repo):
    d = os.path.join(HF_CACHE, "models--" + repo.replace("/", "--"), "blobs")
    if not os.path.isdir(d):
        return False
    files = os.listdir(d)
    return bool(files) and not any(f.endswith(".incomplete") for f in files) and \
        max(os.path.getsize(os.path.join(d, f)) for f in files) > 500e6


def whisper_backend():
    """Prefer faster-whisper (already installed in .venv), else transformers (ComfyUI's Python)."""
    try:
        import faster_whisper  # noqa
        return "faster-whisper", FW_ID
    except ImportError:
        return "transformers", WHISPER_ID


def whisper_cached():
    return _complete(whisper_backend()[1])


def transcribe_fw(audio, language):
    import glob, site
    for sp in site.getsitepackages():                    # pip-installed cuBLAS/cuDNN DLLs
        for d in glob.glob(os.path.join(sp, "nvidia", "*", "bin")):
            os.add_dll_directory(d) if hasattr(os, "add_dll_directory") else None
            os.environ["PATH"] = d + os.pathsep + os.environ["PATH"]
    from faster_whisper import WhisperModel
    lang = {"hindi": "hi", "english": "en"}.get(language, language)
    try:
        m = WhisperModel("large-v3-turbo", device="cuda", compute_type="float16", download_root=HF_CACHE)
        pass
    except Exception as e:
        print(f"      GPU failed ({e}); using CPU")
        m = WhisperModel("large-v3-turbo", device="cpu", compute_type="int8", download_root=HF_CACHE)
    # decode ourselves: faster-whisper 1.2.1's own decoder breaks with PyAV 19 (metadata_errors kwarg)
    wav = sv_av.load_audio_16k_mono(audio)
    segs, _ = m.transcribe(wav, language=lang, vad_filter=True, word_timestamps=True,
                           vad_parameters={"min_silence_duration_ms": 700}, beam_size=5)
    # split long VAD blocks into phrases at punctuation, using word timestamps,
    # so each caption appears exactly when its line is spoken
    out = []
    for s in segs:
        words = list(s.words or [])
        if not words:
            out.append((s.start, s.end, s.text, [])); continue
        cur, st, cw = [], None, []
        for w in words:
            if st is None:
                st = w.start
            cur.append(w.word)
            cw.append({"w": w.word.strip(), "start": round(float(w.start), 2), "end": round(float(w.end), 2),
                       "p": round(float(w.probability), 3)})
            txt = "".join(cur).strip()
            end_punct = txt[-1:] in ",.?!।|" if txt else False
            if (end_punct and w.end - st >= 2.5) or w.end - st >= 12:
                out.append((st, w.end, txt, cw)); cur, st, cw = [], None, []
        if cur:
            out.append((st, words[-1].end, "".join(cur).strip(), cw))
    return out


def transcribe_hf(audio, language):
    os.environ.setdefault("HF_HUB_CACHE", HF_CACHE)
    import torch
    from transformers import pipeline
    dev = "cuda:0" if torch.cuda.is_available() else "cpu"
    dt = torch.float16 if dev != "cpu" else torch.float32
    print(f"      transformers Whisper on {dev}")
    try:
        asr = pipeline("automatic-speech-recognition", model=WHISPER_ID, dtype=dt, device=dev)
    except TypeError:
        asr = pipeline("automatic-speech-recognition", model=WHISPER_ID, torch_dtype=dt, device=dev)
    wav = sv_av.load_audio_16k_mono(audio)
    res = asr({"raw": wav, "sampling_rate": 16000}, chunk_length_s=30, batch_size=8, return_timestamps=True,
              generate_kwargs={"language": language, "task": "transcribe"})
    out = [(c["timestamp"][0], c["timestamp"][1], c["text"]) for c in res.get("chunks", [])]
    del asr
    if dev != "cpu":
        torch.cuda.empty_cache()
    return out


# ------------------------------------------------------------------ 1. transcribe
def transcribe(audio, out_json, language, yes):
    if os.path.exists(out_json):
        old = json.load(open(out_json, encoding="utf-8"))
        if any(s.get("words") for s in old):                     # older files have no word timings
            info(f"quick transcript exists, reusing")
            return old
    backend, repo = whisper_backend()
    if not whisper_cached() and not yes:
        ans = input(f"[1/4] Whisper model ({repo}) not fully downloaded (~1.6 GB -> {HF_CACHE}). Download now? [y/N] ")
        if ans.strip().lower() != "y":
            sys.exit("Stopped - nothing downloaded.")
    info(f"quick Whisper turbo pass ({backend}) ...")
    t0 = time.time()
    raw = transcribe_fw(audio, language) if backend == "faster-whisper" else transcribe_hf(audio, language)
    out, prev = [], None
    for item in raw:
        st, en, text = item[:3]
        words = [w for w in (item[3] if len(item) > 3 else []) if w["w"]]
        text = text.strip()
        if st is None:
            continue
        en = en if en is not None else st + 4
        key = re.sub(r"[\W_]+", " ", text.lower()).strip()
        if key in JUNK or len(key) < 3 or key == prev:      # music hallucinations / repeats
            continue
        prev = key
        out.append({"id": len(out), "start": round(float(st), 2), "end": round(float(en), 2), "text": text,
                    "words": words})
    save_json(out, out_json)
    info(f"quick transcript ready: {len(out)} phrases in {time.time()-t0:.0f}s")
    return out


# ------------------------------------------------------------------ 2. pick lines
PROMPT = """You are the writer of "Sukoon", a calm Hindi sleep-therapy channel. Below is a timestamped
transcript of the Hindi voice-over (format: [id] text). It comes from speech recognition, so words
are often misspelled - understand the meaning, do not copy the spelling.

Choose the {n} passages whose MEANING is most peaceful and soothing (rest, safety, breath, softness,
nature, letting go, self-kindness). Spread them across the whole recording. NEVER choose passages about
stress, burden, duty, fear, problems, greetings, or likes/subscribe/channel talk.

For each chosen passage WRITE a new on-screen line:
  "hi": 3 to 7 words of simple, standard, correctly spelled Hindi (Devanagari) that gently expresses
        the passage's peaceful meaning - like a soft poem line. Use common everyday words.
  "en": its English meaning.

Reply ONLY with JSON: {{"lines": [{{"id": <passage id>, "hi": "...", "en": "..."}}, ...]}}

Transcript:
{transcript}"""


MIN_GAP = 25.0
PROOF = """You are a careful Hindi editor for a calm sleep-meditation video. Check this on-screen line:

  {line}

Make sure every word is correctly spelled standard Hindi (Devanagari), the grammar is right, and it is
3 to 7 words. Keep it gentle and simple. Then judge it: "ok" is true only if it is a complete,
meaningful, PEACEFUL thought (no stress, burden, fear, duty, sadness; nothing about video/channel).
Reply ONLY with JSON: {{"hi": "...", "ok": true}}"""


def thought_units(segments, min_len=7.0, max_len=15.0, gap=1.5):
    """Merge short phrases into complete thoughts (keeps the start time of the first phrase)."""
    units, cur = [], None
    for sg in segments:
        if cur and (sg["start"] - cur["end"] > gap or sg["end"] - cur["start"] > max_len):
            units.append(cur); cur = None
        if cur is None:
            cur = {"id": len(units), "start": sg["start"], "end": sg["end"], "text": sg["text"], "parts": [sg]}
        else:
            cur["end"] = sg["end"]; cur["text"] += " " + sg["text"]; cur["parts"].append(sg)
        if cur["end"] - cur["start"] >= min_len and cur["text"].rstrip()[-1:] in "।.?!":
            units.append(cur); cur = None
    if cur:
        units.append(cur)
    for i, u in enumerate(units):
        u["id"] = i
    return units


def _bigrams(t):
    t = re.sub(r"\s+", "", t)
    return {t[i:i + 2] for i in range(len(t) - 1)}


def align_start(unit, line):
    """Start time of the phrase inside a thought-unit that best matches the chosen line."""
    parts = unit.get("parts") or [unit]
    lb = _bigrams(line)
    best = max(parts, key=lambda p: len(lb & _bigrams(p["text"])) / (1 + len(_bigrams(p["text"])) ** 0.5))
    return best["start"], max(best["end"], best["start"] + 4)


def pick_lines(segments, out_json, llm, max_lines, total, redo):
    if os.path.exists(out_json) and not redo:
        print(f"[2/4] captions exist, reusing {out_json} (edit freely, or --redo-captions)")
        return json.load(open(out_json, encoding="utf-8"))
    n = max(6, min(max_lines, int(total / 45)))      # ask for more, spacing filter keeps ~1 per 25-60 s
    segments = thought_units(segments)
    by_id = {s["id"]: s for s in segments}
    chosen, chunk = [], 120
    parts = [segments[i:i + chunk] for i in range(0, len(segments), chunk)] or [[]]
    for k, part in enumerate(parts):
        want = max(1, round(n * len(part) / max(1, len(segments))))
        text = "\n".join(f"[{s['id']}] {s['text']}" for s in part)
        print(f"[2/4] asking {llm} for {want} lines (part {k+1}/{len(parts)}) ...")
        try:
            raw = sv_av.local_chat(llm, PROMPT.format(n=want, transcript=text), think=True, timeout=1500)
        except Exception as e:
            sys.exit(f"Could not reach Ollama - is its server running? ({e})")
        try:
            lines = json.loads(raw).get("lines", [])
        except json.JSONDecodeError:
            lines = [json.loads(m) for m in re.findall(r"\{[^{}]*\}", raw)]
        for l in lines:
            try:
                sid = int(l.get("id"))
            except (TypeError, ValueError):
                continue
            en = str(l.get("en", "")).strip().strip('"')
            hi = str(l.get("hi", "")).strip().strip('"').strip("।").strip()
            if sid in by_id and (hi or en):
                s = by_id[sid]
                st, en_t = align_start(s, hi or s["text"])
                chosen.append({"id": sid, "start": st, "end": en_t, "src": s["text"],
                               "hi": hi or s["text"], "en": en})
    cta_hi = re.compile(r"लाइक|सब्सक्राइब|कमेंट|चैनल|बटन|शेयर|वीडियो")
    cta_en = re.compile(r"\b(a|the) like\b|\blike (button|it|this video)\b|subscrib|comment|channel|\bbutton\b|\bbell\b|share (this|it)", re.I)
    chosen = [c for c in chosen if not (cta_en.search(c["en"]) or cta_hi.search(c["hi"]) or cta_hi.search(c.get("src", "")))]
    chosen = sorted({c["id"]: c for c in chosen}.values(), key=lambda c: c["start"])
    # keep it calm: at least MIN_GAP seconds between lines
    spaced = []
    for c in chosen:
        if not spaced or c["start"] - spaced[-1]["start"] >= MIN_GAP:
            spaced.append(c)
    chosen = spaced
    # proofread pass: correct spelling + keep it short (one small, focused request per line)
    for c in chosen:
        try:
            fixed = sv_av.local_chat(llm, PROOF.format(line=c["hi"]), json_mode=True, num_ctx=4096,
                                      timeout=600, think=True)
            js = json.loads(fixed)
            hi = str(js.get("hi", "")).strip().strip('"').strip("।").strip()
            if hi:
                c["hi"] = hi
            c["ok"] = bool(js.get("ok", True)) and 2 <= len(c["hi"].split()) <= 9
        except Exception as e:
            print(f"      (proofread skipped: {e})")
    dropped = [c for c in chosen if not c.get("ok", True)]
    chosen = [c for c in chosen if c.get("ok", True)]
    for c in dropped:
        print(f"      dropped (not a clear standalone line): {c['hi']}")
    save_json(chosen, out_json)
    for c in chosen:
        print(f"      {ts(c['start'])}  {c['hi']}   ({c['en']})")
    print(f"      {len(chosen)} lines -> {out_json}")
    return chosen


# ============================================================== Mukhtalif word-by-word typography
import paths  # noqa: E402
COMFY_PY = paths.GPU_PYTHON

MOMENTS_PROMPT = """You are the editor of "Mukhtalif Awaaz", a calm Hindi sleep-therapy channel. Below are numbered
THOUGHTS from one episode's narration, transcribed automatically (some contain recognition mistakes).
Score EVERY thought 0-10 for how good it would be as on-screen typography:
 10 = a complete, meaningful line that carries the heart of the episode (a feeling the listener recognises,
      a gentle insight, a vivid image) AND reads as correct, natural Hindi.
  0 = a fragment, filler, an instruction (like/subscribe/comment), or garbled / nonsensical words.
Also give the KEY WORD of each thought: the single emotional centre - a noun or feeling word copied exactly
from the thought. Never a pronoun or helper word (तुम, तुम्हें, मैं, है, हो, नहीं, था, को, में, कि, ना).
If ANY word looks mis-heard or the line does not make complete sense on its own, score it 3 or lower.
Prefer an image or feeling word (मुस्कान, दोस्त, सुकून, बचपन, नींद, खामोशी); never an English word written in
Hindi (प्रॉब्लम, टाइम, ऑफिस) and never a plain verb.
Examples: "इस खामोशी में जो सबसे तेज सुनाई देती है" -> key "खामोशी"; "तुमने खुद को मशीन बना दिया" -> key "मशीन".
Reply ONLY with JSON: {{"scores": [{{"i": 0, "s": 7, "key": "..."}}, ...]}} - one entry per thought.

{lines}"""


MOMENTS_PROMPT_EN = """You are the editor of "Mukhtalif Awaaz", a calm English sleep-therapy channel (Sukoon). Below are
numbered THOUGHTS from one episode's narration, transcribed automatically (some contain recognition mistakes).
Score EVERY thought 0-10 for how good it would be as on-screen typography:
 10 = a complete, meaningful line that carries the heart of the episode (a feeling the listener recognises,
      a gentle insight, a vivid image) AND reads as correct, natural English.
  0 = a fragment, filler, an instruction (like/subscribe/comment), or garbled / nonsensical words.
Also give the KEY WORD of each thought: the single emotional centre - a noun or feeling word copied exactly
from the thought. Never a pronoun or helper word (you, I, it, is, are, was, the, a, to, and, of, not).
Prefer an image or feeling word (smile, friend, silence, childhood, sleep, rain); never a plain verb.
Examples: "in this silence, the loudest thing is your own heart" -> key "silence";
"you turned yourself into a machine" -> key "machine".
Reply ONLY with JSON: {{"scores": [{{"i": 0, "s": 7, "key": "..."}}, ...]}} - one entry per thought.

{lines}"""

CHECK_PROMPT_EN = """Is this a complete, grammatically correct and meaningful English sentence, with every word a real,
correctly used word (no mis-heard or nonsense words)? Line: {line}
Reply ONLY with JSON: {{"ok": true}} or {{"ok": false}}"""

TITLE_PROMPT_EN = """Here is a calm English sleep-therapy voice-over (beginning, middle and end):
{text}
Suggest 3 different titles for this episode: gentle, poetic, 2 to 5 words each, about what THIS episode is really
about (its subject and feeling), no quotes, no channel name, not generic words like "Calm Night" or "Sleep".
Reply ONLY with JSON: {{"titles": ["...", "...", "..."]}}"""

STOP_EN = {"you", "your", "yours", "i", "me", "my", "we", "us", "our", "it", "its", "is", "are", "was", "were", "be",
           "been", "am", "the", "a", "an", "to", "and", "or", "but", "of", "in", "on", "at", "for", "with", "not", "no",
           "so", "just", "that", "this", "these", "those", "there", "here", "then", "when", "if", "as", "by", "from",
           "all", "some", "one", "can", "will", "would", "could", "do", "does", "did", "have", "has", "had", "let",
           "now", "very", "too", "into", "out", "up", "down", "over", "what", "who", "how", "why", "they", "them",
           "he", "she", "him", "her", "his", "even", "still", "only", "more", "much", "really", "maybe"}


def _bare(w):
    return re.sub(r"[^\w\u0900-\u097F']", "", w or "").lower() if EN else w


def _stop(w):
    return _bare(w) in STOP_EN if EN else w in STOP


AUDIO_EXT = (".wav", ".mp3", ".m4a", ".flac", ".aac", ".ogg")
IMG_EXT = (".png", ".jpg", ".jpeg", ".webp")


def episode_inputs(path):
    """A dropped episode FOLDER (files found by type) or a single audio file (old way: neighbours by name)."""
    path = os.path.abspath(path)
    if os.path.isdir(path):
        fs = [os.path.join(path, f) for f in os.listdir(path)]
        aud = sorted([f for f in fs if f.lower().endswith(AUDIO_EXT)], key=os.path.getsize, reverse=True)
        aup = [f for f in fs if f.lower().endswith(".aup3")]
        if not aud and aup:                       # no exported mp3/wav: mix the Audacity project ourselves
            import subprocess
            mixed = os.path.splitext(aup[0])[0] + "_mix.wav"
            info("no exported audio in the folder - making the mix from the Audacity project ...")
            r = subprocess.run([sys.executable, "-s", os.path.join(ENGINE, "aup3_mix.py"), aup[0], mixed])
            if r.returncode == 0 and os.path.exists(mixed):
                aud = [mixed]
        if not aud:
            sys.exit(f"No audio (.mp3/.wav) found in {path}")
        not_script = ("readme", "how_to", "title", "artwork", "transcript")   # these .txt files are never the script
        txt = [f for f in fs if f.lower().endswith(".txt") and not os.path.basename(f).lower().startswith(not_script)]
        txt.sort(key=lambda f: (os.path.basename(f).lower() != "script.txt", -os.path.getsize(f)))
        img = [f for f in fs if f.lower().endswith(IMG_EXT)]
        img.sort(key=os.path.getctime, reverse=True)       # several pictures: the one added last
        aw = os.path.join(path, "artwork.txt")              # ...unless artwork.txt names the one to use
        if os.path.exists(aw):
            want = open(aw, encoding="utf-8-sig").read().strip().splitlines()
            want = os.path.join(path, want[0].strip()) if want and want[0].strip() else None
            if want and os.path.exists(want):
                img = [want] + [f for f in img if f != want]
        name = os.path.basename(path.rstrip("\\/"))
        return dict(audio=aud[0], aup=aup[0] if aup else None, script=txt[0] if txt else None,
                    image=img[0] if img else None, name=name, folder=True, path=path)
    stem = os.path.splitext(path)[0]
    d = os.path.dirname(path)
    script = next((c for c in (stem + ".txt", os.path.join(d, "script.txt")) if os.path.exists(c)), None)
    aup = stem + ".aup3" if os.path.exists(stem + ".aup3") else None
    return dict(audio=path, aup=aup, script=script, image=None, name=os.path.splitext(os.path.basename(path))[0],
                folder=False)


def script_segments(script_path, anchor_words, work):
    """Text from the narrator's script, timing from the recogniser (see script_align.py)."""
    import script_align as sa
    out = os.path.join(work, "transcript_script.json")
    d0 = os.path.dirname(script_path)
    newest = max([os.path.getmtime(script_path)] + [os.path.getmtime(os.path.join(d0, f)) for f in os.listdir(d0)
                                                     if re.match(r"(?i)script.*\.txt$", f)])
    if os.path.exists(out) and os.path.getmtime(out) > newest:
        info("script already lined up with your voice, reusing")
        return json.load(open(out, encoding="utf-8"))
    txt = open(script_path, encoding="utf-8-sig", errors="replace").read()
    versions = sa.split_versions(txt)
    d = os.path.dirname(script_path)                       # extra drafts as separate files: script2.txt, ...
    for f in sorted(os.listdir(d)):
        fp = os.path.join(d, f)
        if fp != script_path and re.match(r"(?i)script.*\.txt$", f):
            versions += sa.split_versions(open(fp, encoding="utf-8-sig", errors="replace").read())
    segs = sa.align_versions(versions, anchor_words)
    save_json(segs, out)
    n_used = sum(len(s["words"]) for s in segs)
    if len(versions) > 1:
        from collections import Counter
        use = Counter(s.get("version") for s in segs)
        info(f"{len(versions)} script versions found - lines used from each: "
             + ", ".join(f"version {v}: {n}" for v, n in sorted(use.items())))
        info(f"{n_used} script words matched to your voice")
    else:
        n_script = sum(len(p) for s in sa.parse_script(txt) for p in s)
        info(f"your script: {n_used} of {n_script} words matched to your voice ({100 * n_used // max(1, n_script)}%)"
             f" - the rest was not read aloud / not found")
    return segs


def gap_fill(src, turbo, segs, work, min_gap=25.0):
    """Parts you SAID that are not in the pasted script (a different script version, ad-libs, a story told
    differently) get their words from the Hindi speech model - only those parts, so it stays quick."""
    import subprocess
    if not turbo or EN:                                    # the gap filler is the Hindi speech model
        return segs
    cov = sorted((float(s["start"]) - 1.0, float(s["end"]) + 1.0) for s in segs)
    def covered(a, b):
        return sum(max(0.0, min(b, y) - max(a, x)) for x, y in cov) > 0.5 * (b - a)
    spans = []
    for t in turbo:
        a, b = float(t["start"]), float(t["end"])
        if covered(a, b) or len(t.get("text", "").split()) < 2:
            continue
        if spans and a - spans[-1][1] < 4.0:
            spans[-1][1] = b
        else:
            spans.append([a, b])
    spans = [(a, b) for a, b in spans if b - a >= min_gap]
    if not spans:
        return segs
    tot = sum(b - a for a, b in spans)
    where = ", ".join(f"{int(a // 60)}:{int(a % 60):02d}-{int(b // 60)}:{int(b % 60):02d}" for a, b in spans[:6])
    info(f"!! {tot / 60:.1f} min of what you said is NOT in this script ({where}) - different script version?")
    PLAN[4] = PLAN.get(4, 0) + 1.5 + 1.8 * tot / 60            # keep the finish-time estimate honest
    gdir = os.path.join(work, "gapfill")
    out = os.path.join(gdir, "transcript_hi.json")
    stamp = json.dumps([[round(a, 1), round(b, 1)] for a, b in spans])
    sp = os.path.join(gdir, "spans.json")
    if not (os.path.exists(out) and os.path.exists(sp) and open(sp).read() == stamp):
        os.makedirs(gdir, exist_ok=True)
        info("   filling those parts from your voice with the Hindi speech model ...")
        ff = sv_av.ffmpeg_exe() or "ffmpeg"
        raw = subprocess.run([ff, "-v", "error", "-i", src, "-ac", "1", "-ar", "16000", "-f", "f32le", "-"],
                             capture_output=True, check=True).stdout
        x = np.frombuffer(raw, np.float32).copy()
        keep = np.zeros(len(x), bool)
        for a, b in spans:
            keep[int(max(0, a - 0.5) * 16000):int((b + 0.5) * 16000)] = True
        x[~keep] = 0.0                                     # same timeline, everything else silent
        gw = os.path.join(gdir, "gaps.wav")
        p = subprocess.Popen([ff, "-y", "-v", "error", "-f", "f32le", "-ar", "16000", "-ac", "1", "-i", "-",
                              "-c:a", "pcm_s16le", gw], stdin=subprocess.PIPE)
        p.stdin.write(x.astype("<f4").tobytes()); p.stdin.close(); p.wait()
        py = COMFY_PY if os.path.exists(COMFY_PY) else sys.executable
        if os.path.exists(out):
            os.remove(out)
        rc = run_live([py, "-s", os.path.join(ENGINE, "asr_hindi.py"), gw, gdir])
        if rc or not os.path.exists(out):
            info("   (could not fill them - those parts will have no on-screen words)")
            return segs
        open(sp, "w", encoding="utf-8").write(stamp)
    extra = [dict(s, source="voice") for s in clean_segments(json.load(open(out, encoding="utf-8"))["segments"])
             if any(a - 1 <= float(s["start"]) <= b + 1 for a, b in spans)]
    info(f"   added {len(extra)} lines from your voice for those parts")
    return sorted(segs + extra, key=lambda s: float(s["start"]))


def pick_llm():
    ensure_local_ai()
    try:
        tags = sv_av.local_tags()
    except Exception:
        return sv_av.paths.CFG.get("prompt_model") or sv_av.paths.CFG.get("llm", "qwen3:8b")
    preferred = sv_av.paths.CFG.get("prompt_model") or sv_av.paths.CFG.get("llm", "qwen3:8b")
    return preferred if preferred in tags else (tags[0] if tags else preferred)


def ensure_local_ai():
    """Check that the selected local model API is reachable."""
    import urllib.request
    url = sv_av.OLLAMA + "/api/tags"
    def up():
        try:
            urllib.request.urlopen(url, timeout=3); return True
        except Exception:
            return False
    if up():
        return
    print("      !! Ollama is not running - start it for AI-generated moment choices")


FIX_PROMPT = """This Hindi line was transcribed automatically from a calm voice-over and may contain small
speech-recognition mistakes (a wrong letter, missing nukta or chandrabindu, a similar-sounding word).
Line: {line}
Correct ONLY clear mistakes. Keep exactly the same number of words, in the same order, in Devanagari.
Do not rephrase, add or remove words. Reply ONLY with JSON: {{"words": ["...", "..."]}}"""


def _close(a, b):
    """Accept only nukta / chandrabindu / anusvara fixes (रोज -> रोज़). The local LLM is not reliable
    enough in Hindi for anything bigger - it 'corrects' grammar and changes the narrator's words."""
    marks = "\u093c\u0901\u0902"
    strip = lambda t: "".join(c for c in t if c not in marks)
    return a == b or strip(a) == strip(b)


def fix_spelling(moments, llm):
    if EN:
        return                                             # English: the script words are used as they are
    for m in moments:
        try:
            raw = sv_av.local_chat(llm, FIX_PROMPT.format(line=m["text"]), num_ctx=4096, timeout=600, think=False)
            new = [str(w).strip() for w in json.loads(raw).get("words", [])]
        except Exception as e:
            print(f"      (spelling check skipped: {e})"); continue
        if len(new) != len(m["words"]):
            continue
        changed = []
        for w, nw in zip(m["words"], new):
            if nw and nw != w["w"] and _close(w["w"], nw):
                changed.append(f"{w['w']}->{nw}"); w["w"] = nw
        if changed:
            m["text"] = " ".join(w["w"] for w in m["words"])
            m["key_word"] = m["words"][m["key"]]["w"]
            print(f"      spelling: {', '.join(changed)}")


# Whisper-family models "hear" Doordarshan/AIR news bulletins in music-only stretches - drop those.
HALLU = re.compile(r"समाचार|बुलेटिन|आकाशवाणी|प्रादेशिक|नमस्कार|शून्य|प्रधानमंत्री|नरेन्द्र|नरेंद्र|मोदी|योगी|"
                   r"मुख्यमंत्री|अर्थव्यवस्था|सब्सक्राइब|लखनऊ|अध्यक्षता|पार्टी|सुशील|तिवारी|उन्होंने")


# Channel vocabulary + common ड़/ढ़ words the speech model writes without the nukta. Applied automatically.
LEXICON = {
    "सुकूम": "सुकून", "सुकूल": "सुकून", "सकून": "सुकून", "सुकुन": "सुकून", "मुख्तलिफ": "मुख़्तलिफ़",
    "छोड": "छोड़", "छोडो": "छोड़ो", "छोडना": "छोड़ना", "छोडकर": "छोड़कर", "छोड़ा": "छोड़ा",
    "पड": "पड़", "पडे": "पड़े", "पडा": "पड़ा", "पडी": "पड़ी", "पडता": "पड़ता", "पडती": "पड़ती", "पडेगा": "पड़ेगा",
    "बडा": "बड़ा", "बडे": "बड़े", "बडी": "बड़ी", "थोडा": "थोड़ा", "थोडी": "थोड़ी", "थोडे": "थोड़े",
    "जोड": "जोड़", "मोड": "मोड़", "सडक": "सड़क", "गाडी": "गाड़ी", "गाडियों": "गाड़ियों", "लडाई": "लड़ाई",
    "पेड": "पेड़", "भीड": "भीड़", "दौड": "दौड़", "पढ": "पढ़", "पढना": "पढ़ना", "बढती": "बढ़ती", "बढता": "बढ़ता",
    "चढ": "चढ़", "कपडे": "कपड़े", "लडका": "लड़का", "लडकी": "लड़की", "जरूरत": "ज़रूरत", "जिंदगी": "ज़िंदगी",
}


def clean_segments(segs):
    for s in segs:                                   # vocabulary fixes keep word count + timing unchanged
        for w in s["words"]:
            w["w"] = LEXICON.get(w["w"], w["w"])
        s["text"] = " ".join(w["w"] for w in s["words"])
    flagged = [any(HALLU.search(w["w"]) for w in s["words"]) for s in segs]
    fl_t = [s["start"] for s, f in zip(segs, flagged) if f]
    out, seen = [], set()
    for s, f in zip(segs, flagged):
        words = s["words"]
        if f:
            cut = next(i for i, w in enumerate(words) if HALLU.search(w["w"]))
            words = words[:cut]                       # keep the real words before the hallucination
            if len(words) < 3:
                continue
        elif any(0 < s["start"] - t < 45 for t in fl_t) and any(0 < t - s["start"] < 45 for t in fl_t):
            continue                                  # sandwiched between hallucinations = music-only stretch
        if not words:
            continue
        dur = max(0.3, words[-1]["end"] - words[0]["start"])
        text = " ".join(w["w"] for w in words)
        if len(words) / dur > 4.5 or text in seen:    # impossible speaking rate or repeated line
            continue
        seen.add(text)
        out.append(dict(s, words=words, text=text, start=words[0]["start"], end=words[-1]["end"]))
    return out


def save_auto_transcript(segs, out, src_json, pause=4.0):
    """No script: the recognised words as plain text in the episode folder (no timings), ready to correct
    (e.g. in Gemini) and save as script.txt - the next run then lines up YOUR corrected words with your voice.
    A new paragraph at every longer pause. Not re-written unless the recognition itself was redone."""
    if os.path.exists(out) and os.path.exists(src_json) and os.path.getmtime(out) >= os.path.getmtime(src_json):
        return
    lines, prev = [], None
    for sg in sorted(segs, key=lambda x: float(x["start"])):
        if prev is not None and float(sg["start"]) - prev >= pause:
            lines.append("")
        lines.append(sg["text"].strip())
        prev = float(sg["end"])
    try:
        with open(out, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        info(f"recognised words saved as {os.path.basename(out)} in the episode folder - correct them and save as"
             f" script.txt for perfect words next time")
    except OSError as e:
        info(f"(could not save {os.path.basename(out)}: {e})")


def voice_track(audio, work, aup=None):
    """Read the voice track from the episode's Audacity project (read-only), if there is one."""
    aup = aup or os.path.splitext(audio)[0] + ".aup3"
    out = os.path.join(work, "voice.wav")
    if os.path.exists(out):
        return out
    if not os.path.exists(aup):
        print("      (no Audacity project next to the audio - using the mixed audio)")
        return None
    print(f"      reading the voice track from {os.path.basename(aup)} (read-only) ...")
    rc = run_live([sys.executable, "-s", os.path.join(ENGINE, "aup3_voice.py"), aup, out])
    if rc or not os.path.exists(out):
        print("      (could not read the project - using the mixed audio)")
        return None
    return out


def transcribe_hi(audio, work):
    """Hindi-tuned Whisper on the clean voice track (or the mix) + pause-based lines + word timing/confidence."""
    out = os.path.join(work, "transcript_hi.json")
    if not os.path.exists(out):
        src = voice_track(audio, work) or audio
        py = COMFY_PY if os.path.exists(COMFY_PY) else sys.executable
        info(f"Hindi speech model on the GPU, listening to {os.path.basename(src)} ...")
        rc = run_live([py, "-s", os.path.join(ENGINE, "asr_hindi.py"), src, work])
        if rc or not os.path.exists(out):
            sys.exit("Hindi transcription failed - see the messages above.")
    else:
        info("Hindi transcript exists, reusing it (delete work\\<name>\\transcript_hi.json or use --redo-transcript to redo)")
    segs = json.load(open(out, encoding="utf-8"))["segments"]
    cleaned = clean_segments(segs)
    if len(cleaned) < len(segs):
        info(f"ignored {len(segs) - len(cleaned)} imagined/garbled lines (music, breaths)")
    return cleaned


STOP = {"तुम", "तुम्हें", "तुम्हारे", "तुम्हारी", "तुम्हारा", "मैं", "मुझे", "हम", "है", "हैं", "हो", "था", "थी", "थे",
        "नहीं", "को", "में", "कि", "ना", "न", "तो", "भी", "ही", "और", "वो", "वह", "यह", "ये", "जो", "से", "का", "की", "के",
        "पर", "एक", "बस", "कर", "रहे", "रहा", "रही", "अपने", "अपनी", "अपना", "जब", "तब", "कभी", "सब", "कुछ",
        "लेकिन", "क्योंकि", "अगर", "शायद", "फिर", "मगर", "इसलिए", "होता", "होती", "होते", "गया", "गई", "दिया"}


CHECK_PROMPT = """Is this a complete, grammatically correct and meaningful Hindi sentence, with every word a real,
correctly used Hindi word (no mis-heard or nonsense words)? Line: {line}
Reply ONLY with JSON: {{"ok": true}} or {{"ok": false}}"""


def _norm(w):
    """Spelling-insensitive form of a Hindi word (nukta, bindu, short/long vowel signs unified)."""
    w = re.sub(r"[\u093c\u0901\u0902\u094d।,.?!\"'’‘]", "", w)
    return w.translate(str.maketrans({"\u0940": "\u093f", "\u0942": "\u0941", "ई": "इ", "ऊ": "उ"}))


def _similar(a, b):
    a, b = _norm(a), _norm(b)
    if a == b:
        return True
    ga = {a[i:i + 2] for i in range(len(a) - 1)} or {a}
    gb = {b[i:i + 2] for i in range(len(b) - 1)} or {b}
    return 2 * len(ga & gb) / (len(ga) + len(gb)) >= 0.6


def agreement(t, second):
    """Share of this thought's words that the second, independent recogniser also heard at that time."""
    other = [w for s in second if s["end"] > t["start"] - 1.0 and s["start"] < t["end"] + 1.0
             for w in s["text"].split()]
    if not other:
        return 0.0
    ws = [w["w"] for w in t["words"]]
    return sum(any(_similar(w, o) for o in other) for w in ws) / len(ws)


def build_thoughts(segs, gap=1.6, max_words=16, second=None, min_p=0.45, min_agree=0.80):
    """Join breath-lines into complete thoughts (short pauses inside a sentence are not line ends).
    A thought is only eligible if every word was heard confidently AND a second recogniser agrees."""
    out = []
    for s in segs:
        if out and s["start"] - out[-1]["end"] < gap and len(out[-1]["words"]) + len(s["words"]) <= max_words:
            out[-1] = dict(out[-1], words=out[-1]["words"] + s["words"], end=s["end"],
                           text=out[-1]["text"] + " " + s["text"])
        else:
            out.append(dict(s))
    good = []
    for t in out:
        ws = [w["w"] for w in t["words"]]
        dur = max(0.5, t["end"] - t["start"])
        garbled = any(ws[i] == ws[i + 1] and len(ws[i]) > 1 for i in range(len(ws) - 1))   # "लग लग"
        if not (5 <= len(ws) <= 16 and dur >= 3.0 and 0.9 <= len(ws) / dur <= 3.8) or garbled:
            continue
        ps = [w.get("p") for w in t["words"] if w.get("p") is not None]
        t["minp"] = min(ps) if ps else None
        if ps and t["minp"] < min_p:
            continue                                   # the speech model was unsure of a word
        if second:
            t["agree"] = agreement(t, second)
            if t["agree"] < min_agree:
                continue                               # the two recognisers heard different words
        good.append(t)
    return good


def _english_loan(w):
    if EN:
        return False
    return "ॉ" in w or "ऑ" in w                       # प्रॉब्लम, ऑफिस, कॉल ... (English words in Devanagari)


def _key_index(words, key):
    k = next((x for x, w in enumerate(words) if _bare(w["w"].strip("।,.?!")) == _bare(key)), None)
    if k is None or _stop(words[k]["w"]) or _english_loan(words[k]["w"]):
        cand = [x for x, w in enumerate(words) if not _stop(w["w"]) and not _english_loan(w["w"])] \
            or list(range(len(words)))
        k = max(cand, key=lambda x: len(_bare(words[x]["w"])))
    return k


OPEN_STOP = {"कितना", "कितनी", "कितने", "जाता", "जाती", "जाते", "बहुत", "सबसे", "सिर्फ", "लगता", "लगती", "सकते", "सकता",
             "करते", "करता", "करती", "रहते", "रहता", "रही", "रहा", "हमारे", "हमारी", "हमारा", "तुम्हारे", "तुम्हारी",
             "उनके", "उनकी", "इसके", "उसके", "जैसे", "वाली", "वाले", "अंदर", "बाहर", "पहले", "बाद"}


def _word_confirmed(w, second, min_p=0.6):
    """A recognised word we dare to show big: the speech model was sure of it AND the second recogniser heard
    (nearly) the same word at the same moment."""
    if w.get("p") is not None and float(w["p"]) < min_p:
        return False
    t0, t1 = float(w["start"]) - 1.5, float(w["end"]) + 1.5
    near = [o for s in second if float(s["end"]) > t0 and float(s["start"]) < t1 for o in s["text"].split()]
    return any(_similar(w["w"], o) for o in near)


def opening_line(moments, segs, t_from=12.8, t_until=24.0, second=None):
    """Viewers decide in the first seconds: right after the opening title, the first whole spoken sentence is
    shown as typography (unless a key moment already starts there).
    second = the independent recogniser of a no-script run: then only lines that passed the confidence +
    cross-check are used, and the key word itself must be confirmed by both recognisers."""
    if any(t_from <= float(m["start"]) <= t_until for m in moments):
        return moments
    if second is not None:                                 # no script: recognised words can be wrong
        src = [dict(t, sent=("th", i)) for i, t in enumerate(build_thoughts(segs, second=second))]
        if not src:
            info("no opening line: no early sentence passed the confidence + cross-check")
            return moments
    else:                                                  # script run: script text is trusted, voice-filled parts are not
        src = [sg for sg in segs if sg.get("source") != "voice"]
    sents = []                                             # sentences as lists of their pieces
    for sg in sorted(src, key=lambda x: float(x["start"])):
        ws = [dict(w=w["w"], start=float(w["start"]), end=float(w["end"]), **({"p": w["p"]} if "p" in w else {}))
              for w in sg.get("words", [])]
        if not ws:
            continue
        sid = sg.get("sent")
        same = (sid == sents[-1][0]) if (sents and sid is not None) else \
               (bool(sents) and sid is None and ws[0]["start"] - sents[-1][1][-1][-1]["end"] <= 0.3)
        if same:
            sents[-1][1].append(ws)
        else:
            sents.append([sid, [ws]])
    sents_w = []
    for _, pieces in sents:                                # whole pieces only, at most 16 words on screen
        out = []
        for pc in pieces:
            if out and len(out) + len(pc) > 16:
                break
            out += pc
        sents_w.append(out[:16])
    later = [float(m["start"]) for m in moments]
    for ws in sents_w:
        if ws[0]["start"] < t_from or ws[0]["start"] > t_until + 6 or len(ws) < 3:
            continue
        nxt = [t for t in later if t > ws[0]["start"]]
        if nxt and ws[-1]["end"] + 3 > min(nxt):
            continue                                       # would run into the next moment
        def clean(w):
            return re.sub(r"[^A-Za-z']", "", w).lower() if EN else re.sub(r"[^\u0900-\u097F]", "", w)
        stop = STOP_EN if EN else STOP | OPEN_STOP
        cand = [i for i, w in enumerate(ws) if clean(w["w"]) not in stop and len(clean(w["w"])) >= (4 if EN else 3)]
        if second is not None:
            cand = [i for i in cand if _word_confirmed(ws[i], second)]
            if not cand:
                continue                                   # no key word we are sure of -> try the next sentence
        key = max(cand, key=lambda i: len(clean(ws[i]["w"]))) if cand else None
        ws = [{k: v for k, v in w.items() if k != "p"} for w in ws]
        line = dict(start=ws[0]["start"], end=ws[-1]["end"], text=" ".join(w["w"] for w in ws), key=key,
                    key_word=ws[key]["w"] if key is not None else "", score=0, words=ws, opening=True)
        info(f"first words on screen at {int(ws[0]['start'])} s: {line['text'][:70]}  [{line['key_word']}]")
        return sorted(moments + [line], key=lambda m: float(m["start"]))
    return moments


def pick_moments(segs, out_json, llm, total, redo=False, min_gap=12.0, second=None):
    if os.path.exists(out_json) and not redo:
        info("key moments exist, reusing them (edit moments.json freely, or --redo-captions)")
        return json.load(open(out_json, encoding="utf-8"))
    cta = re.compile(r"लाइक|सब्सक्राइब|कमेंट|चैनल|बटन|शेयर|वीडियो|subscribe|\blike\b|comment|channel|share|video", re.I)
    allth = build_thoughts(segs)
    th = [t for t in build_thoughts(segs, second=second)
          if not cta.search(t["text"]) and 14 <= t["start"] and t["end"] <= total - 20]
    print(f"      {len(th)} of {len(allth)} thoughts passed the confidence + cross-check", flush=True)
    n = max(6, min(16, int(total / 60)))
    info(f"{llm} is choosing ~{n} key moments from {len(th)} candidate lines ...")
    ensure_local_ai()
    scores = {}
    step = 30
    for c0 in range(0, len(th), step):                     # small batches, numbered from 0 each time
        part = th[c0:c0 + step]
        rows = "\n".join(f"[{k}] {t['text']}" for k, t in enumerate(part))
        try:
            raw = sv_av.local_chat(llm, (MOMENTS_PROMPT_EN if EN else MOMENTS_PROMPT).format(lines=rows), think=True,
                                    timeout=1800, num_ctx=8192)
            got = 0
            for e in json.loads(raw).get("scores", []):
                try:
                    k = int(e["i"])
                    if 0 <= k < len(part):
                        scores[c0 + k] = (float(e.get("s", 0)), str(e.get("key", "")).strip()); got += 1
                except (KeyError, TypeError, ValueError):
                    pass
            print(f"      scored {got}/{len(part)} thoughts ({c0}-{c0 + len(part) - 1})", flush=True)
        except Exception as e:
            print(f"      (LLM unavailable: {e}) - using rhythm only")
    # Batch the second opinion to avoid one sequential model round-trip per high scorer.
    # The acceptance rule is unchanged: a negative judgment lowers that line below the
    # selection threshold; unavailable or missing judgments leave the original score intact.
    high = [i for i, (sc, _) in scores.items() if sc >= 6]
    if high:
        try:
            lang_rule = ("complete, grammatically correct and meaningful English sentence, with every word a real, "
                         "correctly used English word" if EN else
                         "complete, grammatically correct and meaningful Hindi sentence, with every word a real, "
                         "correctly used Hindi word")
            rows = "\n".join("[{}] {}".format(k, th[i]["text"]) for k, i in enumerate(high))
            prompt = ("Review each numbered line independently. Decide whether it is a {} ".format(lang_rule)
                      + "(no mis-heard or nonsense words). Return ONLY JSON in this form: "
                      + '{"results":[{"i":0,"ok":true},{"i":1,"ok":false}]}. '
                      + "Include one result for every line, preserving its number.\n" + rows)
            raw = sv_av.local_chat(llm, prompt, json_mode=True, num_ctx=4096, timeout=600, think=False)
            checked = json.loads(raw).get("results", [])
            for result in checked:
                try:
                    k = int(result["i"])
                    if 0 <= k < len(high) and result.get("ok") is False:
                        i = high[k]
                        scores[i] = (min(scores[i][0], 3.0), scores[i][1])
                except (KeyError, TypeError, ValueError):
                    pass
        except Exception:
            pass
    if scores:
        ranked = sorted(range(len(th)), key=lambda i: (-scores.get(i, (0, ""))[0], th[i]["start"]))
    else:
        # No extra model call: prefer complete, clearly spoken thoughts and let the spacing
        # rule below distribute them across the episode.
        def fallback_quality(t):
            count = len(t["words"])
            duration = max(0.5, float(t["end"]) - float(t["start"]))
            rate = count / duration
            punctuation = 1.0 if re.search(r"[।.!?]$", t["text"].strip()) else 0.0
            confidence = float(t.get("minp") or 0.5)
            return (12 - abs(count - 9)) + 3 * confidence + 2 * punctuation - 2 * abs(rate - 2.0)
        ranked = sorted(range(len(th)), key=lambda i: (-fallback_quality(th[i]), th[i]["start"]))
    chosen = []
    for i in ranked:
        sc = scores.get(i, (0, ""))[0]
        if scores and sc < 6:
            break
        t = th[i]
        # timing follows the content (the AI's scores), not a clock: strong moments may come close together
        # (>= min_gap apart), quiet stretches stay empty; only crowding is stopped (<= 2 lines per minute)
        near = [c for c in chosen if abs(t["start"] - c["start"]) < 60]
        if all(abs(t["start"] - c["start"]) >= min_gap for c in chosen) and len(near) < 2:
            chosen.append(t | {"score": sc, "keyhint": scores.get(i, (0, ""))[1]})
        if len(chosen) >= n:
            break
    moments = []
    for t in sorted(chosen, key=lambda t: t["start"]):
        words = [dict(w) for w in t["words"]]
        k = _key_index(words, t["keyhint"])
        moments.append({"start": words[0]["start"], "end": words[-1]["end"], "text": " ".join(w["w"] for w in words),
                        "key": k, "key_word": words[k]["w"], "score": t["score"], "words": words})
    fix_spelling(moments, llm)
    save_json(moments, out_json)
    for m in moments:
        info(f"  {ts(m['start'])[:-3]}  {m['text']}   [{m['key_word']}]")
    info(f"{len(moments)} key moments chosen")
    return moments


TITLE_PROMPT = """Here is a calm Hindi sleep-therapy voice-over (beginning, middle and end):
{text}
Suggest 3 different titles for this episode: gentle, poetic, 2 to 5 words each, correct Devanagari, about what
THIS episode is really about (its subject and feeling), no English, no quotes, no channel name, not generic
words like "शांत रात्रि" or "सुकून". Reply ONLY with JSON: {{"titles": ["...", "...", "..."]}}"""


def _ask_console(prompt, timeout):
    """Read a line from the console, giving up after `timeout` seconds (None = nobody answered)."""
    try:
        import msvcrt
    except ImportError:
        return None
    print(prompt, end="", flush=True)
    buf, t_end = "", time.time() + timeout
    while time.time() < t_end:
        if msvcrt.kbhit():
            ch = msvcrt.getwch()
            if ch in "\r\n":
                print("", flush=True)
                return buf
            if ch == "\b":
                if buf:
                    buf = buf[:-1]; print("\b \b", end="", flush=True)
                continue
            buf += ch; print(ch, end="", flush=True)
            t_end = max(t_end, time.time() + 60)              # typing: don't cut you off
        time.sleep(0.05)
    print("", flush=True)
    return None


def _ask_title_window(titles, episode, seconds=180):
    """A small window (Hindi typing / pasting works there) to pick a suggested title or type your own.
    Returns the title, "" if the window could not be shown, None if nobody chose (time ran out)."""
    import subprocess, tempfile
    ps1 = os.path.join(ENGINE, "title_picker.ps1")
    if os.name != "nt" or not os.path.exists(ps1):
        return None
    d = tempfile.mkdtemp(prefix="title_")
    fin, fout = os.path.join(d, "in.json"), os.path.join(d, "out.txt")
    json.dump({"episode": episode, "titles": titles}, open(fin, "w", encoding="utf-8"), ensure_ascii=False)
    print("      a window has opened: choose the title there (or type / paste your own) ...", flush=True)
    try:
        subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-STA", "-File", ps1,
                        fin, fout, str(seconds)], timeout=seconds + 120)
    except Exception as e:
        print(f"      (title window failed: {e})", flush=True)
        return None
    if os.path.exists(fout):
        t = open(fout, encoding="utf-8-sig").read().strip()
        return t or titles[0]
    return titles[0]                                       # time ran out: the first suggestion


def make_title(segments, out_json, llm, ep_dir=None, ask=True):
    """Your title first: title.txt in the episode folder (the launcher asks for it). Otherwise the AI suggests 3
    titles here in the window and you choose one or type your own (the first is used if nobody answers)."""
    tf = os.path.join(ep_dir, "title.txt") if ep_dir else None
    if tf and os.path.exists(tf):
        t = open(tf, encoding="utf-8-sig").read().strip().splitlines()
        if t and t[0].strip():
            info(f"your title: {t[0].strip()}")
            return t[0].strip()
    if os.path.exists(out_json):
        old = json.load(open(out_json, encoding="utf-8"))
        if old.get("chosen") or not ep_dir or not ask:   # only a title YOU chose (or accepted) is reused
            return old.get("title")
    n = len(segments)
    pick = segments[:12] + segments[n // 2 - 6:n // 2 + 6] + segments[-8:] if n > 30 else segments
    text = "\n".join(s["text"] for s in pick)
    titles = []
    try:
        r = json.loads(sv_av.local_chat(llm, (TITLE_PROMPT_EN if EN else TITLE_PROMPT).format(text=text), num_ctx=4096,
                                         timeout=300))
        titles = [str(x).strip().strip('"') for x in r.get("titles", []) if 0 < len(str(x).split()) <= 6][:3]
    except Exception as e:
        print(f"      (title suggestions skipped: {e})")
    titles = titles or ["Sukoon" if EN else "सुकून"]
    print("\n      TITLE for the opening screen - the AI suggests:", flush=True)
    for i, x in enumerate(titles, 1):
        print(f"         {i}.  {x}", flush=True)
    t = _ask_title_window(titles, os.path.basename(ep_dir or "")) if ask else None
    if t is None and ask:                                  # no window possible: ask here in the console
        try:
            import msvcrt
            while msvcrt.kbhit():                          # forget old key presses (e.g. an Enter from earlier)
                msvcrt.getwch()
        except ImportError:
            pass
        ans = (_ask_console("      type 1/2/3, or your own title, then Enter (2 min, else 1 is used): ", 120) or "").strip()
        t = titles[int(ans) - 1] if ans.isdigit() and 1 <= int(ans) <= len(titles) else (ans or None)
    t = t or titles[0]
    info(f"title: {t}   (change it any time: put it in title.txt in the episode folder and run again)")
    save_json({"title": t, "suggestions": titles, "chosen": True}, out_json)
    if tf:
        open(tf, "w", encoding="utf-8").write(t + "\n")
    return t


def schedule(captions, total, hold=11.0, lang="hi"):
    """Turn picked lines into non-overlapping on-screen windows."""
    out = []
    for i, c in enumerate(captions):
        st = max(0.0, float(c["start"]))
        en = max(float(c["end"]), st + hold)
        if i + 1 < len(captions):
            en = min(en, float(captions[i + 1]["start"]) - 1.0)
        en = min(en, total)
        if en - st >= 3:
            out.append((st, en, c["hi"] if lang == "hi" else c["en"]))
    return out


# ------------------------------------------------------------------ 3. living artwork loop
IMAGES_DIR = paths.IMAGES_DIR


@__import__("functools").lru_cache(maxsize=None)
def find_image(audio_name):
    """Pick the episode artwork: same/similar name as the audio, else the newest 'sukoon*' image."""
    if not os.path.isdir(IMAGES_DIR):
        return None
    imgs = [f for f in os.listdir(IMAGES_DIR) if f.lower().endswith((".png", ".jpg", ".jpeg", ".webp"))]
    key = re.sub(r"[^a-z0-9]", "", audio_name.lower())
    best, score = None, 0
    for f in imgs:
        k = re.sub(r"[^a-z0-9]", "", os.path.splitext(f)[0].lower())
        common = len(os.path.commonprefix([k, key]))
        if k and (k in key or key in k):
            sc = 100 + len(k)                                  # name contained in the other name
        elif common >= max(8, int(0.8 * len(key))):
            sc = common                                        # e.g. "sukoon_4_1" vs "sukoon 4.1 final"
        else:
            continue                                           # just starting with "sukoon" is NOT a match
        if sc > score:
            best, score = f, sc
    if not best:
        sk = [f for f in imgs if "sukoon" in f.lower()]
        sk.sort(key=lambda f: os.path.getmtime(os.path.join(IMAGES_DIR, f)), reverse=True)
        best = sk[0] if sk else None
        if best:
            info(f"!! no artwork named like this episode - using the newest 'sukoon' image ({best}).")
            info(f"   For the right picture: save it as '{audio_name}.png' in {IMAGES_DIR}, or use --image")
    return os.path.join(IMAGES_DIR, best) if best else None


def ensure_art_loop(image, seconds, fps, seed):
    import artloop
    base = re.sub(r"[^\w\-]+", "_", os.path.splitext(os.path.basename(image))[0]).strip("_")
    os.makedirs(paths.VIDEO_LOOP_DIR, exist_ok=True)
    path = os.path.join(paths.VIDEO_LOOP_DIR, f"art_{base}_s{seed}_{int(seconds)}s_alive.mp4")   # _alive: lamps glow, tea steams
    meta = path + ".json"
    if os.path.exists(path) and os.path.exists(meta):
        info(f"animated artwork exists, reusing it")
        return path, json.load(open(meta))["moon"]
    info(f"animating {os.path.basename(image)} ({seconds:.0f}s seamless loop, one time per image, ~5 min) ...")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    sc = artloop.render_loop(image, path, seconds, fps, seed=seed)       # elements list: <loop>.elements.json
    moon = [float(v) for v in sc.moon_out]
    save_json({"moon": moon, "image": image}, meta)
    return path, moon


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("audio")
    ap.add_argument("--script", default=None, help="trusted subtitle script to align to this narration")
    ap.add_argument("--no-script", action="store_true", help="ignore nearby text files and subtitle only from this audio")
    ap.add_argument("--image", default=None, help="episode artwork (default: auto-pick from D:\\Audio asset\\Images)")
    ap.add_argument("--viz", default="line", choices=["line", "bars", "none"], help="audio-reactive equalizer style")
    ap.add_argument("--lang", default="hi", choices=["hi", "en"], help="caption language")
    ap.add_argument("--no-brand", action="store_true")
    ap.add_argument("--label", default=None, help='episode badge, e.g. "Sukoon 8.0" (default: from the folder name)')
    ap.add_argument("--clips", type=int, default=4, help="short clips around the best moments (0 = none)")
    ap.add_argument("--reels", type=int, default=3, help="Instagram teaser reels, 9:16 (0 = none)")
    ap.add_argument("--no-title", action="store_true")
    ap.add_argument("--captions-only", action="store_true", help="stop after transcript + captions + title")
    ap.add_argument("--no-dim", action="store_true", help="don't dim the picture over the last third")
    ap.add_argument("--loop-seconds", type=float, default=40)
    ap.add_argument("--fps", type=int, default=24)
    ap.add_argument("--language", default="hindi")
    ap.add_argument("--llm", default="auto", help="Ollama model (auto uses the configured story model)")
    ap.add_argument("--max-lines", type=int, default=20)
    ap.add_argument("--font", default=None, help="path to a .ttf (default Georgia Italic)")
    ap.add_argument("--redo-captions", action="store_true")
    ap.add_argument("--typo", default="mukhtalif", choices=["mukhtalif", "verse", "diary", "shayari", "classic"],
                    help="typography: Mukhtalif word-by-word (default) or the older line captions (classic)")
    ap.add_argument("--redo-transcript", action="store_true")
    ap.add_argument("--tag", default="", help="extra text added to the output file name")
    ap.add_argument("--preview", type=float, default=0, help="only make the first N seconds")
    ap.add_argument("--yes", action="store_true", help="don't ask before downloading the Whisper model")
    a = ap.parse_args()

    global EN
    EN = a.language.lower() in ("english", "en")
    if EN:
        import overlay as _ov
        _ov.BRAND_WORD = "Sukoon"
    if a.llm == "auto":
        a.llm = pick_llm()
    ep = episode_inputs(a.audio)
    audio = ep["audio"]
    if a.script:
        if not os.path.isfile(a.script):
            sys.exit(f"Subtitle script not found: {a.script}")
        ep["script"] = os.path.abspath(a.script)
    elif a.no_script:
        ep["script"] = None
    if ep["image"] and not a.image:
        a.image = ep["image"]
    name = re.sub(r"[^\w\-]+", "_", ep["name"]).strip("_")
    # HubGPT supplies a per-episode work folder so transcripts, caption plans,
    # and reel metadata stay beside the source episode. Standalone use keeps
    # Studio's historical engine-local cache.
    work = os.environ.get("STUDIO_WORK") or os.path.join(paths.VIDEO_WORK_DIR, name)
    prepare_work_cache(work, source_fingerprint(audio, ep["script"]))
    open(os.path.join(work, "lang.txt"), "w", encoding="utf-8").write("en\n" if EN else "hi\n")   # reels read it
    total = sv_av.audio_duration(audio)
    bar = "=" * 70
    print(f"{bar}\n  SUKOON VIDEO MAKER  -  Mukhtalif Awaaz\n  episode : {ep['name']}  ({total/60:.1f} min)"
          f"\n  audio   : {os.path.basename(audio)}"
          f"\n  project : {os.path.basename(ep['aup']) if ep['aup'] else '(none - using the mixed audio)'}"
          f"\n  script  : {os.path.basename(ep['script']) if ep['script'] else '(none - words are recognised from the audio)'}"
          f"\n  artwork : {os.path.basename(a.image) if a.image else '(auto)'}"
          f"\n  started : {time.strftime('%H:%M')}\n{bar}", flush=True)

    if a.redo_transcript:
        stale = ("transcript.json", "transcript_turbo.json", "transcript_hi.json", "transcript_script.json",
                 "captions.json", "moments.json", "title.json")
        present = [os.path.join(work, f) for f in stale if os.path.exists(os.path.join(work, f))]
        gapfill = os.path.join(work, "gapfill")
        if os.path.isdir(gapfill):
            present.append(gapfill)
        if present:
            archive = os.path.join(work, "_redo_" + time.strftime("%Y%m%d_%H%M%S"))
            suffix = 1
            while os.path.exists(archive):
                archive = os.path.join(work, "_redo_" + time.strftime("%Y%m%d_%H%M%S") + f"_{suffix}")
                suffix += 1
            os.makedirs(archive, exist_ok=True)
            for src in present:
                os.replace(src, os.path.join(archive, os.path.basename(src)))
            info(f"previous transcript/caption files preserved in {os.path.basename(archive)}")
    if a.redo_captions and os.path.exists(os.path.join(work, "title.json")):
        os.replace(os.path.join(work, "title.json"), os.path.join(work, "title.json.old"))
    kin, caps_path = None, os.path.join(work, "captions.json")
    if a.typo != "classic":
        mins = total / 60
        has = lambda f: os.path.exists(os.path.join(work, f))
        script = ep["script"]
        img_guess = a.image or find_image(name)
        loop_done = bool(img_guess) and os.path.exists(os.path.join(
            HERE, "loops", f"art_{re.sub(r'[^\w\-]+', '_', os.path.splitext(os.path.basename(img_guess))[0]).strip('_')}"
                           f"_s{sum(map(ord, name)) % 997}_{int(a.loop_seconds)}s_alive.mp4"))
        if script:
            PLAN.update({1: 0.1, 2: 0.0 if has("transcript_turbo.json") else 0.6 + 0.12 * mins * 0.45, 3: 0.1})
        else:
            PLAN.update({1: 0.1, 2: 0.0 if has("transcript_hi.json") else 1 + 3.0 * mins * 0.45,
                         3: 0.0 if has("transcript_turbo.json") else 0.1 * mins})
        PLAN.update({4: 0.1 if (has("moments.json") and not a.redo_captions) else 2.0,
                     5: 0.0 if loop_done else 5.0,
                     6: 0.6 + 0.25 * (min(mins, a.preview / 60) if a.preview else mins) + (0 if a.preview else 0.4 + 1.0 * a.reels)})
        vsrc = None
        if not script and not has("transcript_hi.json"):
            # no script: the Hindi model's time depends on how long you actually SPEAK, not on the episode
            # length (Sukoon 4.0: 91 min episode, ~13 min of voice). Read the voice first, then estimate.
            vsrc = voice_track(audio, work, ep["aup"]) or audio
            try:
                import asr_hindi as ah
                x = ah.load_audio(vsrc)
                runs, _thr = ah.speech_runs(x)
                speech = sum(e - s for s, e in runs) / 60
                PLAN[2] = 1 + 3.0 * speech
                PLAN[3] = 0.0 if has("transcript_turbo.json") else 0.1 * len(x) / 16000 / 60
                info(f"you speak for about {speech:.0f} of the {mins:.0f} minutes")
            except Exception as e:
                info(f"(could not measure the speech length: {e} - rough estimate)")
        _, fin = eta_line(1)
        print(f"  estimate: about {sum(PLAN.values()):.0f} min  ->  ready around {fin}\n{bar}", flush=True)
        step(1, "Your voice: reading the voice track from the Audacity project")
        src = vsrc or voice_track(audio, work, ep["aup"]) or audio
        turbo_json = os.path.join(work, "transcript_turbo.json")
        if script:
            step(2, "Timing your words (quick recogniser on the GPU)")
            second = transcribe(src, turbo_json, a.language, True)
            step(3, "Lining up your script with your voice - your exact words, perfect spelling")
            anchors = [w for s in second for w in s.get("words", [])]
            segs = script_segments(script, anchors, work)
            segs = gap_fill(src, second, segs, work)       # spoken parts that are not in this script
            second = None                                  # the script itself is the trusted text
        else:
            step(2, "Understanding your words (Hindi speech model, GPU) - the longest step")
            segs = transcribe_hi(audio, work)
            step(3, "Double-checking every line with a second recogniser")
            try:                                           # independent second recogniser for cross-checking
                second = transcribe(src, turbo_json, a.language, True)
            except Exception as e:
                info(f"(second recogniser unavailable: {e})"); second = None
        if not script and ep.get("folder"):
            save_auto_transcript(segs, os.path.join(ep["path"], "transcript_auto.txt"),
                                 os.path.join(work, "transcript_hi.json"))
        step(4, "Choosing the key moments for the typography")
        caps_path = os.path.join(work, "moments.json")
        redo = a.redo_captions
        if script and os.path.exists(caps_path) and \
                os.path.getmtime(caps_path) < os.path.getmtime(os.path.join(work, "transcript_script.json")):
            redo = True                                    # new / changed script -> choose moments again
        gf = os.path.join(work, "gapfill", "transcript_hi.json")
        if script and os.path.exists(caps_path) and os.path.exists(gf) and os.path.getmtime(caps_path) < os.path.getmtime(gf):
            redo = True                                    # parts filled from your voice -> choose moments again
        moments = pick_moments(segs, caps_path, a.llm, total, redo, second=second)
        moments = opening_line(moments, segs, second=second)   # words on screen straight after the opening title
        kin = {"style": a.typo, "lines": moments}
        windows = []
    else:
        segs = transcribe(audio, os.path.join(work, "transcript.json"), a.language, a.yes)
        caps = pick_lines(segs, caps_path, a.llm, a.max_lines, total, a.redo_captions)
        windows = [w for w in schedule(caps, total, lang=a.lang) if w[0] > 13 and w[1] < total - 18]
    title = None if a.no_title else make_title(segs, os.path.join(work, "title.json"), a.llm,
                                                ep.get("path") if ep.get("folder") else None, ask=not a.preview)
    if title:
        info(f"episode title: {title}")
    # Release every configured role model (Qwen reasoning and vision, Gemma stories)
    # before the GPU video renderer starts; the Ollama service itself stays running.
    models = {a.llm, sv_av.paths.CFG.get("llm"), sv_av.paths.CFG.get("story_model"),
              sv_av.paths.CFG.get("prompt_model"), sv_av.paths.CFG.get("vision_llm")}
    for model in sorted(m for m in models if m):
        try:
            sv_av.unload_model(model)
            info(f"   Ollama: unloaded {model} before GPU rendering")
        except Exception as e:
            info(f"   (could not unload {model} before rendering: {e})")
    if a.captions_only:
        info("captions-only: stopping before the video.")
        return
    image = a.image or find_image(name)
    if not image or not os.path.exists(image):
        sys.exit("No artwork found - pass --image \"path\\to\\image.png\"")
    step(5, "Artwork: " + os.path.basename(image))
    seed = sum(map(ord, name)) % 997                          # unique motion per episode
    loop, moon = ensure_art_loop(image, a.loop_seconds, a.fps, seed)
    length = min(total, a.preview) if a.preview else total
    # an episode folder keeps everything together: <episode>\Output\ (video, clips, reels, sample frames)
    out_dir = os.path.join(ep["path"], paths.EPISODE_OUTPUT_NAME) if ep.get("folder") else os.path.join(paths.VIDEO_OUTPUT_DIR, name)
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, f"{name}{'_' + a.tag if a.tag else ''}{'_preview' if a.preview else ''}.mp4")
    step(6, f"Drawing the final video ({length/60:.1f} min)")
    import compose

    def _draw(lp, mx):
        compose.compose(audio, lp, windows, out, fps=a.fps, duration=length, viz=a.viz,
                        brand=False if a.no_brand else dict(label=episode_label(a.label or ep["name"])), moon_xy=mx,
                        lang=a.lang, title=title, dim_last=0 if a.no_dim else 0.33, kinetic=kin,
                        goodnight="Good night" if EN else "शुभ रात्रि")

    _draw(loop, moon)
    # quality gate: every frame is looked at; a video that jumps, pops or freezes is drawn again once
    try:
        import reels as _rq
        probs = _rq.motion_problems(out)
        loop_len = compose._loop_len(loop) / max(1, a.fps) if hasattr(compose, "_loop_len") else 0
        if loop_len:
            for p_ in probs:
                t_ = p_["at"]
                if p_["kind"] == "jump" and min(t_ % loop_len, loop_len - t_ % loop_len) < 0.15:
                    p_["kind"] = "loop pop"
        save_json({"video": out, "checked": time.strftime("%Y-%m-%d %H:%M:%S"), "motion": probs, "auto": True},
                  os.path.splitext(out)[0] + ".check.json")
        if probs:
            info(f"quality check: {', '.join(p_['kind'] + ' at ' + ts(p_['at'])[:-3] for p_ in probs[:5])} - drawing it again")
            od_ = os.path.join(out_dir, "_old"); os.makedirs(od_, exist_ok=True)
            os.replace(out, os.path.join(od_, "broken_" + os.path.basename(out)))
            if any(p_["kind"] == "loop pop" for p_ in probs):
                for ext in ("", ".json"):
                    if os.path.exists(loop + ext):
                        os.replace(loop + ext, os.path.join(os.path.dirname(loop), "_old_" + os.path.basename(loop) + ext))
                loop, moon = ensure_art_loop(image, a.loop_seconds, a.fps, seed)
            _draw(loop, moon)
            left = _rq.motion_problems(out)
            save_json({"video": out, "checked": time.strftime("%Y-%m-%d %H:%M:%S"), "motion": left, "auto": True},
                      os.path.splitext(out)[0] + ".check.json")
            info("quality check: smooth now" if not left else f"quality check: still {len(left)} spot(s) - open Repair")
        else:
            info("quality check: every frame smooth")
    except Exception as e:
        info(f"(quality check skipped: {e})")
    try:                                                       # register: which video uses which downloaded clip
        if os.path.exists(loop + ".clips.json"):
            import elements
            elements.note_use(json.load(open(loop + ".clips.json", encoding="utf-8"))["clips"], out)
    except Exception as e:
        info(f"(register not updated: {e})")
    try:
        import qa
        rep = qa.run(out, caps_path, quiet=True)
        qa_line = f"{rep['size_MB']:.0f} MB, {rep['video']}, {rep['bitrate_Mbps']} Mbps, audio {rep.get('audio_rms_dBFS_mid')} dBFS"
    except Exception as e:
        qa_line = f"QA skipped: {e}"
    clip_line = ""
    if a.clips and not a.preview:
        try:
            print("      cutting short clips around the best moments ...", flush=True)
            import clips
            made = clips.make(work, out, a.clips)
            clip_line = f"\n  clips   : {len(made)} in {os.path.join(os.path.dirname(out), 'clips')}"
        except Exception as e:
            clip_line = f"\n  clips   : skipped ({e})"
    if a.reels and not a.preview:
        try:
            print("      making Instagram teaser reels (9:16) ...", flush=True)
            import reels
            made = reels.make(work, out, a.reels)
            clip_line += f"\n  reels   : {len(made)} in {os.path.join(os.path.dirname(out), 'reels')}  (captions next to them)"
        except Exception as e:
            clip_line += f"\n  reels   : skipped ({e})"
    el = time.time() - T0
    _title("Sukoon video - DONE")
    print(f"\n{bar}\n  DONE in {int(el // 60)} min {int(el % 60)} s\n  video   : {out}\n  check   : {qa_line}"
          f"\n  frames  : {os.path.splitext(out)[0]}_qa\\  (sample pictures to glance at)"
          f"\n  moments : {caps_path}{clip_line}\n{bar}", flush=True)


if __name__ == "__main__":
    main()
