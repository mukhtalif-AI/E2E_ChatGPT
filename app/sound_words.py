"""sound_words.py - sound words written into a genre script ('ठक... ठक...', "ट्रिंग ट्रिंग", 'खुर्रर्र...') are not read
aloud: before the voice they are taken out of the spoken text (the sentence stays whole, a short pause takes their
place), and after the scenes are planned that moment gets a sound effect cue instead. Added 17 Oct 2026.

Experimental genre runs only (experimental_workspace._feedback_steps adds the two steps):
  exp_sound_words      before the voice: script -> script without the sound words, <name>.sound_words.json next to it,
                       the script as it was in Scripts\\_old\\<name>_before_sound_words_<time>.txt
  exp_sound_word_cues  before the cue timing: one cue per removed sound into shots.json (words = the words spoken
                       right after the sound, "in_gap" = self_review.cue_times puts it in the pause before them)
SOUND_WORDS is a Tinker table: kind -> {words: Hindi / Roman spellings, files: parts of library names, best first}.
"""
import json, os, re, shutil, time

SOUND_WORDS = {
    "on": True,
    "pause_s": 1.5,               # the held pause where the sound plays
    "kinds": {
        "knock": {"words": ["ठक", "ठकठक", "खट", "खटखट", "खटाक", "टक", "टकटक", "thak", "thak thak", "khat", "khatkhat",
                            "tak", "tak tak", "knock"],
                  "files": ["door_knock:", "knock"]},
        "bell_ring": {"words": ["ट्रिंग", "ट्रिन", "ट्रिंग्", "टिंग", "टिंगटिंग", "ट्रन", "tring", "trin", "ting", "ring ring",
                                "ding", "ding dong", "डिंग", "डिंग डॉन्ग", "डिंगडॉन्ग"],
                      "files": ["bike bell", "phone_ring:", "school bell", "bell"]},
        "temple_bell": {"words": ["टन", "टनटन", "टन्न", "घन", "घनघन", "tan tan", "tann", "ghan ghan"],
                        "files": ["bell_temple:", "bell"]},
        "scratch": {"words": ["खुर", "खुर्र", "खर", "खर्र", "खरखर", "खुरखुर", "किर्र", "किरकिर", "khurr", "khur", "kharr",
                              "khar khar", "kirr", "scratch"],
                    "files": ["drag", "grind", "scratch", "brush_soft:"]},
        "creak": {"words": ["चर्र", "चरर", "चूँ", "चूं", "चूँचूँ", "चरमर", "चर्रचूँ", "charr", "chun", "choon", "creak"],
                  "files": ["door_creak:", "creak"]},
        "tick": {"words": ["टिक", "टिकटिक", "टिक टिक", "tik tik", "tick tock", "टिकटॉक"],
                 "files": ["clock_tick:", "tick"]},
        "drip": {"words": ["टप", "टपटप", "टिप", "टिपटिप", "tap tap", "tip tip", "tapak"],
                 "files": ["water_drip:", "drip"]},
        "thud": {"words": ["धड़ाम", "धडाम", "धम", "धम्म", "धप", "धपधप", "dhadaam", "dhadam", "dham", "dhap", "thud"],
                 "files": ["body fall", "impact:", "thud", "drop"]},
        "glass": {"words": ["छन", "छनाक", "छन्न", "chhanak", "chhan"], "files": ["glass_break:", "glass"]},
        "heartbeat": {"words": ["धक", "धकधक", "dhak dhak", "dhak"], "files": ["heartbeat:", "heartbeat"]},
        "dog": {"words": ["भौं", "भौंभौं", "भौ", "bhow", "bhow bhow", "woof"], "files": ["dog_howl:", "dog"]},
        "crow": {"words": ["काँव", "कांव", "काँवकाँव", "kaanv", "kaw kaw"], "files": ["crow:"]},
    },
}

_WORD = re.compile(r"[\wऀ-ॣ०-ॿ]+")
QUOTES = "'\"‘’“”"
SEP = ".…,!?—–-:;।()"
_TAG = re.compile(r"^(\s*\[[^\]\n]{1,20}\]\s*)")
_PAUSE = re.compile(r"^\s*\(\s*(?:\.{2,}|…)\s*([0-9.]*)\s*s?\s*\)\s*$")
_SIGNS = set("ंँु़")        # ं ँ ु ़ : an onomatopoeia has no other vowel sign


