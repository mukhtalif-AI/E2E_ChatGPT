"""voice_cast.py - a ghost's own lines get a ghost voice (Experimental genre runs only). Added 19 Oct 2026.

Your method: the line is layered with a pitched-down copy, a pitched-up copy and one quiet reversed copy, each a
few milliseconds late. Fully automatic, nothing to approve; anything the code is not sure about stays in the
normal narrator voice. Two steps (experimental_workspace._feedback_steps adds them):
  exp_voice_cast  before the voice: every "double-quoted" line in the script is found by code, the director model
                  says who speaks each one (ghost / human / written / unsure, heard aloud or not, sure or not); only
                  lines that pass every rule below are chosen, and each chosen quote is put on its own line so the
                  voice reads it as a separate sentence. Plan: Scripts\\<name>.voice_cast.json
  exp_voice_fx    before the mix: the chosen lines are found in timing.json and re-voiced in narration.wav from a
                  clean copy (narration_dry.wav), so a re-run never stacks the effect. words.json keeps its time
                  stamp (the timing does not move), so Whisper is not run again.
Rules (all must pass): the words are inside double quotes (single quotes name things, they are never dialogue);
the role is one the series allows (CAST: ghost by default); the AI says the words are heard aloud at that moment
and is sure, in every ask (asks = 2 by default); the words before the quote do not say it is written (message,
letter, wall ...); at most max_lines lines per story. A report goes into the episode folder (voice_cast.txt);
it is read afterwards and never stops the run.
Tinker: tables voice_cast.CAST (per series) and voice_cast.PRESETS (the sound), prompt voice_cast.CAST_ASK.
Case study (19 Oct): _tmp\\ghost_voice_study_20261019 in the hub folder (tests\\voice_cast_study.py).

Try a preset on any wav (no hub needed):
    python app\\voice_cast.py <in.wav> <out.wav> [preset] [--ffmpeg path]
"""
import difflib, json, os, re, shutil, subprocess, sys, tempfile, time, wave

