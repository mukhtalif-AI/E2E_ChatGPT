"""speak.py - a Sukoon script read in YOUR (generated) voice.

    run.py speak <script.txt | episode folder with script.txt> [--style Burari Kand] [--name N] [--model best|base|<run>]
                 [--redo 3,17]      --style = read in the tone of that episode (default: your Sukoon prompt)

One phrase per line = a breath, (...) = long pause, blank line = paragraph; headings / (notes) are skipped;
a line of only dashes (-----) ends the script (only the FIRST version is read). Every sentence is made on its own, checked by ear (Hindi Whisper) and retaken
if needed; then joined with your kind of pauses and brought to one loudness.
Output  <media>\\narrations\\<name>\\  narration.wav (48 kHz), script.txt (what was read), timing.json
(start/end of every sentence), report.txt (sentences that still sounded off -> --redo those numbers),
lines\\NNNN.wav (one per sentence; delete one = it is made again next run). Crash-safe: re-run continues.
"""
import json, os, re, subprocess, time
import numpy as np
from . import cfg
from .model import load_base, resolve_checkpoint, resolve_reference, use_checkpoint, set_voice, speak_sentence, \
    words_per_second, say
from .textsplit import sentences


CONJ = {"और", "लेकिन", "पर", "जब", "तो", "कि", "क्योंकि", "जैसे", "अगर", "ताकि", "या", "फिर", "मगर", "बल्कि",
        "जहाँ", "जहां", "वरना", "इसलिए"}
OBL = {"उस", "इस", "जिस", "किस", "छत", "मेज़", "मेज", "दीवार", "खिड़की", "दरवाज़े", "दरवाजे", "ज़मीन", "जमीन", "बिस्तर",
       "सड़क", "रास्ते", "कंधे", "चेहरे", "हाथ", "सिर", "पेड़", "घर", "छाती", "पलंग", "कुर्सी", "गाड़ी", "बस", "स्टेशन"}
VERB_END = re.compile(r"(है|हैं|था|थी|थे|हूं|हूँ|हो|गा|गी|गे|ें|ो|ना)$")
EDITORIAL_TAIL = re.compile(
    r"(?i)\b(?:the repetition of|the repeated (?:phrase|line|words)|this repetition|"
    r"the ending is meant to|this ending is meant to|the final line is meant to|"
    r"the story is meant to|this story is intended to|the intention is|"
    r"note:|editor['’]s note|as requested,|i hope you (?:enjoy|like)|"
    r"the (?:story|script|ending) above)\b"
)


def _strip_editorial_tail(text):
    """Keep appended English author notes out of Hindi narration."""
    match = EDITORIAL_TAIL.search(text)
    if not match:
        return text
    text = text[:match.start()]
    hindi = list(re.finditer(r"[\u0900-\u097F]", text))
    if hindi:
        end = hindi[-1].end()
        while end < len(text) and text[end] in "।.!?…\"”'’":
            end += 1
        text = text[:end]
    return text.rstrip()


def chunk(s, max_words=28):
    """Only very long sentences get a fallback clause split; punctuation remains the main guide."""
    w = s.split()
    if len(w) <= max_words:
        return [s]
    mid, best, best_score = len(w) / 2, None, -1e9
    for i in range(4, len(w) - 3):
        score = -abs(i - mid) / len(w)
        if w[i] in CONJ and not (w[i] in ("पर", "तो") and not (VERB_END.search(w[i - 1].strip(",.।")) or
                                                           re.search(r"(ा|ी|े)$", w[i - 1]) and w[i - 1] not in OBL)):
            score += 1.0                                    # 'पर' after a noun = 'on' (खिड़की पर) - never cut there
        elif VERB_END.search(w[i - 1].strip(",.।")):
            score += 0.6
        if w[i - 1].endswith(","):
            score += 1.2
        if score > best_score:
            best, best_score = i, score
    return chunk(" ".join(w[:best]), max_words) + chunk(" ".join(w[best:]), max_words)


