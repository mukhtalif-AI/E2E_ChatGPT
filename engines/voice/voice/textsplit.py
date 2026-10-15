"""textsplit.py - ONE way to cut Hindi text into sentences, used by the voice (timing of every sentence),
the shot planner (a picture starts at a sentence) and the captions - so they always agree. No imports."""
import re


def sentences(text, max_words=22):
    """'...' pauses kept; long sentences are split at commas."""
    t = re.sub(r"\s+", " ", str(text).replace("…", "...")).strip().strip('"“”')
    parts = [s.strip() for s in re.split(r"(?<=[।?!])\s+|(?<=\.\.\.)\s+(?=\S{2})", t) if s.strip()]
    out = []
    for s in parts:
        if len(s.split()) <= max_words:
            out.append(s)
            continue
        cur = ""
        for c in re.split(r"(?<=,)\s+", s):
            if cur and len((cur + " " + c).split()) > max_words:
                out.append(cur)
                cur = c
            else:
                cur = (cur + " " + c).strip()
        if cur:
            out.append(cur)
    out = [s for s in out if re.search("[\u0900-\u097Fa-zA-Z0-9]", s)]
    merged = []                                   # 1-3 word bits ("गाँव में।") join a neighbour: the voice
    for s in out:                                 # model is unstable on them and captions would flash
        if merged and (len(merged[-1].split()) < 4 or len(s.split()) < 3) and \
                len((merged[-1] + " " + s).split()) <= max_words:
            merged[-1] = merged[-1] + " " + s
        else:
            merged.append(s)
    return merged