# per series: on, which roles get which preset, how many lines at most, how many asks must agree
CAST = {
    # model: the one who reads the story ("" = the script writer's model); think: Gemma 4 hidden thinking.
    # Case study 19 Oct: gemma4:26b thinking off 9/9 ghost lines, 0 wrong, 9 s a story; 12b thinking on the same
    # but 63 s; 12b thinking off missed 1 of 9.
    "model": "", "think": False,
    "bhay": {"on": True, "roles": {"ghost": "ghost"}, "max_lines": 4, "asks": 2},
    "rahasya": {"on": False, "roles": {"ghost": "ghost"}, "max_lines": 2, "asks": 2},
    "kisse": {"on": False, "roles": {}, "max_lines": 0, "asks": 2},
    "romanch": {"on": False, "roles": {}, "max_lines": 0, "asks": 2},
    "gyaan": {"on": False, "roles": {}, "max_lines": 0, "asks": 2},
    "kareeb": {"on": False, "roles": {}, "max_lines": 0, "asks": 2},
    # words before a quote that say it is written, not heard (the AI's 'heard' answer is not trusted alone)
    "written_words": ["संदेश", "मैसेज", "मेसेज", "message", "लिखा", "लिखे", "लिखी", "चिट्ठी", "पत्र", "ख़त", "खत", "नोट",
                      "पर्ची", "स्क्रीन पर", "दीवार पर", "डायरी में", "पढ़ा", "टाइप"],
}
# the sound: dry = the voice itself; semitones and loudness of each layer; delay_ms = [down, up, reversed];
# echo, whisper (a breathy copy), growl (a rough low copy) 0-1, 0 = off
PRESETS = {
    "ghost": {"dry": 0.8, "down_st": -4, "down_vol": 0.7, "up_st": 3, "up_vol": 0.35, "rev_vol": 0.2,
              "delay_ms": [25, 45, 60], "echo": 0.25, "whisper": 0, "growl": 0},
    "ghost_deep": {"dry": 0.6, "down_st": -6, "down_vol": 0.9, "up_st": 2, "up_vol": 0.25, "rev_vol": 0.25,
                   "delay_ms": [30, 50, 80], "echo": 0.35, "whisper": 0, "growl": 0},
    "ghost_airy": {"dry": 0.8, "down_st": -3, "down_vol": 0.4, "up_st": 4, "up_vol": 0.6, "rev_vol": 0.15,
                   "delay_ms": [20, 35, 50], "echo": 0.3, "whisper": 0.3, "growl": 0},
    "growl_male": {"dry": 0.5, "down_st": -5, "down_vol": 1.0, "up_st": 0, "up_vol": 0, "rev_vol": 0.1,
                   "delay_ms": [15, 0, 40], "echo": 0.15, "whisper": 0, "growl": 0.7},
    "whisper_female": {"dry": 0.0, "down_st": 0, "down_vol": 0, "up_st": 3, "up_vol": 0.0, "rev_vol": 0.1,
                       "delay_ms": [0, 0, 40], "echo": 0.3, "whisper": 1.0, "whisper_st": 3, "growl": 0},
}
ROLES = ("ghost", "human", "written", "unsure")
CAST_ASK = (
    "You read a Hindi {desc} audio story. Below is the whole story; every quoted line in it is marked [Q1], [Q2] ... "
    "just before its opening quote. For EACH marked quote decide who says it.\n"
    "role: ghost = a ghost, spirit, demon, witch, possessed person, creature, or the voice of someone dead or of "
    "something that is not a living human; human = a living person (the narrator too), also words a person said "
    "while still alive; written = a text message, letter, note, diary, words on a wall or a screen; unsure = the "
    "story does not make it clear.\n"
    "heard: true only when the words are heard as a voice at that moment in the story (said, whispered, called, "
    "on a phone call); false for written words, thoughts, words someone used to say long ago, or a quote that only "
    "names a thing.\nsure: true only when the story makes the speaker plain.\n"
    "speaker: a name or a few words.\n"
    "Answer JSON only, one entry per quote: {{\"lines\":[{{\"q\":1,\"speaker\":\"...\",\"role\":\"ghost\","
    "\"heard\":true,\"sure\":true}}]}}\n\nSTORY:\n{story}\n")

_TAG = re.compile(r"^\s*\[[^\]\n]{1,24}\]\s*")
_PAUSE = re.compile(r"^\s*\(\s*(?:\.{2,}|…)[^)]*\)\s*$")
_HINDI = re.compile(r"[ऀ-ॿ]")
_WORD = re.compile(r"[\wऀ-ॿ]+")


def _content(line):
    m = _TAG.match(line)
    return (m.end() if m else 0)


# ------------------------------------------------------------------ finding the quotes
def spans(text, max_lines=2):
    """every double-quoted span in a script -> [{n, line, col, end_line, end_col, quote, before, orphan}]
    (line/col of the opening quote, end_* of the closing one, 0-based). “...”, "...", a quote running over up to
    max_lines more lines (pause lines in between are skipped), and a lone closing quote whose opening was lost
    (span = from the start of its line). Never closed -> left out. Single quotes are never dialogue here."""
    lines = text.replace("\r\n", "\n").split("\n")
    out, cur = [], None
    for i, ln in enumerate(lines):
        if cur and (i - cur["line"] > max_lines or not ln.strip()):
            cur = None                                   # never closed: not trusted
        first = True                                     # no quote mark before this one on the line
        for j, c in enumerate(ln):
            if c not in "“”\"":
                continue
            straight_close = (c == '"' and cur is None and first and j > _content(ln)
                              and not ln[j - 1].isspace() and ln[j - 1] not in "—–-:("
                              and (j + 1 == len(ln) or ln[j + 1].isspace() or ln[j + 1] in ".,।!?…"))
            if c == "“" or (c == '"' and cur is None and not straight_close):
                if cur is None:
                    cur = {"line": i, "col": j}
            elif cur:
                out.append(dict(cur, end_line=i, end_col=j, orphan=False))
                cur = None
            elif first:                                  # a closing quote whose opening was lost
                a = _content(ln)
                if j > a and _HINDI.search(ln[a:j]):
                    out.append({"line": i, "col": a - 1, "end_line": i, "end_col": j, "orphan": True})
            first = False
    res = []
    for s in out:
        parts = []
        for k in range(s["line"], s["end_line"] + 1):
            ln = lines[k]
            a = s["col"] + 1 if k == s["line"] else _content(ln)
            b = s["end_col"] if k == s["end_line"] else len(ln)
            seg = ln[a:b]
            if _PAUSE.match(seg):
                continue
            parts.append(_TAG.sub("", seg).strip())
        q = re.sub(r"\(\s*(?:\.{2,}|…)[^)]*\)", " ", " ".join(p for p in parts if p))
        q = re.sub(r"\s+", " ", q).strip()
        own = lines[s["line"]]
        if not _HINDI.search(q) or re.search(r'["“”]', q) or \
                len(re.findall(r"[A-Za-z]", own)) > len(_HINDI.findall(own)):   # a quote mark in it, or an English note
            continue
        before = lines[s["line"]][_content(lines[s["line"]]):max(_content(lines[s["line"]]), s["col"])].strip()
        if not before and s["line"] > 0:                 # quote at the start of a line: the line before says who
            k = s["line"] - 1
            while k > 0 and (not lines[k].strip() or _PAUSE.match(lines[k])):
                k -= 1
            before = _TAG.sub("", lines[k]).strip()
        res.append(dict(s, n=len(res) + 1, quote=q, before=before))
    return res