def read_script(path):
    """script -> [{"text", "pause"}]. Understands the Sukoon script styles: one phrase per line (line break =
    breath), '(...)' = a long pause, '...' = a pause, blank line = paragraph. Headings (emoji / 'भाग 1:')
    and stage notes in (brackets) are not read. A line of only dashes ends the script (first version only)."""
    if os.path.isdir(path):
        path = os.path.join(path, "script.txt")
    if not os.path.exists(path):
        raise SystemExit(f"no script found: {path}")
    S = cfg.C["speak"]
    raw = open(path, encoding="utf-8-sig").read().replace("\r\n", "\n")
    clean_raw = _strip_editorial_tail(raw)
    if clean_raw != raw:
        say("   removed an AI editor note appended after the story")
        raw = clean_raw
    parts = [p for p in re.split(r"\n\s*-{3,}\s*\n", "\n" + raw + "\n") if p.strip()]
    if len(parts) > 1:
        say("   (the script has several versions separated by ----- : reading the FIRST one)")
    text = parts[0] if parts else ""
    paced = bool(re.search(r"\(\s*paced\s*\)", text, re.I))     # pauses already baked in (dramatic pass)
    text = re.sub(r"[ \t]*\n?[ \t]*\(\s*(?:\.{2,}|…)\s*([0-9]+(?:\.[0-9]+)?)\s*s?\s*\)[ \t]*\n?",
                  lambda m: f"\n\u23f8{m.group(1)}\n", text)           # (... 1.8) = a pause of exactly 1.8 s
    text = re.sub(r"[ \t]*\n?[ \t]*\(\s*(\.{2,}|…)\s*\)[ \t]*\n?", "\n\u23f8\n", text)  # (...) = long pause
    text = re.sub(r"\([^)]*\)", " ", text)                             # stage notes
    text = re.sub(r"^[ \t]*\[([^\]\n]{1,20})\][ \t]*", lambda m: "\u2766" + m.group(1).strip() + "\u2766", text,
                  flags=re.M)                                              # [डर] at a line start = how it feels
    text = re.sub(r"\[[^\]\n]*\]", " ", text)
    text = text.replace("…", "...")
    units, ln = [], 0

    def pause(v):
        if units:
            units[-1]["pause"] = max(units[-1]["pause"], float(v))

    for line in text.split("\n"):
        emo = ""
        em = re.match(r"^\s*\u2766([^\u2766]*)\u2766", line)
        if em:
            emo, line = em.group(1), line[em.end():]
        l = line.strip().strip('"“”\'‘’').strip()
        if not l:
            pause(S["pause_paragraph"]); continue
        if l == "⏸":
            pause(S["pause_marker"]); continue
        if l.startswith("⏸"):
            if units:
                units[-1]["pause"] = float(l[1:]); units[-1]["fixed"] = True
            continue
        q = re.search(r'["“](.*\S.*)$', line)
        if q and re.match(r"^\s*[^\w\"'“‘\u0900-\u097F.]", line):    # heading + text on one line
            l = q.group(1).strip().strip('"“”').strip()
        if re.match(r"^[^\w\"'“‘ऀ-ॿ.]", l) or \
                re.match(r"^(भाग|part|episode|एपिसोड|chapter)\s*\d+\s*[:：.-]", l, re.I):
            pause(S["pause_paragraph"]); continue                       # a heading
        if not re.search("[ऀ-ॿ]", l):
            continue
        ln += 1
        if units and len(l.split()) <= 2 and units[-1]["pause"] <= float(S["pause_line"]) and \
                units[-1].get("emo", "") == emo and \
                not re.search(r"[।.?!]$", units[-1]["text"]):
            units[-1]["text"] += " " + l                                # a line that only finishes the last one
            continue
        for s in sentences(l):
            for c in chunk(s):
                c = c.strip().strip('"“”').strip()
                if not re.search("[ऀ-ॿ]", c):
                    continue
                e = c.rstrip()
                p = S["pause_dots"] if e.endswith("...") else S["pause_comma"] if e.endswith(",") else \
                    S["pause_sentence"] if re.search(r"[।.?!]$", e) else S["pause_comma"]
                units.append(dict({"text": c, "pause": float(p), "ln": ln}, **({"emo": emo} if emo else {})))
        pause(S["pause_line"])
    if units:
        units[-1]["pause"] = 0.0
        units[0]["paced"] = paced
    return path, units


NUMW = {"एक", "दो", "तीन", "चार", "पांच", "पाँच", "छह", "छः", "सात", "आठ", "नौ", "दस", "one", "two", "three", "four",
        "five", "six", "seven", "eight", "nine", "ten"}
