"""
script_align.py - use the narrator's own written script (Gemini) for the on-screen text.

The speech recogniser is only used for TIMING: its words (even if misspelled) are matched to the script's
words in order, so every script word gets the time it was spoken. Script parts that were not read aloud
are skipped automatically; ad-libs in the recording are simply not used.
"""
import re
import numpy as np

PAUSE = "‖"                      # internal marker for a breath / pause in the script


def _norm(w):
    import unicodedata
    w = unicodedata.normalize("NFD", w).lower()
    w = re.sub(r"[\u093c\u0901\u0902\u094d।,.?!\"'’‘“”…:;\-–—]", "", w)
    return w.translate(str.maketrans({"\u0940": "\u093f", "\u0942": "\u0941", "ई": "इ", "ऊ": "उ"}))


def _grams(w):
    w = _norm(w)
    return w, ({w[i:i + 2] for i in range(len(w) - 1)} or {w})


def _sim_g(ga, gb):
    (a, sa), (b, sb) = ga, gb
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    return 2 * len(sa & sb) / (len(sa) + len(sb))


def _sim(a, b):
    return _sim_g(_grams(a), _grams(b))


def parse_script(text):
    """-> list of sentences; each sentence = list of phrases; each phrase = list of words (display spelling)."""
    t = text.replace("﻿", "")
    t = re.sub(r"\(\s*\.{2,}\s*\)|\.{3,}|…", f" {PAUSE} ", t)                 # (...)  ...  = breath
    t = re.sub(r"\([^)]*\)|\[[^\]]*\]|\{[^}]*\}", f" {PAUSE} ", t)            # stage directions
    t = re.sub("[^\u0900-\u097F0-9A-Za-z%.,?!:'\u2019\u0964\\s" + PAUSE + "]", " ", t)   # emojis and symbols
    t = re.sub("(?i)(?:भाग|part|scene|दृश्य)[ \\t]*\\d+[ \\t]*:[^" + PAUSE + "\\n]*", f" {PAUSE} ", t)  # "भाग 2: ..." headings
    t = t.replace(":", " ")
    sentences = []
    for sent in re.split(r"[।?!]+|\n\s*\n|\.(?=\s|$)", t):
        phrases = []
        for ph in re.split(PAUSE + r"|,|\n", sent):
            ws = [w for w in ph.split() if re.search(r"[ऀ-ॿ0-9A-Za-z]", w)]
            if ws:
                phrases.append(ws)
        if phrases:
            sentences.append(phrases)
    return sentences