def story_for_ask(text, sp):
    """the script with [Qn] in front of each quote, feeling marks and pause lines left out"""
    lines = text.replace("\r\n", "\n").split("\n")
    marks = {}
    for s in sp:
        col = max(s["col"], _content(lines[s["line"]]))
        marks.setdefault(s["line"], []).append((col, s["n"]))
    out = []
    for i, ln in enumerate(lines):
        if _PAUSE.match(ln) or not ln.strip():
            continue
        for col, n in sorted(marks.get(i, []), reverse=True):
            ln = ln[:col] + f"[Q{n}] " + ln[col:]
        out.append(_TAG.sub("", ln).strip())
    return "\n".join(out)


def parse_reply(d):
    """model JSON -> {q: {role, heard, sure, speaker}}"""
    got = {}
    rows = d.get("lines") if isinstance(d, dict) else d if isinstance(d, list) else []
    for x in rows or []:
        if not isinstance(x, dict):
            continue
        try:
            q = int(str(x.get("q", x.get("n", ""))).strip().lstrip("Qq"))
        except ValueError:
            continue
        role = str(x.get("role", "")).strip().lower()
        yes = lambda v: v is True or str(v).strip().lower() in ("true", "yes", "1")
        got[q] = {"role": role if role in ROLES else "unsure", "heard": yes(x.get("heard")),
                  "sure": yes(x.get("sure")), "speaker": str(x.get("speaker", ""))[:60]}
    return got


def settings(genre):
    t = CAST if isinstance(CAST, dict) else {}
    s = t.get(str(genre or "").lower()) or {}
    return s if isinstance(s, dict) else {}