ENDING = re.compile(r"सो जाओ|सो जाइए|गुड नाईट|गुड नाइट|शुभ रात्रि|शब्बा ख़ैर|शब्बा खैर")


def slow_counts(units, S):
    """counted breaths ("एक... दो... तीन...") were read fast, in one go. Every number becomes its own phrase with a
    breath-long pause after it (pause_count, default 2.4 s) - the listener really has time to breathe."""
    pc = float(S.get("pause_count", 2.4))
    out = []
    for u in units:
        pieces = [x.strip(" ,।.") for x in re.split(r"\.\.\.|…|,", u["text"])]
        nums = [x for x in pieces if x in NUMW]
        if len(nums) < 2:
            out.append(u); continue
        rest = []
        for x in pieces:
            if not x:
                continue
            if x in NUMW:
                if rest:
                    out.append({"text": "... ".join(rest) + "...", "pause": float(S["pause_dots"])}); rest = []
                out.append({"text": x + "...", "pause": pc, "count": True})
            else:
                rest.append(x)
        if rest:
            out.append({"text": "... ".join(rest) + "...", "pause": u["pause"]})
        else:
            out[-1]["pause"] = max(out[-1]["pause"], u["pause"])
    return out


def gentle_ending(plan, S):
    """the good-night lines are whispered, not commanded: quieter, slower, with long pauses between them"""
    n = len(plan)
    start = next((i for i in range(max(0, n - 8), n) if ENDING.search(plan[i]["text"])), None)
    if start is None:
        return plan
    for i in range(start, n):
        plan[i]["ending"] = True
        if i + 1 < n:
            plan[i]["pause"] = max(plan[i]["pause"] * float(S.get("ending_pause_scale", 1.8)), 2.5)
    if start > 0:
        plan[start - 1]["pause"] = max(plan[start - 1]["pause"], 2.5)
    return plan

def _jit(text, lo=0.85, hi=1.2):
    import zlib
    return lo + (hi - lo) * (zlib.crc32(text.encode("utf-8")) % 1000) / 999.0


def dramatic(units, S):
    """Honor explicit author/LLM ellipses; do not infer drama from fixed words or line length."""
    out = [dict(u) for u in units]
    for u in out:
        if u.get("fixed"):
            continue
        if u["text"].rstrip().endswith(("...", "…")):
            u["pause"] = max(float(u["pause"]), float(S.get("pause_tension", 1.7)))
            u["hold"] = True
    out = vary(out, S)
    if out:
        out[-1]["pause"] = 0.0
    return out


def vary(units, S):
    """every ordinary line / paragraph silence a little different (0.85-1.2x, fixed per line) - no metronome"""
    for u in units:
        if not u.get("hold") and not u.get("fixed") and u["pause"] >= float(S["pause_line"]) - 0.01:
            u["pause"] = round(u["pause"] * _jit(u["text"]), 2)
    return units


# --- emotions: a line marked [डर] / [उदास] / … is read a little differently - only gentle nudges of the SAME voice
# (expressiveness, loudness, the pause after it); between lines the change is limited, so no sudden pitch jumps.
EMO = {  # expressiveness +/-, temperature +/-, loudness dB, pause x
    "डर": (0.12, 0.0, -1.0, 1.15), "fear": (0.12, 0.0, -1.0, 1.15),
    "फुसफुसाहट": (-0.15, -0.05, -5.0, 1.2), "whisper": (-0.15, -0.05, -5.0, 1.2),
    "रहस्य": (0.05, 0.0, -1.5, 1.2), "mystery": (0.05, 0.0, -1.5, 1.2),
    "उदास": (-0.05, 0.0, -2.0, 1.25), "sad": (-0.05, 0.0, -2.0, 1.25),
    "प्यार": (-0.05, 0.0, -2.0, 1.1), "tender": (-0.05, 0.0, -2.0, 1.1),
    "गुस्सा": (0.15, 0.0, 1.0, 0.9), "angry": (0.15, 0.0, 1.0, 0.9),
    "उत्साह": (0.12, 0.0, 1.0, 0.9), "excited": (0.12, 0.0, 1.0, 0.9),
    "सामान्य": (0, 0, 0, 1), "normal": (0, 0, 0, 1)}