def align(script_text, asr_words, band=400):
    """asr_words: [{"w","start","end"}] in time order (from any recogniser).
    Returns segments like the transcript format: [{"start","end","text","words":[{"w","start","end","p"}]}]
    one segment per spoken script phrase-group (complete sentence, split at breaths if long)."""
    sents = parse_script(script_text)
    toks = []                                                  # (sentence, phrase, word)
    for si, s in enumerate(sents):
        for pi, ph in enumerate(s):
            for w in ph:
                toks.append((si, pi, w))
    A = [w for _, _, w in toks]
    B = [w["w"] for w in asr_words]
    GA = [_grams(w) for w in A]
    GB = [_grams(w) for w in B]
    n, m = len(A), len(B)
    if not n or not m:
        return []
    NEG = -1e9
    S = np.full((n + 1, m + 1), NEG, np.float32)
    T = np.zeros((n + 1, m + 1), np.int8)                      # 0 diag, 1 up (skip script), 2 left (skip asr)
    S[0, :] = -0.3 * np.arange(m + 1); S[:, 0] = -0.6 * np.arange(n + 1)
    T[0, 1:] = 2; T[1:, 0] = 1
    for i in range(1, n + 1):
        c = int(i * m / n)
        lo, hi = max(1, c - band), min(m, c + band)
        ga = GA[i - 1]
        for j in range(lo, hi + 1):
            sm = _sim_g(ga, GB[j - 1])
            d = S[i - 1, j - 1] + (2.0 * sm if sm >= 0.7 else -1.0)
            u = S[i - 1, j] - 0.6                              # script word not heard (or not read)
            l = S[i, j - 1] - 0.3                              # extra recognised word (ad-lib / noise)
            if d >= u and d >= l:
                S[i, j], T[i, j] = d, 0
            elif u >= l:
                S[i, j], T[i, j] = u, 1
            else:
                S[i, j], T[i, j] = l, 2
    match = [None] * n
    i, j = n, int(np.argmax(S[n]))
    while i > 0 and j > 0:
        t = T[i, j]
        if t == 0:
            if _sim_g(GA[i - 1], GB[j - 1]) >= 0.7:
                match[i - 1] = j - 1
            i -= 1; j -= 1
        elif t == 1:
            i -= 1
        else:
            j -= 1
    # Never invent timestamps for unrecognized script words. A sentence with missing words
    # is omitted below, so displayed text remains anchored to words the recognizer actually heard.
    times = [None] * n
    for k, mj in enumerate(match):
        if mj is not None:
            times[k] = [float(asr_words[mj]["start"]), float(asr_words[mj]["end"])]
    # group back into sentences; keep only what was actually spoken (enough anchored words)
    segs = []
    for si in range(len(sents)):
        idx = [k for k in range(n) if toks[k][0] == si]
        matched = sum(1 for k in idx if match[k] is not None)
        if matched < 3 or matched < 0.8 * len(idx) or any(times[k] is None for k in idx):
            continue
        words = [{"w": A[k], "start": round(times[k][0], 2), "end": round(max(times[k][1], times[k][0] + 0.08), 2),
                  "p": 1.0, "phrase": toks[k][1], "_m": match[k] is not None} for k in idx]
        # long sentence -> split at the script's own breaths into pieces of <= 16 words
        pieces, cur = [], []
        for w in words:
            if cur and w["phrase"] != cur[-1]["phrase"] and len(cur) >= 4 and len(cur) + 1 > 10:
                pieces.append(cur); cur = []
            cur.append(w)
        if cur:
            pieces.append(cur)
        for pc in pieces:
            q = sum(1 for w in pc if w["_m"]) / len(pc)
            for w in pc:
                w.pop("phrase", None); w.pop("_m", None)
            segs.append({"start": pc[0]["start"], "end": pc[-1]["end"], "text": " ".join(w["w"] for w in pc),
                         "words": pc, "source": "script", "sent": si, "match": round(q, 2)})
    segs.sort(key=lambda s: s["start"])
    return segs


SEP = re.compile(r"^\s*(?:-{3,}|={3,}|_{3,}|\*{3,}|#{3,}|(?:version|v|वर्ज़न|वर्जन|ड्राफ्ट|draft)\s*\d+\s*:?)\s*$", re.I | re.M)


def split_versions(text):
    """Several drafts pasted one after another, separated by a line like ----- or ===== or "Version 2"."""
    parts = [p.strip() for p in SEP.split(text)]
    return [p for p in parts if len(p.split()) >= 30] or [text]


def align_versions(versions, asr_words):
    """Align every draft to the recording and keep, for each moment of the recording, the draft that matches
    what was actually said best (sentence by sentence)."""
    if len(versions) == 1:
        return align(versions[0], asr_words)
    cands = []
    for vi, v in enumerate(versions):
        for sg in align(v, asr_words):
            sg["version"] = vi + 1
            cands.append(sg)
    cands.sort(key=lambda s: (-s["match"] * len(s["words"]) ** 0.5, s["start"]))
    chosen = []
    for sg in cands:                                       # best-matching sentences first, no time overlaps
        if all(sg["end"] <= c["start"] + 0.25 or sg["start"] >= c["end"] - 0.25 for c in chosen):
            chosen.append(sg)
    for c in chosen:                                       # keep sentence ids unique across drafts
        c["sent"] = c["version"] * 100000 + c["sent"]
    return sorted(chosen, key=lambda s: s["start"])


def coverage(segs, asr_words):
    said = sum(len(s["words"]) for s in segs)
    return said, len(asr_words)