def decide(sp, replies, s, written_words=None):
    """the rules -> (chosen, rejected): chosen = [{n, quote, role, preset, speaker}], rejected = [{n, quote, why}]"""
    roles = s.get("roles") or {}
    ww = [w.lower() for w in (written_words if written_words is not None else CAST.get("written_words") or [])]
    chosen, rejected = [], []
    for x in sp:
        votes = [r.get(x["n"]) for r in replies]
        why = ""
        if not replies or any(v is None for v in votes):
            why = "no answer from the AI for this quote"
        elif len({v["role"] for v in votes}) > 1:
            why = "the asks disagree on who speaks (" + " / ".join(v["role"] for v in votes) + ")"
        elif votes[0]["role"] not in roles or not roles.get(votes[0]["role"]):
            why = f"role '{votes[0]['role']}' does not get a voice effect in this series"
        elif not all(v["heard"] for v in votes):
            why = "not heard aloud (written, remembered or a name)"
        elif not all(v["sure"] for v in votes):
            why = "the AI is not sure who speaks"
        elif any(w in x["before"].lower() for w in ww):
            why = "the words before the quote say it is written"
        elif not _WORD.search(x["quote"]):
            why = "no words"
        if why:
            rejected.append({"n": x["n"], "quote": x["quote"], "why": why,
                             "speaker": (votes[0] or {}).get("speaker", "") if votes and votes[0] else ""})
        else:
            chosen.append({"n": x["n"], "quote": x["quote"], "role": votes[0]["role"],
                           "preset": roles[votes[0]["role"]], "speaker": votes[0]["speaker"]})
    cap = int(s.get("max_lines", 4) or 0)
    if len(chosen) > cap:                                # keep the first ones and always the last (the big scare)
        keep = chosen[:max(0, cap - 1)] + chosen[-1:] if cap > 0 else []
        for x in chosen:
            if x not in keep:
                rejected.append({"n": x["n"], "quote": x["quote"], "why": f"over the {cap}-line cap",
                                 "speaker": x["speaker"]})
        chosen = keep
    rejected.sort(key=lambda r: r["n"])
    return chosen, rejected


def own_lines(text, sp, chosen):
    """each chosen quote on a line of its own (the words before / after it on their own lines, same feeling mark),
    so the voice reads the quote as separate sentence(s) and timing.json has it as separate line(s)"""
    lines = text.replace("\r\n", "\n").split("\n")
    by_n = {x["n"]: x for x in sp}
    for c in sorted(chosen, key=lambda c: (by_n[c["n"]]["line"], by_n[c["n"]]["col"]), reverse=True):
        s = by_n[c["n"]]
        first, last = lines[s["line"]], lines[s["end_line"]]
        tag = (_TAG.match(first).group(0).strip() + " ") if _TAG.match(first) else ""
        head = first[_content(first):max(_content(first), s["col"])].rstrip()
        tail = last[s["end_col"] + 1:].strip()
        lead_punct = re.match(r"^[।.!?,…]+", tail)
        close = lead_punct.group(0) if lead_punct else ""
        tail = tail[len(close):].strip()
        q0 = max(_content(first), s["col"])
        if s["line"] == s["end_line"]:
            body = [tag + first[q0:s["end_col"] + 1] + close]
        else:
            body = [tag + first[q0:]] + lines[s["line"] + 1:s["end_line"]] + [last[:s["end_col"] + 1] + close]
        ltag = (_TAG.match(last).group(0).strip() + " ") if _TAG.match(last) else tag
        new = ([tag + head] if _HINDI.search(head) else []) + body + ([ltag + tail] if _HINDI.search(tail) else [])
        lines[s["line"]:s["end_line"] + 1] = new
    return "\n".join(lines)