def emotions(plan, S):
    """sets ex / temp / gain per phrase from its [mark]; the step from one phrase to the next is at most
    emotion_step (0.08) of expressiveness - feelings fade in and out instead of switching"""
    k = float(S.get("emotion_strength", 1.0))
    base_ex, base_t = float(S["exaggeration"]), float(S["temperature"])
    step = float(S.get("emotion_step", 0.08))
    lo, hi = float(S.get("emotion_min_ex", 0.25)), float(S.get("emotion_max_ex", 0.75))
    prev = base_ex
    for p in plan:
        d = EMO.get(str(p.get("emo", "")).lower() if p.get("emo", "").isascii() else p.get("emo", ""), (0, 0, 0, 1))
        want = min(hi, max(lo, base_ex + d[0] * k))
        ex = round(min(prev + step, max(prev - step, want)), 3)
        p["ex"], p["temp"], p["gain_db"] = ex, round(base_t + d[1] * k, 3), d[2] * k
        if not p.get("fixed") and not p.get("hold") and p["pause"] > 0:
            p["pause"] = round(p["pause"] * (1 + (d[3] - 1) * k), 2)
        prev = ex
    return plan


def bake_script(spath, units, S):
    """write the paced script back (the original is kept in _old\\): every held silence as (... 1.8), so the
    script itself says how it is read - edit a number there and remake to change one pause"""
    import shutil
    old = os.path.join(os.path.dirname(spath), "_old")
    os.makedirs(old, exist_ok=True)
    base = os.path.splitext(os.path.basename(spath))[0]
    shutil.copy2(spath, os.path.join(old, f"{base}_before_pacing_{time.strftime('%Y%m%d_%H%M')}.txt"))
    para = float(S["pause_paragraph"])
    lines, cur, cur_ln = ["(paced)", ""], [], None
    for i, u in enumerate(units):
        if cur and (u.get("ln") != cur_ln):
            lines.append(" ".join(cur)); cur = []
        cur.append(("[" + u["emo"] + "] " if u.get("emo") and not cur else "") + u["text"].strip())
        cur_ln = u.get("ln")
        last = i + 1 == len(units)
        if u.get("hold") or u.get("fixed"):
            lines.append(" ".join(cur)); cur = []; cur_ln = None
            if not last:
                lines.append(f"(... {u['pause']:.2f})")
        elif u["pause"] >= para * 0.84 and not last:
            lines.append(" ".join(cur)); cur = []; cur_ln = None
            lines.append("")
    if cur:
        lines.append(" ".join(cur))
    tmp = spath + ".part"
    open(tmp, "w", encoding="utf-8").write("\n".join(lines).rstrip() + "\n")
    os.replace(tmp, spath)


def flow_groups(plan, S):
    """flow mode: neighbouring phrases are spoken in ONE breath-group (like your real reading), so the voice
    carries on from one phrase into the next instead of ending every phrase. A long pause ((...) or a new
    paragraph) or ~flow_max_words words ends a group. Ordinary boundaries stay connected; supplied punctuation is
    preserved, and ellipses are never added just because a line ended."""
    mx = int(S.get("flow_max_words", 28))
    stop = float(S.get("pause_marker", 1.6)) - 0.01
    out, cur = [], []

    def close():
        if cur:
            t = cur[0]["text"].strip()
            for a, b in zip(cur, cur[1:]):
                right = b["text"].strip()
                # Keep source punctuation; a normal breath boundary is not a new comma or stop.
                t += " " + right
            out.append(dict({"text": t, "pause": cur[-1]["pause"], "parts": len(cur)},
                            **({"emo": cur[0]["emo"]} if cur[0].get("emo") else {}),
                            **{k: True for k in ("hold", "fixed") if cur[-1].get(k)}))
            cur.clear()

    for p in plan:
        if p.get("count") or p.get("ending"):              # counted breaths + good-night lines stay alone
            close()
            out.append(dict(p, parts=1))
            continue
        if cur and (sum(len(c["text"].split()) for c in cur) + len(p["text"].split()) > mx or
                    cur[-1].get("emo", "") != p.get("emo", "")):
            close()
        cur.append(p)
        if p["pause"] >= stop or p.get("hold") or p.get("fixed"):
            close()
    close()
    return out


