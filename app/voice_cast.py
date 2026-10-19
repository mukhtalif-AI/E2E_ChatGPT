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
Case study (19 Oct): _tmp\\ghost_voice_study_20261019 in the hub folder.
"""
import json, os, re, shutil, subprocess, tempfile, time

# per series: on, which roles get which preset, how many lines at most, how many asks must agree
CAST = {
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
# the sound: semitones and loudness of each layer, delays in ms; echo/whisper/growl 0-1 (0 = off)
PRESETS = {
    "ghost": {"dry": 0.8, "down_st": -4, "down_vol": 0.7, "up_st": 3, "up_vol": 0.35, "rev_vol": 0.2,
              "delay_ms": [25, 45, 60], "echo": 0.25, "whisper": 0, "growl": 0},
    "ghost_deep": {"dry": 0.6, "down_st": -6, "down_vol": 0.9, "up_st": 2, "up_vol": 0.25, "rev_vol": 0.25,
                   "delay_ms": [30, 50, 80], "echo": 0.35, "whisper": 0, "growl": 0},
    "ghost_airy": {"dry": 0.8, "down_st": -3, "down_vol": 0.4, "up_st": 4, "up_vol": 0.6, "rev_vol": 0.15,
                   "delay_ms": [20, 35, 50], "echo": 0.3, "whisper": 0.3, "growl": 0},
    "growl_male": {"dry": 0.5, "down_st": -5, "down_vol": 1.0, "up_st": 0, "up_vol": 0, "rev_vol": 0.1,
                   "delay_ms": [15, 0, 40], "echo": 0.15, "whisper": 0, "growl": 0.7},
    "whisper_female": {"dry": 0.0, "down_st": 0, "down_vol": 0, "up_st": 3, "up_vol": 0.9, "rev_vol": 0.1,
                       "delay_ms": [0, 0, 40], "echo": 0.3, "whisper": 1.0, "growl": 0},
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