# ------------------------------------------------------------------ the sound
def _filter(p, sr):
    """ffmpeg filter graph for one preset (input [0:a], mono) -> [out]"""
    layers, chains = [], []
    rate = lambda st: 2 ** (float(st) / 12.0)
    dl = list(p.get("delay_ms") or [0, 0, 0]) + [0, 0, 0]

    def shift(st):
        r = rate(st)
        if abs(r - 1) < 1e-3:
            return "anull"
        t = 1.0 / r
        tempo = []
        while t > 2.0:
            tempo.append("atempo=2.0"); t /= 2.0
        while t < 0.5:
            tempo.append("atempo=0.5"); t /= 0.5
        tempo.append(f"atempo={t:.5f}")
        return f"asetrate={int(round(sr * r))},aresample={sr}," + ",".join(tempo)

    def delay(ms):
        return f",adelay={int(ms)}" if ms and ms > 0 else ""

    if float(p.get("dry", 0)) > 0:
        layers.append(f"volume={float(p['dry']):.3f}")
    if float(p.get("down_vol", 0)) > 0:
        layers.append(f"{shift(p.get('down_st', -4))},volume={float(p['down_vol']):.3f}{delay(dl[0])}")
    if float(p.get("up_vol", 0)) > 0:
        layers.append(f"{shift(p.get('up_st', 3))},volume={float(p['up_vol']):.3f}{delay(dl[1])}")
    if float(p.get("rev_vol", 0)) > 0:
        layers.append(f"areverse,volume={float(p['rev_vol']):.3f}{delay(dl[2])}")
    if float(p.get("whisper", 0)) > 0:                   # breath: the spectrum kept, the phase random
        layers.append(f"{shift(p.get('whisper_st', 0))},highpass=f=300,"
                      "afftfilt=real='hypot(re,im)*cos((random(0)*2-1)*3.14)':"
                      "imag='hypot(re,im)*sin((random(1)*2-1)*3.14)':win_size=512:overlap=0.75,"
                      f"volume={2.5 * float(p['whisper']):.3f}")
    if float(p.get("growl", 0)) > 0:                     # rough: a low copy shaken 45 times a second
        layers.append(f"{shift(p.get('down_st', -5))},tremolo=f=45:d=0.9,lowpass=f=2500,"
                      f"volume={float(p['growl']):.3f}{delay(dl[0])}")
    if not layers:
        layers = ["anull"]
    n = len(layers)
    chains.append(f"[0:a]asplit={n}" + "".join(f"[s{i}]" for i in range(n)) if n > 1 else "[0:a]anull[s0]")
    for i, l in enumerate(layers):
        chains.append(f"[s{i}]{l}[l{i}]")
    mix = "".join(f"[l{i}]" for i in range(n)) + (f"amix=inputs={n}:normalize=0:duration=longest" if n > 1 else "anull")
    e = float(p.get("echo", 0))
    if e > 0:
        mix += f",aecho=0.8:0.85:70|140:{0.45 * e:.3f}|{0.25 * e:.3f}"
    chains.append(mix + ",highpass=f=70[out]")
    return ";".join(chains)