def acoustic_prompt_from_tail(m, anchor, audio, sr, seconds=2.5):
    """Replace only the end of the reference speech prompt with tokens from the previous phrase.

    Keep the original prompt as the speaker anchor. Chatterbox caches prompt embeddings, so clear
    that cache whenever the token sequence changes.
    """
    from math import gcd
    from scipy.signal import resample_poly
    import torch

    if audio is None or not len(audio) or anchor is None or anchor.ndim != 2 or not anchor.shape[1]:
        return None
    n_audio = min(len(audio), max(1, int(float(seconds) * sr)))
    tail = np.asarray(audio[-n_audio:], dtype=np.float32)
    if sr != 16000:
        div = gcd(int(sr), 16000)
        tail = resample_poly(tail, 16000 // div, int(sr) // div).astype(np.float32, copy=False)
    with torch.inference_mode():
        speech, lengths = m.s3gen.tokenizer.forward([np.asarray(tail, dtype=np.float32)])
    valid = int(lengths[0])
    max_tokens = min(anchor.shape[1], max(1, int(float(seconds) * 25)))
    count = min(valid, max_tokens)
    if count < 1:
        return None
    tail_tokens = speech[0, valid - count:valid].to(device=anchor.device, dtype=anchor.dtype).unsqueeze(0)
    return torch.cat((anchor[:, :-count], tail_tokens), dim=1)


def main(src, name=None, model=None, redo="", style=None, pace=None, s3gen=None, flow=None, overrides=None):
    import soundfile as sf
    S = dict(cfg.C["speak"])
    for k, v in (overrides or {}).items():              # --set key=value (one run only, config.json unchanged)
        S[k] = v
        say(f"   setting for this run: {k} = {v}")
    if pace:
        S["pace"] = pace
    spath, plan = read_script(src)
    if not plan:
        raise SystemExit("the script is empty")
    # plan version 2 (slow counts, soft ending) only for NEW narrations: an older narration keeps its line numbering,
    # so remaking single lines of it (Repair) still replaces the right lines
    _nm = name or (os.path.basename(os.path.dirname(spath)) if os.path.basename(spath).lower() == "script.txt"
                   else os.path.splitext(os.path.basename(spath))[0])
    _out = os.path.join(cfg.NARRATIONS, re.sub(r'[<>:"/\\|?*]', "_", _nm))
    _pv = os.path.join(_out, "plan_version.txt")
    _old = os.path.isdir(os.path.join(_out, "lines")) and any(f.endswith(".wav") for f in os.listdir(os.path.join(_out, "lines")))
    if not _old or (os.path.exists(_pv) and open(_pv).read().strip() == "2"):
        n0 = len(plan)
        plan = gentle_ending(slow_counts(plan, S), S)
        if str(S.get("dramatic", "")).lower() in ("1", "true", "yes"):
            if plan[0].get("paced"):
                plan = vary(plan, S)
                say("   dramatic pacing: already baked into the script (paced) - read as written")
            else:
                plan = dramatic(plan, S)
                nh = sum(1 for p in plan if p.get("hold"))
                say(f"   dramatic pacing: {nh} held silences (tension words, reveals, questions) - pitch untouched")
                try:
                    bake_script(spath, plan, S)
                    say("   the paced script was saved back (old one kept in _old) - (... 1.8) = a held pause you can edit")
                except Exception as e:
                    say(f"   (could not save the paced script: {e})")
        if len(plan) != n0:
            say(f"   counted breaths read slowly: one number at a time ({len(plan) - n0} extra pauses)")
        os.makedirs(_out, exist_ok=True)
        open(_pv, "w").write("2")
    if flow is not None:
        S["flow"] = bool(flow)
    if S.get("flow"):
        n0 = len(plan)
        plan = flow_groups(plan, S)
        say(f"   flow mode: {n0} phrases spoken as {len(plan)} breath-groups")
    if str(S.get("emotions", "")).lower() in ("1", "true", "yes"):
        plan = emotions(plan, S)
        ne = sum(1 for p in plan if p.get("emo"))
        say(f"   emotions: {ne} phrases with a feeling mark (gentle: expressiveness {min(p['ex'] for p in plan):.2f}"
            f"-{max(p['ex'] for p in plan):.2f}, pitch untouched)")
    name = name or (os.path.basename(os.path.dirname(spath)) if os.path.basename(spath).lower() == "script.txt"
                    else os.path.splitext(os.path.basename(spath))[0])
    if pace and str(pace).lower() != "auto":
        name = f"{name}_{re.sub(r'[^a-z]+', '_', str(pace).lower()).strip('_')}"   # its own folder -> compare
    out = os.path.join(cfg.NARRATIONS, re.sub(r'[<>:"/\\|?*]', "_", name))
    ldir = os.path.join(out, "lines")
    os.makedirs(ldir, exist_ok=True)
    ck_path = resolve_checkpoint(model or S["model"])
    stamp = os.path.join(out, "voice_used.txt")
    ref = resolve_reference(style)
    from .model import resolve_s3gen
    s3_path = resolve_s3gen(s3gen if s3gen is not None else S.get("s3gen", "base"))
    gen = {k: v for k, v in S.items() if not k.startswith(("pause", "breath", "loudness", "music", "with_music", "_"))}
    used = f"{ck_path or 'base (no training)'}|{s3_path or 's3 base'}|{ref}|{json.dumps(gen, sort_keys=True)}"   # joining-only settings
    # (pauses, breaths, loudness) do not force new phrases
    if os.path.exists(stamp) and open(stamp, encoding="utf-8").read().strip() != used:
        old = os.path.join(out, time.strftime("lines_old_%Y%m%d_%H%M"))
        os.replace(ldir, old)
        prev = os.path.join(out, "narration.wav")              # keep the previous version to compare
        if os.path.exists(prev):
            os.replace(prev, os.path.join(out, time.strftime("narration_old_%Y%m%d_%H%M.wav")))
        os.makedirs(ldir)
        say(f"   voice or settings changed since the last run - old lines kept in {os.path.basename(old)}\\")
    open(stamp, "w", encoding="utf-8").write(used)
    for n in [int(x) for x in re.findall(r"\d+", redo or "")]:
        p = os.path.join(ldir, f"{n:04d}.wav")
        if os.path.exists(p):
            os.replace(p, p[:-4] + time.strftime("_old_%H%M%S.wav"))
    infos_path = os.path.join(out, "_lines.json")
    infos = json.load(open(infos_path, encoding="utf-8")) if os.path.exists(infos_path) else {}
    # a phrase file is kept only while its line still says the same thing: a new script under the same name
    # (a re-run that writes a fresh story) used to reuse the old story's voice line by line
    _t = lambda x: " ".join(str(x or "").split())
    stale = [i for i in range(len(plan)) if os.path.exists(os.path.join(ldir, f"{i + 1:04d}.wav"))
             and (infos.get(str(i + 1)) or {}).get("text") is not None
             and _t(infos[str(i + 1)]["text"]) != _t(plan[i]["text"])]
    if stale:
        old = os.path.join(out, time.strftime("lines_old_%Y%m%d_%H%M%S"))
        os.makedirs(old, exist_ok=True)
        for i in stale:
            os.replace(os.path.join(ldir, f"{i + 1:04d}.wav"), os.path.join(old, f"{i + 1:04d}.wav"))
            infos.pop(str(i + 1), None)
        say(f"   {len(stale)} phrase(s) changed since the last run - made again (old ones in {os.path.basename(old)})")
    todo = [i for i in range(len(plan)) if not os.path.exists(os.path.join(ldir, f"{i + 1:04d}.wav"))]
    words = sum(len(p["text"].split()) for p in plan)
    say(f"   {len(plan)} sentences, {words} words (~{words / words_per_second() / 60:.0f} min of voice); "
        f"{len(todo)} to make")
    if todo:
        m = load_base()
        ck = use_checkpoint(m, ck_path) if ck_path else None
        if s3_path:
            from .model import use_s3gen
            use_s3gen(m, s3_path)
        if ck:
            from .model import patient_brakes
            patient_brakes(S)
        else:
            S["repetition_penalty"] = 2.0                  # plain clone: Chatterbox's own setting
        if not ck:
            say("   voice: plain clone of your voice prompt (no trained add-on found / chosen)")
        say(f"   style (voice prompt): {os.path.basename(ref)}")
        from . import match
        do_match = bool(ck) and S.get("match", True) and match.available()
        if ck and S.get("match", True) and not do_match:
            say("   (match to you is off: praat not installed - run setup.bat)")
        if do_match:
            T = match.targets("sukoon" if (not style or "sukoon" in str(style).lower()) else "story")
            say(f"   match to you: pitch -> {T['f0']:.0f} Hz, pace -> {T['rate']:.1f} words/s")
        # Genre runs pass their series ID separately from `style`, which names
        # the reference recording. Use that ID to select the checkpoint's
        # learned pace class; retain the legacy inference for ordinary runs.
        series = str(S.get("pace_style") or ("sukoon" if (not style or "sukoon" in str(style).lower()) else "story"))
        set_voice(m, ref, ck, float(S["exaggeration"]), style=series, pace_setting=S.get("pace", "auto"))
        wps = words_per_second()
        t0, done_audio = time.time(), 0.0
        use_acoustic_context = bool(S.get("acoustic_context", False))
        context_anchor = None
        if use_acoustic_context:
            prompt = getattr(m.conds.t3, "cond_prompt_speech_tokens", None)
            if prompt is None:
                say("   acoustic context: unavailable (no reference speech tokens); using normal generation")
                use_acoustic_context = False
            else:
                context_anchor = prompt.detach().clone()
                say("   acoustic context: on (2.5 s tail, original prompt retained as anchor)")
        for k, i in enumerate(todo):
            seed = 7 + i + (1000 if redo and f"{i + 1}" in re.findall(r"\d+", redo) else 0)
            Si = dict(S, exaggeration=plan[i]["ex"], temperature=plan[i]["temp"]) if "ex" in plan[i] else S
            if use_acoustic_context:
                # Rebuild each prompt from the original anchor and the immediately previous line.
                # This also handles partial reruns where earlier lines already exist on disk.
                m.conds.t3.cond_prompt_speech_tokens = context_anchor.clone()
                m.conds.t3.cond_prompt_speech_emb = None
                prev_i = i - 1
                prev_path = os.path.join(ldir, f"{prev_i + 1:04d}.wav")
                prev_info = infos.get(str(prev_i + 1), {}) if prev_i >= 0 else {}
                can_chain = prev_i >= 0 and plan[prev_i]["pause"] < float(S["pause_paragraph"]) \
                    and prev_info.get("ok", True) and os.path.exists(prev_path)
                if can_chain:
                    try:
                        prev_audio, prev_sr = sf.read(prev_path, dtype="float32")
                        chained = acoustic_prompt_from_tail(m, context_anchor, prev_audio, prev_sr,
                                                            float(S.get("acoustic_context_seconds", 2.5)))
                        if chained is not None:
                            m.conds.t3.cond_prompt_speech_tokens = chained
                            say(f"   acoustic context: line {prev_i + 1} -> {i + 1}")
                    except Exception as e:
                        say(f"   acoustic context skipped for line {i + 1}: {e}")
            a, info = speak_sentence(m, plan[i]["text"], seed, Si, wps, label=f"[{i + 1}] ")
            if ck and S.get("match", True) and do_match:
                a, mi = match.apply(a, m.sr, plan[i]["text"], series, S)
                info.update(mi)
            p = os.path.join(ldir, f"{i + 1:04d}.wav")
            sf.write(p + ".part.wav", a, m.sr, subtype="PCM_16")
            os.replace(p + ".part.wav", p)
            infos[str(i + 1)] = dict(info, text=plan[i]["text"])
            json.dump(infos, open(infos_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            done_audio += len(a) / m.sr
            el = time.time() - t0
            say(f"   sentence {i + 1}/{len(plan)}  {len(a) / m.sr:4.1f} s"
                f"{'' if info.get('ok') else '  (still not perfect - see report)'}   "
                f"[{k + 1}/{len(todo)} done, ~{el / (k + 1) * (len(todo) - k - 1) / 60:.0f} min left]")
    # join
    sr = 24000
    timing, t, pp = [], 0.0, []
    for i, p in enumerate(plan):
        a, r = sf.read(os.path.join(ldir, f"{i + 1:04d}.wav"), dtype="float32")
        sr = r
        if p.get("ending"):                                # whispered good night: softer than the story
            a = a * float(10 ** (float(S.get("ending_db", -5)) / 20))
        if p.get("count"):
            a = a * float(10 ** (-2 / 20))
        if p.get("gain_db"):
            a = a * float(10 ** (float(p["gain_db"]) / 20))
        timing.append({"n": i + 1, "start": round(t, 3), "end": round(t + len(a) / r, 3), "text": p["text"]})
        g = p["pause"] if i + 1 < len(plan) else 0.0
        pp.append((a, g)); t += len(a) / r + g
    from . import breaths
    pieces, nb = breaths.place(pp, sr, S.get("breaths", "light"), seed=len(plan), S=S)
    if nb:
        say(f"   added {nb} of your real breaths in the pauses (setting: {S.get('breaths')})")
    full = np.concatenate([np.zeros(int(0.5 * sr), np.float32)] + pieces + [np.zeros(int(1.0 * sr), np.float32)])
    if S.get("room_tone", False):
        try:
            from . import roomtone
            full, lvl = roomtone.lay_under(full, sr, float(S.get("room_tone_db", 0)))
            if lvl is not None:
                say(f"   room tone of your recordings laid under the pauses ({lvl:.0f} dB below your voice)")
        except Exception as e:
            say(f"   (room tone skipped: {e})")
    for x in timing:
        x["start"] += 0.5; x["end"] += 0.5
    raw = os.path.join(out, "_joined.wav")
    sf.write(raw, full, sr, subtype="FLOAT")
    final = os.path.join(out, "narration.wav")
    lufs = float(S.get("loudness_lufs", -18))
    af = ("adeclick=w=55:o=75:t=4," if S.get("declick") else "") + f"loudnorm=I={lufs}:TP=-2:LRA=11"
    if S.get("declick"):
        say("   de-click: removing small clicks / pops before the final loudness")
    r = subprocess.run([cfg.ffmpeg(), "-v", "error", "-y", "-i", raw, "-af", af,
                        "-ar", "48000", "-ac", "1", "-c:a", "pcm_s16le", final + ".part.wav"])
    if r.returncode == 0:
        os.replace(final + ".part.wav", final)
        os.remove(raw)
    else:
        os.replace(raw, final)
    open(os.path.join(out, "script.txt"), "w", encoding="utf-8").write(
        "".join(p["text"] + ("\n\n" if p["pause"] >= float(S["pause_paragraph"]) else "\n") for p in plan))
    json.dump(timing, open(os.path.join(out, "timing.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    try:                                                   # the word rack: words this voice keeps stumbling on
        rp = os.path.join(os.path.dirname(cfg.NARRATIONS), "word_rack.json")
        rack = json.load(open(rp, encoding="utf-8")) if os.path.exists(rp) else {}
        new = 0
        for k, v in infos.items():
            for w in v.get("missed") or []:
                r = rack.setdefault(w, {"count": 0, "examples": [], "narrations": []})
                if os.path.basename(out) in r["narrations"] and v.get("text") in r["examples"]:
                    continue
                r["count"] += 1; new += 1
                r["last"] = time.strftime("%Y-%m-%d")
                if v.get("text") and v["text"] not in r["examples"]:
                    r["examples"] = (r["examples"] + [v["text"]])[-3:]
                if os.path.basename(out) not in r["narrations"]:
                    r["narrations"] = (r["narrations"] + [os.path.basename(out)])[-10:]
        if new:
            json.dump(rack, open(rp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            say(f"   word rack: {new} stumble(s) noted -> {rp}")
    except Exception as e:
        say(f"   (word rack not updated: {e})")
    bad = [(int(k), v) for k, v in infos.items() if not v.get("ok", True) and int(k) <= len(plan)]
    rep = [f"narration of {spath}", f"voice: {ck_path or 'base'}", f"length {len(full) / sr / 60:.1f} min", ""]
    if bad:
        rep.append("sentences that still did not sound right after all retakes (listen; redo with --redo n,n):")
        rep += [f"  {k}: {v['text']}  (letters wrong {v.get('letter_error')}, {v.get('dur')} s)" for k, v in sorted(bad)]
    else:
        rep.append("every sentence passed the check.")
    open(os.path.join(out, "report.txt"), "w", encoding="utf-8").write("\n".join(rep) + "\n")
    if S.get("with_music") and S.get("music"):
        try:
            from . import music
            music.mix(final)
        except BaseException as e:
            say(f"   (music mix skipped: {e})")
    say(f"\nDONE: {final}  ({len(full) / sr / 60:.1f} min)")
    if bad:
        say(f"   {len(bad)} sentences to listen to: {', '.join(str(k) for k, _ in sorted(bad))}  (report.txt)")