def _norm(w):
    """ठक/ठक्क, खुर्रर्र/खुर्र, Tring/trrring -> one spelling: no virama, no repeated letters, lower case"""
    w = str(w).lower().replace("्", "")
    return re.sub(r"(.)\1+", r"\1", w)


def _table():
    t = SOUND_WORDS if isinstance(SOUND_WORDS, dict) else {}
    return t, t.get("kinds") or {}


def _index():
    """normalized single word -> kind (a phrase in the table counts word by word)"""
    _, kinds = _table()
    out = {}
    for kind, v in kinds.items():
        for w in (v or {}).get("words") or []:
            for part in re.split(r"[\s\-]+", str(w)):
                if part:
                    out.setdefault(_norm(part), kind)
    return out


def _generic(w):
    """a word not in the table that still looks like a sound: Hindi consonants only (ठक, छम), or drawn out (खुर्रर्र)"""
    if re.fullmatch(r"[a-z]{1,5}", w.lower()):
        return True
    if not re.search(r"[ऀ-ॿ]", w):
        return False
    core = [c for c in w if c != "्"]
    letters = [c for c in core if c not in _SIGNS]
    vowels = [c for c in letters if "ा" <= c <= "ौ" or c in "ॢॣ"]
    if len(core) - len(_norm(w)) >= 2:                   # three or more of one letter: drawn-out sound
        return True
    return not vowels and len(letters) <= 3


def find(line, idx=None):
    """-> [(start, end, kind, said, standalone)] sound runs in one spoken line"""
    idx = idx if idx is not None else _index()
    words = [(m.start(), m.end(), m.group()) for m in _WORD.finditer(line)]
    glued = lambda a, b: not re.search(r"[\w\u0900-\u097F]", line[words[a][1]:words[b][0]])
    drawn_w = lambda w: len([c for c in w if c != "\u094d"]) - len(_norm(w)) >= 2

    def sound(k):                                       # a table word, or a sound-like word said twice in a row / drawn out
        w = words[k][2]
        if idx.get(_norm(w)):
            return True
        if not _generic(w):
            return False
        twin = any(0 <= n < len(words) and glued(min(k, n), max(k, n)) and _norm(words[n][2]) == _norm(w)
                   for n in (k - 1, k + 1))
        return twin or drawn_w(w)
    runs, i = [], 0
    while i < len(words):
        if not sound(i):
            i += 1
            continue
        kind = idx.get(_norm(words[i][2]))
        j = i
        while j + 1 < len(words) and glued(j, j + 1) and sound(j + 1):
            kind = kind or idx.get(_norm(words[j + 1][2]))
            j += 1
        said = [x[2] for x in words[i:j + 1]]
        start, end = words[i][0], words[j][1]
        left, right = line[:start].rstrip(), line[end:].lstrip()
        lq, rq = left.rstrip(QUOTES + " "), right.lstrip(QUOTES + " ")
        quoted = left[-1:] in tuple(QUOTES) and right[:1] in tuple(QUOTES) if left and right else False
        # on its own: after a dash / full stop / line start, and before a pause or the end ("वह 'हम-हम' करता" is a word)
        standalone = (not lq or lq[-1] in SEP) and (not rq or rq[0] in SEP or quoted)
        repeated = len(said) > 1 and len({_norm(x) for x in said}) < len(said)
        drawn = any(drawn_w(x) for x in said)
        if kind and (standalone or repeated):
            runs.append((start, end, kind, said, standalone))
        elif not kind and standalone and (repeated or drawn):
            runs.append((start, end, "", said, True))
        i = j + 1
    return runs


def _files(cfg, kind):
    """library files for this kind of sound, best pattern first"""
    import tools
    _, kinds = _table()
    lib = tools._sfx_names(cfg)
    for pat in ((kinds.get(kind) or {}).get("files") or []):
        hit = sorted(k for k in lib if str(pat).lower() in k.lower())
        if hit:
            return [lib[k] for k in hit]
    return []


def _first_words(text, n=4):
    return " ".join(_WORD.findall(text)[:n])


def _last_words(text, n=4):
    w = _WORD.findall(text)
    return " ".join(w[-n:])