def render(ffmpeg, x, sr, preset, tail_s=0.4):
    """float mono samples -> the preset's sound, len(x) + tail_s, the same loudness as x while the words are spoken"""
    import numpy as np
    p = (PRESETS.get(preset) if isinstance(preset, str) else preset) or PRESETS["ghost"]
    x = np.asarray(x, dtype=np.float32)
    pad = int(round(tail_s * sr))
    with tempfile.TemporaryDirectory() as td:
        a, b = os.path.join(td, "in.f32"), os.path.join(td, "out.f32")
        np.concatenate([x, np.zeros(pad, np.float32)]).tofile(a)
        cmd = [ffmpeg or "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "f32le", "-ar", str(sr), "-ac", "1",
               "-i", a, "-filter_complex", _filter(p, sr), "-map", "[out]", "-f", "f32le", "-ar", str(sr), "-ac", "1", b]
        r = subprocess.run(cmd, capture_output=True, text=True,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if r.returncode != 0 or not os.path.exists(b):
            raise RuntimeError("ffmpeg: " + (r.stderr or "").strip()[-300:])
        y = np.fromfile(b, dtype=np.float32)
    n = len(x) + pad
    y = np.concatenate([y, np.zeros(max(0, n - len(y)), np.float32)])[:n]
    rms = lambda v: float(np.sqrt(np.mean(v.astype(np.float64) ** 2))) if len(v) else 0.0
    gx, gy = rms(x), rms(y[:len(x)])
    if gy > 1e-6 and gx > 0:
        y *= gx / gy
    peak = float(np.max(np.abs(y))) if len(y) else 0
    if peak > 0.97:
        y *= 0.97 / peak
    return y


def read_wav(path):
    """-> (float samples [n, ch], sr, sampwidth) for 16/24/32-bit PCM wav"""
    import numpy as np
    with wave.open(path, "rb") as w:
        ch, width, sr, n = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
        raw = w.readframes(n)
    if width == 2:
        a = np.frombuffer(raw, "<i2").astype(np.float32) / 32768.0
    elif width == 4:
        a = np.frombuffer(raw, "<i4").astype(np.float32) / 2147483648.0
    elif width == 3:
        b = np.frombuffer(raw, np.uint8).reshape(-1, 3)
        v = (b[:, 0].astype(np.int32) | (b[:, 1].astype(np.int32) << 8) | (b[:, 2].astype(np.int32) << 16))
        a = (np.where(v >= 1 << 23, v - (1 << 24), v)).astype(np.float32) / 8388608.0
    else:
        raise ValueError(f"{width * 8}-bit wav")
    return a.reshape(-1, ch), sr, width


def write_wav(path, a, sr, width):
    import numpy as np
    a = np.clip(a, -1.0, 1.0)
    if width == 2:
        raw = (a * 32767.0).astype("<i2").tobytes()
    elif width == 4:
        raw = (a * 2147483647.0).astype("<i4").tobytes()
    else:
        v = (a * 8388607.0).astype(np.int32).reshape(-1)
        raw = np.stack([v & 255, (v >> 8) & 255, (v >> 16) & 255], 1).astype(np.uint8).tobytes()
    tmp = path + ".part"
    with wave.open(tmp, "wb") as w:
        w.setnchannels(a.shape[1]); w.setsampwidth(width); w.setframerate(sr)
        w.writeframes(raw)
    os.replace(tmp, path)


def apply(ffmpeg, dry, sr, segs):
    """segs = [{start, end, tail, preset}] in seconds -> a copy of dry (samples [n, ch]) with each segment re-voiced"""
    import numpy as np
    out = dry.copy()
    n = len(dry)
    fade = int(0.015 * sr)
    for s in segs:
        a = max(0, int((float(s["start"]) - 0.03) * sr))
        b = min(n, int((float(s["end"]) + 0.02) * sr))
        tail = max(0.0, float(s.get("tail", 0.3)))
        c = min(n, b + int(tail * sr))
        if b - a < int(0.2 * sr):
            continue
        y = render(ffmpeg, dry[a:b].mean(axis=1), sr, s.get("preset") or "ghost", (c - b) / sr)[:c - a]
        if c - b > 2 * fade:                             # the tail dies out before the next words
            y[-2 * fade:] *= np.linspace(1, 0, 2 * fade, dtype=np.float32)
        w = np.ones(c - a, np.float32)
        w[:fade] = np.linspace(0, 1, fade, dtype=np.float32)
        for k in range(dry.shape[1]):
            out[a:c, k] = dry[a:c, k] * (1 - w) + y * w
    return out


# ------------------------------------------------------------------ matching the voiced lines
def _words(t):
    t = re.sub(r"\(\s*(?:\.{2,}|…)[^)]*\)", " ", _TAG.sub("", str(t or "")))
    return [w.replace("़", "").replace("ँ", "ं") for w in _WORD.findall(t.lower())]


def find_lines(tim, quote, after=0):
    """the run of timing.json lines that is the quote -> (first, last, ratio) or None (then it is left alone)"""
    q = " ".join(_words(quote))
    if not q:
        return None
    best = None
    for i in range(after, len(tim)):
        run = []
        for j in range(i, min(len(tim), i + 8)):
            run += _words(tim[j].get("text", ""))
            got = " ".join(run)
            if len(got) > 1.4 * len(q) + 8:
                break
            r = difflib.SequenceMatcher(None, q, got, autojunk=False).ratio()
            if r >= 0.85 and (best is None or r > best[2] + 1e-9):
                best = (i, j, r)
        if best and best[2] >= 0.98:
            break
    return best


# ------------------------------------------------------------------ steps
def _plan_path(ctx, name):
    import tools
    return os.path.join(tools.ctx_folder(ctx, "scripts"), tools.slug(name) + ".voice_cast.json")


def step(ctx, name, genre=""):
    """before the voice: who speaks each quote; ghost lines chosen by the rules and put on lines of their own"""
    import tools
    cfg, say = ctx["cfg"], ctx["say"]
    s = settings(genre)
    if not s.get("on") or not any((s.get("roles") or {}).values()):
        return say(f"   ghost voice: off for {genre or 'this series'} (Tinker table voice_cast.CAST)")
    p = os.path.join(tools.ctx_folder(ctx, "scripts"), tools.slug(name) + ".txt")
    if not os.path.exists(p):
        return say("   (no script - ghost voice not checked)")
    text = open(p, encoding="utf-8").read()
    sp = spans(text)
    plan = {"script": p, "made": time.strftime("%Y-%m-%d %H:%M:%S"), "genre": genre, "quotes": len(sp),
            "chosen": [], "rejected": [], "replies": []}
    if not sp:
        say("   ghost voice: no quoted lines in the script - everything stays in the narrator voice")
    else:
        G = tools.GENRES.get(genre) or {}
        prompt = CAST_ASK.format(desc=G.get("desc", genre), story=story_for_ask(text, sp))
        model = str(CAST.get("model") or "").strip() if isinstance(CAST, dict) else ""
        if not model:
            import story
            model = story.local_model(cfg)
        c = dict(cfg, _cast_model=model, _no_think=not (isinstance(CAST, dict) and CAST.get("think")))
        replies = []
        for k in range(max(1, int(s.get("asks", 2) or 1))):
            try:
                t, model = tools._ask_step(c, "_cast_model", prompt, 0.2, say)
                replies.append(parse_reply(tools._loads(t)))
                plan["replies"].append(t)
            except Exception as e:
                say(f"   (ghost voice: the AI gave no usable answer, ask {k + 1}: {e})")
                replies.append({})
        _unload(cfg, model)                              # the voice needs the graphics card next
        chosen, rejected = decide(sp, replies, s)
        plan.update(model=model, chosen=chosen, rejected=rejected)
        for c in chosen:
            say(f"   ghost voice ({c['preset']}): {c['speaker'] or c['role']}: \"{c['quote'][:70]}\"")
        for r in rejected:
            say(f"   narrator voice: \"{r['quote'][:50]}\" - {r['why']}")
        if chosen:
            new = own_lines(text, sp, chosen)
            if new != text:
                old = os.path.join(os.path.dirname(p), "_old")
                os.makedirs(old, exist_ok=True)
                keep = os.path.join(old, f"{tools.slug(name)}_before_voice_cast_{time.strftime('%Y%m%d_%H%M%S')}.txt")
                shutil.copy2(p, keep)
                open(p + ".part", "w", encoding="utf-8").write(new)
                os.replace(p + ".part", p)
                plan["original"] = keep
        say(f"   {len(chosen)} of {len(sp)} quoted line(s) will get the ghost voice")
    json.dump(plan, open(_plan_path(ctx, name), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return True


def _unload(cfg, model):
    import urllib.request
    try:
        url = str(cfg.get("ollama_url") or "http://127.0.0.1:11434").rstrip("/") + "/api/generate"
        urllib.request.urlopen(urllib.request.Request(url, json.dumps({"model": model, "keep_alive": 0}).encode(),
                                                      {"Content-Type": "application/json"}), timeout=30).read()
    except Exception:
        pass


def _sig(path):
    st = os.stat(path)
    return [st.st_size, round(st.st_mtime, 3)]


def _report(ctx, nar, plan, done, missing):
    """voice_cast.txt in the episode folder (or the narration folder): what got the ghost voice and what did not"""
    ws = ctx["cfg"].get("_experimental_workspace") or {}
    folder = ws.get("episode_dir") or nar
    mmss = lambda t: f"{int(t) // 60}:{int(t) % 60:02d}"
    rows = [f"Ghost voice - {time.strftime('%Y-%m-%d %H:%M')} (voice_cast.py; change it on the Tinker page: "
            "voice_cast.CAST / voice_cast.PRESETS)", ""]
    rows.append(f"Got the ghost voice ({len(done)}):" if done else "Nothing got the ghost voice.")
    for d in done:
        rows.append(f"  {mmss(d['start'])}  {d['preset']:<14} {d['speaker'] or d['role']}: \"{d['quote']}\"")
    if missing:
        rows += ["", "Chosen but not found in the voice (left in the narrator voice):"]
        rows += [f"  \"{m['quote']}\"" for m in missing]
    if plan.get("rejected"):
        rows += ["", "Quoted lines left in the narrator voice:"]
        rows += [f"  \"{r['quote'][:80]}\" - {r['why']}" for r in plan["rejected"]]
    rows += ["", f"The voice without the effect: {os.path.join(nar, 'narration_dry.wav')}"]
    try:
        open(os.path.join(folder, "voice_cast.txt"), "w", encoding="utf-8").write("\n".join(rows) + "\n")
    except OSError:
        pass


def fx(ctx, name):
    """before the mix: the chosen lines re-voiced in narration.wav (always from the clean narration_dry.wav)"""
    import tools
    cfg, say = ctx["cfg"], ctx["say"]
    jp = _plan_path(ctx, name)
    if not os.path.exists(jp):
        return True
    try:
        plan = json.load(open(jp, encoding="utf-8"))
    except (OSError, ValueError) as e:
        return say(f"   (ghost voice skipped: {e})")
    nar = tools.narration_dir(cfg, name, existing=True)
    wav, dry_p, rec_p = (os.path.join(nar, f) for f in ("narration.wav", "narration_dry.wav", "voice_fx.json"))
    if not os.path.exists(wav):
        return say("   (no narration.wav - ghost voice skipped)")
    try:
        rec = json.load(open(rec_p, encoding="utf-8"))
    except (OSError, ValueError):
        rec = {}
    ours = os.path.exists(dry_p) and rec.get("out") == _sig(wav)
    if not ours:                                         # a new narration (or the first run): it is the clean voice
        shutil.copy2(wav, dry_p)
    chosen = plan.get("chosen") or []
    try:
        tim = json.load(open(os.path.join(nar, "timing.json"), encoding="utf-8"))
    except (OSError, ValueError):
        tim = []
    segs, done, missing, after = [], [], [], 0
    for c in chosen:
        hit = find_lines(tim, c["quote"], after)
        if not hit:
            missing.append(c)
            say(f"   ⚠ ghost voice: \"{c['quote'][:50]}\" not found in the voice's lines - narrator voice kept")
            continue
        i, j, r = hit
        after = j + 1
        for k in range(i, j + 1):
            nxt = float(tim[k + 1]["start"]) if k + 1 < len(tim) else float(tim[k]["end"]) + 1.0
            segs.append({"start": float(tim[k]["start"]), "end": float(tim[k]["end"]), "preset": c["preset"],
                         "tail": max(0.0, min(0.45, nxt - float(tim[k]["end"]) - 0.08))})
        done.append(dict(c, start=float(tim[i]["start"]), lines=[tim[k].get("n", k + 1) for k in range(i, j + 1)]))
    words = os.path.join(nar, "words.json")
    words_fresh = os.path.exists(words) and os.path.getmtime(words) >= os.path.getmtime(wav)
    if segs:
        dry, sr, width = read_wav(dry_p)
        write_wav(wav, apply(cfg.get("ffmpeg") or "ffmpeg", dry, sr, segs), sr, width)
    elif ours:                                           # nothing to do any more: back to the clean voice
        shutil.copy2(dry_p, wav)
    if words_fresh:
        os.utime(words, None)                            # same timing: Whisper need not listen again
    json.dump({"out": _sig(wav), "made": time.strftime("%Y-%m-%d %H:%M:%S"), "done": done, "missing": missing},
              open(rec_p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    for d in done:
        say(f"   ghost voice at {int(d['start']) // 60}:{int(d['start']) % 60:02d} ({d['preset']}): \"{d['quote'][:60]}\"")
    say(f"   {len(done)} line(s) in the ghost voice" + (f", {len(missing)} not found" if missing else "")
        + " - report: voice_cast.txt in the episode folder")
    _report(ctx, nar, plan, done, missing)
    return True


if __name__ == "__main__":
    args = sys.argv[1:]
    ff = "ffmpeg"
    if "--ffmpeg" in args:
        k = args.index("--ffmpeg"); ff = args[k + 1]; del args[k:k + 2]
    if len(args) < 2:
        print(__doc__)
        sys.exit(1)
    a, sr, width = read_wav(args[0])
    y = render(ff, a.mean(axis=1), sr, args[2] if len(args) > 2 else "ghost", 0.5)
    write_wav(args[1], y.reshape(-1, 1), sr, width)
    print("wrote", args[1])