def strip_text(text, pause_s=1.5, idx=None):
    """-> (new script text, removals). Each removal: kind, said, line (1-based in the new text), next/prev words."""
    idx = idx if idx is not None else _index()
    lines = text.replace("\r\n", "\n").split("\n")
    out, removed, kept = [], [], []
    for ln in lines:
        tag_m = _TAG.match(ln)
        tag = tag_m.group(1) if tag_m else ""
        body = ln[len(tag):]
        if _PAUSE.match(ln) or not re.search(r"[ऀ-ॿa-zA-Z]", body):
            out.append(ln)
            continue
        runs = find(body, idx)
        if not runs:
            out.append(ln)
            continue
        pieces, pos, cut = [], 0, []
        for s, e, kind, said, standalone in runs:
            if not standalone:
                after = body[e:]
                m = re.match(r"\s+की\s+(?=आवाज़|आवाज)", after)
                if m:                                       # 'खट-खट की आवाज़ आई' -> 'एक आवाज़ आई'
                    pieces.append(("text", body[pos:s] + "एक "))
                    pos = e + m.end()
                    cut.append((kind, said, False, _first_words("एक " + after[m.end():])))
                    pieces.append(("cue", len(cut) - 1))
                    continue
                kept.append(" ".join(said))                 # 'दिल धक-धक कर रहा था': part of the sentence, kept
                continue
            pieces.append(("text", body[pos:s]))
            pieces.append(("sound", len(cut)))
            cut.append((kind, said, True, ""))
            pos = e
        if not cut:
            out.append(ln)
            continue
        pieces.append(("text", body[pos:]))
        # rebuild: text around a removed sound is split at it, with a held pause where the sound plays
        segs, cur, marks = [], "", []
        for kind_, v in pieces:
            if kind_ == "text":
                cur += v
            elif kind_ == "cue":
                marks.append((len(segs), len(cur), v))      # cue on the words that follow, no pause
            else:
                segs.append(cur)
                cur = ""
                marks.append((len(segs), 0, v))
        segs.append(cur)
        new_lines, seg_line = [], {}
        for k, sg in enumerate(segs):
            sg = sg.strip()
            sg = sg.lstrip(SEP + QUOTES + " ").strip() if k > 0 else sg
            if k < len(segs) - 1:
                sg = sg.rstrip(" —–-:;,'\"‘“").strip()
                sg = sg.rstrip("'\"’”").strip()
            if k > 0:
                if new_lines and _PAUSE.match(new_lines[-1]):
                    pass
                else:
                    new_lines.append(f"(... {pause_s:.2f})")
            if re.search(r"[ऀ-ॿa-zA-Z0-9]", sg):
                if k < len(segs) - 1 and sg[-1] not in "।.!?…":
                    sg += "।"
                seg_line[k] = len(new_lines)
                new_lines.append(tag + sg)
        base = len(out)
        out.extend(new_lines)
        for seg_k, char_pos, ci in marks:
            kind, said, gap, nxt = cut[ci]
            removed.append({"kind": kind, "said": " ".join(said), "in_gap": gap, "_next": nxt, "_seg": seg_k, "_base": base,
                            "_lines": new_lines, "_seg_line": seg_line})
    # collapse two pauses in a row (a removed line between two held pauses), keep the longer
    final = []
    for ln in out:
        if final and _PAUSE.match(ln) and _PAUSE.match(final[-1]):
            a, b = _PAUSE.match(final[-1]).group(1), _PAUSE.match(ln).group(1)
            try:
                if float(b or 0) > float(a or 0):
                    final[-1] = ln
            except ValueError:
                pass
            continue
        final.append(ln)
    spoken = lambda t: _TAG.sub("", t) if not _PAUSE.match(t) else ""
    for r in removed:                                   # words around each sound, from the new spoken text
        lines_r, seg_line = r.pop("_lines"), r.pop("_seg_line")
        seg_k, base, own = r.pop("_seg"), r.pop("_base"), r.pop("_next")
        after_k = next((k for k in sorted(seg_line) if k >= seg_k), None)
        before_k = next((k for k in sorted(seg_line, reverse=True) if k < seg_k), None)
        nxt = _TAG.sub("", lines_r[seg_line[after_k]]) if after_k is not None else ""
        prv = _TAG.sub("", lines_r[seg_line[before_k]]) if before_k is not None else ""
        if not nxt:                                     # the sound was a line of its own: next spoken line
            tail = "\n".join(spoken(x) for x in out[base + len(lines_r):])
            nxt = next((t for t in tail.split("\n") if t.strip()), "")
        if not prv:
            head = [spoken(x) for x in out[:base]]
            prv = next((t for t in reversed(head) if t.strip()), "")
        r["next_words"] = own or _first_words(nxt)
        r["prev_words"] = _last_words(prv)
    return "\n".join(final), removed, kept


# ------------------------------------------------------------------ steps
def _json_path(ctx, name):
    import tools
    return os.path.join(tools.ctx_folder(ctx, "scripts"), tools.slug(name) + ".sound_words.json")


def step(ctx, name, genre=""):
    """before the voice: sound words out of the spoken script, a pause in their place, the list kept for the cues"""
    import tools
    cfg, say = ctx["cfg"], ctx["say"]
    t, _ = _table()
    if not t.get("on", True):
        return say("   sound words: off (Tinker table sound_words.SOUND_WORDS)")
    p = os.path.join(tools.ctx_folder(ctx, "scripts"), tools.slug(name) + ".txt")
    if not os.path.exists(p):
        return say("   (no script - sound words not checked)")
    text = open(p, encoding="utf-8").read()
    new, removed, kept = strip_text(text, float(t.get("pause_s") or 1.5))
    for k in kept:
        say(f"   sound word kept (part of the sentence): '{k}'")
    if not removed:
        say("   no sound words read aloud in the script")
        return True
    old = os.path.join(os.path.dirname(p), "_old")
    os.makedirs(old, exist_ok=True)
    keep = os.path.join(old, f"{tools.slug(name)}_before_sound_words_{time.strftime('%Y%m%d_%H%M%S')}.txt")
    shutil.copy2(p, keep)
    for r in removed:
        f = _files(cfg, r["kind"]) if r["kind"] else []
        r["file"] = f[0] if f else ""
        say(f"   sound words '{r['said']}' taken out of the speech -> "
            + (f"{r['kind']} effect {os.path.basename(r['file'])}" if r["file"] else
               (f"no {r['kind']} effect in the library, words only dropped" if r["kind"] else
                "not a known sound (no effect), words only dropped"))
            + (f" before '{r['next_words']}'" if r["next_words"] else ""))
    open(p + ".part", "w", encoding="utf-8").write(new)
    os.replace(p + ".part", p)
    json.dump({"script": p, "original": keep, "made": time.strftime("%Y-%m-%d %H:%M:%S"), "genre": genre,
               "removed": removed}, open(_json_path(ctx, name), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    say(f"   {len(removed)} sound word(s) will be heard as effects, not read aloud (script as it was: {keep})")
    return True


def cues(ctx, name, pictures):
    """after the scene plan: each removed sound becomes a cue in shots.json at the words spoken right after it"""
    import tools
    cfg, say = ctx["cfg"], ctx["say"]
    jp = _json_path(ctx, name)
    if not os.path.exists(jp):
        return True
    sj = os.path.join(pictures or "", "shots.json")
    try:
        rows = json.load(open(jp, encoding="utf-8")).get("removed") or []
        meta = json.load(open(sj, encoding="utf-8"))
    except (OSError, ValueError) as e:
        return say(f"   (sound word effects skipped: {e})")
    shots = [s for s in meta.get("shots") or [] if isinstance(s, dict)]
    added, missing, last = 0, 0, 0
    for r in rows:
        f = r.get("file") or ""
        if f and not os.path.exists(f) or not f and r.get("kind"):
            alt = _files(cfg, r.get("kind")) if r.get("kind") else []
            f = alt[0] if alt else ""
        if not f:
            continue
        w = r.get("next_words") or r.get("prev_words") or ""
        hit = [i for i, s in enumerate(shots) if w and tools._words_in(s.get("text", ""), w)]
        near = lambda i: " ".join(shots[j].get("text", "") for j in range(max(0, i - 1), i + 1))
        both = [i for i in hit if r.get("prev_words") and tools._words_in(near(i), r["prev_words"])]
        pick = next((i for i in (both or hit) if i >= last), (both or hit or [None])[0])
        if pick is None:
            missing += 1
            say(f"   ⚠ sound '{r.get('said')}': the words after it ('{w}') are not in any scene - no effect")
            continue
        last = pick
        cs = shots[pick].setdefault("cues", [])
        if any(c.get("words") == w and c.get("file") == f for c in cs if isinstance(c, dict)):
            continue
        cs.append({"words": w, "file": f, "from_script": r.get("said", ""), "in_gap": bool(r.get("in_gap"))
                   and w == r.get("next_words")})
        added += 1
        say(f"   scene {pick + 1}: {os.path.basename(f)} where the script said '{r.get('said')}'")
    if added:
        json.dump(meta, open(sj, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    say(f"   {added} sound effect(s) from the script's sound words" + (f", {missing} not placed" if missing else ""))
    return True
